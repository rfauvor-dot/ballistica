"""Rifle names built from caliber and barrel length (Rick, 2026-11-16).

"When I add a rifle, suggest a name built from its caliber and barrel length,
like '338 24-inch'. If another rifle already has the same caliber and length,
add the action type or maker to tell them apart ... I can accept her
suggestion or say my own name. ... Also, go through my existing rifles once,
suggest new names in that same style, and let me approve or change each one.
And add a voice command like 'rename this rifle' for later."
"""
from __future__ import annotations

import pytest

import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI
from ballistica.profiles import Load, ProfileStore, Rifle
from ballistica.rifle_names import (
    clean_maker, clean_spoken_name, maker_from_name, short_caliber, suggest_name,
)


def _load() -> Load:
    return Load(name="140 ELD", bullet_weight_gr=140, bc=0.3, drag_model="G7",
                muzzle_velocity_fps=2700, zero_distance_yd=100)


def _make(tmp_path, rifles):
    store = ProfileStore(tmp_path / "profiles.json")
    for name, caliber, barrel in rifles:
        r = Rifle(name=name, scope_height_in=1.5, caliber=caliber, barrel_length_in=barrel)
        r.add_load(_load())
        store.add_rifle(r)
    store.save()
    return BallisticaCLI(store), store


def _extract(monkeypatch, by_field: dict):
    """extract_setup_fields stand-in: what each asked-for field's answer gives."""
    def fake(text, kind, asking_about=None):
        if asking_about is None:
            return {}
        return by_field.get(asking_about, {})
    monkeypatch.setattr(cli_module, "extract_setup_fields", fake)


def _no_intent_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the intent LLM")
    monkeypatch.setattr(cli_module, "extract_intent", boom)


# ----------------------------------------------------------- the names

@pytest.mark.parametrize("caliber,short", [
    (".338 Lapua Magnum", "338"), ("5.56 NATO", "556"), (".308 Winchester", "308"),
    ("6.5 Creedmoor", "6.5 Creedmoor"), ("300 Blackout", "300 Blackout"),
    ("300 Win Mag", "300 Win Mag"), ("9mm", "9mm"), (".223 Wylde", "223 Wylde"),
])
def test_short_caliber(caliber, short):
    assert short_caliber(caliber) == short


def test_suggested_names_follow_ricks_examples():
    assert suggest_name(".338 Lapua Magnum", 24, []) == "338 24-inch"
    ar = Rifle(name="556 14.5-inch", scope_height_in=2.6, caliber="5.56 NATO", barrel_length_in=14.5)
    assert suggest_name("5.56 NATO", 14.5, [ar], maker="AR") == "556 14.5-inch AR"
    # The maker only goes in when it's needed to tell two apart.
    assert suggest_name("5.56 NATO", 16, [ar], maker="AR") == "556 16-inch"
    # Never a name that's already taken.
    assert suggest_name("5.56 NATO", 14.5, [ar]) == "556 14.5-inch 2"


def test_maker_and_spoken_name_cleanup():
    assert maker_from_name("Tikka T3x 6.5 Creedmoor") == "Tikka"
    assert maker_from_name("AR-15 20in Faxon") == "AR-15"
    assert maker_from_name("6.5 Creedmoor 24-inch") == ""
    assert clean_maker("it's a ruger") == "Ruger"
    assert clean_maker("an ar") == "AR"
    assert clean_spoken_name("call it the Big Bertha.") == "Big Bertha"
    assert clean_spoken_name("no, Long Tom") == "Long Tom"


# ----------------------------------------------------- adding a rifle

def test_new_rifle_is_offered_a_caliber_and_barrel_name(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24)])
    _extract(monkeypatch, {"caliber": {"caliber": ".338 Lapua Magnum"}, "barrel_length_in": {"barrel_length_in": 24}})
    assert "What caliber?" in cli.handle("new rifle")
    assert "Barrel length" in cli.handle("338 lapua")
    assert "tell it apart" in cli.handle("24 inch")     # always offered (Rick, 2026-11-25)
    assert "How about '338 24-inch'?" in cli.handle("skip")
    assert "scope height" in cli.handle("yes").lower()
    assert cli._setup.draft["name"] == "338 24-inch"


