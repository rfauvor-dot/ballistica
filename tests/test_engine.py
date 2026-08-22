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


def test_voice_query_conversation_state_and_error_handling():
    """/voice/query is the seam a future STT->this->TTS loop calls. It
    deliberately keeps conversation state across calls (switching load,
    setting conditions) unlike the stateless /calc/* endpoints -- pins
    that continuity, plus that "quit" and a nonsense utterance both
    come back as spoken-safe text instead of ever 500ing, and that the
    "set conditions" reply doesn't leak a raw Python object repr into
    what's meant to be read aloud by TTS (regression: it used to)."""
    from pathlib import Path

    import ballistica.api as api_module
    from ballistica.cli import bootstrap_default_profile
    from ballistica.trajectory import WindCondition
    from fastapi.testclient import TestClient

    profiles_path = Path(__file__).resolve().parent.parent / "data" / "profiles.json"
    if profiles_path.exists():
        profiles_path.unlink()
    api_module.store.rifles.clear()
    api_module.store.active_rifle_name = None
    bootstrap_default_profile(api_module.store)
    api_module.voice_cli.atmosphere = STANDARD_ATMOSPHERE
    api_module.voice_cli.wind = WindCondition()

    client = TestClient(api_module.app)

    r = client.post("/voice/query", json={"text": "switch to 21.0gr"})
    assert r.status_code == 200
    assert "21.0gr" in r.json()["reply"]

    baseline = client.post("/voice/query", json={"text": "what's my drop at 500 yards"}).json()["reply"]

    r = client.post("/voice/query", json={
        "text": "set conditions temp 90 pressure 26.5 altitude 3500 humidity 40",
    })
    reply = r.json()["reply"]
    assert "AtmosphereConditions" not in reply
    assert "90 degrees" in reply

    after = client.post("/voice/query", json={"text": "what's my drop at 500 yards"}).json()["reply"]
    assert after != baseline

    r = client.post("/voice/query", json={"text": "quit"})
    assert r.status_code == 200
    assert "session" in r.json()["reply"].lower()

    r = client.post("/voice/query", json={"text": "gibberish nonsense query"})
    assert r.status_code == 200


def test_repeat_solution_reuses_last_drop_without_recalculating():
    """"repeat windage/elevation/solution" should re-speak the last
    drop-at-range answer from memory, not recompute it -- so it still
    reflects what was actually last spoken even if conditions changed
    in between. Also pins the no-solution-yet case (fresh session,
    asked to repeat before ever getting a solution) doesn't crash."""
    from pathlib import Path

    import ballistica.api as api_module
    from ballistica.cli import bootstrap_default_profile
    from ballistica.trajectory import WindCondition
    from fastapi.testclient import TestClient

    profiles_path = Path(__file__).resolve().parent.parent / "data" / "profiles.json"
    if profiles_path.exists():
        profiles_path.unlink()
    api_module.store.rifles.clear()
    api_module.store.active_rifle_name = None
    bootstrap_default_profile(api_module.store)
    api_module.voice_cli.atmosphere = STANDARD_ATMOSPHERE
    api_module.voice_cli.wind = WindCondition()
    api_module.voice_cli._last_solution = None

    client = TestClient(api_module.app)

    r = client.post("/voice/query", json={"text": "repeat solution"})
    assert "no solution" in r.json()["reply"].lower()

    baseline = client.post("/voice/query", json={"text": "drop at 400 yards"}).json()["reply"]

    # Conditions change after the solution was spoken -- repeat should
    # still hand back the original answer, not a freshly recalculated one.
    client.post("/voice/query", json={"text": "set conditions temp 100 pressure 26.0 altitude 5000 humidity 10"})

    full = client.post("/voice/query", json={"text": "repeat solution"}).json()["reply"]
    assert full == baseline

    elevation = client.post("/voice/query", json={"text": "repeat elevation"}).json()["reply"]
    assert elevation in baseline
    assert "Windage" not in elevation

    windage = client.post("/voice/query", json={"text": "repeat the windage"}).json()["reply"]
    assert windage in baseline
    assert "Elevation" not in windage


