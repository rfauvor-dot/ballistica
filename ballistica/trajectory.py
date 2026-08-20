"""Point-mass (3-DOF) exterior ballistics trajectory solver.

Integrates the bullet's position and velocity through time with a
4th-order Runge-Kutta method, under three forces: gravity, and drag
computed from the standard G1/G7 drag function scaled by the bullet's
ballistic coefficient and the local air density ratio.

Drag formula
------------
Retardation (deceleration opposing the bullet's velocity relative to
the air mass) is:

    a_drag = density_ratio * v_rel^2 * Cd(Mach) * K / BC

    K = STANDARD_DENSITY_LBFT3 * pi / (4 * 2 * 144)

This is the standard point-mass ballistics formula (BC = weight / (d^2
* form_factor), Cd from the G1/G7 standard projectile shape, density
scaled by the local/standard air density ratio). K's derivation: drag
force = 0.5 * rho * v^2 * Cd_actual * A; Cd_actual = form_factor *
Cd_std; sectional density SD = weight_lb / d_in^2 = BC * form_factor;
A/d_in^2 = pi/4 but d_in^2 must be converted to d_ft^2 (/144) to match
rho in lb/ft^3 (weight-density) paired with mass via F=ma <-> a=F*g/W;
the 2 in the denominator comes from the 0.5 in the drag force formula.
Independently re-derived here and cross-checked against the
open-source py-ballisticcalc project's `drag_by_mach` (constant
2.08551e-4 there vs. 2.08550e-4 derived here from the identical
inputs -- agrees to 5 significant figures).

Coordinate frame
-----------------
All internal integration happens in the "line-of-sight frame": the
sight line is the horizontal x-axis (y=0 for all x), the bore starts
below it by `scope_height` at x=0, and is canted upward by the solved
launch angle so the bullet's path crosses y=0 at the zero distance.
Reported "drop" is -y (positive = below line of sight = dial/hold up).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from .atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE
from .drag_tables import drag_coefficient

GRAVITY_FPS2 = 32.17405
STANDARD_DENSITY_LBFT3 = 0.076474
DRAG_K = STANDARD_DENSITY_LBFT3 * math.pi / (4 * 2 * 144)

FT_PER_YARD = 3.0
IN_PER_FT = 12.0
MPH_TO_FPS = 5280.0 / 3600.0


@dataclass(frozen=True)
class WindCondition:
    """Wind specified the way shooters actually give it: speed plus a
    clock direction relative to the direction of fire. 12 o'clock (0
    deg) is a headwind blowing from the target toward the shooter; 3
    o'clock (90 deg) blows from the shooter's right toward their left,
    pushing the bullet left; 6 o'clock (180 deg) is a tailwind; 9
    o'clock (270 deg) pushes the bullet right.
    """
    speed_mph: float = 0.0
    clock_deg: float = 0.0

    def vector_fps(self) -> tuple[float, float]:
        """Returns (wind_x, wind_z) in the ground/LOS frame, feet/sec.
        +x is downrange, +z is to the shooter's right."""
        speed_fps = self.speed_mph * MPH_TO_FPS
        theta = math.radians(self.clock_deg)
        wind_x = -speed_fps * math.cos(theta)
        wind_z = -speed_fps * math.sin(theta)
        return wind_x, wind_z


@dataclass(frozen=True)
class TrajectoryPoint:
    time_s: float
    range_ft: float
    drop_ft: float       # signed height in LOS frame (0 = on line of sight)
    windage_ft: float
    velocity_fps: float
    mach: float

    @property
    def range_yd(self) -> float:
        return self.range_ft / FT_PER_YARD

    @property
    def drop_in(self) -> float:
        """Positive = below line of sight (needs up-hold)."""
        return -self.drop_ft * IN_PER_FT

    @property
    def windage_in(self) -> float:
        """Positive = drifted right (needs left-hold to compensate)."""
        return self.windage_ft * IN_PER_FT


def _acceleration(
    vx: float, vy: float, vz: float,
    wind_x: float, wind_z: float,
    bc: float, drag_model: str, density_ratio: float, speed_of_sound_fps: float,
) -> tuple[float, float, float]:
    rel_vx = vx - wind_x
    rel_vy = vy
    rel_vz = vz - wind_z
    rel_speed = math.sqrt(rel_vx * rel_vx + rel_vy * rel_vy + rel_vz * rel_vz)
    if rel_speed < 1e-9:
        return 0.0, -GRAVITY_FPS2, 0.0

    mach = rel_speed / speed_of_sound_fps
    cd = drag_coefficient(drag_model, mach)
    sdf = cd * DRAG_K / bc
    drag_mag = density_ratio * rel_speed * rel_speed * sdf

    ax = -drag_mag * rel_vx / rel_speed
    ay = -drag_mag * rel_vy / rel_speed - GRAVITY_FPS2
    az = -drag_mag * rel_vz / rel_speed
    return ax, ay, az


def _rk4_step(state: list[float], dt: float, deriv) -> list[float]:
    def f(s):
        x, y, z, vx, vy, vz = s
        ax, ay, az = deriv(vx, vy, vz)
        return [vx, vy, vz, ax, ay, az]

    k1 = f(state)
    k2 = f([state[i] + dt / 2 * k1[i] for i in range(6)])
    k3 = f([state[i] + dt / 2 * k2[i] for i in range(6)])
    k4 = f([state[i] + dt * k3[i] for i in range(6)])
    return [state[i] + dt / 6 * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) for i in range(6)]


