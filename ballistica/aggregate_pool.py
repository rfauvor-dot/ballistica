"""Anonymized aggregate contribution pipeline (2026-08-30, per Rick's
explicit instruction): every load a user saves or enters is
automatically anonymized and merged into the shared `events` table --
a standard, non-optional part of how the app works, not an opt-in
feature. Disclosed in ballistica/waiver.py's Section 4 and the
acceptance mechanism in its Section 11.

Anonymization is structural, not a promise this module has to keep by
being careful: `events` has no user_id column at all
(db/005_anonymize_events_at_ingestion.sql), so there is nothing here
that COULD be linked back to a contributing account even if this code
tried to. This module's own job is narrower and just as important:
never put an identifying value INTO the payload in the first place.
Rifle names, load names, and free-text notes are all user-chosen and
excluded entirely -- only objective physical/ballistic facts (bullet,
powder, charge, velocity, and the rifle specs needed to make sense of
them) go into a contribution.

Insertion uses the CALLER'S OWN access token, not a service role -- the
events_insert_any_authenticated RLS policy only requires a real
authenticated session, not ownership of anything, which is exactly
what "insert is real but the row carries no identity" means in
practice. A contribution failure is always best-effort: it must never
block or fail the user's own rifle/load save, which is the actual
product action they took.
"""
from __future__ import annotations

import logging
import os

import httpx

from .profiles import Load, Rifle

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

_EVENT_SCHEMA_VERSION = 1


def build_load_event_payload(rifle: Rifle, load: Load) -> dict:
    """The exact, complete set of fields a 'load' event carries.
    Deliberately an allow-list (only these keys, spelled out
    explicitly), not "everything except a few excluded fields" -- an
    allow-list can't accidentally start leaking a new identifying field
    just because someone added one to the Rifle/Load dataclasses later
    without also updating an exclude-list here."""
    return {
        "caliber": rifle.caliber,
        "barrel_length_in": rifle.barrel_length_in,
        "twist_rate": rifle.twist_rate,
        "bullet_type": load.bullet_type,
        "bullet_weight_gr": load.bullet_weight_gr,
        "bc": load.bc,
        "drag_model": load.drag_model,
        "muzzle_velocity_fps": load.muzzle_velocity_fps,
        "zero_distance_yd": load.zero_distance_yd,
        "powder": load.powder,
        "powder_charge_gr": load.powder_charge_gr,
    }


def contribute_load(rifle: Rifle, load: Load, access_token: str) -> None:
    """Best-effort, fire-and-forget: logs and returns on any failure,
    never raises. The aggregate pool existing is secondary to the
    user's own save succeeding -- a network blip or a not-yet-applied
    migration (db/009) here must never surface as an error on an
    otherwise-successful rifle/load save."""
    if not _SUPABASE_URL:
        return
    try:
        httpx.post(
            f"{_SUPABASE_URL}/rest/v1/events",
            headers={
                "apikey": _SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "event_type": "load",
                "schema_version": _EVENT_SCHEMA_VERSION,
                "payload": build_load_event_payload(rifle, load),
            },
            timeout=10,
        ).raise_for_status()
    except Exception:
        logger.exception("aggregate pool contribution failed (non-fatal, load save still succeeded)")
