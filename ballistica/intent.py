"""LLM-based fallback for understanding voice commands that don't match
one of the fast, free, deterministic regex patterns in cli.py.

Important boundary: this module never computes a ballistic answer
itself, and never states a specific numeric safety judgment on a
reloading question (see extract_intent's system prompt for the exact
rule Rick decided on 2026-09-05) -- extracted parameters for a real
command get handed to the exact same deterministic Python functions the
regex path already calls, and the physics stays 100% deterministic
regardless of how the intent was recognized. As of 2026-09-05,
extract_intent() ALSO covers genuine open-ended conversation (tool_choice
"auto", not "any" -- see its own docstring): it's no longer only a
classifier forced to pick one of a fixed set of commands, it can just
talk when nothing tool-shaped was actually meant. That's the "what did
they actually ask for, or did they just want to talk" layer; the answer
to any real command still comes from deterministic code either way.

Runs on Claude (via the Messages API's tool use), not OpenAI -- swapped
from gpt-4o-mini after Rick found it too literal on loose, natural
phrasing ("AR-15 18 inch Faxon" got shredded across unrelated fields
instead of read as one rifle name). Whisper (STT) and TTS stay on
OpenAI; only the "what did they mean" layer moved.
"""
from __future__ import annotations

import logging

import anthropic

from .anthropic_client import get_anthropic_client

# Every failure path here used to swallow the real exception and just
# return None/{} -- which meant a genuine auth/billing/API error and a
# clean "nothing matched" looked identical from the outside, including in
# Render's own log stream (nothing was ever written to it). Logged at
# ERROR with the traceback so an actual failure is visible in production
# logs, not just inferred from the caller getting "didn't understand".
logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You are Ballistica's voice assistant at a shooting range or reloading "
    "bench -- not just a command parser. The shooter's speech has already "
    "been transcribed (it may contain transcription errors, informal "
    "phrasing, or filler words) and didn't match a fast exact-phrase "
    "pattern, so it's routed to you. "
    "If it's a real ballistics/rifle/load/conditions request, use exactly "
    "one matching tool -- only fill in parameters actually stated or "
    "clearly implied, leave everything else out. "
    "Otherwise -- small talk, a genuine question, reloading/shooting "
    "conversation, anything that isn't a command -- don't force it into a "
    "tool. Just respond in your own natural voice, like a knowledgeable "
    "range partner. This is spoken aloud by text-to-speech, not read on a "
    "screen: 2-3 short sentences MAX, like you'd actually say out loud "
    "standing at the bench -- never a list, never multiple paragraphs, "
    "never markdown formatting of any kind (no asterisks, no headers, no "
    "bullet points, no literal line breaks). Plain spoken sentences only, "
    "plain ASCII punctuation (a plain hyphen if you need one, never an "
    "em-dash or curly quotes). Warm, capable, a little dry humor is fine, "
    "never corny or over-the-top. General reloading/shooting conversation "
    "and terminology are fine to discuss -- what pressure signs typically "
    "look like, how load development generally works, and so on -- but "
    "keep even that to the length of one real spoken turn, not a briefing; "
    "say the single most useful thing, not everything you know. "
    "Hard rule, no exceptions, for both tool use and free conversation: "
    "never state, estimate, or imply a specific numeric ballistics value "
    "you haven't actually computed via a tool (yardage, elevation, MOA, "
    "mils, clicks, drop, windage, velocity, angle, temperature, pressure) "
    "-- you have no ability to compute one in conversation, and guessing "
    "one would be dangerous. This rule is about NEW values only -- it "
    "never applies to stating a fact already saved in the shooter's own "
    "rifle/load profile (bullet weight, BC, muzzle velocity, zero "
    "distance, scope height, click value, and so on) or already-set "
    "current conditions -- those aren't computed or guessed, they're "
    "just read back, exactly what get_status is for. Asked 'what load "
    "are we using' or 'what's the velocity on this load', answer "
    "directly from get_status -- never deflect a question about "
    "already-known saved data to a reloading manual or 'I can't give "
    "specific data'; that deflection is only for genuinely unknown or "
    "safety-judgment values (see the next rule). "
    "Second hard rule, no exceptions: never state, confirm, or imply a "
    "specific numeric safety judgment about whether a charge weight or "
    "load is safe -- not a yes/no, not a number, and not even a "
    "qualitative reassurance like 'that sounds fine' or 'you're probably "
    "okay' (that's just the same judgment without a digit in it). Always "
    "redirect to the shooter's own reloading manual or published "
    "reference data as the source of truth for anything safety-critical, "
    "the way a knowledgeable person naturally defers when the stakes are "
    "too high to answer from memory -- an honest redirect, not a dead-end "
    "refusal. Example: asked 'I don't see any pressure signs, is it "
    "possible we could bump this up?', a good reply is close to 'Worth "
    "checking your reloading manual to see how close you are to max "
    "load -- I don't want to rely on memory for something like that,' "
    "not a yes/no and not a number. "
    "Third hard rule, no exceptions: if the shooter states a new wind or "
    "atmospheric condition value (speed, clock direction, temperature, "
    "pressure, humidity, altitude), you MUST call set_wind or "
    "set_conditions -- never just acknowledge it conversationally. This "
    "applies even if a similar value was already set earlier in this "
    "conversation; a restated or corrected value always means call the "
    "tool again with the new number, not repeat back what's already "
    "there from memory. Found live (2026-09-06): stating new wind values "
    "multiple times in a row got acknowledged in reply but the solution "
    "never actually recalculated -- that's this exact failure mode. "
    "Fourth hard rule, no exceptions: never claim, guess, or imply that a "
    "rifle or load does or doesn't exist, or ask the shooter for more "
    "identifying details about one, without first calling switch_load, "
    "switch_rifle, or get_status to actually check -- you have no way to "
    "know what's saved without calling one of those, the same way you "
    "have no way to compute a ballistics value without a tool. Found "
    "live (2026-09-06): asked to switch to a load that was genuinely "
    "saved, you responded 'I'm not finding a load with that in the "
    "system, do you remember any other details' -- a fabricated claim, "
    "since no tool was ever called to check. If a switch tool call "
    "itself comes back saying nothing matched, trust that real result "
    "and offer what it says IS saved instead of guessing further. "
    "Range talk uses yards, mils/MRAD, MOA, clicks, wind speed/direction "
    "(o'clock), temperature (F), humidity (%), altitude (ft), and "
    "barometric pressure (inHg)."
)

