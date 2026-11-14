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

# Steepest launch angle the zero solver will consider. Real zeros need a
# degree or two; anything that can't be zeroed within this can't be zeroed.
_MAX_ZERO_ANGLE_DEG = 15.0

# Solved zero angles, keyed on everything the angle depends on. Shared by
# every solver in the process; emptied when full rather than tracking
# recency -- refilling costs one zero solve per entry.
_ZERO_ANGLE_CACHE: dict[tuple, float] = {}
_ZERO_ANGLE_CACHE_MAX = 2000


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

    def _steps(self, launch_angle_rad: float, max_range_ft: float):
        """The integration itself: yields (t, x, y, z, speed) after each
        RK4 step until the bullet passes max_range_ft, stops moving
        downrange, or the flight-time cap runs out. Nothing is yielded for
        the muzzle (t=0); callers build that point themselves.

        The RK4 step is written out longhand (2026-11-14). It is the same
        arithmetic, in the same order, as the generic list-based step it
        replaced (and as engine.js's rk4Step) -- results are bit-for-bit
        identical -- without building a dozen short lists per step. Position doesn't feed the
        acceleration, so only the velocities are advanced between stages."""
        density_ratio = self.atmosphere.density_ratio()
        speed_of_sound = self.atmosphere.speed_of_sound_fps()
        wind_x, wind_z = self.wind.vector_fps()
        bc, drag_model, dt = self.bc, self.drag_model, self.dt
        half, sixth = dt / 2, dt / 6
        accel, sqrt = _acceleration, math.sqrt

        x, y, z = 0.0, -(self.scope_height_in / IN_PER_FT), 0.0
        vx = self.muzzle_velocity_fps * math.cos(launch_angle_rad)
        vy = self.muzzle_velocity_fps * math.sin(launch_angle_rad)
        vz = 0.0

        t = 0.0
        max_time_s = 15.0
        while x < max_range_ft and t < max_time_s:
            ax1, ay1, az1 = accel(vx, vy, vz, wind_x, wind_z, bc, drag_model, density_ratio, speed_of_sound)
            vx2, vy2, vz2 = vx + half * ax1, vy + half * ay1, vz + half * az1
            ax2, ay2, az2 = accel(vx2, vy2, vz2, wind_x, wind_z, bc, drag_model, density_ratio, speed_of_sound)
            vx3, vy3, vz3 = vx + half * ax2, vy + half * ay2, vz + half * az2
            ax3, ay3, az3 = accel(vx3, vy3, vz3, wind_x, wind_z, bc, drag_model, density_ratio, speed_of_sound)
            vx4, vy4, vz4 = vx + dt * ax3, vy + dt * ay3, vz + dt * az3
            ax4, ay4, az4 = accel(vx4, vy4, vz4, wind_x, wind_z, bc, drag_model, density_ratio, speed_of_sound)

            x = x + sixth * (vx + 2 * vx2 + 2 * vx3 + vx4)
            y = y + sixth * (vy + 2 * vy2 + 2 * vy3 + vy4)
            z = z + sixth * (vz + 2 * vz2 + 2 * vz3 + vz4)
            vx = vx + sixth * (ax1 + 2 * ax2 + 2 * ax3 + ax4)
            vy = vy + sixth * (ay1 + 2 * ay2 + 2 * ay3 + ay4)
            vz = vz + sixth * (az1 + 2 * az2 + 2 * az3 + az4)
            t += dt
            if vx <= 0:
                break
            yield t, x, y, z, sqrt(vx ** 2 + vy ** 2 + vz ** 2)

    def _muzzle_point(self) -> TrajectoryPoint:
        return TrajectoryPoint(
            0.0, 0.0, -(self.scope_height_in / IN_PER_FT), 0.0, self.muzzle_velocity_fps,
            self.muzzle_velocity_fps / self.atmosphere.speed_of_sound_fps(),
        )

    def _run(self, launch_angle_rad: float, max_range_ft: float) -> list[TrajectoryPoint]:
        """Every point of the trajectory out to max_range_ft."""
        speed_of_sound = self.atmosphere.speed_of_sound_fps()
        points = [self._muzzle_point()]
        for t, x, y, z, vel in self._steps(launch_angle_rad, max_range_ft):
            points.append(TrajectoryPoint(t, x, y, z, vel, vel / speed_of_sound))
        return points

    def _point_at(self, launch_angle_rad: float, target_x_ft: float) -> tuple[TrajectoryPoint | None, float]:
        """The trajectory point at one downrange distance, without keeping
        the thousands of points before it or integrating past it. Returns
        (point, range reached in ft); point is None if the bullet never got
        to target_x_ft. Interpolates between the same two steps
        _interp_point_at_range() would pick from a full _run(), so the
        result is identical."""
        prev = self._muzzle_point()
        if target_x_ft <= prev.range_ft:
            return prev, prev.range_ft
        speed_of_sound = self.atmosphere.speed_of_sound_fps()
        last = (prev.time_s, prev.range_ft, prev.drop_ft, prev.windage_ft, prev.velocity_fps)
        for step in self._steps(launch_angle_rad, target_x_ft):
            if step[1] >= target_x_ft:
                p0 = TrajectoryPoint(*last, last[4] / speed_of_sound) if last[0] else prev
                p1 = TrajectoryPoint(*step, step[4] / speed_of_sound)
                return _interp_point_at_range([p0, p1], target_x_ft), step[1]
            last = step
        return None, last[1]

    def solve_zero_angle(self, zero_distance_yd: float) -> float:
        """Bisects on launch angle (radians) so the trajectory crosses
        y=0 (the line of sight) at zero_distance_yd."""
        if not zero_distance_yd > 0:
            raise ValueError("Zero distance must be more than 0 yards.")
        zero_ft = zero_distance_yd * FT_PER_YARD
        unreachable = ValueError(
            f"A {zero_distance_yd:g} yard zero is beyond this load's reach -- "
            f"check the zero distance and muzzle velocity."
        )

        # The zero angle depends only on the load, the scope height and the
        # conditions, and finding it is ~40 short trajectories. Asking for
        # several ranges under the same conditions -- the normal case --
        # used to redo that search for every one.
        cache_key = (self.muzzle_velocity_fps, self.bc, self.drag_model, self.scope_height_in,
                     self.atmosphere, self.wind, self.dt, zero_distance_yd)
        cached = _ZERO_ANGLE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        def height_at_zero(angle: float) -> float:
            point, _ = self._point_at(angle, zero_ft)
            # A bullet that never got to the zero distance (it stopped, or
            # the flight-time cap ran out) counts as falling short.
            return -math.inf if point is None else point.drop_ft

        def remember(angle: float) -> float:
            if len(_ZERO_ANGLE_CACHE) >= _ZERO_ANGLE_CACHE_MAX:
                _ZERO_ANGLE_CACHE.clear()
            _ZERO_ANGLE_CACHE[cache_key] = angle
            return angle

        # Both bracket searches are bounded. They used to widen forever: a
        # zero the bullet can't reach (found 2026-11-14: 1050 fps, BC 0.12,
        # zero 2000 yd) is below the line of sight at every angle, so the
        # upper search never ended and the request hung a server thread.
        lo, hi = math.radians(-3.0), math.radians(3.0)
        limit = math.radians(_MAX_ZERO_ANGLE_DEG)
        f_lo, f_hi = height_at_zero(lo), height_at_zero(hi)
        while f_lo > 0:
            lo -= math.radians(1.0)
            if lo < -limit:
                raise unreachable
            f_lo = height_at_zero(lo)
        if f_hi < 0:
            # One run at the steepest angle allowed settles "can it get
            # there at all" before stepping up a degree at a time.
            if height_at_zero(limit) < 0:
                raise unreachable
            while f_hi < 0:
                hi = min(hi + math.radians(1.0), limit)
                f_hi = height_at_zero(hi)

        for _ in range(40):
            mid = (lo + hi) / 2
            f_mid = height_at_zero(mid)
            if abs(f_mid) < 1e-5:
                return remember(mid)
            if f_mid < 0:
                lo = mid
            else:
                hi = mid
        return remember((lo + hi) / 2)

    def trajectory(self, zero_distance_yd: float, max_range_yd: float,
                    launch_angle_rad: float | None = None) -> list[TrajectoryPoint]:
        angle = launch_angle_rad if launch_angle_rad is not None else self.solve_zero_angle(zero_distance_yd)
        return self._run(angle, max_range_yd * FT_PER_YARD)

    def drop_table(self, zero_distance_yd: float, max_range_yd: float,
                    step_yd: float = 100.0) -> list[TrajectoryPoint]:
        """Rows past where the bullet can be tracked are left out rather
        than filled in with the last tracked point (see at_range)."""
        pts = self.trajectory(zero_distance_yd, max_range_yd + step_yd)
        out = []
        r = 0.0
        while r <= max_range_yd + 1e-6 and r * FT_PER_YARD <= pts[-1].range_ft:
            out.append(_interp_point_at_range(pts, r * FT_PER_YARD))
            r += step_yd
        return out

    def at_range(self, zero_distance_yd: float, range_yd: float) -> TrajectoryPoint:
        """Raises ValueError for a range the bullet is never tracked to (it
        stopped, or the 15 second flight cap ran out). This used to hand
        back the last tracked point instead, so a request for 5000 yards
        was answered with a real-looking solution for about 3500."""
        point, reached_ft = self._point_at(self.solve_zero_angle(zero_distance_yd), range_yd * FT_PER_YARD)
        if point is None:
            raise ValueError(
                f"{range_yd:g} yards is past where this load can be solved -- "
                f"it only carries to about {reached_ft / FT_PER_YARD:.0f} yards."
            )
        return point


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
