"""Engine validation tests.

The reference values in test_matches_reference_implementation were
produced by independently running the open-source py-ballisticcalc
library (https://github.com/o-murphy/py-ballisticcalc) with identical
inputs -- not recalled from memory -- specifically to catch unit or
physics-constant errors that would otherwise silently bias every drop
number this engine produces.
"""
import math
import os
import tempfile

import pytest

from ballistica.angle import solve_incline_angle, _drop_clicks_at
from ballistica.atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE
from ballistica.drag_tables import drag_coefficient
from ballistica.profiles import Load, ProfileStore, Rifle
from ballistica.trajectory import TrajectorySolver, WindCondition
from ballistica.units import inches_to_mrad, mrad_to_inches, moa_to_inches
from ballistica.zero import find_minimum_spread_zero, vertical_spread


def test_matches_reference_implementation_standard_atmosphere():
    """.308 168gr SMK, BC .462 G1, 2650 fps, 1.5in sight height, 100yd
    zero, standard atmosphere, no wind."""
    solver = TrajectorySolver(
        muzzle_velocity_fps=2650, bc=0.462, drag_model="G1",
        scope_height_in=1.5, atmosphere=STANDARD_ATMOSPHERE,
    )
    table = {p.range_yd: p for p in solver.drop_table(100, 500, 50)}

    # These are the raw "height" values (their sign convention: negative
    # = below line of sight) as printed by py-ballisticcalc; our
    # drop_in is the negation of that (positive = below LOS).
    expected_height_in = {
        0: -1.500, 50: -0.083, 100: -0.000, 150: -1.357, 200: -4.271,
        250: -8.870, 300: -15.294, 350: -23.700, 400: -34.260,
        450: -47.166, 500: -62.627,
    }
    for yd, expected_height in expected_height_in.items():
        assert table[yd].drop_in == pytest.approx(-expected_height, abs=0.05)

    expected_velocity = {0: 2650.0, 250: 2182.0, 500: 1765.4}
    for yd, expected in expected_velocity.items():
        assert table[yd].velocity_fps == pytest.approx(expected, abs=1.0)


def test_matches_reference_implementation_nonstandard_atmosphere_and_wind():
    """77gr SMK, BC .372 G7, 2422 fps, 2.5in sight height, 36yd zero,
    3500ft/85F/26.5inHg/40%RH, 10mph 3-o'clock-equivalent crosswind."""
    atmo = AtmosphereConditions(temp_f=85, pressure_inhg=26.5, humidity_pct=40, altitude_ft=3500)
    solver = TrajectorySolver(
        muzzle_velocity_fps=2422, bc=0.372, drag_model="G7",
        scope_height_in=2.5, atmosphere=atmo,
        wind=WindCondition(speed_mph=10, clock_deg=90),
    )
    table = {p.range_yd: p for p in solver.drop_table(36, 500, 100)}

    expected_drop_in = {0: -2.500, 100: 2.476, 200: 1.014, 300: -7.453, 400: -23.552, 500: -47.977}
    for yd, expected in expected_drop_in.items():
        assert table[yd].drop_in == pytest.approx(-expected, abs=0.1)

    expected_windage_abs_in = {100: 0.456, 300: 4.271, 500: 12.369}
    for yd, expected in expected_windage_abs_in.items():
        assert abs(table[yd].windage_in) == pytest.approx(expected, abs=0.1)


def test_standard_atmosphere_density_matches_icao_reference():
    rho_lbft3 = STANDARD_ATMOSPHERE.air_density_slug_ft3() * 32.17405
    assert rho_lbft3 == pytest.approx(0.076474, abs=1e-4)
    assert STANDARD_ATMOSPHERE.density_ratio() == pytest.approx(1.0, abs=1e-9)
    assert STANDARD_ATMOSPHERE.speed_of_sound_fps() == pytest.approx(1116.45, abs=0.5)


def test_drag_table_endpoints_and_clamping():
    assert drag_coefficient("G1", 0.0) == 0.2629
    assert drag_coefficient("G1", 10.0) == drag_coefficient("G1", 5.0)
    assert drag_coefficient("G1", -1.0) == drag_coefficient("G1", 0.0)
    assert drag_coefficient("G7", 1.0) == 0.3803


def test_zero_solver_hits_line_of_sight_at_zero_distance():
    solver = TrajectorySolver(
        muzzle_velocity_fps=2766, bc=0.372, drag_model="G7",
        scope_height_in=2.5, atmosphere=STANDARD_ATMOSPHERE,
    )
    point = solver.at_range(36, 36)
    assert point.drop_in == pytest.approx(0.0, abs=0.02)


def test_minimum_spread_zero_balances_rise_and_terminal_drop():
    solver = TrajectorySolver(
        muzzle_velocity_fps=2766, bc=0.372, drag_model="G7",
        scope_height_in=2.5, atmosphere=STANDARD_ATMOSPHERE,
    )
    result = find_minimum_spread_zero(solver, max_range_yd=500)
    assert result.max_height_in == pytest.approx(result.min_height_in, abs=0.5)

    # It should actually be better than either a short or a very long zero.
    short = vertical_spread(solver, 100, 500)
    long_ = vertical_spread(solver, 490, 500)
    assert result.spread_in < short.spread_in
    assert result.spread_in < long_.spread_in


def test_angle_solver_recovers_synthetic_incline():
    solver = TrajectorySolver(
        muzzle_velocity_fps=2766, bc=0.372, drag_model="G7",
        scope_height_in=2.5, atmosphere=STANDARD_ATMOSPHERE,
    )
    zero_yd, click_value, ref_yd, los_yd, true_angle = 36, 0.1, 100, 400, 35.0

    shoot_to = los_yd * math.cos(math.radians(true_angle))
    ref_clicks = _drop_clicks_at(solver, zero_yd, ref_yd, click_value)
    shoot_to_clicks = _drop_clicks_at(solver, zero_yd, shoot_to, click_value)
    observed_diff = shoot_to_clicks - ref_clicks

    result = solve_incline_angle(solver, zero_yd, ref_yd, los_yd, observed_diff, click_value)
    assert result.angle_deg == pytest.approx(true_angle, abs=0.05)
    assert result.shoot_to_distance_yd == pytest.approx(shoot_to, abs=0.5)


def test_angle_solver_rejects_unreachable_observation():
    solver = TrajectorySolver(
        muzzle_velocity_fps=2766, bc=0.372, drag_model="G7",
        scope_height_in=2.5, atmosphere=STANDARD_ATMOSPHERE,
    )
    with pytest.raises(ValueError):
        solve_incline_angle(solver, 36, 100, 400, observed_diff_clicks=-500, click_value_mrad=0.1)


def test_unit_conversions_are_exact():
    assert mrad_to_inches(1.0, 100) == pytest.approx(3.6)
    assert inches_to_mrad(3.6, 100) == pytest.approx(1.0)
    assert moa_to_inches(1.0, 100) == pytest.approx(1.047, abs=0.001)


