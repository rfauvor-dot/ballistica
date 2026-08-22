"""LLM-based fallback for understanding voice commands that don't match
one of the fast, free, deterministic regex patterns in cli.py.

Important boundary: this module only ever decides *which* command was
meant and *what* the parameters are (e.g. "switch to my heavier load"
-> switch_load(query="heavier")). It never computes a ballistic answer
itself -- extracted parameters get handed to the exact same
deterministic Python functions the regex path already calls. The
physics stays 100% deterministic regardless of how the intent was
recognized; only "what did they actually ask for" gets smarter.
"""
from __future__ import annotations

import json
import re

import openai

from .openai_client import get_openai_client

_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = (
    "You are the command-understanding layer for Ballistica, a voice-driven "
    "ballistic calculator used hands-free at a shooting range. The user's "
    "speech has already been transcribed (it may contain transcription errors, "
    "informal phrasing, or filler words) and none of it matched a fast exact-"
    "phrase pattern, so it's being routed to you as a fallback. "
    "Call exactly one tool that best matches what the shooter is asking for. "
    "If the utterance doesn't correspond to any available command -- small "
    "talk, an unrelated question, or something genuinely unclear -- call "
    "no_match rather than guessing at a ballistics command. "
    "Range talk uses yards, mils/MRAD, MOA, clicks, wind speed/direction "
    "(o'clock), temperature (F), humidity (%), altitude (ft), and barometric "
    "pressure (inHg). Only fill in parameters actually stated or clearly "
    "implied; leave everything else out."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_drop_at_range",
            "description": "Get the drop/windage solution at a specific distance.",
            "parameters": {
                "type": "object",
                "properties": {"range_yd": {"type": "number", "description": "Target distance in yards"}},
                "required": ["range_yd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_load",
            "description": "Switch the active ammunition load on the current rifle.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Fuzzy name/description of the load, e.g. '21 grain' or 'H335'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_rifle",
            "description": "Switch the active rifle.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Fuzzy name of the rifle"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_minimum_spread_zero",
            "description": "Find the zero distance that minimizes total vertical spread out to a max range.",
            "parameters": {
                "type": "object",
                "properties": {"max_range_yd": {"type": "number"}},
                "required": ["max_range_yd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solve_incline_angle",
            "description": "Back-calculate an uphill/downhill shooting angle from an observed click "
                            "difference between a reference distance and the actual target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "observed_diff_clicks": {"type": "number"},
                    "line_of_sight_distance_yd": {"type": "number"},
                    "reference_distance_yd": {"type": "number", "description": "Defaults to 100 if not stated"},
                },
                "required": ["observed_diff_clicks", "line_of_sight_distance_yd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_conditions",
            "description": "Update current atmospheric conditions. Only include fields actually "
                            "mentioned -- this merges into whatever conditions are already set, it "
                            "does not require restating everything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_f": {"type": "number"},
                    "pressure_inhg": {"type": "number"},
                    "altitude_ft": {"type": "number"},
                    "humidity_pct": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wind",
            "description": "Set wind speed and direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "speed_mph": {"type": "number"},
                    "clock_hours": {"type": "number", "description": "Wind clock position, 0-12 "
                                                                      "(12 = headwind, 3 = full crosswind from the right)"},
                },
                "required": ["speed_mph", "clock_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_load_setup",
            "description": "Begin a guided voice interview to add a new ammunition load "
                            "(e.g. 'let's log a new load', 'I want to add a load').",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_rifle_setup",
            "description": "Begin a guided voice interview to add a new rifle "
                            "(e.g. 'set up a new rifle', 'let's build a new rifle profile').",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repeat_last_solution",
            "description": "Re-speak the most recently given drop-at-range solution (elevation and/or "
                            "windage) without recalculating it -- for phrasing like 'say that again', "
                            "'what was the elevation again', or 'repeat the windage'.",
            "parameters": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": "Report the active rifle, load, and current conditions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_match",
            "description": "The utterance doesn't correspond to any available ballistics command.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def extract_intent(text: str) -> tuple[str, dict] | None:
    """Returns (function_name, arguments) for the best-matching command,
    or None if the call failed outright (network/API error -- distinct
    from a clean "no_match", which is returned as ("no_match", {})."""
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=_TOOLS,
            tool_choice="required",
        )
        call = response.choices[0].message.tool_calls[0]
        args = json.loads(call.function.arguments) if call.function.arguments else {}
        return call.function.name, args
    except (openai.OpenAIError, json.JSONDecodeError, IndexError, AttributeError):
        return None


# --- Personality layer -------------------------------------------------
#
# Only called after extract_intent() already came back no_match, i.e. the
# utterance didn't fit any real ballistics command. A separate call (not
# folded into extract_intent's own no_match branch) so this narrower,
# safety-sensitive prompt -- and its guardrail -- can be reasoned about and
# tightened on its own, without touching the 9 real command tools above.

_PERSONALITY_MODEL = "gpt-4o-mini"

_PERSONALITY_SYSTEM_PROMPT = (
    "You are Ballistica's warm, off-duty voice -- used only when the shooter "
    "said something that didn't match any ballistics command: a greeting, "
    "small talk, thanks, a personal remark, banter. "
    "Decide: is this genuine small talk, or does it actually sound like an "
    "attempt at a ballistics/rifle/load/conditions request that just didn't "
    "come through clearly? "
    "If it's small talk, call warm_reply with a short (under 20 words), "
    "natural, spoken-style reply in character -- friendly, capable, a little "
    "dry humor is fine, never corny or over-the-top. "
    "If it sounds like an unclear ballistics request, or you're genuinely "
    "unsure, call not_smalltalk instead so the caller can ask them to "
    "rephrase. "
    "Hard rule, no exceptions: never state, estimate, or imply any numeric "
    "ballistics value (yardage, elevation, MOA, mils, clicks, drop, "
    "windage, velocity, angle, temperature, pressure) in warm_reply -- you "
    "have no ability to compute one here, and guessing one would be "
    "dangerous."
)

_PERSONALITY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "warm_reply",
            "description": "Genuine small talk -- respond warmly and briefly, with no ballistics numbers.",
            "parameters": {
                "type": "object",
                "properties": {"reply": {"type": "string"}},
                "required": ["reply"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "not_smalltalk",
            "description": "This sounds like an unclear or unsupported ballistics request, not small talk.",
            "parameters": {"type": "object", "properties": {}},
        },
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
        client = get_openai_client()
        response = client.chat.completions.create(
            model=_PERSONALITY_MODEL,
            messages=[
                {"role": "system", "content": _PERSONALITY_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=_PERSONALITY_TOOLS,
            tool_choice="required",
        )
        call = response.choices[0].message.tool_calls[0]
        if call.function.name != "warm_reply":
            return None
        args = json.loads(call.function.arguments) if call.function.arguments else {}
        reply = str(args.get("reply") or "").strip()
        if not reply or re.search(r"\d", reply):
            return None
        return reply
    except (openai.OpenAIError, json.JSONDecodeError, IndexError, AttributeError):
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
    "breath, or restate/correct a field they already gave. Call the given "
    "tool once, filling in only the fields actually stated or clearly "
    "implied this turn -- never invent or guess a value for anything not "
    "mentioned, and never fill in a field with a default just because it's "
    "common (e.g. don't assume MRAD or G1 unless they said so)."
)

_LOAD_SETUP_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_load_fields",
        "description": "Record any ammunition load fields mentioned in this utterance.",
        "parameters": {
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
    },
}

_RIFLE_SETUP_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_rifle_fields",
        "description": "Record any rifle fields mentioned in this utterance.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "scope_height_in": {"type": "number", "description": "Scope height above bore, in inches"},
                "caliber": {"type": "string"},
                "barrel_length_in": {"type": "number"},
                "twist_rate": {"type": "string", "description": "e.g. '1:7'"},
                "click_value_mrad": {"type": "number"},
                "reticle_unit": {"type": "string", "enum": ["MRAD", "MOA"]},
                "scope_make": {"type": "string"},
                "scope_model": {"type": "string"},
                "magnification": {"type": "string", "description": "e.g. '5-25x'"},
                "objective_lens_mm": {"type": "number"},
                "focal_plane": {"type": "string", "enum": ["FFP", "SFP"]},
                "reticle_type": {"type": "string"},
            },
        },
    },
}


def extract_setup_fields(text: str, kind: str) -> dict | None:
    """Returns whatever Load/Rifle fields (kind: "load" or "rifle") were
    mentioned in this utterance, or None on an outright API failure. An
    utterance that genuinely stated nothing usable comes back as {},
    distinct from None only in that the caller doesn't need to treat it
    as a hard error -- both currently get the same "didn't catch that"
    handling in cli.py, but keeping them distinct leaves room to do
    better later without another API shape change."""
    tool = _LOAD_SETUP_TOOL if kind == "load" else _RIFLE_SETUP_TOOL
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SETUP_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=[tool],
            tool_choice="required",
        )
        call = response.choices[0].message.tool_calls[0]
        return json.loads(call.function.arguments) if call.function.arguments else {}
    except (openai.OpenAIError, json.JSONDecodeError, IndexError, AttributeError):
        return None