_TOOLS = [
    {
        "name": "get_drop_at_range",
        "description": "Get the drop/windage solution at a specific distance. ANY utterance that "
                        "states or clearly implies a specific yardage and wants a solution belongs "
                        "here, no matter how it's framed or what else is said alongside it -- e.g. "
                        "'this is what I'm shooting, four hundred yards, give me a solution' is "
                        "still this, not get_status, even though it opens like a general "
                        "description. The distance is the signal; use get_status only when NO "
                        "distance is stated anywhere in the utterance.",
        "input_schema": {
            "type": "object",
            "properties": {"range_yd": {"type": "number", "description": "Target distance in yards"}},
            "required": ["range_yd"],
        },
    },
    {
        "name": "switch_load",
        "description": "Switch the active ammunition load on the current rifle.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Fuzzy name/description of the load, e.g. '21 grain' or 'H335'"}},
            "required": ["query"],
        },
    },
    {
        "name": "switch_rifle",
        "description": "Switch the active rifle. If the SAME utterance also volunteers details for "
                        "a NEW load on that rifle (not just switching to one that's already saved --"
                        "e.g. 'switching to the 300 blackout, first load is the 110s at 24 grains of "
                        "Lil Gun, zero at 50'), also fill in the matching new_load_* fields so that "
                        "information isn't lost just because it rode along with a rifle switch. Leave "
                        "every new_load_* field out entirely if no new load info was actually stated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Fuzzy name of the rifle"},
                "new_load_name": {"type": "string"},
                "new_load_bullet_weight_gr": {"type": "number"},
                "new_load_bullet_type": {"type": "string"},
                "new_load_bc": {"type": "number"},
                "new_load_drag_model": {"type": "string", "description": "G1 or G7"},
                "new_load_muzzle_velocity_fps": {"type": "number"},
                "new_load_zero_distance_yd": {"type": "number"},
                "new_load_powder": {"type": "string"},
                "new_load_powder_charge_gr": {"type": "number"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_minimum_spread_zero",
        "description": "Find the zero distance that minimizes total vertical spread out to a max range.",
        "input_schema": {
            "type": "object",
            "properties": {"max_range_yd": {"type": "number"}},
            "required": ["max_range_yd"],
        },
    },
    {
        "name": "solve_incline_angle",
        "description": "Back-calculate an uphill/downhill shooting angle from an observed click "
                        "difference between a reference distance and the actual target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "observed_diff_clicks": {"type": "number"},
                "line_of_sight_distance_yd": {"type": "number"},
                "reference_distance_yd": {"type": "number", "description": "Defaults to 100 if not stated"},
            },
            "required": ["observed_diff_clicks", "line_of_sight_distance_yd"],
        },
    },
    {
        "name": "set_conditions",
        "description": "Update current atmospheric conditions. Only include fields actually "
                        "mentioned -- this merges into whatever conditions are already set, it "
                        "does not require restating everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "temp_f": {"type": "number"},
                "pressure_inhg": {"type": "number"},
                "altitude_ft": {"type": "number"},
                "humidity_pct": {"type": "number"},
            },
        },
    },
    {
        "name": "set_wind",
        "description": "Set wind speed and direction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "speed_mph": {"type": "number"},
                "clock_hours": {"type": "number", "description": "Wind clock position, 0-12 "
                                                                  "(12 = headwind, 3 = full crosswind from the right)"},
            },
            "required": ["speed_mph", "clock_hours"],
        },
    },
    {
        "name": "start_load_setup",
        "description": "Begin a guided voice interview to add a new ammunition load. Covers both an "
                        "explicit trigger phrase ('let's log a new load', 'I want to add a load') AND "
                        "a fully spoken-out load description with no trigger phrase at all -- e.g. "
                        "'Sierra Match King 77 grain, run with H335 at 23.5 grains of powder' names a "
                        "bullet, powder, and charge in one breath and should route here just the same, "
                        "not be treated as small talk. Whatever details were actually said get "
                        "extracted automatically once this fires (bullet weight/type, powder, charge, "
                        "etc.) -- only the fields still missing get asked for afterward, so this never "
                        "throws away information that was already volunteered.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_rifle_setup",
        "description": "Begin a guided voice interview to add a new rifle "
                        "(e.g. 'set up a new rifle', 'let's build a new rifle profile').",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_calibration",
        "description": "Begin a live chronograph calibration session for the active load "
                        "(e.g. 'let's chrono this load', 'start calibration', "
                        "'true up the velocity on this one').",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "repeat_last_solution",
        "description": "Re-speak the most recently given drop-at-range solution (elevation and/or "
                        "windage) without recalculating it -- for phrasing like 'say that again', "
                        "'what was the elevation again', or 'repeat the windage'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "part": {
                    "type": "string",
                    "enum": ["elevation", "windage", "solution"],
                    "description": "Which part to repeat; 'solution' for the full elevation+windage callout.",
                },
            },
        },
    },
    {
        "name": "update_rifle_field",
        "description": "Change one or more fields on the ACTIVE rifle's existing saved profile -- "
                        "e.g. 'change the twist rate to 1:8', 'switch my reticle to MRAD', 'the scope "
                        "height is actually 2.6 inches'. Only for editing a rifle that's already "
                        "saved -- use start_rifle_setup instead for adding a brand new rifle. There "
                        "was previously no command for this at all, which read as edits silently not "
                        "saving when someone tried to speak a correction to an existing rifle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope_height_in": {"type": "number"},
                "caliber": {"type": "string"},
                "barrel_length_in": {"type": "number"},
                "twist_rate": {"type": "string", "description": "e.g. '1:7'"},
                "click_value_mrad": {"type": "number"},
                "reticle_unit": {"type": "string", "enum": ["MRAD", "MOA"]},
                "optic_type": {"type": "string", "enum": ["scope", "red_dot"]},
                "scope_make": {"type": "string"},
                "scope_model": {"type": "string"},
                "magnification": {"type": "string"},
                "objective_lens_mm": {"type": "number"},
                "focal_plane": {"type": "string", "enum": ["FFP", "SFP"]},
                "reticle_type": {"type": "string"},
                "dot_size_moa": {"type": "number"},
                "has_suppressor": {"type": "boolean"},
                "suppressor_type": {"type": "string", "description": "Open text -- brand if known, "
                                                                       "otherwise a generic/custom "
                                                                       "description."},
            },
        },
    },
    {
        "name": "delete_rifle",
        "description": "Delete a saved rifle/pistol profile. Covers phrasing that doesn't lead with "
                        "the delete verb, or that never uses the word 'rifle'/'pistol' at all -- e.g. "
                        "'can you get rid of the 5.7x28 11 inch' or 'that Taurus in black, get rid of "
                        "it'. Pull whatever identifying details were actually said (caliber, barrel "
                        "length, manufacturer, color, model) into query verbatim -- the fuzzy match "
                        "against the saved name happens downstream, not here. Leave query empty only "
                        "if nothing identifying was said at all (e.g. bare 'delete it').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Whatever identifying words were said, "
                                                             "e.g. '5.7x28 11 inch' or 'the black one'"},
            },
        },
    },
    {
        "name": "get_status",
        "description": "Report the active rifle, load, and current conditions -- only when NO "
                        "specific distance is mentioned anywhere in the utterance. If a distance "
                        "is stated, that's always get_drop_at_range instead, even if the phrasing "
                        "sounds like a general description of what's being shot. This is ALSO the "
                        "right tool for any question about the currently active/saved rifle or load "
                        "itself -- 'what load are we using', 'what load were we using on that', "
                        "'what rifle is this', 'what's the bullet weight on this load' -- these ask "
                        "about data already saved in the shooter's own profile, not a value to "
                        "compute or a lookup in a reloading manual. Never deflect a question like "
                        "this to 'check your manual' or 'I can't give specific data' -- the app "
                        "already has and can state exactly what's saved.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _first_tool_use(response):
    """The first tool_use content block in a Messages API response, or
    raises IndexError if none is present -- shouldn't happen with
    tool_choice={"type": "any"}, but callers already treat that the same
    as any other API-shape surprise."""
    for block in response.content:
        if block.type == "tool_use":
            return block
    raise IndexError("no tool_use block in response")


def extract_intent(text: str, history: list[dict] | None = None) -> tuple[str, dict] | None:
    """Returns (tool_name, arguments) for the best-matching command, or
    ("converse", {"reply": <text>}) if the model responded in its own
    conversational voice instead of calling a tool -- tool_choice is
    "auto" here, not "any" like every other extraction in this module,
    specifically so it's ALLOWED to just talk when nothing tool-shaped was
    actually meant (2026-09-05, replacing the old no_match tool + a
    separate generate_warm_reply personality call with one unified path).
    Returns None only if the call failed outright (network/API error).

    history: recent conversational exchanges (BallisticaCLI._chat_history)
    to include as prior turns -- lets a follow-up like "is that safe to
    bump up" resolve what "that" refers to. Omit/empty for a fresh
    conversation; ordinary ballistics commands don't populate this."""
    try:
        client = get_anthropic_client()
        messages = list(history or []) + [{"role": "user", "content": text}]
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=messages,
            tools=_TOOLS,
            tool_choice={"type": "auto"},
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.name, dict(block.input)
        # No tool call -- tool_choice="auto" let the model just respond
        # conversationally instead of forcing it into one of the tools.
        reply = "".join(b.text for b in response.content if b.type == "text").strip()
        return ("converse", {"reply": reply}) if reply else None
    except (anthropic.AnthropicError, TypeError, IndexError, AttributeError):
        # TypeError (not an AnthropicError subclass) is what the SDK
        # actually raises for a missing/misconfigured ANTHROPIC_API_KEY --
        # reproduced directly. Caught here so that misconfiguration
        # degrades to "didn't understand" like any other fallback failure,
        # not a 500 in the middle of a voice conversation -- but logged
        # first so the failure is actually visible in production logs
        # instead of just inferred from "didn't understand" on the phone.
        logger.exception("extract_intent failed for %r", text)
        return None


# --- Calibration turn fallback ------------------------------------------
#
# Live-tested (2026-09-05, Rick's first real-voice Session Mode run): after
# reading off ~10 shots, natural ways of signaling "I'm done" -- "that's
# ten shots", "I think that's good", "that's enough", "okay stop there" --
# all missed cli.py's anchored end-calibration regex and came back
# "Didn't catch a number there," even though a person would obviously
# understand every one of them. That regex was the ONE modal flow in this
# app that never got a fast-path-then-LLM-fallback treatment (setup fields,
# confirmations, and general intent all already have one) -- this closes
# that gap the same way, only invoked when cli.py's own fast, free regex
# checks (cancel/confirm/end-phrase/average/discard/a bare number) all miss.

_CALIBRATION_SYSTEM_PROMPT = (
    "The shooter is in the middle of reading chronograph shot velocities out "
    "loud to log a load's muzzle velocity. Their last utterance didn't "
    "contain a number and didn't match any of the app's known control "
    "phrases, so it's being routed to you as a fallback. Classify it as "
    "exactly one of: they're signaling they're done reading shots and want "
    "the average/to wrap up (e.g. 'that's ten shots', 'I think that's "
    "good', 'that's enough', 'stop there', 'go ahead and save it'); they "
    "want to throw out the last shot; they want to abandon calibration "
    "entirely without saving; or none of the above (small talk, unrelated "
    "question, genuinely unclear noise/mistranscription)."
)

_CALIBRATION_TOOLS = [
    {
        "name": "end_calibration",
        "description": "Shooter is done reading shots for this string and wants to see the "
                        "average or move on -- any natural way of signaling that, not just an "
                        "exact phrase.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "discard_last_shot",
        "description": "Shooter wants to throw out the most recently read shot.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_calibration",
        "description": "Shooter wants to abandon this calibration session entirely, nothing saved.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "unclear",
        "description": "Doesn't match any of the above and isn't a shot number either.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def classify_calibration_turn(text: str) -> str | None:
    """Returns one of "end_calibration"/"discard_last_shot"/
    "cancel_calibration"/"unclear", or None on an outright API failure
    (network/auth) -- distinct from a clean "unclear", same distinction
    extract_intent() makes for the same reason."""
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=64,
            system=_CALIBRATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            tools=_CALIBRATION_TOOLS,
            tool_choice={"type": "any"},
        )
        return _first_tool_use(response).name
    except (anthropic.AnthropicError, TypeError, IndexError, AttributeError):
        logger.exception("classify_calibration_turn failed for %r", text)
        return None


# --- Load/rifle setup slot extraction -----------------------------------
#
# Used turn-by-turn by cli.py's guided setup flow (see _SetupSession /
# _handle_setup_turn): each call extracts whatever profile fields the
# shooter mentioned in one utterance, however many or few. It never
# decides *whether* a value is valid (a bad drag_model or a non-positive
# BC still gets caught by Load/Rifle's own __post_init__ in cli.py) -- it
# only pulls out what was said.

_SETUP_SYSTEM_PROMPT = (
    "You are extracting structured fields from one turn of a spoken, "
    "conversational rifle/load setup interview. The shooter is answering "
    "whatever was just asked, but may volunteer extra fields in the same "
    "breath, or restate/correct a field they already gave. Use the given "
    "tool once, filling in only the fields actually stated or clearly "
    "implied this turn -- never invent or guess a value for anything not "
    "mentioned, and never fill in a field with a default just because it's "
    "common (e.g. don't assume MRAD or G1 unless they said so). "
    "The 'name' field is a free-form label the shooter is choosing for "
    "this rifle/load, not a set of sub-fields to parse apart -- if they say "
    "something like 'call it the AR-15 18 inch Faxon', that whole phrase is "
    "the name. Only split a token out into caliber/twist_rate/bullet_type/"
    "etc. instead of the name when it's clearly stated as that specific "
    "field (e.g. 'caliber is 6.5 Creedmoor'). "
    "The user message tells you which field was just asked about. If that "
    "field is 'name' and nothing in the answer reads as a distinct, "
    "separate name (the whole thing sounds like a description of the "
    "caliber/platform/type instead, e.g. 'pistol caliber carbine, nine "
    "millimeter'), use the answer as the name anyway, cleaned up as a "
    "short label -- in addition to extracting it into caliber/etc. too, "
    "not instead of. Leaving name blank here just means asking the exact "
    "same question again, which reads as not having heard the shooter at "
    "all even though the words were understood. "
    "If this turn doesn't actually answer or add anything (e.g. the shooter "
    "asked a question back, or said something unrelated to any field), call "
    "the tool with no fields set at all -- never fill a field with a "
    "placeholder like 'unknown', 'n/a', or similar just because the tool "
    "call needs some argument. An omitted field and a placeholder value are "
    "not the same thing to the caller: omitting means 'not stated this "
    "turn', a placeholder would be read as a real answer and could "
    "overwrite one."
)

_LOAD_SETUP_TOOL = {
    "name": "provide_load_fields",
    "description": "Record any ammunition load fields mentioned in this utterance.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "A short label for this load, e.g. '23.5gr H335'"},
            "bullet_weight_gr": {"type": "number"},
            "bc": {"type": "number", "description": "Ballistic coefficient, e.g. 0.362"},
            "drag_model": {"type": "string", "enum": ["G1", "G7"]},
            "muzzle_velocity_fps": {"type": "number"},
            "zero_distance_yd": {"type": "number"},
            "bullet_type": {"type": "string", "description": "e.g. '77gr Sierra MatchKing'"},
            "powder": {"type": "string"},
            "powder_charge_gr": {"type": "number"},
            "notes": {"type": "string"},
        },
    },
}