def test_profile_store_roundtrip_and_fuzzy_switching(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    rifle = Rifle(name="AR-15 20in Faxon", scope_height_in=2.5, click_value_mrad=0.1)
    rifle.add_load(Load(
        name="21.0gr H335", bullet_weight_gr=77, bc=0.372, drag_model="G7",
        muzzle_velocity_fps=2422, zero_distance_yd=36,
    ), make_active=False)
    rifle.add_load(Load(
        name="23.5gr H335", bullet_weight_gr=77, bc=0.372, drag_model="G7",
        muzzle_velocity_fps=2766, zero_distance_yd=36,
    ), make_active=True)
    store.add_rifle(rifle)
    store.save()

    reloaded = ProfileStore(path)
    assert reloaded.get_active_rifle().get_active_load().name == "23.5gr H335"

    switched = reloaded.set_active_load("21 grain")
    assert switched.name == "21.0gr H335"

    updated = reloaded.update_load_velocity("Faxon", "21.0gr", 2450)
    assert updated.muzzle_velocity_fps == 2450
    reloaded.save()

    reloaded_again = ProfileStore(path)
    assert reloaded_again.find_rifle("faxon").find_load("21.0").muzzle_velocity_fps == 2450


def test_find_rifle_matches_on_caliber_and_barrel_length_not_just_name(tmp_path):
    """Regression, found live (2026-09-05): find_rifle() used to match
    against the bare name only. A real, correct disambiguating
    description -- "the 5.7x28 with the 11 inch barrel", said specifically
    BECAUSE two similarly-named rifles existed -- added query tokens
    ("11", "inch", "barrel") that don't appear in either rifle's name at
    all. Since _tokens_match requires every query token to match
    something, that legitimate extra detail made the match fail
    completely (zero matches) instead of helping pick the right one --
    the opposite of what describing a rifle in more detail should do.
    find_load() already casts this same wider net across name/powder/
    notes; this pins the identical fix extended to rifles (caliber,
    barrel length, twist, scope make/model)."""
    store = ProfileStore(tmp_path / "profiles.json")
    target = Rifle(name="PSA Rattler", scope_height_in=2.0, caliber="5.7x28mm",
                    barrel_length_in=11, click_value_mrad=0.1)
    store.add_rifle(target)
    decoy = Rifle(name="AR-15", scope_height_in=2.5, caliber=".223 Wylde",
                   barrel_length_in=18, click_value_mrad=0.1)
    store.add_rifle(decoy, make_active=False)

    found = store.find_rifle("5.7x28 with the 11 inch barrel")
    assert found.name == "PSA Rattler"

    # A genuinely ambiguous case (two rifles whose searchable text both
    # satisfy every query token) must still fail honestly, asking for
    # disambiguation, rather than silently guessing one -- not "no rifle
    # found" (today's confusing dead end), and not a wrong silent pick.
    store.add_rifle(Rifle(name="5.7x28", scope_height_in=2.0, caliber="5.7x28mm",
                           barrel_length_in=11, click_value_mrad=0.1), make_active=False)
    with pytest.raises(KeyError, match="matches multiple rifles"):
        store.find_rifle("5.7x28 with the 11 inch barrel")


def test_update_rifle_fields_persists_and_rejects_invalid_values_without_corrupting(tmp_path):
    """Addendum 29: confirmed live that the raw save mechanism itself
    works (a PUT persisted correctly through a fresh, separate GET) --
    this pins that at the storage layer directly, plus a real bug found
    while fixing the validation gap: update_rifle_fields() used to
    setattr() before validating, so a rejected update (e.g. a bad
    reticle_unit) still left the rifle mutated for every subsequent
    call, not just the one that (correctly) raised."""
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    rifle = Rifle(name="Test Rifle", scope_height_in=2.5, twist_rate="1:7", reticle_unit="MRAD")
    store.add_rifle(rifle)
    store.save()

    updated = store.update_rifle_fields("Test Rifle", twist_rate="1:8")
    assert updated.twist_rate == "1:8"
    store.save()
    reloaded = ProfileStore(path)
    assert reloaded.find_rifle("Test Rifle").twist_rate == "1:8"

    with pytest.raises(ValueError):
        store.update_rifle_fields("Test Rifle", reticle_unit="banana")
    # The rejected update must not leave the object half-mutated --
    # reticle_unit must still read a valid, unchanged value afterward.
    assert store.find_rifle("Test Rifle").reticle_unit == "MRAD"
    # And a subsequent valid call must still work correctly, proving the
    # object wasn't left in a corrupted state by the rejected one.
    again = store.update_rifle_fields("Test Rifle", reticle_unit="MOA")
    assert again.reticle_unit == "MOA"


def test_delete_rifle_removes_it_and_reassigns_active(tmp_path):
    """No delete existed at all before Addendum 29. Also pins that
    deleting the active rifle hands active status to whatever's left,
    rather than leaving the store pointing at a rifle that no longer
    exists."""
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.add_rifle(Rifle(name="Rifle A", scope_height_in=2.5), make_active=False)
    store.add_rifle(Rifle(name="Rifle B", scope_height_in=2.6), make_active=True)

    deleted = store.delete_rifle("Rifle B")
    assert deleted.name == "Rifle B"
    assert "Rifle B" not in store.rifles
    assert store.active_rifle_name == "Rifle A"

    store.delete_rifle("Rifle A")
    assert store.rifles == {}
    assert store.active_rifle_name is None

    with pytest.raises(KeyError):
        store.delete_rifle("Rifle A")


def test_rifle_supports_zero_or_multiple_loads_and_suppressor_tracking(tmp_path):
    """Addendum 36 (correction from Rick): a rifle with no loads yet is a
    valid, normal state -- e.g. building the profile before load
    development -- and one rifle can hold multiple loads. Rick's own
    example: a single suppressed .300 BLK carrying both a subsonic and a
    supersonic load. Suppressor data lives on the RIFLE, not any one
    load, since the same can stays attached across both -- and it's
    deliberately open text, not a brand enum, since plenty of real cans
    are homemade/custom builds with no commercial name to pick from."""
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)

    rifle = Rifle(name="300 BLK SBR", scope_height_in=2.0, caliber=".300 BLK",
                  has_suppressor=True, suppressor_type="custom build, ATF Form 1")
    store.add_rifle(rifle)
    store.save()  # zero loads -- must not raise

    reloaded = ProfileStore(path)
    saved = reloaded.find_rifle("300 BLK SBR")
    assert saved.loads == {}
    assert saved.active_load_name is None
    assert saved.has_suppressor is True
    assert saved.suppressor_type == "custom build, ATF Form 1"

    saved.add_load(Load(name="Subsonic 220gr", bullet_weight_gr=220, bc=0.35, drag_model="G1",
                         muzzle_velocity_fps=1050, zero_distance_yd=50), make_active=False)
    saved.add_load(Load(name="Supersonic 125gr", bullet_weight_gr=125, bc=0.28, drag_model="G1",
                         muzzle_velocity_fps=2150, zero_distance_yd=100), make_active=True)
    reloaded.save()

    final = ProfileStore(path).find_rifle("300 BLK SBR")
    assert set(final.loads.keys()) == {"Subsonic 220gr", "Supersonic 125gr"}
    assert final.active_load_name == "Supersonic 125gr"
    # Suppressor stays a rifle-level property regardless of which load is active.
    assert final.has_suppressor is True
    assert final.suppressor_type == "custom build, ATF Form 1"


