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