_RIFLE_SETUP_TOOL = {
    "name": "provide_rifle_fields",
    "description": "Record any rifle fields mentioned in this utterance.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "scope_height_in": {"type": "number", "description": "Scope height above bore, in inches"},
            "optic_type": {
                "type": "string",
                "enum": ["scope", "red_dot"],
                "description": "'scope' for any magnified optic, 'red_dot' for a fixed-1x reflex/"
                                "holographic sight (e.g. Holosun, Aimpoint, EOTech). Infer this from "
                                "the make/model or the word 'red dot' even if not stated explicitly.",
            },
            "caliber": {"type": "string"},
            "barrel_length_in": {"type": "number"},
            "twist_rate": {"type": "string", "description": "e.g. '1:7'"},
            "click_value_mrad": {"type": "number"},
            "reticle_unit": {"type": "string", "enum": ["MRAD", "MOA"]},
            "scope_make": {"type": "string"},
            "scope_model": {"type": "string"},
            "magnification": {"type": "string", "description": "e.g. '5-25x' -- magnified scopes only"},
            "objective_lens_mm": {"type": "number"},
            "focal_plane": {"type": "string", "enum": ["FFP", "SFP"], "description": "Magnified scopes only"},
            "reticle_type": {"type": "string", "description": "Magnified scope: crosshair pattern, e.g. "
                                                                "'MOA Christmas tree'. Red dot: the dot/"
                                                                "circle pattern, e.g. '65 MOA circle + dot'"},
            "dot_size_moa": {"type": "number", "description": "Red dot only -- the dot's size in MOA"},
            "has_suppressor": {"type": "boolean", "description": "Whether this rifle runs a suppressor. "
                                                                   "A property of the rifle itself, not any "
                                                                   "one load -- the same can stays attached "
                                                                   "regardless of which load is fired."},
            "suppressor_type": {"type": "string", "description": "Open text -- a real brand if there is "
                                                                   "one, or a generic/custom description "
                                                                   "(e.g. 'custom build', 'not sure') if "
                                                                   "not. Never force a brand guess."},
        },
    },
}