def test_voice_query_conversation_state_and_error_handling(tmp_path):
    """BallisticaCLI.handle() deliberately keeps conversation state
    across calls (switching load, setting conditions) unlike the
    stateless /v2/calc/* endpoints -- pins that continuity, plus that
    "quit" and a nonsense utterance both come back as spoken-safe text
    instead of ever raising, and that the "set conditions" reply
    doesn't leak a raw Python object repr into what's meant to be read
    aloud by TTS (regression: it used to). Exercises the CLI directly
    (not through HTTP) -- this is engine/conversation-state behavior,
    not an API-layer concern, so it doesn't need a live server or
    network access to verify (previously went through the old single-
    tenant /voice/query endpoint, removed 2026-08-28's security
    hardening pass; the underlying handle() logic this covers is
    identical either way)."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    reply = cli.handle("switch to 21.0gr")
    assert "21.0gr" in reply

    baseline = cli.handle("what's my drop at 500 yards")

    reply = cli.handle("set conditions temp 90 pressure 26.5 altitude 3500 humidity 40")
    assert "AtmosphereConditions" not in reply
    assert "90 degrees" in reply

    after = cli.handle("what's my drop at 500 yards")
    assert after != baseline

    with pytest.raises(SystemExit):
        cli.handle("quit")

    reply = cli.handle("gibberish nonsense query")
    assert isinstance(reply, str)


def test_repeat_solution_reuses_last_drop_without_recalculating(tmp_path):
    """"repeat windage/elevation/solution" should re-speak the last
    drop-at-range answer from memory, not recompute it -- so it still
    reflects what was actually last spoken even if conditions changed
    in between. Also pins the no-solution-yet case (fresh session,
    asked to repeat before ever getting a solution) doesn't crash."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    reply = cli.handle("repeat solution")
    assert "no solution" in reply.lower()

    baseline = cli.handle("drop at 400 yards")

    # Conditions change after the solution was spoken -- repeat should
    # still hand back the original answer, not a freshly recalculated one.
    cli.handle("set conditions temp 100 pressure 26.0 altitude 5000 humidity 10")

    full = cli.handle("repeat solution")
    assert full == baseline

    elevation = cli.handle("repeat elevation")
    assert elevation in baseline
    assert "Windage" not in elevation

    windage = cli.handle("repeat the windage")
    assert windage in baseline
    assert "Elevation" not in windage


def test_voice_query_signals_awaiting_response_during_conversation(tmp_path):
    """Regression: the voice frontend used to always drop back to
    wake-word-only listening after one question/answer exchange, which
    silently ate every field after the first during guided setup (Rick
    would answer the first question, get silence, and only "waking" her
    back up mid-setup would resume it -- but from the frontend's
    perspective every answer given without saying "Ballistica" again
    was never even sent). The API tells the frontend whether to keep
    listening without the wake word by checking exactly this: true
    while a guided setup is genuinely mid-conversation, false once it's
    done, cancelled, or for an ordinary one-shot command -- checked here
    directly against the CLI's own session state (api.py's
    `awaiting_response` field is a one-line derivation of this same
    state, see v2_voice_query in api.py)."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    def awaiting_response() -> bool:
        return cli._setup is not None or cli._calibration is not None or cli._pending_delete is not None

    cli.handle("what's my drop at 400 yards")
    assert awaiting_response() is False

    cli.handle("let's set up a new load")
    assert awaiting_response() is True

    cli.handle("never mind, cancel")
    assert awaiting_response() is False


def test_update_rifle_fields_command_edits_the_active_rifle(tmp_path):
    """Addendum 29: there was previously no voice command at all for
    editing an existing rifle's fields -- "change the twist rate to
    1:8" declined with "didn't understand", which reads exactly like a
    save that silently failed even though nothing was ever attempted.
    Exercises the dispatch method directly (bypassing the live LLM
    classification step, which was verified by hand) to deterministically
    cover the actual new logic: filtering to valid fields, persisting,
    and surfacing a real error rather than crashing."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    reply = cli._update_rifle_fields({"twist_rate": "1:8", "reticle_unit": "MOA"})
    assert "twist rate 1:8" in reply
    assert "reticle unit MOA" in reply
    rifle = store.get_active_rifle()
    assert rifle.twist_rate == "1:8"
    assert rifle.reticle_unit == "MOA"

    # Reload from disk to prove it actually persisted, not just an
    # in-memory mutation on the same object.
    reloaded = ProfileStore(store.path)
    assert reloaded.get_active_rifle().twist_rate == "1:8"

    # An invalid value must surface as a real error, not crash or silently
    # do nothing.
    bad = cli._update_rifle_fields({"reticle_unit": "banana"})
    assert "must be" in bad.lower()

    # A dict with nothing recognizable declines cleanly.
    empty = cli._update_rifle_fields({"unrelated_key": "value"})
    assert "didn't catch" in empty.lower()


def test_delete_rifle_voice_flow_requires_explicit_confirmation(tmp_path):
    """Addendum 29: no delete existed at all before this. A destructive
    action gets a confirm gate that defaults SAFE -- only an explicit
    yes actually deletes; an ambiguous reply keeps the data."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)
    original_rifle = store.active_rifle_name

    ask = cli.handle("delete this rifle")
    assert "can't be undone" in ask.lower()
    assert original_rifle in ask
    assert cli._pending_delete == original_rifle

    kept = cli.handle("hmm not sure")
    assert "keeping it" in kept.lower()
    assert cli._pending_delete is None
    assert original_rifle in store.rifles

    cli.handle("delete this rifle")
    deleted = cli.handle("yes")
    assert "deleted" in deleted.lower()
    assert original_rifle not in store.rifles
    assert cli._pending_delete is None


def test_setup_confirmation_recognizes_natural_phrasing(monkeypatch, tmp_path):
    """Regression (Addendum 28): "that is correct" -- a completely
    natural confirmation -- matched none of the old patterns (only
    "correct" as the literal first word, and one specific "that's
    right" phrasing). It fell through to field extraction, found
    nothing, and got stuck repeating "didn't catch that" with the
    setup session still open, which looked exactly like a dead mic
    from the outside even though this reproduces identically with
    plain text -- confirmed live to be an interpretation bug, not an
    audio one. Also pins that a rejection phrased mid-sentence ("that's
    not correct") is never misread as a confirmation just because it
    contains the word "correct"."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"name": "Test Rifle"})
    cli.handle("set up a new rifle")
    cli.handle("call it Test Rifle")
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"scope_height_in": 2.5})
    cli.handle("scope height 2.5")
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"optic_type": "scope"})
    cli.handle("it's a magnified scope")

    summary = None
    for _ in range(15):
        reply = cli.handle("skip")
        if "sound right" in reply.lower():
            summary = reply
            break
    assert summary is not None, "never reached the confirmation summary"

    saved = cli.handle("that is correct")
    assert "saved" in saved.lower()
    assert store.active_rifle_name == "Test Rifle"


