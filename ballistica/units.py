"""Unit conversion constants and helpers.

All internal physics runs in imperial engineering units (feet, seconds,
pounds, slugs) since ballistic coefficients are calibrated against a
standard atmosphere expressed in those units. This module centralizes
the conversions to/from the units users actually type (yards, inches,
fps, MOA/MRAD).
"""
from __future__ import annotations

import math

FT_PER_YARD = 3.0
IN_PER_FT = 12.0
FT_PER_IN = 1.0 / IN_PER_FT

GRAINS_PER_LB = 7000.0

# 1 minute of angle subtends this many inches at 100 yards (exact, via
# the true angular definition: 100yd * tan(1/60 deg) * 12 in/ft).
MOA_INCHES_PER_100YD = 100.0 * 3.0 * 12.0 * math.tan(math.radians(1.0 / 60.0))
# 1 milliradian subtends exactly 3.6 inches at 100 yards (0.001 * 3600).
MRAD_INCHES_PER_100YD = 3.6


def yards_to_feet(yards: float) -> float:
    return yards * FT_PER_YARD


def feet_to_yards(feet: float) -> float:
    return feet / FT_PER_YARD


def inches_to_feet(inches: float) -> float:
    return inches * FT_PER_IN


def feet_to_inches(feet: float) -> float:
    return feet * IN_PER_FT


def grains_to_lb(grains: float) -> float:
    return grains / GRAINS_PER_LB


def inches_to_moa(inches: float, range_yards: float) -> float:
    """Convert a linear deflection in inches at a given range to MOA."""
    if range_yards <= 0:
        return 0.0
    return inches / (MOA_INCHES_PER_100YD * range_yards / 100.0)


def inches_to_mrad(inches: float, range_yards: float) -> float:
    """Convert a linear deflection in inches at a given range to mrad."""
    if range_yards <= 0:
        return 0.0
    return inches / (MRAD_INCHES_PER_100YD * range_yards / 100.0)


def moa_to_inches(moa: float, range_yards: float) -> float:
    return moa * MOA_INCHES_PER_100YD * range_yards / 100.0


def mrad_to_inches(mrad: float, range_yards: float) -> float:
    return mrad * MRAD_INCHES_PER_100YD * range_yards / 100.0


def clicks_to_mrad(clicks: float, click_value_mrad: float) -> float:
    return clicks * click_value_mrad


def mrad_to_clicks(mrad: float, click_value_mrad: float) -> float:
    if click_value_mrad <= 0:
        raise ValueError("click_value_mrad must be positive")
    return mrad / click_value_mrad