@dataclass
class TrajectorySolver:
    muzzle_velocity_fps: float
    bc: float
    drag_model: str  # "G1" or "G7"
    scope_height_in: float
    atmosphere: AtmosphereConditions = field(default_factory=lambda: STANDARD_ATMOSPHERE)
    wind: WindCondition = field(default_factory=WindCondition)
    dt: float = 0.0005

    def __post_init__(self) -> None:
        if self.drag_model not in ("G1", "G7"):
            raise ValueError("drag_model must be 'G1' or 'G7'")
        if self.bc <= 0:
            raise ValueError("bc must be positive")

    def _run(self, launch_angle_rad: float, max_range_ft: float) -> list[TrajectoryPoint]:
        density_ratio = self.atmosphere.density_ratio()
        speed_of_sound = self.atmosphere.speed_of_sound_fps()
        wind_x, wind_z = self.wind.vector_fps()
        scope_height_ft = self.scope_height_in / IN_PER_FT

        vx0 = self.muzzle_velocity_fps * math.cos(launch_angle_rad)
        vy0 = self.muzzle_velocity_fps * math.sin(launch_angle_rad)
        state = [0.0, -scope_height_ft, 0.0, vx0, vy0, 0.0]

        def deriv(vx, vy, vz):
            return _acceleration(vx, vy, vz, wind_x, wind_z, self.bc, self.drag_model,
                                  density_ratio, speed_of_sound)

        points = [TrajectoryPoint(0.0, 0.0, state[1], state[2],
                                   self.muzzle_velocity_fps, self.muzzle_velocity_fps / speed_of_sound)]

        t = 0.0
        max_time_s = 15.0
        while state[0] < max_range_ft and t < max_time_s:
            state = _rk4_step(state, self.dt, deriv)
            t += self.dt
            if state[3] <= 0:
                break
            vel = math.sqrt(state[3] ** 2 + state[4] ** 2 + state[5] ** 2)
            points.append(TrajectoryPoint(t, state[0], state[1], state[2],
                                           vel, vel / speed_of_sound))
        return points

    def solve_zero_angle(self, zero_distance_yd: float) -> float:
        """Bisects on launch angle (radians) so the trajectory crosses
        y=0 (the line of sight) at zero_distance_yd."""
        zero_ft = zero_distance_yd * FT_PER_YARD

        def height_at_zero(angle: float) -> float:
            pts = self._run(angle, zero_ft + 5.0)
            return _interp_y_at_x(pts, zero_ft)

        lo, hi = math.radians(-3.0), math.radians(3.0)
        f_lo, f_hi = height_at_zero(lo), height_at_zero(hi)
        while f_lo > 0:
            lo -= math.radians(1.0)
            f_lo = height_at_zero(lo)
        while f_hi < 0:
            hi += math.radians(1.0)
            f_hi = height_at_zero(hi)

        for _ in range(40):
            mid = (lo + hi) / 2
            f_mid = height_at_zero(mid)
            if abs(f_mid) < 1e-5:
                return mid
            if f_mid < 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def trajectory(self, zero_distance_yd: float, max_range_yd: float,
                    launch_angle_rad: float | None = None) -> list[TrajectoryPoint]:
        angle = launch_angle_rad if launch_angle_rad is not None else self.solve_zero_angle(zero_distance_yd)
        return self._run(angle, max_range_yd * FT_PER_YARD)

    def drop_table(self, zero_distance_yd: float, max_range_yd: float,
                    step_yd: float = 100.0) -> list[TrajectoryPoint]:
        pts = self.trajectory(zero_distance_yd, max_range_yd + step_yd)
        out = []
        r = 0.0
        while r <= max_range_yd + 1e-6:
            out.append(_interp_point_at_range(pts, r * FT_PER_YARD))
            r += step_yd
        return out

    def at_range(self, zero_distance_yd: float, range_yd: float) -> TrajectoryPoint:
        pts = self.trajectory(zero_distance_yd, range_yd + 10.0)
        return _interp_point_at_range(pts, range_yd * FT_PER_YARD)


def _interp_y_at_x(points: list[TrajectoryPoint], target_x_ft: float) -> float:
    return _interp_point_at_range(points, target_x_ft).drop_ft


def _interp_point_at_range(points: list[TrajectoryPoint], target_x_ft: float) -> TrajectoryPoint:
    if target_x_ft <= points[0].range_ft:
        return points[0]
    for i in range(1, len(points)):
        if points[i].range_ft >= target_x_ft:
            p0, p1 = points[i - 1], points[i]
            span = p1.range_ft - p0.range_ft
            frac = 0.0 if span <= 0 else (target_x_ft - p0.range_ft) / span
            return TrajectoryPoint(
                time_s=p0.time_s + frac * (p1.time_s - p0.time_s),
                range_ft=target_x_ft,
                drop_ft=p0.drop_ft + frac * (p1.drop_ft - p0.drop_ft),
                windage_ft=p0.windage_ft + frac * (p1.windage_ft - p0.windage_ft),
                velocity_fps=p0.velocity_fps + frac * (p1.velocity_fps - p0.velocity_fps),
                mach=p0.mach + frac * (p1.mach - p0.mach),
            )
    return points[-1]
