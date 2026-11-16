"""Picking a rifle out loud (Rick, 2026-11-16): "Instead of listing rifles,
she should ask 'What caliber are you shooting?', then if more than one rifle
matches, 'What barrel length?', then confirm the one rifle that fits. She
should never read a list longer than three or four."

The narrowing itself (ballistica/narrowing.py) and the conversations that use
it: the equipment check's rifle and ammunition steps, "switch to a new
rifle", ambiguous switches and "list rifles".
"""
from __future__ import annotations

import pytest

import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI
from ballistica.narrowing import (
    MAX_SPOKEN_LIST, confirm_question, narrow_loads, narrow_rifles, rifle_spoken, short_list,
)
from ballistica.profiles import Load, ProfileStore, Rifle


def _load(name, grains=140):
    return Load(name=name, bullet_weight_gr=grains, bc=0.6, drag_model="G7",
                muzzle_velocity_fps=2700, zero_distance_yd=100)


def _rifle(name, caliber="", barrel=None, loads=("Factory",)):
    r = Rifle(name=name, scope_height_in=2.0, caliber=caliber, barrel_length_in=barrel)
    for ld in loads:
        r.add_load(_load(ld))
    return r


def _store(tmp_path, rifles):
    store = ProfileStore(tmp_path / "profiles.json")
    for r in rifles:
        store.add_rifle(r)
    return store


def _three(tmp_path):
    return _store(tmp_path, [
        _rifle("Tikka", "6.5 Creedmoor", 24),
        _rifle("Bergara HMR", "6.5 Creedmoor", 22),
        _rifle("AR-15 Faxon", ".223 Wylde", 20),
    ])


def _nine(tmp_path):
    return _store(tmp_path, [
        _rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Bergara HMR", "6.5 Creedmoor", 22),
        _rifle("Ruger Precision", "6.5 Creedmoor", 26), _rifle("Savage 110", "6.5 Creedmoor", 20),
        _rifle("Christensen", "6.5 Creedmoor", 18),
        _rifle("AR-15 Faxon", ".223 Wylde", 20), _rifle("Aero Pistol", "300 Blackout", 7),
        _rifle("Sig MCX", "300 Blackout", 9), _rifle("Rem 700", ".308 Winchester", 24),
    ])


class NoCamera:
    def status(self):
        return {"connected": False, "device_id": None, "seen_s_ago": None, "same_device": False}

    def look(self):
        return None


def _cli(store):
    cli = BallisticaCLI(store)
    cli._camera = NoCamera()
    return cli


def _say(cli, text):
    """One voice turn the way the API runs it: state saved and restored in between."""
    cli.load_state(cli.to_state())
    return cli.handle(text)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the LLM")
    monkeypatch.setattr(cli_module, "extract_intent", boom)


# ------------------------------------------------------------------ the narrowing itself
def test_short_list_reads_at_most_four():
    assert short_list(["a", "b"]) == "a or b"
    assert short_list(["a", "b", "c", "d"], "and") == "a, b, c and d"
    assert short_list(["a", "b", "c", "d", "e"]) is None
    assert MAX_SPOKEN_LIST == 4


def test_rifle_is_named_with_caliber_and_barrel_once():
    assert confirm_question(_rifle("Tikka", "6.5 Creedmoor", 24)) == "The 6.5 Creedmoor Tikka, 24-inch barrel -- right?"
    # Caliber or barrel already in the name isn't said twice.
    assert rifle_spoken(_rifle("Tikka T3x 6.5 Creedmoor", "6.5 Creedmoor")) == "the Tikka T3x 6.5 Creedmoor"
    assert rifle_spoken(_rifle("AR-15 20-inch Faxon", ".223 Wylde", 20)) == "the .223 Wylde AR-15 20-inch Faxon"