def test_voice_query_signals_awaiting_response_during_conversation():
    """Regression: the voice frontend used to always drop back to
    wake-word-only listening after one question/answer exchange, which
    silently ate every field after the first during guided setup (Rick
    would answer the first question, get silence, and only "waking" her
    back up mid-setup would resume it -- but from the frontend's
    perspective every answer given without saying "Ballistica" again
    was never even sent). /voice/query now tells the frontend whether
    to keep listening without the wake word: true while a guided setup
    is genuinely mid-conversation, false once it's done, cancelled, or
    for an ordinary one-shot command."""
    from pathlib import Path

    import ballistica.api as api_module
    from ballistica.cli import bootstrap_default_profile
    from fastapi.testclient import TestClient

    profiles_path = Path(__file__).resolve().parent.parent / "data" / "profiles.json"
    if profiles_path.exists():
        profiles_path.unlink()
    api_module.store.rifles.clear()
    api_module.store.active_rifle_name = None
    bootstrap_default_profile(api_module.store)

    client = TestClient(api_module.app)

    r = client.post("/voice/query", json={"text": "what's my drop at 400 yards"})
    assert r.json()["awaiting_response"] is False

    r = client.post("/voice/query", json={"text": "let's set up a new load"})
    assert r.json()["awaiting_response"] is True

    r = client.post("/voice/query", json={"text": "never mind, cancel"})
    assert r.json()["awaiting_response"] is False


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
        {"name": "25gr Varget"},
        {"bullet_weight_gr": 75, "bc": 0.37, "drag_model": "G1"},
        {"muzzle_velocity_fps": 2900, "zero_distance_yd": 100},
        {"zero_distance_yd": 50},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: next(responses))

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
    again, while the other ten optional fields (barrel length, twist,
    scope info, etc.) each get asked and are passed with "skip"."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    responses = iter([
        {"name": "Creedmoor bolt gun", "caliber": "6.5 Creedmoor"},
        {"scope_height_in": 2.0},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: next(responses))

    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    cli = BallisticaCLI(store)

    assert "call this rifle" in cli.handle("set up a new rifle").lower()
    next_prompt = cli.handle("call it the Creedmoor bolt gun, caliber 6.5 Creedmoor")
    assert "scope height" in next_prompt.lower()

    after_required = cli.handle("scope height is 2 inches")
    # Caliber was already given -- shouldn't be asked again. Required
    # fields are done, so this should be the first *other* optional field,
    # not caliber and not the confirmation summary yet.
    assert "caliber" not in after_required.lower()
    assert "sound right" not in after_required.lower()

    for _ in range(9):
        reply = cli.handle("skip")
        assert "sound right" not in reply.lower()
    summary = cli.handle("skip")
    assert "sound right" in summary.lower()

    saved = cli.handle("yes")
    assert "Creedmoor bolt gun" in saved
    assert store.active_rifle_name == "Creedmoor bolt gun"
    assert store.rifles["Creedmoor bolt gun"].caliber == "6.5 Creedmoor"


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
                         lambda text, kind: {"caliber": "5.7x28mm", "name": "CMMG"})
    cli.handle("let's set up a new rifle")
    cli.handle("5.7x28mm, 11.5 inch, CMMG")
    assert cli._setup.draft["caliber"] == "5.7x28mm"

    for placeholder in ["<UNKNOWN>", "unknown", "n/a", "N/A", "null", "[not specified]"]:
        cli._setup.failed_attempts = 0  # isolate each placeholder, independent of the retry cap
        monkeypatch.setattr(cli_module, "extract_setup_fields",
                             lambda text, kind, p=placeholder: {"caliber": p})
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

    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {"name": "AR-15"})
    cli.handle("let's set up a new rifle")
    cli.handle("call it the AR-15")  # real progress -- resets the counter

    # Now every turn fails to extract anything new (simulates the LLM
    # genuinely not understanding, or repeated silence/noise).
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {})
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
        {"name": "25gr Varget"},
        {"bullet_weight_gr": 75, "bc": 0.37, "drag_model": "G1"},
        {"muzzle_velocity_fps": 2900, "zero_distance_yd": 100},
    ])
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: next(responses))
    cli.handle("let's set up a new load")
    cli.handle("call it 25gr Varget")
    cli.handle("75 grains, point three seven, G1")
    cli.handle("2900 feet per second, zeroed at 100 yards")
    for _ in range(4):
        cli.handle("skip")

    # Two "no progress" turns, then a same-key-overwrite correction --
    # the correction must reset the counter, not be swallowed by it.
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {})
    cli.handle("uh")
    cli.handle("uh")
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {"zero_distance_yd": 50})
    corrected = cli.handle("no, actually zero it at 50 yards")
    assert "50 yards" in corrected
    assert cli._setup.failed_attempts == 0

    # Two more failures shouldn't be enough to trip the cap now that
    # the counter was reset by the correction above.
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {})
    cli.handle("uh")
    still_open = cli.handle("uh")
    assert "didn't catch" in still_open.lower()
    assert cli._setup is not None


def test_setup_cancel_discards_draft_without_saving(monkeypatch, tmp_path):
    """"never mind" mid-interview should walk away clean -- nothing
    written to the store, and the CLI drops back to normal command
    handling rather than staying stuck in setup mode."""
    import ballistica.cli as cli_module
    from ballistica.cli import BallisticaCLI, bootstrap_default_profile

    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind: {"name": "should not save"})

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


def test_voice_query_understands_natural_range_phrasing():
    """Regression: the parser used to only recognize the literal phrase
    "drop at X yards" -- real speech doesn't come out that precisely.
    Caught live: "set range for 400 yard and give solution" (Rick's
    actual wake-word command) returned "Didn't understand that" even
    though a working drop-at-range command exists for that same load."""
    from pathlib import Path

    import ballistica.api as api_module
    from ballistica.cli import bootstrap_default_profile

    profiles_path = Path(__file__).resolve().parent.parent / "data" / "profiles.json"
    if profiles_path.exists():
        profiles_path.unlink()
    api_module.store.rifles.clear()
    api_module.store.active_rifle_name = None
    bootstrap_default_profile(api_module.store)

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app)

    baseline = client.post("/voice/query", json={"text": "what's my drop at 400 yards"}).json()["reply"]

    for phrasing in [
        "set range for 400 yard and give solution",
        "set raNGE FOR 400 YRD AND GIVE SOLUTION",
        "give me a solution for 400 yards",
    ]:
        r = client.post("/voice/query", json={"text": phrasing})
        assert r.status_code == 200
        assert r.json()["reply"] == baseline, f"{phrasing!r} didn't match the drop-at-range reply"

    # Still must not hijack the other command types, which all also
    # mention "yards" -- these have to keep routing to their own handlers.
    assert "21.0gr" in client.post("/voice/query", json={"text": "switch to 21.0gr"}).json()["reply"]
    assert "yard zero" in client.post(
        "/voice/query", json={"text": "what zero minimizes my spread out to 500 yards"},
    ).json()["reply"]
    assert "Angle confirmed" in client.post(
        "/voice/query", json={"text": "I'm seeing 12 clicks at 400 yards"},
    ).json()["reply"]


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
    assert "trouble hearing" in gave_up.lower()
    assert cli._calibration is None

    assert "yards" in cli.handle("drop at 300 yards").lower()
