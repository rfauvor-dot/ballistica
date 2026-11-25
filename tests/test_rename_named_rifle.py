"""Renaming one rifle by naming it (2026-11-24): "rename my 6mm ARC to ARC 18
inch Bergara". The rifle keeps every load, velocity and zero/click/calibration
value; the new name is always read back and only saved on a yes; a name that
doesn't say the caliber (or asking for one) gets the caliber, barrel length
and maker scheme, built from what the rifle has saved.

No network: a flat-file ProfileStore, the intent LLM made to fail loudly, and
the Supabase store's HTTP layer replaced by a recorder.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

import ballistica.cli as cli_module
import ballistica.supabase_store as supabase_store
from ballistica.cli import BallisticaCLI
from ballistica.profiles import Load, ProfileStore, Rifle
from ballistica.rifle_names import mentions_caliber, scheme_name


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the intent LLM")
    monkeypatch.setattr(cli_module, "extract_intent", boom)


def _rifle(name, caliber, barrel, click=0.1, unit="MRAD") -> Rifle:
    r = Rifle(name=name, scope_height_in=1.75, caliber=caliber, barrel_length_in=barrel,
              click_value_mrad=click, reticle_unit=unit, twist_rate="1:7.5")
    r.add_load(Load(name="108 ELD", bullet_weight_gr=108, bc=0.27, drag_model="G7",
                    muzzle_velocity_fps=2640, zero_distance_yd=100))
    r.add_load(Load(name="103 ELD-X", bullet_weight_gr=103, bc=0.25, drag_model="G7",
                    muzzle_velocity_fps=2700, zero_distance_yd=200), make_active=False)
    return r


def _make(tmp_path, rifles, active=None):
    store = ProfileStore(tmp_path / "profiles.json")
    for r in rifles:
        store.add_rifle(r)
    if active:
        store.active_rifle_name = active
    store.save()
    return BallisticaCLI(store), store


def _snapshot(rifle: Rifle) -> dict:
    """Everything about the rifle except its name."""
    d = dataclasses.asdict(rifle)
    d.pop("name")
    return copy.deepcopy(d)


# ------------------------------------------------------------ the helpers

@pytest.mark.parametrize("name, says_caliber", [
    ("ARC 18 inch Bergara", True), ("6mm ARC", True), ("308 20-inch Ruger", True),
    ("6.5 Creedmoor", True), ("Wylde Faxon", True), ("7.62x39 SKS", True),
    ("Long Tom", False), ("Bergara 18 inch", False), ("my gun", False), ("Rifle 2", False),
])
def test_mentions_caliber(name, says_caliber):
    assert mentions_caliber(name) is says_caliber


def test_scheme_name_always_has_the_maker_when_known():
    r = _rifle("Bergara", "6mm ARC", 18)
    assert scheme_name(r, [r], "Bergara") == "6mm ARC 18-inch Bergara"
    assert scheme_name(r, [r]) == "6mm ARC 18-inch"
    no_cal = Rifle(name="Mystery", scope_height_in=1.5)
    assert scheme_name(no_cal, [no_cal], "Ruger") is None


# ----------------------------------------------------- the voice command

def test_rename_my_named_rifle_to_a_new_name(tmp_path):
    arc = _rifle("6mm ARC", "6mm ARC", 18)
    other = _rifle("Tikka", "6.5 Creedmoor", 24, click=0.25, unit="MOA")
    cli, store = _make(tmp_path, [arc, other], active="Tikka")
    arc.active_load_name = "103 ELD-X"
    before = _snapshot(arc)

    reply = cli.handle("rename my 6mm ARC to ARC 18 inch Bergara")
    assert reply == "Rename the 6mm ARC to 'ARC 18 inch Bergara' -- right?"
    assert cli._last_tool_name == "rename_rifle"
    assert "6mm ARC" in store.rifles                       # nothing saved before the yes

    assert cli.handle("yes") == "Renamed to 'ARC 18 inch Bergara'."
    assert "6mm ARC" not in store.rifles
    renamed = store.rifles["ARC 18 inch Bergara"]
    assert renamed is arc                                   # the same object, re-keyed
    assert _snapshot(renamed) == before                     # loads, velocities, zeros, clicks untouched
    assert store.active_rifle_name == "Tikka"               # the active rifle didn't change

    reloaded = ProfileStore(tmp_path / "profiles.json")
    assert _snapshot(reloaded.rifles["ARC 18 inch Bergara"]) == before
    assert "6mm ARC" not in reloaded.rifles


def test_change_the_name_of_phrasing_and_active_rifle_follows(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Ruger", ".308 Winchester", 20)],
                       active="Tikka")
    assert cli.handle("change the name of the Tikka to 6.5 Creedmoor 24 inch Tikka") == (
        "Rename the Tikka to '6.5 Creedmoor 24 inch Tikka' -- right?")
    cli.handle("yep")
    assert store.active_rifle_name == "6.5 Creedmoor 24 inch Tikka"


def test_no_means_nothing_is_saved(tmp_path):
    cli, store = _make(tmp_path, [_rifle("6mm ARC", "6mm ARC", 18)])
    cli.handle("rename my 6mm ARC to ARC Bergara")
    assert "what should I call the 6mm ARC" in cli.handle("no")
    assert cli.handle("keep") == "Okay, kept it."
    assert list(store.rifles) == ["6mm ARC"]
    assert cli._rename is None


def test_a_vague_name_gets_the_scheme_offered_and_can_be_taken(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Bergara B14", "6mm ARC", 18)])
    reply = cli.handle("rename my Bergara to Old Faithful")
    assert reply == ("Rename the Bergara B14 to 'Old Faithful' -- right? "
                     "Or, by caliber, barrel and maker: '6mm ARC 18-inch Bergara'.")
    assert cli.handle("go with yours") == "Renamed to '6mm ARC 18-inch Bergara'."
    assert list(store.rifles) == ["6mm ARC 18-inch Bergara"]


def test_a_vague_name_can_still_be_kept(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Bergara B14", "6mm ARC", 18)])
    cli.handle("rename my Bergara to Old Faithful")
    assert cli.handle("yes") == "Renamed to 'Old Faithful'."
    assert list(store.rifles) == ["Old Faithful"]


def test_asking_for_a_name_up_front(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Bergara B14", "6mm ARC", 18)])
    reply = cli.handle("rename my Bergara, what should I call it?")
    assert reply == ("Caliber, barrel length and maker works well: how about '6mm ARC 18-inch Bergara'? "
                     "Say yes, a different name, or keep.")
    assert list(store.rifles) == ["Bergara B14"]
    assert cli.handle("yes") == "Renamed to '6mm ARC 18-inch Bergara'."


def test_asking_for_a_name_partway(tmp_path):
    cli, store = _make(tmp_path, [_rifle("6mm ARC", "6mm ARC", 18)])
    cli.handle("rename my 6mm ARC to Shorty")
    # No maker in the old name: the scheme leaves it for him to add.
    reply = cli.handle("hmm, any ideas?")
    assert "how about '6mm ARC 18-inch'? Add the maker if you like." in reply
    assert cli.handle("call it 6mm ARC 18 inch Bergara") == "'6mm ARC 18 inch Bergara' -- right?"
    assert cli.handle("yes") == "Renamed to '6mm ARC 18 inch Bergara'."


def test_asking_for_a_name_without_a_saved_caliber(tmp_path):
    cli, store = _make(tmp_path, [Rifle(name="Mystery Gun", scope_height_in=1.5)])
    reply = cli.handle("rename the Mystery Gun and suggest a name")
    assert "caliber, barrel length and maker" in reply and "caliber saved" in reply
    assert cli.handle("call it 22 Hornet Savage") == "'22 Hornet Savage' -- right?"
    cli.handle("yes")
    assert list(store.rifles) == ["22 Hornet Savage"]


def test_rename_it_means_the_active_rifle(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Ruger", ".308 Winchester", 20)],
                       active="Ruger")
    assert cli.handle("rename it to 308 20 inch Ruger") == "Rename the Ruger to '308 20 inch Ruger' -- right?"


def test_an_ambiguous_rifle_asks_which(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka T3x", "6.5 Creedmoor", 24),
                                  _rifle("Bergara B14", "6.5 Creedmoor", 22)])
    reply = cli.handle("rename my 6.5 Creedmoor to Plinker")
    assert reply.startswith("That could be the") and "Tikka T3x" in reply and "Bergara B14" in reply
    assert cli._rename is None
    assert sorted(store.rifles) == ["Bergara B14", "Tikka T3x"]


def test_an_unknown_rifle_is_said_so(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24)])
    assert cli.handle("rename my Remington to Old Faithful") == (
        "I don't have a rifle called 'Remington'. Say rename and the rifle's name.")
    assert cli._rename is None
    # A load named in it isn't this command's: the turn carries on past it.
    assert cli._request_named_rename("my 108 ELD load to Match") is None
    assert list(store.rifles) == ["Tikka"]


def test_a_name_already_taken_is_refused(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Ruger", ".308 Winchester", 20)])
    cli.handle("rename my Ruger to Tikka")
    assert "There's already a rifle called 'Tikka'" in cli.handle("yes")
    assert sorted(store.rifles) == ["Ruger", "Tikka"]


def test_the_older_phrasings_still_mean_what_they_did(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Ruger", ".308 Winchester", 20)],
                       active="Tikka")
    assert cli.handle("rename this rifle to 6.5 Creedmoor 24 inch Tikka") == (
        "Rename the Tikka to '6.5 Creedmoor 24 inch Tikka' -- right?")
    cli.handle("cancel")
    assert cli.handle("rename my rifles").startswith("Let's go through your 2 rifles")


def test_pending_confirmation_survives_between_requests(tmp_path):
    cli, store = _make(tmp_path, [_rifle("Bergara B14", "6mm ARC", 18)])
    cli.handle("rename my Bergara to Old Faithful")
    state = cli.to_state()

    cli2 = BallisticaCLI(store)                 # a fresh object per request, as the API does
    cli2.load_state(state)
    assert cli2.handle("the suggestion") == "Renamed to '6mm ARC 18-inch Bergara'."


# ------------------------------------------------ the Supabase store's rows

class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_supabase_rename_patches_the_same_rows(monkeypatch):
    """A rename is one PATCH of the rifle's own row: the row id (which its
    loads point at) is kept, and no load is deleted or re-inserted."""
    rifle_row = {"id": "rifle-1", "user_id": "u1", "active_load_id": "load-1",
                 **{k: None for k in supabase_store._RIFLE_COLUMNS}}
    rifle_row.update(name="6mm ARC", scope_height_in=1.75, caliber="6mm ARC", barrel_length_in=18,
                     twist_rate="1:7.5", click_value_mrad=0.1, reticle_unit="MRAD", optic_type="",
                     scope_make="", scope_model="", magnification="", focal_plane="", reticle_type="",
                     has_suppressor=False, suppressor_type="")
    base_load = _rifle("x", "6mm ARC", 18).loads["108 ELD"]
    load_row = {"id": "load-1", "rifle_id": "rifle-1", "user_id": "u1",
                **{k: getattr(base_load, k) for k in supabase_store._LOAD_COLUMNS}}
    calls = []

    def fake_rest(self, method, table, headers=None, **kwargs):
        calls.append((method, table, kwargs.get("params", {}).get("id"), kwargs.get("json")))
        if method == "GET":
            return _Resp([rifle_row] if table == "rifles" else [load_row])
        return _Resp([])

    monkeypatch.setattr(supabase_store.SupabaseProfileStore, "_rest", fake_rest)
    monkeypatch.setattr(supabase_store.SupabaseProfileStore, "get_conversation_state",
                        lambda self: {"active_rifle_id": "rifle-1"})
    monkeypatch.setattr(supabase_store.SupabaseProfileStore, "set_conversation_state",
                        lambda self, **u: None)
    store = supabase_store.SupabaseProfileStore("u1", "token")
    calls.clear()

    cli = BallisticaCLI(store)
    cli.handle("rename my 6mm ARC to ARC 18 inch Bergara")
    assert calls == []                                  # nothing written before the yes
    cli.handle("yes")

    writes = [c for c in calls if c[0] != "GET"]
    assert writes == [("PATCH", "rifles", "eq.rifle-1", writes[0][3])]
    assert writes[0][3]["name"] == "ARC 18 inch Bergara"
    assert store.active_rifle_name == "ARC 18 inch Bergara"


# ------------------------- other ways of saying it (Rick's first live try)

@pytest.mark.parametrize("said", [
    "change my rifle name to ARC 18 inch Bergara",
    "change the rifle name to ARC 18 inch Bergara",
    "update my rifle's name to ARC 18 inch Bergara",
    "change my 6mm ARC's name to ARC 18 inch Bergara",
    "change the name on my 6mm ARC to ARC 18 inch Bergara",
    "Rename my six millimeter ARC to ARC eighteen inch Bergara",
    "rename my 6 mm ARC to ARC 18 inch Bergara.",
])
def test_other_phrasings_reach_the_read_back(tmp_path, said):
    cli, store = _make(tmp_path, [_rifle("6mm ARC", "6mm ARC", 18), _rifle("Tikka", "6.5 Creedmoor", 24)],
                       active="6mm ARC")
    assert cli.handle(said) == "Rename the 6mm ARC to 'ARC 18 inch Bergara' -- right?"
    assert cli.handle("yes") == "Renamed to 'ARC 18 inch Bergara'."
    assert sorted(store.rifles) == ["ARC 18 inch Bergara", "Tikka"]


@pytest.mark.parametrize("said", ["rename rifle", "I'd like to rename a rifle", "update my rifle name"])
def test_no_rifle_said_means_the_current_one(tmp_path, said):
    cli, store = _make(tmp_path, [_rifle("6mm ARC", "6mm ARC", 18), _rifle("Tikka", "6.5 Creedmoor", 24)],
                       active="6mm ARC")
    assert cli.handle(said).startswith("The 6mm ARC: how about '6mm ARC 18-inch'?")


def test_the_model_tool_catches_the_rest_and_still_reads_back(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [_rifle("6mm ARC", "6mm ARC", 18), _rifle("Tikka", "6.5 Creedmoor", 24)],
                       active="Tikka")
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None, **k: (
        "rename_rifle", {"rifle": "six millimeter ARC", "new_name": "ARC eighteen inch Bergara"}))
    assert cli.handle("call my 6mm ARC ARC 18 inch Bergara") == (
        "Rename the 6mm ARC to 'ARC 18 inch Bergara' -- right?")
    assert cli._last_tool_name == "rename_rifle"
    assert "6mm ARC" in store.rifles                       # still nothing saved
    assert cli.handle("yes") == "Renamed to 'ARC 18 inch Bergara'."


def test_the_model_tool_asking_for_a_name(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [_rifle("Bergara B14", "6mm ARC", 18)])
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None, **k: (
        "rename_rifle", {"rifle": "", "new_name": "", "wants_suggestion": True}))
    assert "how about '6mm ARC 18-inch Bergara'?" in cli.handle("what would be a better name for this thing")


def test_the_model_tool_with_an_unknown_rifle(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Ruger", ".308 Winchester", 20)])
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None, **k: (
        "rename_rifle", {"rifle": "Remington", "new_name": "Old Faithful"}))
    assert cli.handle("my Remington should be Old Faithful") == (
        "I don't have a rifle called 'Remington'. Say rename and the rifle's name.")
    assert cli._rename is None
