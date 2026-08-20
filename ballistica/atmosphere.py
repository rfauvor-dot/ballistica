"""Atmospheric model: converts field conditions into air density and
speed of sound, which is where temperature/altitude/humidity/pressure
actually feed into the drag calculation.

Physics, not tuning:

- Air density from the ideal gas law, corrected for humidity via the
  standard "virtual temperature" method (moist air is *less* dense
  than dry air at the same pressure/temperature, because water vapor
  molecules are lighter than N2/O2).
- Saturation vapor pressure from the Arden Buck equation.
- Speed of sound from actual local temperature (humidity-corrected),
  since Mach number for drag lookup depends on the shooter's local
  conditions, not the standard reference atmosphere.

G1/G7 ballistic coefficients as published by Sierra/Hornady/Berger/
Litz and used by JBM Ballistics are referenced against the ICAO/US
standard atmosphere at sea level: 59 degF, 29.92 inHg, 0% RH (dry
air). That is the reference DENSITY_RATIO must be computed against
(cross-checked against the open-source py-ballisticcalc project's
`cStandardDensity = 0.076474 lb/ft^3`, which is exactly ICAO sea-level
density -- not the older military "Army Standard Metro" convention,
which uses different figures and is not what commercial BCs use).
"""
from __future__ import annotations

from dataclasses import dataclass
import math

GAMMA_AIR = 1.4
R_DRY = 287.058       # J/(kg*K), specific gas constant, dry air
R_VAPOR = 461.495      # J/(kg*K), specific gas constant, water vapor
EPSILON = R_DRY / R_VAPOR  # ~0.622

SLUG_PER_FT3_TO_KG_PER_M3 = 515.379
MPS_TO_FPS = 3.280839895
INHG_TO_PA = 3386.389

STANDARD_TEMP_F = 59.0
STANDARD_PRESSURE_INHG = 29.92
STANDARD_HUMIDITY_PCT = 0.0
STANDARD_ALTITUDE_FT = 0.0


def f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def f_to_kelvin(temp_f: float) -> float:
    return f_to_c(temp_f) + 273.15


def saturation_vapor_pressure_pa(temp_c: float) -> float:
    """Arden Buck equation, water over liquid. Returns Pa."""
    hpa = 6.1121 * math.exp((18.678 - temp_c / 234.5) * (temp_c / (257.14 + temp_c)))
    return hpa * 100.0


def pressure_at_altitude_inhg(altitude_ft: float, sea_level_inhg: float = 29.92) -> float:
    """Standard barometric formula fallback when no direct station
    pressure reading is available -- e.g. a field estimate from a
    known altitude alone. Prefer a measured station pressure when one
    is available; this is an approximation.
    """
    return sea_level_inhg * (1.0 - 6.8756e-6 * altitude_ft) ** 5.2559


@dataclass(frozen=True)
class AtmosphereConditions:
    temp_f: float
    pressure_inhg: float
    humidity_pct: float
    altitude_ft: float = 0.0

    def air_density_slug_ft3(self) -> float:
        temp_c = f_to_c(self.temp_f)
        temp_k = temp_c + 273.15
        total_pressure_pa = self.pressure_inhg * INHG_TO_PA
        vapor_pressure_pa = (self.humidity_pct / 100.0) * saturation_vapor_pressure_pa(temp_c)
        vapor_pressure_pa = min(vapor_pressure_pa, total_pressure_pa)

        virtual_temp_k = temp_k / (1.0 - (vapor_pressure_pa / total_pressure_pa) * (1.0 - EPSILON))
        rho_kg_m3 = total_pressure_pa / (R_DRY * virtual_temp_k)
        return rho_kg_m3 / SLUG_PER_FT3_TO_KG_PER_M3

    def speed_of_sound_fps(self) -> float:
        temp_c = f_to_c(self.temp_f)
        temp_k = temp_c + 273.15
        total_pressure_pa = self.pressure_inhg * INHG_TO_PA
        vapor_pressure_pa = (self.humidity_pct / 100.0) * saturation_vapor_pressure_pa(temp_c)
        vapor_pressure_pa = min(vapor_pressure_pa, total_pressure_pa)
        virtual_temp_k = temp_k / (1.0 - (vapor_pressure_pa / total_pressure_pa) * (1.0 - EPSILON))

        c_mps = math.sqrt(GAMMA_AIR * R_DRY * virtual_temp_k)
        return c_mps * MPS_TO_FPS

    def density_ratio(self) -> float:
        """Ratio of this atmosphere's air density to the standard
        reference atmosphere G1/G7 BCs are calibrated against."""
        return self.air_density_slug_ft3() / STANDARD_ATMOSPHERE.air_density_slug_ft3()

    @classmethod
    def from_altitude_estimate(
        cls, temp_f: float, altitude_ft: float, humidity_pct: float = 50.0,
        sea_level_pressure_inhg: float = 29.92,
    ) -> "AtmosphereConditions":
        """Build conditions from altitude when no station-pressure
        reading is available (e.g. no Kestrel in the field)."""
        pressure = pressure_at_altitude_inhg(altitude_ft, sea_level_pressure_inhg)
        return cls(temp_f=temp_f, pressure_inhg=pressure, humidity_pct=humidity_pct,
                    altitude_ft=altitude_ft)


STANDARD_ATMOSPHERE = AtmosphereConditions(
    temp_f=STANDARD_TEMP_F,
    pressure_inhg=STANDARD_PRESSURE_INHG,
    humidity_pct=STANDARD_HUMIDITY_PCT,
    altitude_ft=STANDARD_ALTITUDE_FT,
)
