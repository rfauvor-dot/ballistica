"""Loads shared by caliber, velocity per rifle (Rick, 2026-11-16).

"Make loads shared by caliber, so I enter the bullet's weight and ballistic
coefficient once and can use it in any rifle of that caliber. Keep velocity
per rifle. If a rifle has no measured speed for that load, have her estimate
it from my chronographed speed in another barrel of the same caliber,
adjusted for the barrel length difference, tell me it's an estimate, and let
me correct it later from a chronograph or from my hits at the range."
"""
from __future__ import annotations

import pytest

import ballistica.aggregate_pool as pool
import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI
from ballistica.profiles import Load, ProfileStore, Rifle
from ballistica.shared_loads import (
    FPS_PER_INCH, estimate_source, is_estimate, share_into, share_load,
)


def _smk(mv: float = 3000, name: str = "69 SMK") -> Load:
    return Load(name=name, bullet_weight_gr=69, bc=0.301, drag_model="G1",
                muzzle_velocity_fps=mv, zero_distance_yd=100)


def _store(tmp_path, rifles):
    store = ProfileStore(tmp_path / "profiles.json")
    for name, caliber, barrel in rifles:
        store.add_rifle(Rifle(name=name, scope_height_in=2.6, caliber=caliber, barrel_length_in=barrel),
                        make_active=False)
    store.active_rifle_name = rifles[0][0]
    return store


def _no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the LLM")
    monkeypatch.setattr(cli_module, "extract_intent", boom)


@pytest.fixture
def three_556(tmp_path):
    store = _store(tmp_path, [("556 20-inch", "5.56 NATO", 20), ("556 14.5-inch", "5.56 NATO", 14.5),
                              ("556 16-inch", "5.56", 16), ("223 Wylde 18-inch", ".223 Wylde", 18)])
    rifle = store.rifles["556 20-inch"]
    rifle.add_load(_smk())
    share_load(store, rifle, rifle.loads["69 SMK"])
    return store


# ---------------------------------------------------- entered once, shared

def test_a_load_reaches_every_rifle_of_the_caliber_with_an_estimate(three_556):
    short = three_556.rifles["556 14.5-inch"].loads["69 SMK"]
    assert short.bullet_weight_gr == 69 and short.bc == 0.301
    assert short.muzzle_velocity_fps == round(3000 - FPS_PER_INCH * 5.5)
    assert is_estimate(short) and estimate_source(short) == "556 20-inch"
    # "5.56" and "5.56 NATO" are the same caliber.
    assert three_556.rifles["556 16-inch"].loads["69 SMK"].muzzle_velocity_fps == 3000 - FPS_PER_INCH * 4
    # .223 Wylde is in the 5.56 family; the original copy is measured.
    assert three_556.rifles["223 Wylde 18-inch"].loads["69 SMK"].muzzle_velocity_fps == 3000 - FPS_PER_INCH * 2
    assert not is_estimate(three_556.rifles["556 20-inch"].loads["69 SMK"])


def test_the_same_ammo_under_another_name_is_not_copied_again(tmp_path):
    store = _store(tmp_path, [("A", "5.56 NATO", 20), ("B", "5.56 NATO", 16)])
    store.rifles["B"].add_load(_smk(2900, name="Sierra 69gr"))
    store.rifles["A"].add_load(_smk())
    assert share_load(store, store.rifles["A"], store.rifles["A"].loads["69 SMK"]) == []
    assert list(store.rifles["B"].loads) == ["Sierra 69gr"]


def test_estimate_comes_from_the_closest_measured_barrel(tmp_path):
    store = _store(tmp_path, [("20", "5.56 NATO", 20), ("16", "5.56 NATO", 16), ("14.5", "5.56 NATO", 14.5)])
    store.rifles["20"].add_load(_smk(3000))
    store.rifles["16"].add_load(_smk(2850))
    added = share_into(store, store.rifles["14.5"])
    assert added == ["69 SMK"]
    copy = store.rifles["14.5"].loads["69 SMK"]
    assert estimate_source(copy) == "16"
    assert copy.muzzle_velocity_fps == round(2850 - FPS_PER_INCH * 1.5)