def test_setup_confirmation_rejects_negated_mid_sentence_phrasing(monkeypatch, tmp_path):
    """"that's not correct" must be read as a rejection, not a
    confirmation, even though it contains the word "correct" -- the
    unanchored yes-detection that fixes Addendum 28 must not fire when
    that word is actually negated."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"name": "Test Rifle"})
    cli.handle("set up a new rifle")
    cli.handle("call it Test Rifle")
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"scope_height_in": 2.5})
    cli.handle("scope height 2.5")
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"optic_type": "scope"})
    cli.handle("it's a magnified scope")

    reached_summary = False
    for _ in range(15):
        reply = cli.handle("skip")
        if "sound right" in reply.lower():
            reached_summary = True
            break
    assert reached_summary, "never reached the confirmation summary"

    reply = cli.handle("that's not correct")
    assert "what needs to change" in reply.lower()
    assert cli._setup is not None
    assert cli._setup.confirming is False


def test_load_setup_slot_filling_multi_turn_correction_and_save(monkeypatch, tmp_path):
    """Guided voice setup for a new load: multi-turn slot-filling through
    the full field set (required fields, then every optional field the
    manual Setup form has -- regression: this used to stop asking the
    moment the required subset was filled, which is exactly what Rick
    flagged as an incomplete-feeling interview), "skip" moving past an
    optional field, a same-breath correction after the read-back summary
    ("no, actually zero it at 50 yards" -- regression: this used to
    discard the correction and just re-ask "what needs to change?",
    leaving the interview stuck), and a final save. The LLM extraction
    call is stubbed so this is deterministic and doesn't hit the real
    API -- live behavior of the extraction itself was verified by hand."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    responses = iter([
        {},  # nothing extra volunteered in the trigger utterance itself
        {"name": "25gr Varget"},
        {"bullet_weight_gr": 75, "bc": 0.37, "drag_model": "G1"},
        {"muzzle_velocity_fps": 2900, "zero_distance_yd": 100},
        {"zero_distance_yd": 50},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: next(responses))

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    assert "call this load" in cli.handle("let's set up a new load").lower()

    # "skip" can't be used to bypass a required field.
    refused = cli.handle("skip")
    assert "need that one" in refused.lower()
    assert "call this load" in refused.lower()

    assert "bullet weight" in cli.handle("call it 25gr Varget").lower()
    assert "muzzle velocity" in cli.handle("75 grains, point three seven, G1").lower()

    # All required fields are in now -- next it should walk through the
    # optional ones (bullet_type, powder, powder_charge_gr, notes) rather
    # than jumping straight to the summary.
    next_prompt = cli.handle("2900 feet per second, zeroed at 100 yards")
    assert "bullet" in next_prompt.lower()
    assert "sound right" not in next_prompt.lower()

    for _ in range(3):
        skip_reply = cli.handle("skip")
        assert "sound right" not in skip_reply.lower()
    summary = cli.handle("skip")
    assert "sound right" in summary.lower()
    assert "100 yards" in summary

    corrected = cli.handle("no, actually zero it at 50 yards")
    assert "sound right" in corrected.lower()
    assert "50 yards" in corrected
    assert "100 yards" not in corrected

    saved = cli.handle("yes, save it")
    assert "25gr Varget" in saved
    assert cli._setup is None

    rifle = store.get_active_rifle()
    assert rifle.active_load_name == "25gr Varget"
    assert rifle.loads["25gr Varget"].zero_distance_yd == 50
    assert rifle.loads["25gr Varget"].bc == 0.37


def test_rifle_setup_saves_and_activates_new_rifle(monkeypatch, tmp_path):
    """Same guided-setup machinery, the other kind -- pins that only
    name/scope_height_in are required (everything else on Rifle has a
    default) and that a new rifle becomes the active one once saved.
    Also covers the full field walkthrough: caliber was volunteered up
    front, so it should be skipped automatically without being asked
    again, while the other eleven optional fields (barrel length, twist,
    scope info, suppressor, etc.) each get asked and are passed with
    "skip"."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    responses = iter([
        {},  # nothing extra volunteered in the trigger utterance itself
        {"name": "Creedmoor bolt gun", "caliber": "6.5 Creedmoor"},
        {"scope_height_in": 2.0},
        {"optic_type": "scope"},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: next(responses))

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    assert "call this rifle" in cli.handle("set up a new rifle").lower()
    next_prompt = cli.handle("call it the Creedmoor bolt gun, caliber 6.5 Creedmoor")
    assert "scope height" in next_prompt.lower()

    optic_prompt = cli.handle("scope height is 2 inches")
    assert "magnified scope or a red dot" in optic_prompt.lower()

    after_required = cli.handle("magnified scope")
    # Caliber was already given -- shouldn't be asked again. Required
    # fields are done, so this should be the first *other* optional field,
    # not caliber and not the confirmation summary yet.
    assert "caliber" not in after_required.lower()
    assert "sound right" not in after_required.lower()

    for _ in range(10):
        reply = cli.handle("skip")
        assert "sound right" not in reply.lower()
    summary = cli.handle("skip")
    assert "sound right" in summary.lower()

    saved = cli.handle("yes")
    assert "Creedmoor bolt gun" in saved
    assert store.active_rifle_name == "Creedmoor bolt gun"
    assert store.rifles["Creedmoor bolt gun"].caliber == "6.5 Creedmoor"


def test_rifle_setup_red_dot_skips_magnification_and_focal_plane(monkeypatch, tmp_path):
    """Regression (Addendum 27): a Holosun 510C red dot couldn't get
    through the setup interview because every optic-info question
    assumed a magnified scope. optic_type="red_dot" must route the
    walkthrough to dot_size_moa/reticle_type instead of magnification/
    objective_lens_mm/focal_plane, which don't apply to a fixed-1x
    reflex sight -- and those fields must never be asked about or
    appear in the summary for a red dot."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    responses = iter([
        {},  # nothing extra volunteered in the trigger utterance itself
        {"name": "red dot AR"},
        {"scope_height_in": 2.6},
        {"optic_type": "red_dot", "scope_make": "Holosun", "scope_model": "510C"},
        {"dot_size_moa": 2, "reticle_type": "65 MOA circle + dot"},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: next(responses))

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("set up a new rifle")
    cli.handle("call it the red dot AR")
    optic_prompt = cli.handle("scope height is 2.6 inches")
    assert "magnified scope or a red dot" in optic_prompt.lower()

    after_optic = cli.handle("it's a Holosun 510C red dot")
    # A common field (not caliber-specific) should come next -- and
    # crucially, never magnification/focal plane for a red dot.
    assert "magnification" not in after_optic.lower()
    assert "focal plane" not in after_optic.lower()

    # Dot size/reticle volunteered out of sequence -- captured, but the
    # remaining common fields still get asked before reaching them again.
    cli.handle("2 MOA dot with a 65 MOA circle")

    seen_prompts = []
    for _ in range(20):
        reply = cli.handle("skip")
        if "sound right" in reply.lower():
            summary = reply
            break
        seen_prompts.append(reply)
    else:
        raise AssertionError("never reached the confirmation summary")

    assert not any("magnification" in p.lower() for p in seen_prompts)
    assert not any("focal plane" in p.lower() for p in seen_prompts)
    assert "magnification" not in summary.lower()
    assert "focal" not in summary.lower()

    saved = cli.handle("yes")
    assert "red dot AR" in saved
    rifle = store.rifles["red dot AR"]
    assert rifle.optic_type == "red_dot"
    assert rifle.dot_size_moa == 2
    assert rifle.reticle_type == "65 MOA circle + dot"
    assert rifle.magnification == ""
    assert rifle.focal_plane == ""


