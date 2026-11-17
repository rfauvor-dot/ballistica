"""Loads shared by caliber, with velocity kept per rifle.

Rick, 2026-11-16: "I shoot the same ammo, like a 69-grain 5.56, in several
rifles with different barrel lengths. Make loads shared by caliber, so I
enter the bullet's weight and ballistic coefficient once and can use it in
any rifle of that caliber. Keep velocity per rifle. If a rifle has no
measured speed for that load, have her estimate it from my chronographed
speed in another barrel of the same caliber, adjusted for the barrel length
difference, tell me it's an estimate, and let me correct it later from a
chronograph or from my hits at the range."

How it's stored: each rifle still holds its own copy of the load (so its
own velocity and zero, and nothing about the database changes). What makes
it shared is that the copies are made and kept in step here:
  - a load added to one rifle is copied to every other rifle of the same
    caliber (share_load), and a rifle added or given a caliber gets the
    loads its caliber already has (share_into);
  - the bullet's own facts -- weight, BC, drag model, bullet, powder and
    charge -- changed on one copy change on all of them (sync_bullet);
  - a copy whose speed hasn't been measured in that rifle carries an
    estimate, worked out from the closest measured barrel and marked in its
    notes (ESTIMATE_RE) until a chronograph, a spoken velocity or the hits
    at the range replace it; every estimate of that load is then worked out
    again from the best measured speed (refresh_estimates).
Two copies are the same ammo when their names match, or when the bullet
weight, BC and drag model all match (names typed differently in two rifles).
"""
from __future__ import annotations

import dataclasses
import re

from .profiles import Load, ProfileStore, Rifle
from .rifle_names import short_caliber

# Speed gained or lost per inch of barrel. Published barrel-length tests of
# common rifle cartridges mostly land between 20 and 35 fps per inch near
# typical barrel lengths; 25 is the usual rule of thumb. It's an estimate
# either way, which is why she says so and asks for a chronograph.
FPS_PER_INCH = 25.0

# The fields that describe the ammunition itself, shared by every copy.
BULLET_FIELDS = ("bullet_weight_gr", "bc", "drag_model", "bullet_type", "powder", "powder_charge_gr")

ESTIMATE_RE = re.compile(r"\s*\[Estimated velocity[^\]]*\]")


def caliber_key(rifle: Rifle) -> str:
    return short_caliber(rifle.caliber).lower() if rifle.caliber.strip() else ""


def same_ammo(a: Load, b: Load) -> bool:
    if a.name.strip().lower() == b.name.strip().lower():
        return True
    return (abs(a.bullet_weight_gr - b.bullet_weight_gr) < 0.05 and abs(a.bc - b.bc) < 0.0005
            and a.drag_model == b.drag_model)


def is_estimate(load: Load) -> bool:
    return bool(ESTIMATE_RE.search(load.notes or ""))


def estimate_source(load: Load) -> str:
    """The rifle an estimated speed came from, as written in the marker."""
    m = re.search(r"\[Estimated velocity from the (.+?) \(", load.notes or "")
    return m.group(1) if m else ""


def mark_measured(load: Load) -> None:
    load.notes = ESTIMATE_RE.sub("", load.notes or "").strip()


def same_caliber_rifles(store: ProfileStore, rifle: Rifle) -> list[Rifle]:
    key = caliber_key(rifle)
    if not key:
        return []
    return [r for r in store.rifles.values() if r is not rifle and caliber_key(r) == key]


def find_same_ammo(rifle: Rifle, load: Load) -> Load | None:
    return next((ld for ld in rifle.loads.values() if same_ammo(ld, load)), None)


def _measured_sources(store: ProfileStore, rifle: Rifle, load: Load) -> list[tuple[Rifle, Load]]:
    return [(r, ld) for r in same_caliber_rifles(store, rifle)
            if (ld := find_same_ammo(r, load)) is not None and not is_estimate(ld)]


def _closest(target: Rifle, sources: list[tuple[Rifle, Load]]) -> tuple[Rifle, Load] | None:
    """The measured copy whose barrel is nearest the target's (one with a
    known length beats one without)."""
    def distance(pair):
        b = pair[0].barrel_length_in
        if target.barrel_length_in is None or b is None:
            return (1, 0.0)
        return (0, abs(b - target.barrel_length_in))
    return min(sources, key=distance) if sources else None