def test_without_barrel_lengths_the_speed_is_carried_over_unchanged(tmp_path):
    store = _store(tmp_path, [("A", "5.56 NATO", 20), ("B", "5.56 NATO", None)])
    store.rifles["A"].add_load(_smk(3000))
    share_into(store, store.rifles["B"])
    assert store.rifles["B"].loads["69 SMK"].muzzle_velocity_fps == 3000
    assert is_estimate(store.rifles["B"].loads["69 SMK"])


def test_bullet_facts_follow_to_every_copy_but_velocity_does_not(three_556):
    three_556.update_load_fields("556 20-inch", "69 SMK", bc=0.310, zero_distance_yd=200)
    short = three_556.rifles["556 14.5-inch"].loads["69 SMK"]
    assert short.bc == 0.310
    assert short.zero_distance_yd == 100     # the zero is per rifle


def test_a_chronographed_speed_replaces_the_estimate_and_refreshes_the_rest(three_556):
    three_556.update_load_velocity("556 16-inch", "69 SMK", 2900)
    sixteen = three_556.rifles["556 16-inch"].loads["69 SMK"]
    assert sixteen.muzzle_velocity_fps == 2900 and not is_estimate(sixteen)
    # The 14.5 is now estimated from the closer 16-inch barrel.
    short = three_556.rifles["556 14.5-inch"].loads["69 SMK"]
    assert estimate_source(short) == "556 16-inch"
    assert short.muzzle_velocity_fps == round(2900 - FPS_PER_INCH * 1.5)


def test_a_web_save_that_leaves_velocity_alone_keeps_it_an_estimate(three_556):
    short = three_556.rifles["556 14.5-inch"].loads["69 SMK"]
    three_556.update_load_fields("556 14.5-inch", "69 SMK", muzzle_velocity_fps=short.muzzle_velocity_fps,
                                 zero_distance_yd=50)
    assert is_estimate(short)
    three_556.update_load_fields("556 14.5-inch", "69 SMK", muzzle_velocity_fps=2880)
    assert not is_estimate(short)


def test_giving_a_rifle_a_caliber_brings_that_calibers_loads(tmp_path):
    store = _store(tmp_path, [("A", "5.56 NATO", 20), ("B", "", 16)])
    store.rifles["A"].add_load(_smk(3000))
    store.update_rifle_fields("B", caliber="5.56 NATO")
    assert store.rifles["B"].loads["69 SMK"].muzzle_velocity_fps == 2900
    store.update_rifle_fields("B", barrel_length_in=18)
    assert store.rifles["B"].loads["69 SMK"].muzzle_velocity_fps == 2950


def test_shared_copies_survive_a_save_and_reload(three_556, tmp_path):
    three_556.save()
    again = ProfileStore(tmp_path / "profiles.json")
    assert is_estimate(again.rifles["556 14.5-inch"].loads["69 SMK"])


def test_an_estimate_is_never_sent_to_the_aggregate_pool(three_556, monkeypatch):
    sent = []
    monkeypatch.setattr(pool, "_SUPABASE_URL", "https://example.invalid")
    monkeypatch.setattr(pool, "supabase_http", lambda: type("H", (), {"post": lambda self, *a, **k: sent.append(1)})())
    pool.contribute_load(three_556.rifles["556 14.5-inch"], three_556.rifles["556 14.5-inch"].loads["69 SMK"], "t")
    assert sent == []


# ------------------------------------------------------------- by voice

def test_a_new_load_by_voice_is_shared_and_said_so(tmp_path, monkeypatch):
    store = _store(tmp_path, [("556 20-inch", "5.56 NATO", 20), ("556 14.5-inch", "5.56 NATO", 14.5)])
    cli = BallisticaCLI(store)
    cli._setup = cli_module._SetupSession("load")
    cli._setup.draft = {"name": "69 SMK", "bullet_weight_gr": 69, "bc": 0.301, "drag_model": "G1",
                        "muzzle_velocity_fps": 3000, "zero_distance_yd": 100}
    reply = cli._finalize_setup()
    assert "Also added to the 556 14.5-inch, with speeds estimated for barrel length." in reply
    assert store.rifles["556 14.5-inch"].loads["69 SMK"].muzzle_velocity_fps == round(3000 - FPS_PER_INCH * 5.5)