def test_setup_extraction_told_which_field_is_being_asked(monkeypatch, tmp_path):
    """Regression (Addendum 27): the extraction call didn't know which
    field it was answering, so "pistol caliber carbine, nine
    millimeter" (answering "what do you want to call this rifle?")
    got read as pure caliber info with no name -- silently re-asking
    the identical question forever, even though the words were
    understood. Pins that the currently-asked field is actually passed
    through to extract_setup_fields()."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    captured_calls = []

    def fake_extract(text, kind, asking_about=None):
        captured_calls.append((text, kind, asking_about))
        # Only the real answer utterance mentions anything -- the bare
        # trigger utterance ("set up a new rifle") extracts nothing,
        # same as a real model would return for it.
        return {"caliber": "9mm"} if "pistol" in text else {}

    monkeypatch.setattr(cli_module, "extract_setup_fields", fake_extract)

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("set up a new rifle")
    cli.handle("pistol caliber carbine, nine millimeter")

    # captured_calls[0] is the trigger-utterance pre-fill call added for
    # same-breath setup info; the field-being-asked pin this test is
    # actually about is the *second* call, once the interview is running.
    assert captured_calls[1] == ("pistol caliber carbine, nine millimeter", "rifle", "name")


def test_setup_acknowledges_progress_when_asked_field_still_unfilled(monkeypatch, tmp_path):
    """When a turn makes real progress but not on the specific field
    that was just asked about (e.g. asked for a name, only got a
    caliber back), the reply must say what it caught rather than
    silently repeating the identical question -- a bare repeat reads
    as "didn't understand at all" even when something real registered."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"caliber": "9mm"} if "pistol" in text else {})

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("set up a new rifle")
    reply = cli.handle("pistol caliber carbine, nine millimeter")

    assert "9mm" in reply
    assert "got it" in reply.lower()
    assert "what do you want to call this rifle" in reply.lower()
    assert cli._setup.draft.get("caliber") == "9mm"
    assert cli._setup.draft.get("name") in (None, "")


def test_setup_rejects_hallucinated_placeholder_values(monkeypatch, tmp_path):
    """The actual root cause behind Addendum 11's infinite loop: asked
    something that doesn't answer the current field (e.g. "what
    caliber" said while scope height is being asked), the real Claude
    extraction was observed live to sometimes return a placeholder like
    "<UNKNOWN>" instead of just omitting the field. That value used to
    sail straight through the None/empty-string filter, silently
    overwriting a real captured value with garbage -- and worse, made
    the draft dict register as "changed" every turn, which defeated the
    no-progress failure counter entirely (the counter never tripped
    because *something* always looked different). Placeholder-shaped
    strings must be treated the same as no answer at all: rejected
    before they reach the draft, so a real value can't be clobbered and
    the failure counter counts correctly."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"caliber": "5.7x28mm", "name": "CMMG"})
    cli.handle("let's set up a new rifle")
    cli.handle("5.7x28mm, 11.5 inch, CMMG")
    assert cli._setup.draft["caliber"] == "5.7x28mm"

    for placeholder in ["<UNKNOWN>", "unknown", "n/a", "N/A", "null", "[not specified]"]:
        cli._setup.failed_attempts = 0  # isolate each placeholder, independent of the retry cap
        monkeypatch.setattr(cli_module, "extract_setup_fields",
                             lambda text, kind, p=placeholder, asking_about=None: {"caliber": p})
        reply = cli.handle("what caliber")
        assert cli._setup.draft["caliber"] == "5.7x28mm", f"placeholder {placeholder!r} overwrote a real value"
        assert "didn't catch" in reply.lower()
        assert cli._setup.failed_attempts == 1, \
            f"placeholder {placeholder!r} looked like progress and reset the failure counter"


def test_setup_gives_up_after_repeated_failures_to_understand(monkeypatch, tmp_path):
    """Regression (Addendum 11): a modal setup session that can't
    understand a repeated answer used to stay open forever, re-asking
    the same question indefinitely -- confirmed live as a real stuck
    loop that even survived disabling voice, since the frontend had no
    way to tell "stuck" apart from "still legitimately in progress".
    After a few consecutive turns with zero actual progress, the
    session must give up and cleanly exit setup instead of staying
    open. Also pins that a correction which overwrites an existing
    field (same key, new value) counts as real progress and does NOT
    trip the failure counter, even though the draft's size doesn't
    grow."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"name": "AR-15"} if "AR-15" in text else {})
    cli.handle("let's set up a new rifle")
    cli.handle("call it the AR-15")  # real progress -- resets the counter

    # Now every turn fails to extract anything new (simulates the LLM
    # genuinely not understanding, or repeated silence/noise).
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {})
    first = cli.handle("what's the scope height")
    assert "didn't catch" in first.lower()
    assert cli._setup is not None

    second = cli.handle("still not understanding")
    assert "didn't catch" in second.lower()
    assert cli._setup is not None

    gave_up = cli.handle("one more try")
    assert "trouble understanding" in gave_up.lower()
    assert cli._setup is None

    # Confirms the CLI is back to normal command handling, not stuck.
    assert "yards" in cli.handle("drop at 300 yards").lower()


