"""Generates reference ballistic solutions from the real Python engine,
across a spread of realistic rifle/load/distance/condition combinations,
for scripts/check_engine_parity.js to verify the JS offline-mode port
(ballistica/web/engine.js) against.

Run whenever the JS port changes, or whenever the Python engine itself
changes (trajectory.py, drag_tables.py, atmosphere.py, units.py,
reporting.py) -- the two must never silently drift apart, since the
whole point of offline mode is that it shows the same number a live
solution would for identical inputs.

Usage:
    python -m scripts.generate_engine_parity_cases
"""
from __future__ import annotations

import json
from pathlib import Path

from ballistica.atmosphere import AtmosphereConditions, pressure_at_altitude_inhg
from ballistica.reporting import report_for_point
from ballistica.trajectory import TrajectorySolver, WindCondition

_OUT_PATH = Path(__file__).resolve().parent / "engine_parity_cases.json"

# Spans realistic small-arms rifle/load combinations: short-range AR
# carbine, mid-range .308 hunting load, long-range 6.5 Creedmoor/.338
# precision loads, both drag models, a range of zero distances, target
# distances from close to past transonic, and atmosphere/wind extremes
# (hot/humid/high-altitude, cold/dry/sea-level, calm, strong crosswind,
# quartering wind) -- not just one easy "everything nominal" case.
CASES = [
    # (label, muzzle_fps, bc, drag_model, scope_height_in, click_value_mrad,
    #  zero_yd, range_yd, temp_f, pressure_inhg, humidity_pct, altitude_ft,
    #  wind_mph, wind_clock)
    ("AR15_223_short", 2900, 0.245, "G1", 2.5, 0.1, 100, 300, 70, 29.92, 40, 500, 5, 3),
    ("AR15_223_long", 2900, 0.245, "G1", 2.5, 0.1, 100, 600, 70, 29.92, 40, 500, 10, 9),
    ("308_hunting_mid", 2650, 0.475, "G7", 1.8, 0.25, 200, 500, 55, 29.5, 60, 2000, 8, 4.5),
    ("6.5CM_precision_long", 2750, 0.315, "G7", 2.0, 0.1, 100, 800, 45, 26.0, 20, 6000, 12, 6),
    ("6.5CM_precision_verylong", 2750, 0.315, "G7", 2.0, 0.1, 100, 1200, 45, 26.0, 20, 6000, 15, 7.5),
    ("338LM_extreme", 2950, 0.768, "G7", 2.25, 0.1, 200, 1500, 30, 24.5, 10, 8000, 20, 10.5),
    ("hot_humid_sealevel", 2800, 0.400, "G1", 2.0, 0.2, 100, 400, 105, 30.1, 90, 0, 0, 0),
    ("cold_dry_highalt", 2800, 0.400, "G1", 2.0, 0.2, 100, 400, -10, 22.0, 5, 9000, 0, 0),
    ("calm_zero_wind", 2600, 0.350, "G1", 1.5, 0.25, 100, 200, 59, 29.92, 0, 0, 0, 0),
    ("close_range_50yd", 2600, 0.350, "G1", 1.5, 0.25, 100, 50, 59, 29.92, 0, 0, 0, 0),
    # pressure_inhg=None exercises the "no station reading -- estimate
    # from altitude" fallback (AtmosphereIn.to_conditions() in api.py),
    # which the JS port has to replicate exactly, not just the common
    # explicit-pressure path every other case above uses.
    ("no_station_pressure_highalt", 2750, 0.315, "G7", 2.0, 0.1, 100, 700, 50, None, 30, 7500, 6, 3),
]


def main() -> None:
    results = []
    for (label, mv, bc, drag_model, scope_h, click_val, zero_yd, range_yd,
         temp_f, pressure_inhg, humidity_pct, altitude_ft, wind_mph, wind_clock) in CASES:
        resolved_pressure = (
            pressure_at_altitude_inhg(altitude_ft) if pressure_inhg is None else pressure_inhg
        )
        atmosphere = AtmosphereConditions(
            temp_f=temp_f, pressure_inhg=resolved_pressure,
            humidity_pct=humidity_pct, altitude_ft=altitude_ft,
        )
        wind = WindCondition(speed_mph=wind_mph, clock_deg=wind_clock)
        solver = TrajectorySolver(
            muzzle_velocity_fps=mv, bc=bc, drag_model=drag_model,
            scope_height_in=scope_h, atmosphere=atmosphere, wind=wind,
        )
        point = solver.at_range(zero_yd, range_yd)
        report = report_for_point(point, click_val)

        results.append({
            "label": label,
            "input": {
                "rifle": {"scope_height_in": scope_h, "click_value_mrad": click_val},
                "load": {
                    "muzzle_velocity_fps": mv, "bc": bc, "drag_model": drag_model,
                    "zero_distance_yd": zero_yd,
                },
                "rangeYd": range_yd,
                "atmosphere": {
                    "temp_f": temp_f, "pressure_inhg": pressure_inhg,
                    "humidity_pct": humidity_pct, "altitude_ft": altitude_ft,
                },
                "wind": {"speed_mph": wind_mph, "clock_deg": wind_clock},
            },
            "expected": {
                "rangeYd": report.range_yd, "dropIn": report.drop_in,
                "dropMoa": report.drop_moa, "dropMrad": report.drop_mrad,
                "dropClicks": report.drop_clicks, "windageIn": report.windage_in,
                "windageMoa": report.windage_moa, "windageMrad": report.windage_mrad,
                "windageClicks": report.windage_clicks, "velocityFps": report.velocity_fps,
                "mach": report.mach, "timeS": report.time_s,
            },
        })

    _OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} cases to {_OUT_PATH}")


if __name__ == "__main__":
    main()
