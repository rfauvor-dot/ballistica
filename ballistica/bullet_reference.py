"""Bundled bullet reference dataset (2026-08-30, per Rick's instruction --
closes the backlogged "Seed dataset" item, BACKLOG.md 2026-08-23).

Loads `data/bullet_reference/bullet_reference.json`, built from a pinned
snapshot of the MIT-licensed ammolytics/projectiles dataset by
scripts/build_bullet_reference.py -- see that script's docstring for
exactly what was included/excluded and why, and
data/bullet_reference/ammolytics_projectiles_source/PROVENANCE.md for
the source commit and license text. Re-run the build script (not this
module) if the source data ever needs updating; this module only reads
the already-built JSON.

This is reference data, nothing else -- explicitly subordinate to a
user's own real data, never a substitute for it:

- Nothing in this module writes to a user's rifles/loads. There is no
  code path anywhere that lets this data silently populate, override,
  or auto-fill an existing Load -- that would require a user's own
  explicit action (a future "start from a factory bullet" UI feature
  could offer these values as a *starting point* the user then saves
  themselves, the same way book-data velocity already works per
  MULTI_TENANCY_DESIGN.md's load-setup design -- but that's a UI
  feature to build later, not something this module does on its own).
- A user's own chronograph-calibrated velocity, and any BC they've
  entered themselves, always wins over anything here -- this dataset
  has no mechanism to know a user's real data exists, let alone touch
  it.
- Community-sourced, not laboratory-verified: this is the manufacturer's
  own published number, reproduced from a third-party open dataset, not
  independently re-measured. Treat it exactly like Ballistica's own
  existing "book data" concept (MULTI_TENANCY_DESIGN.md) -- a
  starting point, not a substitute for a user's own verification.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "bullet_reference" / "bullet_reference.json"


@dataclass(frozen=True)
class BulletReference:
    bullet_type: str
    company: str
    sku: str
    type: str  # "rifle" or "pistol", as classified by the source dataset
    caliber_in: float
    bullet_weight_gr: float
    bc: float
    drag_model: str  # "G1" or "G7"
    source: str
    source_commit: str


def _load() -> tuple[BulletReference, ...]:
    if not _DATA_PATH.exists():
        return ()
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return tuple(BulletReference(**entry) for entry in raw)


BULLET_REFERENCE: tuple[BulletReference, ...] = _load()


def search(query: str) -> list[BulletReference]:
    """Case-insensitive substring match against company/bullet_type --
    a minimal lookup helper, not a UI feature. Matches the same
    fuzzy-but-simple spirit as profiles.py's own find_load()."""
    q = query.strip().lower()
    if not q:
        return []
    return [
        b for b in BULLET_REFERENCE
        if q in b.bullet_type.lower() or q in b.company.lower()
    ]