def test_switching_rifles_brings_the_calibers_loads_and_says_so(tmp_path, monkeypatch):
    _no_llm(monkeypatch)
    store = _store(tmp_path, [("556 20-inch", "5.56 NATO", 20), ("556 14.5-inch", "5.56 NATO", 14.5)])
    store.rifles["556 20-inch"].add_load(_smk())   # added before sharing existed
    cli = BallisticaCLI(store)
    reply = cli.handle("switch rifle to the 556 14.5-inch")
    assert "It now has 69 SMK from your other 5.56 and .223 rifles" in reply
    assert "69 SMK" in store.rifles["556 14.5-inch"].loads


def test_she_says_when_a_speed_is_an_estimate(three_556, monkeypatch):
    _no_llm(monkeypatch)
    three_556.active_rifle_name = "556 14.5-inch"
    cli = BallisticaCLI(three_556)
    reply = cli._switch_load("69 SMK")
    assert "That speed is an estimate, from the 556 20-inch, adjusted for barrel length" in reply
    assert "(estimated)" in cli.handle("status")
    assert cli.handle("drop at 400 yards").endswith("Speed is an estimate.")
    three_556.active_rifle_name = "556 20-inch"
    assert "estimate" not in cli.handle("drop at 400 yards")


def test_correcting_the_speed_from_hits(three_556, monkeypatch):
    _no_llm(monkeypatch)
    three_556.active_rifle_name = "556 14.5-inch"
    cli = BallisticaCLI(three_556)
    # What the solver says a 2,950 fps load needs at 500 yards...
    load = three_556.rifles["556 14.5-inch"].loads["69 SMK"]
    target = cli._velocity_for_elevation(three_556.rifles["556 14.5-inch"], load, 500, 3.1)
    reply = cli.handle("at 500 yards I needed 3.1 mils")
    assert reply == (f"3.1 mils at 500 yards works out to {target:.0f} feet per second for the 69 SMK "
                     f"in the 556 14.5-inch, against the {load.muzzle_velocity_fps:.0f} estimate. Save it?")
    # ...waits on a yes, across requests.
    state = cli.to_state()
    cli2 = BallisticaCLI(three_556)
    cli2.load_state(state)
    assert cli2.handle("yes") == f"Saved -- the 69 SMK is now {target:.0f} feet per second in the 556 14.5-inch."
    assert load.muzzle_velocity_fps == target and not is_estimate(load)
    assert "Trued from hits: 3.1 mils at 500 yards." in load.notes
    # And saved to disk.
    assert ProfileStore(three_556.path).rifles["556 14.5-inch"].loads["69 SMK"].muzzle_velocity_fps == target


def test_hits_in_clicks_and_moa_and_a_no(three_556, monkeypatch):
    _no_llm(monkeypatch)
    cli = BallisticaCLI(three_556)
    assert "works out to" in cli.handle("my hits at 600 yards took 45 clicks")
    assert cli.handle("no") == "Okay, speed unchanged."
    assert three_556.rifles["556 20-inch"].loads["69 SMK"].muzzle_velocity_fps == 3000
    assert "works out to" in cli.handle("at 600 yards I dialed 15 moa")


def test_hits_that_cant_tell_the_speed(three_556, monkeypatch):
    _no_llm(monkeypatch)
    cli = BallisticaCLI(three_556)
    assert "too close to the 100-yard zero" in cli.handle("I hit at 120 yards with 0.1 mils")
    assert "doesn't fit any sensible speed" in cli.handle("at 300 yards I needed 40 mils")
    assert cli._pending_true_velocity is None


# ------------------------------------- the 5.56 / Wylde / .223 Rem family

def _family(tmp_path):
    return _store(tmp_path, [("556 16-inch", "5.56 NATO", 16), ("Wylde 18-inch", ".223 Wylde", 18),
                             ("223 Rem 22-inch", ".223 Remington", 22), ("308 20-inch", ".308 Winchester", 20)])


