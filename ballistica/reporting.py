"""Formats raw trajectory points into the drop/windage tables users
actually read: inches, MOA, MRAD, and scope clicks at a stored click
value."""
from __future__ import annotations

from dataclasses import dataclass

from .trajectory import TrajectoryPoint
from .units import inches_to_moa, inches_to_mrad, mrad_to_clicks


@dataclass(frozen=True)
class RangeReport:
    range_yd: float
    drop_in: float
    drop_moa: float
    drop_mrad: float
    drop_clicks: float
    windage_in: float
    windage_moa: float
    windage_mrad: float
    windage_clicks: float
    velocity_fps: float
    mach: float
    time_s: float


def report_for_point(point: TrajectoryPoint, click_value_mrad: float) -> RangeReport:
    r = point.range_yd
    drop_mrad = inches_to_mrad(point.drop_in, r)
    windage_mrad = inches_to_mrad(point.windage_in, r)
    return RangeReport(
        range_yd=r,
        drop_in=point.drop_in,
        drop_moa=inches_to_moa(point.drop_in, r),
        drop_mrad=drop_mrad,
        drop_clicks=mrad_to_clicks(drop_mrad, click_value_mrad) if r > 0 else 0.0,
        windage_in=point.windage_in,
        windage_moa=inches_to_moa(point.windage_in, r),
        windage_mrad=windage_mrad,
        windage_clicks=mrad_to_clicks(windage_mrad, click_value_mrad) if r > 0 else 0.0,
        velocity_fps=point.velocity_fps,
        mach=point.mach,
        time_s=point.time_s,
    )


def report_table(points: list[TrajectoryPoint], click_value_mrad: float) -> list[RangeReport]:
    return [report_for_point(p, click_value_mrad) for p in points]


def format_table_text(reports: list[RangeReport]) -> str:
    lines = [
        f"{'yd':>5} {'drop(in)':>9} {'drop(mrad)':>11} {'drop(MOA)':>10} {'clicks':>7} "
        f"{'wind(in)':>9} {'wind(clk)':>10} {'fps':>6}"
    ]
    for r in reports:
        lines.append(
            f"{r.range_yd:5.0f} {r.drop_in:9.2f} {r.drop_mrad:11.2f} {r.drop_moa:10.2f} "
            f"{r.drop_clicks:7.1f} {r.windage_in:9.2f} {r.windage_clicks:10.1f} {r.velocity_fps:6.0f}"
        )
    return "\n".join(lines)