@pytest.mark.parametrize("said, expected", [
    ("6.5 Creedmoor", {"Tikka", "Bergara HMR"}),
    ("I'm shooting the creedmoor", {"Tikka", "Bergara HMR"}),
    ("six five creedmoor", {"Tikka", "Bergara HMR"}),
    (".223", {"AR-15 Faxon"}),
    ("223 wylde", {"AR-15 Faxon"}),
    ("6.5 creedmoor, 24 inch", {"Tikka"}),
    ("the 22 inch", {"Bergara HMR"}),
    ("twenty four inch barrel", {"Tikka"}),
    ("the Bergara", {"Bergara HMR"}),
    ("308", set()),
])
def test_narrowing_by_caliber_barrel_or_name(tmp_path, said, expected):
    rifles = list(_three(tmp_path).rifles.values())
    narrowed, heard = narrow_rifles(rifles, said)
    assert heard and {r.name for r in narrowed} == expected


def test_a_bare_number_after_the_barrel_question_is_a_barrel_length(tmp_path):
    creedmoors = [r for r in _three(tmp_path).rifles.values() if "Creedmoor" in r.caliber]
    narrowed, _ = narrow_rifles(creedmoors, "24", expect_barrel=True)
    assert [r.name for r in narrowed] == ["Tikka"]


def test_nothing_usable_said_leaves_the_rifles_as_they_were(tmp_path):
    rifles = list(_three(tmp_path).rifles.values())
    narrowed, heard = narrow_rifles(rifles, "umm, the blue one")
    assert not heard and narrowed == rifles


def test_loads_narrow_by_bullet_weight():
    loads = [_load("Hornady ELD-M", 140), _load("Berger Hybrid", 140), _load("Sierra TMK", 130)]
    fits, heard = narrow_loads(loads, "the 140s")
    assert heard and [ld.name for ld in fits] == ["Hornady ELD-M", "Berger Hybrid"]


# ------------------------------------------------------------------ the equipment check
def test_caliber_then_barrel_then_confirm(tmp_path):
    store = _three(tmp_path)
    cli = _cli(store)
    first = _say(cli, "equipment check")
    assert first.endswith("What caliber are you shooting?")
    assert "Tikka" not in first and "Bergara" not in first
    assert _say(cli, "6.5 Creedmoor") == "What barrel length?"
    assert _say(cli, "24") == "The 6.5 Creedmoor Tikka, 24-inch barrel -- right?"
    assert store.active_rifle_name == "AR-15 Faxon"          # nothing changed until he says yes
    yes = _say(cli, "yes")
    assert yes.startswith("The Tikka.") and store.active_rifle_name == "Tikka"


def test_one_rifle_in_that_caliber_goes_straight_to_the_confirm(tmp_path):
    store = _three(tmp_path)
    cli = _cli(store)
    _say(cli, "equipment check")
    assert _say(cli, "two twenty three") == "The .223 Wylde AR-15 Faxon, 20-inch barrel -- right?" \
        or _say(cli, ".223") == "The .223 Wylde AR-15 Faxon, 20-inch barrel -- right?"
    assert _say(cli, "that's right").startswith("The AR-15 Faxon.")


def test_no_on_the_confirm_starts_over_and_a_correction_is_used(tmp_path):
    store = _three(tmp_path)
    cli = _cli(store)
    _say(cli, "equipment check")
    _say(cli, ".223")
    assert _say(cli, "no") == "Okay. What caliber are you shooting?"
    _say(cli, ".223")
    assert _say(cli, "no, the Bergara").startswith("The Bergara HMR.")
    assert store.active_rifle_name == "Bergara HMR"


def test_naming_the_rifle_itself_needs_no_confirm(tmp_path):
    store = _three(tmp_path)
    cli = _cli(store)
    _say(cli, "equipment check")
    assert _say(cli, "I'm shooting the Bergara").startswith("The Bergara HMR.")


def test_a_caliber_with_no_rifle_is_said_then_given_up_on(tmp_path):
    cli = _cli(_three(tmp_path))
    _say(cli, "equipment check")
    assert _say(cli, "308") == "I don't have a rifle saved in that caliber. What caliber are you shooting?"
    assert _say(cli, "308").endswith("I didn't get which rifle.")
    assert cli._equipment_check is None


def test_an_ordinal_is_not_a_choice_when_no_list_was_read(tmp_path):
    store = _three(tmp_path)
    cli = _cli(store)
    _say(cli, "equipment check")
    assert _say(cli, "the second one").startswith("Didn't catch which rifle.")
    assert store.active_rifle_name == "AR-15 Faxon"