def estimate_velocity(target: Rifle, source_rifle: Rifle, source_load: Load) -> float:
    fps = source_load.muzzle_velocity_fps
    if target.barrel_length_in is not None and source_rifle.barrel_length_in is not None:
        fps += FPS_PER_INCH * (target.barrel_length_in - source_rifle.barrel_length_in)
    return round(max(fps, 300.0))


def _marker(source_rifle: Rifle, source_load: Load) -> str:
    barrel = (f"{source_rifle.barrel_length_in:g}-inch barrel, "
              if source_rifle.barrel_length_in is not None else "")
    return (f"[Estimated velocity from the {source_rifle.name} ({barrel}"
            f"{source_load.muzzle_velocity_fps:.0f} fps), adjusted {FPS_PER_INCH:g} fps per inch of barrel]")


def _set_estimate(target: Rifle, load: Load, source_rifle: Rifle, source_load: Load) -> None:
    load.muzzle_velocity_fps = estimate_velocity(target, source_rifle, source_load)
    load.notes = f"{ESTIMATE_RE.sub('', load.notes or '').strip()} {_marker(source_rifle, source_load)}".strip()


def _copy_for(target: Rifle, load: Load, store: ProfileStore) -> Load:
    copy = dataclasses.replace(load, notes="")
    pair = _closest(target, _measured_sources(store, target, load))
    if pair is not None:
        _set_estimate(target, copy, *pair)
    else:
        copy.notes = load.notes   # an estimate copied from an estimate: same speed, same marker
    name = copy.name
    n = 2
    while name in target.loads:
        name = f"{copy.name} {n}"
        n += 1
    copy.name = name
    return copy


def share_load(store: ProfileStore, rifle: Rifle, load: Load) -> list[str]:
    """Copies a load just added to `rifle` to every other rifle of its
    caliber that doesn't have that ammo yet. Returns their names."""
    added = []
    for other in same_caliber_rifles(store, rifle):
        if find_same_ammo(other, load) is None:
            other.add_load(_copy_for(other, load, store), make_active=other.active_load_name is None)
            added.append(other.name)
    return added


def share_into(store: ProfileStore, rifle: Rifle) -> list[str]:
    """Gives `rifle` every load its caliber's other rifles have that it
    doesn't. Returns the names of the loads added."""
    added = []
    for other in same_caliber_rifles(store, rifle):
        for load in list(other.loads.values()):
            if find_same_ammo(rifle, load) is None:
                # Prefer a measured copy of this ammo as what's copied.
                best = _closest(rifle, _measured_sources(store, rifle, load))
                copy = _copy_for(rifle, best[1] if best else load, store)
                rifle.add_load(copy, make_active=rifle.active_load_name is None)
                added.append(copy.name)
    return added


def sync_bullet(store: ProfileStore, rifle: Rifle, before: Load, changed: dict) -> None:
    """The bullet's own facts changed on one copy (`before` is that copy as
    it was): change them on the other rifles' copies of the same ammo."""
    shared = {k: v for k, v in changed.items() if k in BULLET_FIELDS}
    if not shared:
        return
    for other in same_caliber_rifles(store, rifle):
        copy = find_same_ammo(other, before)
        if copy is not None:
            for k, v in shared.items():
                setattr(copy, k, v)


def refresh_estimates(store: ProfileStore, rifle: Rifle, load: Load) -> None:
    """A measured speed for this ammo changed: work every estimated copy of
    it out again from the closest measured barrel."""
    for target in [rifle, *same_caliber_rifles(store, rifle)]:
        copy = find_same_ammo(target, load)
        if copy is None or not is_estimate(copy):
            continue
        pair = _closest(target, _measured_sources(store, target, copy))
        if pair is not None:
            _set_estimate(target, copy, *pair)


def refresh_rifle(store: ProfileStore, rifle: Rifle) -> None:
    """The rifle's barrel length or caliber changed: its estimates, and the
    ones worked out from its measured speeds, are redone."""
    for load in rifle.loads.values():
        refresh_estimates(store, rifle, load)


def spoken_estimate_note(load: Load) -> str:
    """'That speed is an estimate, from the 556 16-inch, adjusted for barrel
    length -- chrono it, or tell me where your hits landed, to correct it.'"""
    if not is_estimate(load):
        return ""
    src = estimate_source(load)
    frm = f", from the {src}," if src else ","
    return (f"That speed is an estimate{frm} adjusted for barrel length -- chrono it, "
            f"or tell me where your hits landed, to correct it.")
