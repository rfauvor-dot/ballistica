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