def _rem55(mv: float = 3200) -> Load:
    return Load(name="55 V-Max", bullet_weight_gr=55, bc=0.255, drag_model="G1",
                muzzle_velocity_fps=mv, zero_distance_yd=100)


@pytest.mark.parametrize("caliber,kind", [
    ("5.56 NATO", "556"), ("5.56x45mm", "556"), ("556", "556"), (".223 Wylde", "wylde"), ("223 Wylde", "wylde"),
    (".223 Remington", "223rem"), (".223 Rem", "223rem"), ("223", "223rem"), (".22-250", None), (".308", None),
])
def test_chambers(caliber, kind):
    from ballistica.shared_loads import chamber
    assert chamber(Rifle(name="x", scope_height_in=2, caliber=caliber)) == kind


def test_wylde_and_556_share_everything_but_556_never_reaches_a_223_rem_chamber(tmp_path):
    store = _family(tmp_path)
    nato = store.rifles["556 16-inch"]
    nato.add_load(_smk(2950, name="69 SMK"))
    added = share_load(store, nato, nato.loads["69 SMK"])
    assert added == ["Wylde 18-inch"]
    assert "69 SMK" not in store.rifles["223 Rem 22-inch"].loads
    assert "69 SMK" not in store.rifles["308 20-inch"].loads
    # Nor when the .223 Remington rifle asks for its family's loads.
    assert share_into(store, store.rifles["223 Rem 22-inch"]) == []


def test_223_rem_loads_go_everywhere_in_the_family_and_stay_223(tmp_path):
    store = _family(tmp_path)
    rem = store.rifles["223 Rem 22-inch"]
    rem.add_load(_rem55())
    assert sorted(share_load(store, rem, rem.loads["55 V-Max"])) == ["556 16-inch", "Wylde 18-inch"]
    copy = store.rifles["556 16-inch"].loads["55 V-Max"]
    assert "[.223 Remington ammo]" in copy.notes
    assert copy.muzzle_velocity_fps == 3200 - FPS_PER_INCH * 6
    # A new .223 Remington rifle may take that copy back from the 5.56: it's still .223 ammo,
    # even after the copy is chronographed in the 5.56.
    store.update_load_velocity("556 16-inch", "55 V-Max", 3010)
    assert "[.223 Remington ammo]" in copy.notes
    store.add_rifle(Rifle(name="223 Rem 20-inch", scope_height_in=2.6, caliber=".223 Rem", barrel_length_in=20),
                    make_active=False)
    assert share_into(store, store.rifles["223 Rem 20-inch"]) == ["55 V-Max"]


def test_ammo_named_as_556_in_a_wylde_rifle_or_223_in_a_556_rifle(tmp_path):
    from ballistica.shared_loads import ammo_kind
    store = _family(tmp_path)
    wylde, nato, rem = store.rifles["Wylde 18-inch"], store.rifles["556 16-inch"], store.rifles["223 Rem 22-inch"]
    assert ammo_kind(_smk(name="69 SMK"), wylde) == "556"            # unsaid in a Wylde: treated as 5.56
    assert ammo_kind(_smk(name="Federal 223 Rem 69"), nato) == "223rem"
    assert ammo_kind(_smk(name="M193"), rem) == "556"
    nato.add_load(_smk(name="Federal 223 Rem 69"))
    assert share_load(store, nato, nato.loads["Federal 223 Rem 69"]) == ["Wylde 18-inch", "223 Rem 22-inch"]


def test_hits_correction_takes_correct_as_yes_and_ignores_windage(three_556, monkeypatch):
    _no_llm(monkeypatch)
    cli = BallisticaCLI(three_556)
    cli._last_tool_name = ""
    cli.handle("at 600 yards I needed 4.8 mils")
    assert cli.handle("that's correct").startswith("Saved -- the 69 SMK is now")
    # A windage figure is never worked back into a speed.
    cli.handle("at 600 yards I held 1.2 mils of wind")
    assert cli._pending_true_velocity is None
