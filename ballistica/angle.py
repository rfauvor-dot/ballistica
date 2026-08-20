"""Back-calculates incline angle from a field observation, for shooters
without an angle cosine indicator or slope-rangefinder.

The technique (rifleman's rule, run in reverse):

1. The engine already knows the level-ground drop, in scope clicks,
   between a close reference distance (e.g. 100 yd) and any farther
   distance -- that's just the normal drop table.
2. In the field, the shooter dials/holds from the reference distance
   up to an inclined target at a known line-of-sight distance and
   counts how many clicks of subtension it actually took.
3. If the incline weren't there, that click count would match the
   level-ground table. The difference is the incline eating into (or
   adding to) the effective drop.
4. Rifleman's rule says effective drop depends on the *horizontal*
   distance (line_of_sight * cos(angle)), not the line-of-sight
   distance itself. So: find the "shoot-to" distance whose *level*
   drop matches what was actually observed, then
   angle = arccos(shoot_to_distance / line_of_sight_distance).
5. That angle can then be applied forward to any future shot on the
   same incline: corrected holdover = level-ground drop at
   (new_line_of_sight_distance * cos(angle)).
"""
from __future__ import annotations

from dataclasses import dataclass

from .trajectory import TrajectorySolver
from .units import inches_to_mrad, mrad_to_clicks


def _drop_clicks_at(solver: TrajectorySolver, zero_distance_yd: float,
                     range_yd: float, click_value_mrad: float) -> float:
    if range_yd <= 0:
        return 0.0
    point = solver.at_range(zero_distance_yd, range_yd)
    return mrad_to_clicks(inches_to_mrad(point.drop_in, range_yd), click_value_mrad)


@dataclass(frozen=True)
class InclineResult:
    angle_deg: float
    shoot_to_distance_yd: float
    line_of_sight_distance_yd: float
    level_ground_diff_clicks: float
    observed_diff_clicks: float

    def corrected_holdover_clicks(self, solver: TrajectorySolver, zero_distance_yd: float,
                                   target_los_distance_yd: float, click_value_mrad: float) -> float:
        """Rifleman's rule applied forward: the level-ground drop at the
        cosine-shortened equivalent distance is the actual required
        holdover for a target at this line-of-sight distance on the
        same incline."""
        import math
        shoot_to = target_los_distance_yd * math.cos(math.radians(self.angle_deg))
        return _drop_clicks_at(solver, zero_distance_yd, shoot_to, click_value_mrad)


def solve_incline_angle(
    solver: TrajectorySolver,
    zero_distance_yd: float,
    reference_distance_yd: float,
    line_of_sight_distance_yd: float,
    observed_diff_clicks: float,
    click_value_mrad: float,
) -> InclineResult:
    if line_of_sight_distance_yd <= reference_distance_yd:
        raise ValueError("line_of_sight_distance_yd must be farther than reference_distance_yd")

    ref_clicks = _drop_clicks_at(solver, zero_distance_yd, reference_distance_yd, click_value_mrad)
    los_clicks = _drop_clicks_at(solver, zero_distance_yd, line_of_sight_distance_yd, click_value_mrad)
    level_ground_diff = los_clicks - ref_clicks

    target_absolute_clicks = ref_clicks + observed_diff_clicks

    lo, hi = reference_distance_yd, line_of_sight_distance_yd
    f_lo = _drop_clicks_at(solver, zero_distance_yd, lo, click_value_mrad) - target_absolute_clicks
    f_hi = _drop_clicks_at(solver, zero_distance_yd, hi, click_value_mrad) - target_absolute_clicks
    if f_lo > 0 or f_hi < 0:
        raise ValueError(
            "Observed click difference is outside what's physically reachable between "
            f"{reference_distance_yd:.0f} and {line_of_sight_distance_yd:.0f} yd on a level "
            "range for this load -- check the observed count or the reference/LOS distances."
        )

    for _ in range(40):
        mid = (lo + hi) / 2
        f_mid = _drop_clicks_at(solver, zero_distance_yd, mid, click_value_mrad) - target_absolute_clicks
        if abs(f_mid) < 1e-4:
            break
        if f_mid < 0:
            lo = mid
        else:
            hi = mid
    shoot_to_distance_yd = (lo + hi) / 2

    import math
    cos_angle = shoot_to_distance_yd / line_of_sight_distance_yd
    cos_angle = max(-1.0, min(1.0, cos_angle))
    angle_deg = math.degrees(math.acos(cos_angle))

    return InclineResult(
        angle_deg=angle_deg,
        shoot_to_distance_yd=shoot_to_distance_yd,
        line_of_sight_distance_yd=line_of_sight_distance_yd,
        level_ground_diff_clicks=level_ground_diff,
        observed_diff_clicks=observed_diff_clicks,
    )