def test_the_third_detail_goes_on_the_name_even_without_a_clash(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24)])
    _extract(monkeypatch, {"caliber": {"caliber": ".223 Wylde"}, "barrel_length_in": {"barrel_length_in": 16}})
    cli.handle("new rifle")
    cli.handle("223 wylde")
    assert "Faxon or Ruger, fluted, left-handed" in cli.handle("16")
    assert "How about '223 Wylde 16-inch Aero Precision'?" in cli.handle("Aero Precision")
    cli.handle("yes")
    assert cli._setup.draft["name"] == "223 Wylde 16-inch Aero Precision"


def test_no_at_the_third_detail_leaves_it_off(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [])
    _extract(monkeypatch, {"caliber": {"caliber": ".338 Win Mag"}, "barrel_length_in": {"barrel_length_in": 24}})
    cli.handle("new rifle")
    cli.handle("338 win mag")
    cli.handle("24")
    assert "How about '338 Win Mag 24-inch'?" in cli.handle("no")


def test_he_can_say_his_own_name_instead(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [])
    _extract(monkeypatch, {"caliber": {"caliber": ".338 Lapua Magnum"}, "barrel_length_in": {"barrel_length_in": 24},
                           "name": {"name": "Big Bertha"}})
    cli.handle("new rifle")
    cli.handle("338 lapua")
    cli.handle("24")      # the third-detail question: his own name works here too
    assert "scope height" in cli.handle("call it Big Bertha").lower()
    assert cli._setup.draft["name"] == "Big Bertha"


def test_a_plain_no_asks_for_his_name(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [])
    _extract(monkeypatch, {"caliber": {"caliber": "6.5 Creedmoor"}, "barrel_length_in": {"barrel_length_in": 22}})
    cli.handle("new rifle")
    cli.handle("six five creedmoor")
    cli.handle("22")
    assert "How about '6.5 Creedmoor 22-inch'?" in cli.handle("skip")
    assert cli.handle("no") == "Okay, what do you want to call it?"
    assert cli._next_field_to_ask() == "name"
    assert "How about" not in cli._prompt_for("name")


def test_same_caliber_and_length_asks_for_the_maker(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [("556 14.5-inch", "5.56 NATO", 14.5)])
    _extract(monkeypatch, {"caliber": {"caliber": "5.56 NATO"}, "barrel_length_in": {"barrel_length_in": 14.5}})
    cli.handle("new rifle")
    cli.handle("five five six")
    reply = cli.handle("fourteen and a half")
    assert "You already have a 556 14.5-inch" in reply and "sets this one apart" in reply
    assert "How about '556 14.5-inch AR'?" in cli.handle("it's an AR")
    cli.handle("yes")
    assert cli._setup.draft["name"] == "556 14.5-inch AR"


def test_skipping_caliber_falls_back_to_asking_the_name(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [])
    _extract(monkeypatch, {})
    cli.handle("new rifle")
    assert "call this rifle" in cli.handle("skip")


def test_a_name_said_up_front_is_kept(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [])
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                        lambda text, kind, asking_about=None: {"name": "9mm PCC"})
    assert "scope height" in cli.handle("new rifle, the 9mm PCC").lower()


# ---------------------------------------------- the rename walk-through

def test_rename_my_rifles_goes_one_at_a_time(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [
        ("Tikka T3x", "6.5 Creedmoor", 24),
        ("Bergara B14", "6.5 Creedmoor", 24),
        ("AR-15 20in Faxon", ".223 Wylde", None),
        ("Old Ruger", "", None),
        ("338 24-inch", ".338 Lapua Magnum", 24),   # already in style: not asked about
    ])
    _no_intent_llm(monkeypatch)
    _extract(monkeypatch, {"caliber": {"caliber": ".308 Winchester"}})

    reply = cli.handle("rename my rifles")
    assert reply.startswith("Let's go through your 4 rifles, one at a time.")
    # Same caliber and barrel as the Bergara: the maker from the old name tells them apart.
    assert "The Tikka T3x: how about '6.5 Creedmoor 24-inch Tikka'?" in reply
    assert "Bergara" not in reply                       # one at a time, never a list

    reply = cli.handle("yes")
    assert reply.startswith("Renamed to '6.5 Creedmoor 24-inch Tikka'.")
    assert "how about '6.5 Creedmoor 24-inch Bergara'?" in reply

    reply = cli.handle("keep")
    assert reply.startswith("Okay, kept it.")
    # A length written into the old name counts -- no barrel question.
    assert "The AR-15 20in Faxon: how about '223 Wylde 20-inch'?" in reply

    reply = cli.handle("no")
    assert "what should I call the AR-15 20in Faxon" in reply
    assert cli.handle("call it the Wylde Faxon") == "'Wylde Faxon' -- right?"
    reply = cli.handle("yes")
    assert reply.startswith("Renamed to 'Wylde Faxon'.")
    assert "The Old Ruger: what caliber is it?" in reply

    assert "what barrel length?" in cli.handle("308")
    assert "how about '308 18-inch'?" in cli.handle("eighteen inches")
    reply = cli.handle("yes")
    assert reply == "Renamed to '308 18-inch'. That's all of them. Renamed 3 rifles."
    assert cli._rename is None

    assert sorted(store.rifles) == sorted(
        ["6.5 Creedmoor 24-inch Tikka", "Bergara B14", "Wylde Faxon", "308 18-inch", "338 24-inch"])
    # The length that was only in the old name is kept as a field.
    assert store.rifles["Wylde Faxon"].barrel_length_in == 20
    assert store.rifles["308 18-inch"].caliber == ".308 Winchester"
    # Saved, not just in memory.
    assert "308 18-inch" in ProfileStore(tmp_path / "profiles.json").rifles