def extract_setup_fields(text: str, kind: str, asking_about: str | None = None) -> dict | None:
    """Returns whatever Load/Rifle fields (kind: "load" or "rifle") were
    mentioned in this utterance, or None on an outright API failure. An
    utterance that genuinely stated nothing usable comes back as {},
    distinct from None only in that the caller doesn't need to treat it
    as a hard error -- both currently get the same "didn't catch that"
    handling in cli.py, but keeping them distinct leaves room to do
    better later without another API shape change.

    asking_about: the field cli.py's setup flow just prompted for (see
    _next_field_to_ask()). Passed through to the model so it can tell
    "answers the question but isn't shaped like the target field" (e.g.
    a caliber description answering "what's the name") apart from
    "doesn't answer it at all" -- confirmed live (Addendum 27) that
    without this context, an answer like "pistol caliber carbine, nine
    millimeter" got read as pure caliber info and left name blank,
    silently re-asking the identical question forever."""
    tool = _LOAD_SETUP_TOOL if kind == "load" else _RIFLE_SETUP_TOOL
    user_content = f"[Currently being asked for: {asking_about}]\n{text}" if asking_about else text
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SETUP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            tools=[tool],
            tool_choice={"type": "any"},
        )
        block = _first_tool_use(response)
        return dict(block.input)
    except (anthropic.AnthropicError, TypeError, IndexError, AttributeError):
        logger.exception("extract_setup_fields failed for %r (kind=%s)", text, kind)
        return None