def test_setup_correction_overwriting_existing_field_resets_failure_counter(monkeypatch, tmp_path):
    """A correction that changes an already-captured field's value (not
    adding a new key) must count as progress, not a failure -- pins the
    fix against the size-of-draft-only check that would have wrongly
    penalized exactly this case."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    responses = iter([
        {},  # nothing extra volunteered in the trigger utterance itself
        {"name": "25gr Varget"},
        {"bullet_weight_gr": 75, "bc": 0.37, "drag_model": "G1"},
        {"muzzle_velocity_fps": 2900, "zero_distance_yd": 100},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: next(responses))
    cli.handle("let's set up a new load")
    cli.handle("call it 25gr Varget")
    cli.handle("75 grains, point three seven, G1")
    cli.handle("2900 feet per second, zeroed at 100 yards")
    for _ in range(4):
        cli.handle("skip")

    # Two "no progress" turns, then a same-key-overwrite correction --
    # the correction must reset the counter, not be swallowed by it.
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {})
    cli.handle("uh")
    cli.handle("uh")
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {"zero_distance_yd": 50})
    corrected = cli.handle("no, actually zero it at 50 yards")
    assert "50 yards" in corrected
    assert cli._setup.failed_attempts == 0

    # Two more failures shouldn't be enough to trip the cap now that
    # the counter was reset by the correction above.
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {})
    cli.handle("uh")
    still_open = cli.handle("uh")
    assert "didn't catch" in still_open.lower()
    assert cli._setup is not None


def test_setup_prefills_fields_volunteered_in_the_trigger_utterance(monkeypatch, tmp_path):
    """Regression, found live while scoping Session Mode (2026-09-05):
    "now let's go to a new rifle, the 9mm PCC" used to start the
    interview and then ask "what do you want to call this rifle?" anyway
    -- the name stated in that same breath was silently discarded,
    because _start_setup() never ran extraction on the utterance that
    triggered it, only on turns after the interview was already running.
    Same bug, two different entry points: the fast regex path in
    handle() (a rich utterance can still match the loose "new...rifle"
    pattern) and the LLM-dispatch path (start_rifle_setup/
    start_load_setup via extract_intent). Both must pre-fill from the
    trigger text and skip straight to whatever's still actually
    missing."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    # Fast regex path: "new...rifle" matches even though real content
    # (the name) follows in the same breath.
    monkeypatch.setattr(cli_module, "extract_setup_fields",
                         lambda text, kind, asking_about=None: {"name": "9mm PCC"})
    reply = cli.handle("now let's go to a new rifle, the 9mm PCC")
    assert "call this rifle" not in reply.lower()
    assert "scope height" in reply.lower()
    assert cli._setup.draft.get("name") == "9mm PCC"
    cli._setup = None  # reset for the next entry point, independent of this one

    # LLM-dispatch path: extract_intent routes to start_load_setup,
    # separately from extract_setup_fields doing the field pre-fill.
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: ("start_load_setup", {}))
    monkeypatch.setattr(
        cli_module, "extract_setup_fields",
        lambda text, kind, asking_about=None: {
            "bullet_weight_gr": 110, "bc": 0.3, "drag_model": "G1",
            "muzzle_velocity_fps": 1150, "powder": "Lil Gun",
            "powder_charge_gr": 24, "zero_distance_yd": 50,
        },
    )
    reply2 = cli.handle(
        "let's log a new one, 110 grain, BC point three, G1, muzzle velocity 1150, "
        "24 grains of Lil Gun, zero at 50",
    )
    assert "bullet weight" not in reply2.lower()
    assert "call this load" in reply2.lower()  # only the genuinely-missing field is asked
    assert cli._setup.draft.get("zero_distance_yd") == 50


def test_switch_rifle_with_new_load_volunteered_in_the_same_breath(monkeypatch, tmp_path):
    """Regression, found live while scoping Session Mode component 2
    (2026-09-05): "switching to the 300 blackout, first load is the 110s
    at 24 grains of Lil Gun, zero at 50" used to switch the rifle and
    silently discard every bit of load info in the same utterance --
    there was no path from a plain switch_rifle intent into a load
    setup at all. switch_rifle's tool schema now carries optional
    new_load_* fields for exactly this case, dispatched into the same
    pre-fill machinery start_rifle_setup/start_load_setup already use
    (_begin_setup_from_fields), so the switch and the new load both
    register instead of only the switch. A plain switch with no load
    info volunteered must behave exactly as before -- no regression."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI

    store = ProfileStore(tmp_path / "profiles.json")
    ar15 = Rifle(name="AR-15", scope_height_in=2.5, click_value_mrad=0.1)
    ar15.add_load(Load(name="75gr ELD", bullet_weight_gr=75, bc=0.402, drag_model="G1",
                        muzzle_velocity_fps=2787, zero_distance_yd=100))
    store.add_rifle(ar15)
    blackout = Rifle(name="300 Blackout SBR", scope_height_in=2.2, click_value_mrad=0.1)
    store.add_rifle(blackout, make_active=False)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: (
        "switch_rifle", {
            "query": "300 blackout",
            "new_load_bullet_weight_gr": 110, "new_load_powder": "Lil Gun",
            "new_load_powder_charge_gr": 24, "new_load_zero_distance_yd": 50,
        },
    ))
    reply = cli.handle(
        "switching to the 300 blackout, first load is the 110s at 24 grains of Lil Gun, zero at 50",
    )
    assert store.active_rifle_name == "300 Blackout SBR"
    assert "switched" in reply.lower()
    assert cli._setup is not None
    assert cli._setup.kind == "load"
    assert cli._setup.draft.get("bullet_weight_gr") == 110
    assert cli._setup.draft.get("powder") == "Lil Gun"
    assert cli._setup.draft.get("zero_distance_yd") == 50
    # Neither name nor BC were stated -- name is first in _LOAD_REQUIRED,
    # so that's what must still be asked for, not silently defaulted.
    assert "call this load" in reply.lower()

    # A plain switch with nothing extra volunteered must NOT start a setup.
    cli._setup = None  # reset -- otherwise handle() would route into the still-open load setup above
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: ("switch_rifle", {"query": "AR-15"}))
    plain_reply = cli.handle("switch back to the AR-15")
    assert store.active_rifle_name == "AR-15"
    assert cli._setup is None
    assert "switched" in plain_reply.lower()


def test_setup_cancel_discards_draft_without_saving(monkeypatch, tmp_path):
    """"never mind" mid-interview should walk away clean -- nothing
    written to the store, and the CLI drops back to normal command
    handling rather than staying stuck in setup mode."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {"name": "should not save"})

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)
    original_rifle_count = len(store.rifles)

    cli.handle("let's build a new load")
    cli.handle("call it something")
    reply = cli.handle("never mind, forget it")
    assert "scrapped" in reply.lower()
    assert cli._setup is None
    assert len(store.rifles) == original_rifle_count
    assert "should not save" not in store.get_active_rifle().loads


