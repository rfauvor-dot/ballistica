"""Minimum-vertical-spread zero optimizer (a generalized "maximum point
blank range" solver).

Rick doesn't zero at a fixed distance like 100 yards. He picks the zero
distance that balances the trajectory's mid-range rise above line of
sight against how far it has dropped below line of sight by his max
working distance -- the classic Maximum Point Blank Range condition,
generalized to solve for the exact balance point rather than a fixed
vital-zone tolerance.

Why balance those two specific quantities and not literally "highest
point vs. lowest point across the whole window": the muzzle itself
sits `scope_height` below the line of sight (that's pure sight-over-
bore geometry, not something a longer/shorter zero changes), so a
naive whole-window min/max degenerates -- zeroing at (or near) the max
range makes the window end exactly on the line of sight, which can
make that small fixed muzzle dip look like the "low point" and falsely
reward zeroing as far out as possible. Anchoring the low side to the
drop *at* max range (rather than the minimum over the whole window)
is the standard fix and is what real MPBR tables do.

As zero distance increases, peak rise increases monotonically and drop
at max range decreases monotonically, so their difference is unimodal
with a unique crossing point -- golden-section search converges to it
without needing derivatives.
"""
from __future__ import annotations

from dataclasses import dataclass

from .trajectory import TrajectorySolver

GOLDEN_RATIO = (5 ** 0.5 - 1) / 2  # ~0.618


@dataclass(frozen=True)
class SpreadResult:
    zero_distance_yd: float
    max_height_in: float   # peak rise above LOS, anywhere in [0, max_range]
    min_height_in: float   # drop below LOS at max_range specifically
    spread_in: float


def vertical_spread(solver: TrajectorySolver, zero_distance_yd: float, max_range_yd: float) -> SpreadResult:
    points = solver.trajectory(zero_distance_yd, max_range_yd)
    drops = [p.drop_in for p in points if p.range_yd <= max_range_yd + 1e-6]
    highest = min(drops)  # most negative drop_in = furthest above LOS
    terminal = solver.at_range(zero_distance_yd, max_range_yd).drop_in
    spread = max(-highest, terminal)
    return SpreadResult(
        zero_distance_yd=zero_distance_yd,
        max_height_in=-highest,
        min_height_in=terminal,
        spread_in=spread,
    )


def find_minimum_spread_zero(
    solver: TrajectorySolver,
    max_range_yd: float,
    min_zero_yd: float = 5.0,
    max_zero_yd: float | None = None,
    tolerance_yd: float = 0.25,
) -> SpreadResult:
    """Golden-section search over candidate zero distances for the one
    that minimizes vertical spread across [0, max_range_yd]."""
    if max_zero_yd is None:
        max_zero_yd = max_range_yd

    a, b = min_zero_yd, max_zero_yd

    def spread_at(z: float) -> float:
        return vertical_spread(solver, z, max_range_yd).spread_in

    c = b - GOLDEN_RATIO * (b - a)
    d = a + GOLDEN_RATIO * (b - a)
    fc, fd = spread_at(c), spread_at(d)

    while abs(b - a) > tolerance_yd:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - GOLDEN_RATIO * (b - a)
            fc = spread_at(c)
        else:
            a, c, fc = c, d, fd
            d = a + GOLDEN_RATIO * (b - a)
            fd = spread_at(d)

    best_z = (a + b) / 2
    return vertical_spread(solver, best_z, max_range_yd)
