"""LLM-based fallback for understanding voice commands that don't match
one of the fast, free, deterministic regex patterns in cli.py.

Important boundary: this module only ever decides *which* command was
meant and *what* the parameters are (e.g. "switch to my heavier load"
-> switch_load(query="heavier")). It never computes a ballistic answer
itself -- extracted parameters get handed to the exact same
deterministic Python functions the regex path already calls. The
physics stays 100% deterministic regardless of how the intent was
recognized; only "what did they actually ask for" gets smarter.

Runs on Claude (via the Messages API's tool use), not OpenAI -- swapped
from gpt-4o-mini after Rick found it too literal on loose, natural
phrasing ("AR-15 18 inch Faxon" got shredded across unrelated fields
instead of read as one rifle name). Whisper (STT) and TTS stay on
OpenAI; only the "what did they mean" layer moved.
"""
from __future__ import annotations

import logging
import re

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
    "You are the command-understanding layer for Ballistica, a voice-driven "
    "ballistic calculator used hands-free at a shooting range. The user's "
    "speech has already been transcribed (it may contain transcription errors, "
    "informal phrasing, or filler words) and none of it matched a fast exact-"
    "phrase pattern, so it's being routed to you as a fallback. "
    "Use exactly one tool that best matches what the shooter is asking for. "
    "If the utterance doesn't correspond to any available command -- small "
    "talk, an unrelated question, or something genuinely unclear -- use "
    "no_match rather than guessing at a ballistics command. "
    "Range talk uses yards, mils/MRAD, MOA, clicks, wind speed/direction "
    "(o'clock), temperature (F), humidity (%), altitude (ft), and barometric "
    "pressure (inHg). Only fill in parameters actually stated or clearly "
    "implied; leave everything else out."
)

_TOOLS = [
    {
        "name": "get_drop_at_range",
        "description": "Get the drop/windage solution at a specific distance.",
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
        "description": "Switch the active rifle.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Fuzzy name of the rifle"}},
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
        "description": "Begin a guided voice interview to add a new ammunition load "
                        "(e.g. 'let's log a new load', 'I want to add a load').",
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
        "name": "get_status",
        "description": "Report the active rifle, load, and current conditions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "no_match",
        "description": "The utterance doesn't correspond to any available ballistics command.",
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


def extract_intent(text: str) -> tuple[str, dict] | None:
    """Returns (tool_name, arguments) for the best-matching command, or
    None if the call failed outright (network/API error -- distinct from
    a clean "no_match", which is returned as ("no_match", {})."""
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            tools=_TOOLS,
            tool_choice={"type": "any"},
        )
        block = _first_tool_use(response)
        return block.name, dict(block.input)
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


# --- Personality layer -------------------------------------------------
#
# Only called after extract_intent() already came back no_match, i.e. the
# utterance didn't fit any real ballistics command. A separate call (not
# folded into extract_intent's own no_match branch) so this narrower,
# safety-sensitive prompt -- and its guardrail -- can be reasoned about and
# tightened on its own, without touching the real command tools above.

_PERSONALITY_SYSTEM_PROMPT = (
    "You are Ballistica's warm, off-duty voice -- used only when the shooter "
    "said something that didn't match any ballistics command: a greeting, "
    "small talk, thanks, a personal remark, banter. "
    "Decide: is this genuine small talk, or does it actually sound like an "
    "attempt at a ballistics/rifle/load/conditions request that just didn't "
    "come through clearly? "
    "If it's small talk, use warm_reply with a short (under 20 words), "
    "natural, spoken-style reply in character -- friendly, capable, a little "
    "dry humor is fine, never corny or over-the-top. "
    "If it sounds like an unclear ballistics request, or you're genuinely "
    "unsure, use not_smalltalk instead so the caller can ask them to "
    "rephrase. "
    "Hard rule, no exceptions: never state, estimate, or imply any numeric "
    "ballistics value (yardage, elevation, MOA, mils, clicks, drop, "
    "windage, velocity, angle, temperature, pressure) in warm_reply -- you "
    "have no ability to compute one here, and guessing one would be "
    "dangerous."
)

_PERSONALITY_TOOLS = [
    {
        "name": "warm_reply",
        "description": "Genuine small talk -- respond warmly and briefly, with no ballistics numbers.",
        "input_schema": {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
    },
    {
        "name": "not_smalltalk",
        "description": "This sounds like an unclear or unsupported ballistics request, not small talk.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def generate_warm_reply(text: str) -> str | None:
    """Returns a short warm reply for genuine small talk, or None if this
    wasn't small talk, the call failed, or the model's reply slipped past
    the prompt's own guardrail and still contains a digit -- checked here
    directly rather than trusting the prompt alone, since a fabricated
    number read aloud as a real ballistics value is a safety issue, not
    just a tone miss."""
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_PERSONALITY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            tools=_PERSONALITY_TOOLS,
            tool_choice={"type": "any"},
        )
        block = _first_tool_use(response)
        if block.name != "warm_reply":
            return None
        reply = str(block.input.get("reply") or "").strip()
        if not reply or re.search(r"\d", reply):
            return None
        return reply
    except (anthropic.AnthropicError, TypeError, IndexError, AttributeError):
        logger.exception("generate_warm_reply failed for %r", text)
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