def test_voice_query_understands_natural_range_phrasing(tmp_path):
    """Regression: the parser used to only recognize the literal phrase
    "drop at X yards" -- real speech doesn't come out that precisely.
    Caught live: "set range for 400 yard and give solution" (Rick's
    actual wake-word command) returned "Didn't understand that" even
    though a working drop-at-range command exists for that same load."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    baseline = cli.handle("what's my drop at 400 yards")

    for phrasing in [
        "set range for 400 yard and give solution",
        "set raNGE FOR 400 YRD AND GIVE SOLUTION",
        "give me a solution for 400 yards",
    ]:
        reply = cli.handle(phrasing)
        assert reply == baseline, f"{phrasing!r} didn't match the drop-at-range reply"

    # Still must not hijack the other command types, which all also
    # mention "yards" -- these have to keep routing to their own handlers.
    assert "21.0gr" in cli.handle("switch to 21.0gr")
    assert "yard zero" in cli.handle("what zero minimizes my spread out to 500 yards")
    assert "Angle confirmed" in cli.handle("I'm seeing 12 clicks at 400 yards")


def test_voice_speak_rejects_empty_text():
    """The one piece of /voice/speak worth unit-testing without a live,
    billed OpenAI call: empty input is rejected before ever reaching the
    network. Full TTS behavior (real audio bytes back) was verified
    manually against the live API, not here -- this suite shouldn't
    make paid third-party calls on every run."""
    import ballistica.api as api_module
    from fastapi.testclient import TestClient

    client = TestClient(api_module.app)
    r = client.post("/voice/speak", json={"text": "   "})
    assert r.status_code == 400


def test_calibration_flow_outlier_flag_discard_and_save(tmp_path):
    """Chronograph calibration never calls the LLM (shot readings are
    just numbers), so this covers the whole flow deterministically:
    running average, outlier flagging on a wild reading, discarding the
    last shot, ending, confirming, and the final save -- including that
    it lands on update_load_velocity() (not a fresh Load) and appends
    chrono provenance to notes rather than overwriting them."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)
    original_notes = store.get_active_rifle().get_active_load().notes

    assert "Read me shots" in cli.handle("start calibration")
    assert "Average 2780" in cli.handle("2780")
    assert "Average 2788" in cli.handle("2795")
    third = cli.handle("2788")
    assert "outlier" not in third

    fourth = cli.handle("2650")
    assert "outlier" in fourth

    avg_reply = cli.handle("average")
    assert "4 shots" in avg_reply

    discard_reply = cli.handle("discard that")
    assert "Tossed 2650" in discard_reply

    summary = cli.handle("end calibration")
    assert "Save as the new velocity" in summary
    assert "2788" in summary  # average of 2780/2795/2788

    saved = cli.handle("yes")
    assert "2788" in saved
    assert cli._calibration is None

    load = store.get_active_rifle().get_active_load()
    assert load.muzzle_velocity_fps == pytest.approx((2780 + 2795 + 2788) / 3)
    assert load.notes.startswith(original_notes)
    assert "Chrono-verified: 3 shots" in load.notes


def test_calibration_confirmation_recognizes_natural_phrasing(tmp_path):
    """Same fix as setup's confirmation matching (Addendum 28), applied
    proactively to calibration's identical confirming-state check
    before it was independently hit live: "that is correct" must
    confirm the save, not fall through to "one more shot came in"
    handling."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("start calibration")
    cli.handle("2780")
    cli.handle("2795")
    summary = cli.handle("end calibration")
    assert "save as the new velocity" in summary.lower()

    saved = cli.handle("that is correct")
    assert "saved" in saved.lower()
    assert cli._calibration is None
    assert store.get_active_rifle().get_active_load().muzzle_velocity_fps == 2787.5


def test_calibration_cancel_and_reject_leave_no_trace(tmp_path):
    """"cancel" mid-string and "no" at the confirm prompt should both
    walk away clean -- the load's velocity must be untouched either
    way, and the CLI must drop back to normal command handling instead
    of staying stuck in a calibration session."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)
    original_fps = store.get_active_rifle().get_active_load().muzzle_velocity_fps

    cli.handle("start calibration")
    cli.handle("2900")
    reply = cli.handle("cancel")
    assert "cancelled" in reply.lower()
    assert cli._calibration is None
    assert store.get_active_rifle().get_active_load().muzzle_velocity_fps == original_fps

    cli.handle("start calibration")
    cli.handle("3000")
    cli.handle("end calibration")
    reply = cli.handle("no")
    assert "discarded" in reply.lower()
    assert cli._calibration is None
    assert store.get_active_rifle().get_active_load().muzzle_velocity_fps == original_fps

    # Confirms the CLI is back to normal command handling, not stuck.
    assert "yards" in cli.handle("drop at 300 yards").lower()


def test_calibration_gives_up_after_repeated_unparseable_shots(tmp_path):
    """Same Addendum 11 regression as the setup version, for calibration:
    if shot readings genuinely can't be parsed turn after turn (silence,
    noise, a garbled transcription with no number in it), the session
    must give up rather than stay open and keep re-asking forever. A
    real shot in between resets the counter."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("start calibration")
    cli.handle("2780")  # a real shot resets the counter

    first = cli.handle("uh what")
    assert "didn't catch" in first.lower()
    assert cli._calibration is not None

    second = cli.handle("static noise")
    assert "didn't catch" in second.lower()
    assert cli._calibration is not None

    gave_up = cli.handle("still nothing")
    assert "trouble understanding" in gave_up.lower()
    assert cli._calibration is None

    assert "yards" in cli.handle("drop at 300 yards").lower()


def test_calibration_end_of_string_natural_phrasing_falls_back_to_llm(monkeypatch, tmp_path):
    """Regression, found live (2026-09-05, Rick's first real-voice Session
    Mode test): after reading off ~10 shots, natural ways of signaling
    "I'm done" -- "that's ten shots", "I think that's good", "that's
    enough" -- all missed the anchored end-calibration regex and came
    back "Didn't catch a number there," identically to genuine silence/
    noise. This was the calibration flow's own missing fast-path-then-
    LLM-fallback (every other modal flow already has one). Pins that an
    utterance the regex misses but the fallback classifies as
    end_calibration reaches the same save-prompt the exact anchored
    phrase does, and that a genuinely unclear utterance still falls back
    to the ordinary retry/give-up counter rather than false-triggering
    an end."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("start calibration")
    cli.handle("2780")
    cli.handle("2795")

    monkeypatch.setattr(cli_module, "classify_calibration_turn", lambda text: "end_calibration")
    summary = cli.handle("I think that's good")
    assert "save as the new velocity" in summary.lower()
    assert cli._calibration.confirming is True

    saved = cli.handle("yes")
    assert "saved" in saved.lower()
    assert cli._calibration is None

    # A genuinely unclear utterance must NOT false-trigger an end -- falls
    # back to the same retry/give-up counter as any other unparseable turn.
    cli.handle("start calibration")
    cli.handle("2900")
    monkeypatch.setattr(cli_module, "classify_calibration_turn", lambda text: "unclear")
    reply = cli.handle("mumble mumble")
    assert "didn't catch" in reply.lower()
    assert cli._calibration is not None
    assert cli._calibration.failed_attempts == 1


