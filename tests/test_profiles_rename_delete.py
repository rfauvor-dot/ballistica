"""Tests for the rifle/load rename fix and the new single-load/rifle
delete methods on ProfileStore (2026-08-30). No network dependency --
this is all in-memory dataclass/dict manipulation, independent of
which store subclass (flat-file or Supabase) is layered on top.
"""
from __future__ import annotations

import pytest

from ballistica.profiles import Load, ProfileStore, Rifle


def _store_with_rifle() -> ProfileStore:
    store = ProfileStore.__new__(ProfileStore)  # skip __init__'s file-path/load() side effects
    store.rifles = {}
    store.active_rifle_name = None
    rifle = Rifle(name="Original Name", scope_height_in=2.0, caliber=".223")
    rifle.add_load(Load(name="Load A", bullet_weight_gr=77, bc=0.2, drag_model="G7",
                         muzzle_velocity_fps=2800, zero_distance_yd=100))
    store.add_rifle(rifle)
    return store


# ------------------------------------------------------- rifle rename

def test_rifle_rename_updates_dict_key_not_just_the_attribute():
    store = _store_with_rifle()
    store.update_rifle_fields("Original Name", name="New Name", scope_height_in=2.0, caliber=".223")
    assert "New Name" in store.rifles
    assert "Original Name" not in store.rifles
    assert store.rifles["New Name"].name == "New Name"


def test_rifle_rename_preserves_loads():
    """The exact failure mode of the original bug: a rename must not
    silently lose the rifle's loads."""
    store = _store_with_rifle()
    store.update_rifle_fields("Original Name", name="New Name", scope_height_in=2.0, caliber=".223")
    assert list(store.rifles["New Name"].loads.keys()) == ["Load A"]


def test_rifle_rename_does_not_leave_a_duplicate_under_the_old_name():
    """The literal bug report: editing the name used to create a
    second rifle rather than renaming the first."""
    store = _store_with_rifle()
    store.update_rifle_fields("Original Name", name="New Name", scope_height_in=2.0, caliber=".223")
    assert len(store.rifles) == 1


def test_rifle_rename_updates_active_rifle_name():
    store = _store_with_rifle()
    assert store.active_rifle_name == "Original Name"
    store.update_rifle_fields("Original Name", name="New Name", scope_height_in=2.0, caliber=".223")
    assert store.active_rifle_name == "New Name"


def test_rifle_rename_to_existing_name_rejected():
    store = _store_with_rifle()
    store.add_rifle(Rifle(name="Taken", scope_height_in=1.5), make_active=False)
    with pytest.raises(ValueError, match="already exists"):
        store.update_rifle_fields("Original Name", name="Taken", scope_height_in=2.0)


def test_rifle_rename_to_empty_name_rejected():
    store = _store_with_rifle()
    with pytest.raises(ValueError, match="cannot be empty"):
        store.update_rifle_fields("Original Name", name="   ", scope_height_in=2.0)


def test_rifle_metadata_update_without_name_does_not_rename():
    store = _store_with_rifle()
    store.update_rifle_fields("Original Name", scope_height_in=3.5, caliber=".223")
    assert "Original Name" in store.rifles
    assert store.rifles["Original Name"].scope_height_in == 3.5


# -------------------------------------------------------- load rename

def test_load_rename_updates_dict_key_not_just_the_attribute():
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    store.update_load_fields(
        "Original Name", "Load A", name="Load A Renamed",
        bullet_weight_gr=77, bc=0.2, drag_model="G7",
        muzzle_velocity_fps=2800, zero_distance_yd=100,
    )
    assert "Load A Renamed" in rifle.loads
    assert "Load A" not in rifle.loads


def test_load_rename_does_not_leave_a_duplicate_under_the_old_name():
    """The load-level counterpart of the reported rifle bug -- found
    while fixing the rifle one, same root cause."""
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    store.update_load_fields(
        "Original Name", "Load A", name="Load A Renamed",
        bullet_weight_gr=77, bc=0.2, drag_model="G7",
        muzzle_velocity_fps=2800, zero_distance_yd=100,
    )
    assert len(rifle.loads) == 1


def test_load_rename_updates_active_load_name():
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    assert rifle.active_load_name == "Load A"
    store.update_load_fields(
        "Original Name", "Load A", name="Load A Renamed",
        bullet_weight_gr=77, bc=0.2, drag_model="G7",
        muzzle_velocity_fps=2800, zero_distance_yd=100,
    )
    assert rifle.active_load_name == "Load A Renamed"


def test_load_rename_to_existing_name_on_same_rifle_rejected():
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    rifle.add_load(Load(name="Load B", bullet_weight_gr=77, bc=0.2, drag_model="G7",
                         muzzle_velocity_fps=2800, zero_distance_yd=100), make_active=False)
    with pytest.raises(ValueError, match="already exists"):
        store.update_load_fields(
            "Original Name", "Load A", name="Load B",
            bullet_weight_gr=77, bc=0.2, drag_model="G7",
            muzzle_velocity_fps=2800, zero_distance_yd=100,
        )


def test_load_field_update_validates_like_a_fresh_load():
    """update_load_fields re-runs Load.__post_init__, same guard
    update_rifle_fields already has for Rifle -- a bad drag_model must
    be rejected, not silently written."""
    store = _store_with_rifle()
    with pytest.raises(ValueError):
        store.update_load_fields(
            "Original Name", "Load A", name="Load A",
            bullet_weight_gr=77, bc=0.2, drag_model="G3",
            muzzle_velocity_fps=2800, zero_distance_yd=100,
        )


# --------------------------------------------------------- deletion

def test_delete_load_removes_only_that_load():
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    rifle.add_load(Load(name="Load B", bullet_weight_gr=77, bc=0.2, drag_model="G7",
                         muzzle_velocity_fps=2800, zero_distance_yd=100), make_active=False)
    store.delete_load("Original Name", "Load A")
    assert list(rifle.loads.keys()) == ["Load B"]


def test_delete_load_does_not_touch_the_rifle():
    store = _store_with_rifle()
    store.delete_load("Original Name", "Load A")
    assert "Original Name" in store.rifles


def test_delete_active_load_reassigns_active_load_name():
    store = _store_with_rifle()
    rifle = store.rifles["Original Name"]
    rifle.add_load(Load(name="Load B", bullet_weight_gr=77, bc=0.2, drag_model="G7",
                         muzzle_velocity_fps=2800, zero_distance_yd=100), make_active=False)
    assert rifle.active_load_name == "Load A"
    store.delete_load("Original Name", "Load A")
    assert rifle.active_load_name == "Load B"


def test_delete_last_load_clears_active_load_name():
    store = _store_with_rifle()
    store.delete_load("Original Name", "Load A")
    assert store.rifles["Original Name"].active_load_name is None


def test_delete_load_missing_rifle_raises():
    store = _store_with_rifle()
    with pytest.raises(KeyError):
        store.delete_load("Nonexistent Rifle", "Load A")


def test_delete_load_missing_load_raises():
    store = _store_with_rifle()
    with pytest.raises(KeyError):
        store.delete_load("Original Name", "Nonexistent Load")
