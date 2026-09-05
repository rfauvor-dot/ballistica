"""Turn freeform, conversationally-narrated range notes into a CSV that
matches import_export.py's TARGET_FIELDS exactly, so it can be reviewed
and run through Ballistica's existing upload/mapping/commit screen --
this deliberately does NOT write to the live profile store itself.

Built 2026-09-05 per Rick's Session Mode brief: the CSV importer already
existed (2026-08-30) and handles the structured half fine; the missing
piece was turning a real range session's freeform narration into that
structured form without reconstructing it by hand every time, which is
what Rick had to do for the 2026-09-05 session before this existed.

Same extraction/math boundary as intent.py's LLM fallback: the model only
ever decides *what was said*. Muzzle velocity is a plain Python average of
the individual shot readings it extracts -- never computed or rounded by
the model itself.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Same reason as cli.py's own copy of this call: this module is run
# standalone (`python -m ballistica.freeform_import`), not through api.py,
# so it needs its own .env load for ANTHROPIC_API_KEY rather than relying
# on some other entry point having already done it.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .anthropic_client import get_anthropic_client
from .import_export import TARGET_FIELDS, _csv_safe

logger = logging.getLogger(__name__)

# Sonnet, not the Haiku used for live per-utterance intent classification
# in intent.py -- this runs once (or a handful of times) per import, not
# once per voice turn, so the cost difference is negligible while a real
# shooter's saved load data is exactly the kind of thing worth spending
# the more careful model on.
_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 8192

_SYSTEM_PROMPT = (
    "You extract structured rifle/load/chronograph data from a shooter's "
    "freeform, conversationally-narrated range notes. The text may cover "
    "multiple rifles and multiple loads per rifle, in any order, with "
    "corrections or restatements (e.g. \"actually that barrel's 18 inches, "
    "not 16\") -- use the corrected/final value when the same field is "
    "restated more than once. Extract only what is actually stated; leave "
    "a field out entirely rather than guessing or inferring a value that "
    "wasn't said. Never compute an average yourself -- report every "
    "individual chronograph shot velocity you find for a load, in the "
    "order given; the average is computed separately, deterministically, "
    "in code."
)

_TOOL = {
    "name": "record_range_session",
    "description": "Record every rifle and load described in the notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rifles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rifle_name": {"type": "string"},
                        "caliber": {"type": "string"},
                        "barrel_length_in": {"type": "number"},
                        "twist_rate": {"type": "string", "description": "e.g. '1:8'"},
                        "loads": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "load_name": {"type": "string"},
                                    "bullet_weight_gr": {"type": "number"},
                                    "bullet_type": {"type": "string"},
                                    "bc": {"type": "number"},
                                    "drag_model": {"type": "string", "description": "G1 or G7"},
                                    "powder": {"type": "string"},
                                    "powder_charge_gr": {"type": "number"},
                                    "zero_distance_yd": {"type": "number"},
                                    "notes": {"type": "string"},
                                    "shot_velocities_fps": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "description": "Every individual chronograph reading for "
                                                        "this load, in the order given -- not an "
                                                        "average.",
                                    },
                                },
                                "required": ["shot_velocities_fps"],
                            },
                        },
                    },
                    "required": ["rifle_name", "loads"],
                },
            },
        },
        "required": ["rifles"],
    },
}


class ExtractionError(Exception):
    """A whole-input problem -- the API call failed, or the model
    returned nothing usable. Distinct from a single row failing later at
    import time, which import_export.py already reports per-row."""


def extract_rifles(freeform_text: str) -> list[dict]:
    """Runs the LLM extraction pass. Returns the raw rifles/loads
    structure -- still containing per-shot velocities, not yet averaged
    or shaped into CSV rows."""
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": freeform_text}],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "record_range_session"},
        )
        for block in response.content:
            if block.type == "tool_use":
                return list(block.input.get("rifles", []))
        raise ExtractionError("Model returned no structured data -- check the input text.")
    except (anthropic.AnthropicError, TypeError, AttributeError) as exc:
        # Same failure-shape gotcha documented in intent.py: a missing/
        # misconfigured ANTHROPIC_API_KEY raises a plain TypeError, not an
        # AnthropicError subclass. Logged with the real traceback rather
        # than surfacing a bare "didn't work" to whoever runs this.
        logger.exception("freeform extraction failed")
        raise ExtractionError(f"Extraction request failed: {exc}") from exc


def _shot_notes(load: dict, shots: list[float]) -> str:
    base_notes = (load.get("notes") or "").strip()
    if not shots:
        return base_notes
    spread = max(shots) - min(shots) if len(shots) > 1 else 0.0
    chrono_note = (
        f"Chrono-verified: {len(shots)} shots, spread {spread:.0f} fps "
        f"({', '.join(f'{s:.0f}' for s in shots)})."
    )
    return f"{base_notes} {chrono_note}".strip()


def rifles_to_rows(rifles: list[dict]) -> list[dict[str, str]]:
    """Flattens the extracted rifle/load structure into one dict per
    load, keyed by TARGET_FIELDS labels exactly -- so this round-trips
    into import_export.py's own column-mapping step with zero ambiguity,
    the same guarantee generate_export_csv() already relies on for its
    own round-trip export/re-import."""
    label_by_key = {f.key: f.label for f in TARGET_FIELDS}
    rows: list[dict[str, str]] = []

    for rifle in rifles:
        rifle_name = (rifle.get("rifle_name") or "").strip()
        if not rifle_name:
            continue  # import_export.py fails a row with no rifle name anyway
        for load in rifle.get("loads", []):
            shots = [s for s in load.get("shot_velocities_fps", []) if isinstance(s, (int, float))]
            avg_velocity = round(sum(shots) / len(shots), 1) if shots else None

            values = {
                "rifle_name": rifle_name,
                "caliber": rifle.get("caliber", ""),
                "barrel_length_in": rifle.get("barrel_length_in", ""),
                "twist_rate": rifle.get("twist_rate", ""),
                "load_name": load.get("load_name", ""),
                "bullet_type": load.get("bullet_type", ""),
                "bullet_weight_gr": load.get("bullet_weight_gr", ""),
                "bc": load.get("bc", ""),
                "drag_model": load.get("drag_model", ""),
                "muzzle_velocity_fps": avg_velocity if avg_velocity is not None else "",
                "zero_distance_yd": load.get("zero_distance_yd", ""),
                "powder": load.get("powder", ""),
                "powder_charge_gr": load.get("powder_charge_gr", ""),
                "notes": _shot_notes(load, shots),
            }
            row = {
                label_by_key[k]: ("" if v is None else str(v))
                for k, v in values.items() if k in label_by_key
            }
            rows.append(row)

    return rows


def write_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    headers = [f.label for f in TARGET_FIELDS]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: _csv_safe(row.get(h, "")) for h in headers})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn freeform range notes into a CSV for Ballistica's existing import screen. "
                    "This writes a CSV file only -- it does not touch the live profile store. "
                    "Review the output, then upload it through Ballistica's own import UI.",
    )
    parser.add_argument("input", type=Path, help="Text file containing freeform range notes.")
    parser.add_argument("-o", "--output", type=Path, default=Path("range_import.csv"))
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    try:
        rifles = extract_rifles(text)
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = rifles_to_rows(rifles)
    if not rows:
        print("No rifles/loads extracted -- nothing written. Check the input text.", file=sys.stderr)
        sys.exit(1)

    write_csv(rows, args.output)
    print(f"Wrote {len(rows)} row(s) to {args.output}.")
    print("Review it before uploading -- this was not checked against Ballistica's own "
          "per-row validation (missing BC/velocity, etc.) yet; that happens at import time.")


if __name__ == "__main__":
    main()
