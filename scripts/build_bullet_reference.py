"""Builds Ballistica's bundled bullet reference dataset from the pinned
ammolytics/projectiles snapshot (data/bullet_reference/ammolytics_
projectiles_source/ -- see PROVENANCE.md there for the exact commit,
license, and what was kept).

Deliberately conservative about what counts as "cleanly mapped" (per
Rick's explicit instruction, 2026-08-30: flag fields that don't map
cleanly rather than guessing): a row only makes it into the output if
it has weight, diameter, and a real single-value G1 or G7 ballistic
coefficient in the source's own bc_g1/bc_g7 columns. Every Sierra row
in this dataset publishes its BC only as a velocity-banded structure in
a separate bc_fn field instead (e.g. multiple G1 values for different
velocity ranges, matching how Sierra actually documents some of their
bullets) -- and that field is inconsistently formatted (the same key
repeated with different values in one entry, which is not valid JSON,
just JSON-shaped text). Parsing it would mean guessing which band to
use and silently trusting a malformed structure; skipping it and
reporting exactly why is the honest choice, not a shortcut.

When a row has both bc_g1 and bc_g7, G7 is preferred -- in every
observed case (all 148 of them, all Berger) this only happens for
boat-tail match/target bullets, the shape G7 is the better-fitting
model for, matching the same reasoning already documented in
drag_tables.py/walkthrough.py for the Sierra 77gr MatchKing specifically.

Usage:
    python -m scripts.build_bullet_reference
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

_SOURCE_CSV = (
    Path(__file__).resolve().parent.parent / "data" / "bullet_reference"
    / "ammolytics_projectiles_source" / "data" / "projectiles.csv"
)
_OUTPUT_JSON = Path(__file__).resolve().parent.parent / "data" / "bullet_reference" / "bullet_reference.json"
_REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "bullet_reference" / "build_report.md"

_SOURCE_COMMIT = "5b51ab231c66f60de6fcb62a6b4c4795240948e5"


def _to_float(raw: str) -> float | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build() -> None:
    with open(_SOURCE_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    included = []
    skipped = []

    for row in rows:
        company = row["company"].strip()
        description = row["description"].strip()
        weight = _to_float(row["weight_gr"])
        diameter = _to_float(row["diameter_in"])
        bc_g1 = _to_float(row["bc_g1"])
        bc_g7 = _to_float(row["bc_g7"])

        if weight is None:
            skipped.append((company, description, "missing bullet weight in source"))
            continue
        if diameter is None:
            skipped.append((company, description, "missing diameter in source"))
            continue

        if bc_g7 is not None:
            bc, drag_model = bc_g7, "G7"
        elif bc_g1 is not None:
            bc, drag_model = bc_g1, "G1"
        else:
            has_fn = bool((row.get("bc_fn") or "").strip())
            reason = (
                "no clean single-value G1/G7 BC -- only a velocity-banded bc_fn value in the "
                "source, not parsed (see this script's own docstring)"
                if has_fn else "no ballistic coefficient of any kind in the source"
            )
            skipped.append((company, description, reason))
            continue

        included.append({
            "bullet_type": description,
            "company": company,
            "sku": row["sku"].strip(),
            "type": row["type"].strip(),
            "caliber_in": diameter,
            "bullet_weight_gr": weight,
            "bc": bc,
            "drag_model": drag_model,
            "source": "ammolytics/projectiles",
            "source_commit": _SOURCE_COMMIT,
        })

    _OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_JSON.write_text(json.dumps(included, indent=2), encoding="utf-8")

    by_company_total: dict[str, int] = {}
    by_company_included: dict[str, int] = {}
    for row in rows:
        by_company_total[row["company"]] = by_company_total.get(row["company"], 0) + 1
    for entry in included:
        by_company_included[entry["company"]] = by_company_included.get(entry["company"], 0) + 1

    lines = [
        "# Bullet reference build report",
        "",
        f"Source: ammolytics/projectiles @ `{_SOURCE_COMMIT}`",
        f"Total source rows: {len(rows)}",
        f"Cleanly mapped and included: {len(included)}",
        f"Skipped: {len(skipped)}",
        "",
        "## By manufacturer",
        "",
        "| Company | Total in source | Included | Skipped |",
        "|---|---|---|---|",
    ]
    for company in sorted(by_company_total):
        total = by_company_total[company]
        inc = by_company_included.get(company, 0)
        lines.append(f"| {company} | {total} | {inc} | {total - inc} |")

    lines += ["", "## Every skipped row, with reason", ""]
    for company, description, reason in skipped:
        lines.append(f"- **{company}** -- {description}: {reason}")

    _REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {len(included)} bullets to {_OUTPUT_JSON}")
    print(f"Skipped {len(skipped)} -- see {_REPORT_PATH}")


if __name__ == "__main__":
    build()