def test_only_one_rifle_saved_is_confirmed_straight_away(tmp_path):
    store = _store(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24)])
    cli = _cli(store)
    assert _say(cli, "equipment check").endswith("The 6.5 Creedmoor Tikka, 24-inch barrel -- right?")
    assert _say(cli, "yep").startswith("The Tikka.")


def test_rifles_with_the_same_barrel_are_told_apart_by_name(tmp_path):
    store = _store(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Rem 700", "6.5 Creedmoor", 24),
                              _rifle("AR-15 Faxon", ".223 Wylde", 20)])
    cli = _cli(store)
    _say(cli, "equipment check")
    assert _say(cli, "creedmoor") == "Which one -- the Tikka or the Rem 700?"
    assert _say(cli, "the second one").startswith("The Rem 700.")   # a list was read this time


def test_many_loads_are_asked_by_bullet_weight(tmp_path):
    loads = ["Hornady 140 ELD-M", "Berger 140 Hybrid", "Sierra 130 TMK", "Nosler 130 RDF", "Handload 147 ELD"]
    rifle = Rifle(name="Tikka", scope_height_in=2.0, caliber="6.5 Creedmoor", barrel_length_in=24)
    for name in loads:
        rifle.add_load(_load(name, float(name.split()[1])))
    store = _store(tmp_path, [rifle])
    cli = _cli(store)
    _say(cli, "equipment check")
    ask = _say(cli, "yes")
    assert ask == "The Tikka. And which ammunition? You have 5 loads on the Tikka. What bullet weight?"
    assert _say(cli, "the 130s") == "Which one -- Sierra 130 TMK or Nosler 130 RDF?"
    assert _say(cli, "the Nosler").startswith("The Nosler 130 RDF.")
    assert store.get_active_rifle().active_load_name == "Nosler 130 RDF"


# ------------------------------------------------------------------ switching and listing
def test_switch_to_a_new_rifle_with_nine_never_reads_them(tmp_path):
    store = _nine(tmp_path)
    cli = _cli(store)
    ask = _say(cli, "switch to a new rifle")
    assert "What caliber?" in ask and not any(name in ask for name in store.rifles)
    assert _say(cli, "300 blackout") == "What barrel length?"
    assert _say(cli, "9") == "Switched you over to the Sig MCX."


def test_same_caliber_and_barrel_are_named_when_only_a_few(tmp_path):
    store = _store(tmp_path, [_rifle("Tikka", "6.5 Creedmoor", 24), _rifle("Rem 700", "6.5 Creedmoor", 24),
                              _rifle("AR-15 Faxon", ".223 Wylde", 20)])
    cli = _cli(store)
    _say(cli, "switch to a new rifle")
    assert _say(cli, "creedmoor") == "Which one -- the Tikka or the Rem 700?"
    assert _say(cli, "the tikka") == "Switched you over to the Tikka."


def test_a_caliber_with_five_rifles_asks_the_barrel_length(tmp_path):
    store = _nine(tmp_path)
    cli = _cli(store)
    _say(cli, "switch to a new rifle")
    assert _say(cli, "6.5 creedmoor") == "What barrel length?"
    assert _say(cli, "26") == "Switched you over to the Ruger Precision."


def test_an_ambiguous_switch_with_many_matches_asks_instead_of_listing(tmp_path):
    store = _nine(tmp_path)
    cli = _cli(store)
    reply = _say(cli, "switch rifle to 6.5 creedmoor")
    assert reply == "That matches 5 rifles. What barrel length?"
    assert _say(cli, "18 inch") == "Switched you over to the Christensen."


def test_list_rifles_with_many_says_how_many_not_every_name(tmp_path):
    store = _nine(tmp_path)
    reply = _say(_cli(store), "list rifles")
    assert reply == "You have 9 rifles, in 6.5 Creedmoor, .223 Wylde, 300 Blackout and .308 Winchester."
    (tmp_path / "few").mkdir()
    few = _store(tmp_path / "few", [_rifle("Tikka"), _rifle("Bergara")])
    assert _say(_cli(few), "list rifles") == "Tikka\nBergara"
