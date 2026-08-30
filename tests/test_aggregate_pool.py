"""Tests for ballistica/aggregate_pool.py's payload construction --
the part that has to be right regardless of network access, since it's
what decides what does and doesn't leave a user's own data. The actual
insert (contribute_load's network call) needs db/009_add_load_event_
type.sql applied against the real Supabase project first -- not tested
here, matching how every other not-yet-migrated feature in this repo
is handled (verified live once Rick confirms the migration is applied,
not blocked on it in the automated suite).
"""
from __future__ import annotations

from ballistica.aggregate_pool import build_load_event_payload
from ballistica.profiles import Load, Rifle


def _rifle_and_load():
    rifle = Rifle(
        name="Rick's Actual Real Rifle Name", scope_height_in=2.0, caliber=".223 Wylde",
        barrel_length_in=20.0, twist_rate="1:8",
    )
    load = Load(
        name="My Secret Personal Load Name", bullet_weight_gr=77, bc=0.188, drag_model="G7",
        muzzle_velocity_fps=2822, zero_distance_yd=100, bullet_type="Sierra 77gr MatchKing BTHP",
        powder="H335", powder_charge_gr=22.5,
        notes="Tested in my backyard in Boise, my email is rick@example.com",
    )
    return rifle, load


def test_payload_includes_only_ballistic_facts():
    rifle, load = _rifle_and_load()
    payload = build_load_event_payload(rifle, load)
    assert payload == {
        "caliber": ".223 Wylde",
        "barrel_length_in": 20.0,
        "twist_rate": "1:8",
        "bullet_type": "Sierra 77gr MatchKing BTHP",
        "bullet_weight_gr": 77,
        "bc": 0.188,
        "drag_model": "G7",
        "muzzle_velocity_fps": 2822,
        "zero_distance_yd": 100,
        "powder": "H335",
        "powder_charge_gr": 22.5,
    }


def test_payload_never_contains_rifle_or_load_name():
    """The whole point: names are user-chosen and excluded, no matter
    what a user names their rifle or load."""
    rifle, load = _rifle_and_load()
    payload = build_load_event_payload(rifle, load)
    serialized = str(payload)
    assert "Rick's Actual Real Rifle Name" not in serialized
    assert "My Secret Personal Load Name" not in serialized
    assert "name" not in payload


def test_payload_never_contains_notes():
    """Notes are free text a user controls entirely -- the single
    highest-risk field for accidentally identifying content (this test
    fixture's notes literally contain an email address and a city)."""
    rifle, load = _rifle_and_load()
    payload = build_load_event_payload(rifle, load)
    serialized = str(payload)
    assert "backyard" not in serialized
    assert "Boise" not in serialized
    assert "example.com" not in serialized
    assert "notes" not in payload