def test_rename_can_be_stopped_partway(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    _no_intent_llm(monkeypatch)
    cli.handle("rename my rifles")
    cli.handle("yes")
    assert cli.handle("stop") == "Okay, stopped. Renamed 1 rifle."
    assert cli._rename is None
    assert "Ruger" in store.rifles


def test_nothing_to_rename(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path, [("338 24-inch", ".338 Lapua Magnum", 24)])
    _no_intent_llm(monkeypatch)
    assert "already named by caliber and barrel length" in cli.handle("rename my rifles")


def test_rename_this_rifle(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    _no_intent_llm(monkeypatch)
    store.active_rifle_name = "Tikka"
    assert cli.handle("rename this rifle") == "The Tikka: how about '6.5 Creedmoor 24-inch'? Say yes, a different name, or keep."
    assert cli.handle("yes") == "Renamed to '6.5 Creedmoor 24-inch'."
    assert store.active_rifle_name == "6.5 Creedmoor 24-inch"
    assert cli._rename is None


def test_rename_this_rifle_to_a_name(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    _no_intent_llm(monkeypatch)
    store.active_rifle_name = "Ruger"
    # 'Long Tom' doesn't say the caliber: the caliber-barrel-maker name is offered beside it.
    assert cli.handle("rename this rifle to Long Tom") == (
        "Rename the Ruger to 'Long Tom' -- right? Or, by caliber, barrel and maker: '308 20-inch Ruger'.")
    assert cli.handle("yes") == "Renamed to 'Long Tom'."
    assert "Long Tom" in store.rifles and "Ruger" not in store.rifles


def test_rename_refuses_a_name_already_taken(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    _no_intent_llm(monkeypatch)
    store.active_rifle_name = "Ruger"
    cli.handle("rename this rifle to Tikka")
    assert "There's already a rifle called 'Tikka'" in cli.handle("yes")
    assert "Ruger" in store.rifles


def test_rename_survives_between_requests_and_carries_session_readings(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    _no_intent_llm(monkeypatch)
    cli._session_log = cli_module._SessionLog()
    cli._session_log.readings = [{"rifle": "Tikka", "load": "140 ELD", "fps": 2700.0}]
    cli.handle("rename my rifles")
    state = cli.to_state()

    # A new request builds a fresh object from the saved state (as the API does).
    cli2 = BallisticaCLI(store)
    cli2.load_state(state)
    assert cli2.handle("yes").startswith("Renamed to '6.5 Creedmoor 24-inch'.")
    assert cli2._session_log.readings[0]["rifle"] == "6.5 Creedmoor 24-inch"


def test_a_different_command_ends_the_walk_through(tmp_path, monkeypatch):
    cli, store = _make(tmp_path, [("Tikka", "6.5 Creedmoor", 24), ("Ruger", ".308 Winchester", 20)])
    cli.handle("rename my rifles")
    reply = cli.handle("switch rifle to the Ruger")
    assert cli._rename is None
    assert store.active_rifle_name == "Ruger"
    assert "Ruger" in reply


def test_confirm_reads_a_new_style_name_once(tmp_path):
    from ballistica.narrowing import confirm_question
    r = Rifle(name="338 24-inch", scope_height_in=1.5, caliber=".338 Lapua Magnum", barrel_length_in=24)
    assert confirm_question(r) == "The 338 24-inch -- right?"