def test_setup_session_dict_round_trip_preserves_all_state():
    """The multi-tenant /v2/voice/query endpoint hydrates a fresh
    BallisticaCLI's _setup from this dict every request and dehydrates
    it back after -- any field that doesn't survive the round trip
    would silently reset part of an in-progress setup interview on the
    very next turn."""
    from ballistica.cli import _SetupSession

    original = _SetupSession("rifle")
    original.draft = {"name": "Test AR", "scope_height_in": 2.5}
    original.skipped = {"barrel_length_in", "twist_rate"}
    original.confirming = True
    original.failed_attempts = 2

    restored = _SetupSession.from_dict(original.to_dict())
    assert restored.kind == "rifle"
    assert restored.draft == {"name": "Test AR", "scope_height_in": 2.5}
    assert restored.skipped == {"barrel_length_in", "twist_rate"}
    assert restored.confirming is True
    assert restored.failed_attempts == 2
    assert restored.last_activity == original.last_activity


def test_converse_reply_gets_spoken_and_remembered(monkeypatch, tmp_path):
    """Open-ended conversation (2026-09-05): when extract_intent() returns
    a "converse" result instead of a real command -- tool_choice is
    "auto", so the model can just talk -- the reply is spoken back
    directly (no _dispatch_intent tool routing) and the exchange is
    appended to _chat_history for later turns to use as context. An
    ordinary ballistics command must NOT touch _chat_history at all --
    only genuine conversation does, so terse commands don't bloat the
    context sent to the LLM on every turn."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    monkeypatch.setattr(
        cli_module, "extract_intent",
        lambda text, history=None: ("converse", {"reply": "Worth checking your manual for that one."}),
    )
    reply = cli.handle("is it safe to bump this load up")
    assert reply == "Worth checking your manual for that one."
    assert cli._chat_history == [
        {"role": "user", "content": "is it safe to bump this load up"},
        {"role": "assistant", "content": "Worth checking your manual for that one."},
    ]

    # A real command (fast regex path, no LLM call at all) must not add
    # anything to the conversational-memory buffer.
    cli.handle("switch to 21.0gr")
    assert len(cli._chat_history) == 2


def test_chat_history_passed_to_extract_intent_and_capped(monkeypatch, tmp_path):
    """Recent conversational turns are handed to extract_intent() as
    history (so a follow-up like "is that safe" can resolve what "that"
    refers to), and the buffer is capped at _CHAT_HISTORY_MAX_TURNS
    exchanges rather than growing for the whole session."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    seen_history = []

    def fake_extract_intent(text, history=None):
        seen_history.append(list(history or []))
        return ("converse", {"reply": f"reply to {text}"})

    monkeypatch.setattr(cli_module, "extract_intent", fake_extract_intent)

    cli.handle("question one")
    assert seen_history[0] == []  # nothing yet on the very first turn

    cli.handle("question two")
    assert seen_history[1] == [
        {"role": "user", "content": "question one"},
        {"role": "assistant", "content": "reply to question one"},
    ]

    # Push well past the cap and confirm the buffer stays bounded --
    # oldest exchanges evicted first, not newest. With the cap at 6
    # exchanges, after "question 19" the surviving user turns must be
    # exactly questions 14-19; anything older must be gone.
    for i in range(3, 20):
        cli.handle(f"question {i}")
    assert len(cli._chat_history) == cli_module._CHAT_HISTORY_MAX_TURNS * 2
    user_turns = [m["content"] for m in cli._chat_history if m["role"] == "user"]
    assert user_turns == [f"question {i}" for i in range(14, 20)]


def test_hydrate_dehydrate_round_trips_chat_history(tmp_path):
    """The multi-tenant /v2/voice/query endpoint hydrates a fresh
    BallisticaCLI's conversational memory from what the previous request
    persisted, same as setup/calibration/pending_delete -- without this,
    every request would start a brand new conversation with no memory of
    what was just discussed."""
    import ballistica.api as api_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)
    cli._chat_history = [
        {"role": "user", "content": "is it safe to bump this up"},
        {"role": "assistant", "content": "Worth checking your manual for that one."},
    ]

    state = api_module._dehydrate_cli(cli)
    assert state["chat_history"] == cli._chat_history

    restored = BallisticaCLI(store)
    api_module._hydrate_cli(restored, state)
    assert restored._chat_history == cli._chat_history

    # A request with no prior conversational state must not crash --
    # ordinary "brand new conversation" case (also today's setup/
    # calibration/pending_delete convention: missing key -> empty/None).
    fresh = BallisticaCLI(store)
    api_module._hydrate_cli(fresh, {})
    assert fresh._chat_history == []


def test_calibration_session_dict_round_trip_preserves_all_state():
    from ballistica.cli import _CalibrationSession

    original = _CalibrationSession("Test AR", "23.5gr H335")
    original.shots = [2750.0, 2761.0, 2758.0]
    original.confirming = True
    original.failed_attempts = 1

    restored = _CalibrationSession.from_dict(original.to_dict())
    assert restored.rifle_name == "Test AR"
    assert restored.load_name == "23.5gr H335"
    assert restored.shots == [2750.0, 2761.0, 2758.0]
    assert restored.confirming is True
    assert restored.failed_attempts == 1
    assert restored.last_activity == original.last_activity


def test_abandoned_calibration_session_expires_instead_of_swallowing_later_command(tmp_path):
    """Root-caused live: an abandoned modal session (never explicitly
    cancelled, no further turns for a long time -- e.g. a forgotten test
    call, or the shooter walking away mid-interview) used to sit open
    indefinitely and silently absorb the next, completely unrelated
    utterance as if it were an answer to a session nobody remembers is
    still there. Confirmed live: "add a new rifle" -- heard correctly,
    verbatim, by STT -- got answered as a failed shot-velocity reading
    inside a stale calibration session instead of starting a new rifle
    setup. _MAX_FAILED_ATTEMPTS doesn't catch this case at all, since it
    only counts consecutive failures *within* an active back-and-forth --
    an abandoned session with zero further turns never increments it."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile
    import ballistica.cli as cli_module

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("start calibration")
    assert cli._calibration is not None

    # Simulate the session having sat untouched well past the staleness
    # threshold, rather than actually sleeping in the test.
    cli._calibration.last_activity -= cli_module._SESSION_STALE_SECONDS + 1

    reply = cli.handle("add a new rifle")
    assert cli._calibration is None
    assert cli._setup is not None
    assert cli._setup.kind == "rifle"
    assert "call this rifle" in reply.lower()


def test_recent_calibration_session_does_not_expire_mid_conversation(tmp_path):
    """The staleness check must not punish a real, actively-in-progress
    conversation just because it's taking a while -- only genuinely
    abandoned sessions (no turns at all for the full threshold) should
    ever be cleared."""
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    cli.handle("start calibration")
    cli.handle("2780")
    assert cli._calibration is not None
    assert len(cli._calibration.shots) == 1
