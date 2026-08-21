"""Text-based stand-in for voice interaction.

Real speech I/O (ElevenLabs STT/TTS, matching Rick's Pearl/Lucia stack)
is not wired up here -- that needs API keys and a decision on the
runtime. This REPL exists to exercise the same query patterns the
voice interface will need to support, and to make the engine usable
today from a terminal. Responses are deliberately terse and numeric,
matching how the brief says the voice interface should behave.
"""
from __future__ import annotations

import re
import sys

from .angle import solve_incline_angle
from .atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE
from .profiles import Load, ProfileStore, Rifle
from .reporting import format_table_text, report_for_point, report_table
from .trajectory import TrajectorySolver, WindCondition
from .zero import find_minimum_spread_zero

HELP_TEXT = """\
Commands (voice-style phrasing is fine, punctuation is ignored):
  drop at <X> yards                        -- single-range query
  table [to <X> yards] [every <Y> yards]    -- full drop/windage table
  switch to <name>                         -- change active load (fuzzy match)
  switch rifle to <name>                   -- change active rifle
  what zero minimizes my spread out to <X> yards
  I'm seeing <N> clicks at <X> yards[, from <R> yards]  -- solve incline angle
  set conditions temp <T> pressure <P> altitude <A> humidity <H>
  set wind <speed> mph from <clock> oclock
  status                                    -- show active rifle/load/atmosphere
  list rifles / list loads
  help
  quit
"""


def bootstrap_default_profile(store: ProfileStore) -> None:
    """Preloads Rick's validation test case: 5.56/.223 Wylde 20in Faxon,
    77gr SMK, two H335 candidate loads, 36yd near-zero.

    BC is Sierra's own published figure for stock #9377 (77gr HPBT
    MatchKing): .362 for the 1700-3000 fps band, which covers both
    loads' muzzle velocities (cross-checked directly against Sierra's
    published ballistic coefficient table -- Sierra publishes this
    bullet as G1 only, no G7 figure). A chronograph-measured or
    Doppler-derived BC (or a proper G7 figure from Litz/Applied
    Ballistics, since G7 tends to track boat-tail match bullets more
    consistently across the transonic band) would be a meaningful
    upgrade once Rick has that data -- update_load_velocity() and the
    Load fields make that a one-line change, no need to touch anything
    else.
    """
    rifle = Rifle(
        name="AR-15 20in Faxon",
        scope_height_in=2.5,
        caliber=".223 Wylde",
        barrel_length_in=20.0,
        twist_rate="1:7",
        click_value_mrad=0.1,
    )
    rifle.add_load(Load(
        name="21.0gr H335", bullet_weight_gr=77, bc=0.362, drag_model="G1",
        muzzle_velocity_fps=2422, zero_distance_yd=36,
        bullet_type="77gr Sierra MatchKing (SMK)", powder="H335",
        powder_charge_gr=21.0,
        notes="Sierra book BC (G1, 1700-3000fps band); velocity is a book estimate pending chrono data",
    ), make_active=False)
    rifle.add_load(Load(
        name="23.5gr H335", bullet_weight_gr=77, bc=0.362, drag_model="G1",
        muzzle_velocity_fps=2766, zero_distance_yd=36,
        bullet_type="77gr Sierra MatchKing (SMK)", powder="H335",
        powder_charge_gr=23.5,
        notes="Sierra book BC (G1, 1700-3000fps band); pending pressure verification",
    ), make_active=True)
    store.add_rifle(rifle)
    store.save()


class BallisticaCLI:
    def __init__(self, store: ProfileStore) -> None:
        self.store = store
        self.atmosphere: AtmosphereConditions = STANDARD_ATMOSPHERE
        self.wind = WindCondition()

    def solver(self) -> tuple[TrajectorySolver, Rifle, Load]:
        rifle = self.store.get_active_rifle()
        load = rifle.get_active_load()
        solver = TrajectorySolver(
            muzzle_velocity_fps=load.muzzle_velocity_fps,
            bc=load.bc,
            drag_model=load.drag_model,
            scope_height_in=rifle.scope_height_in,
            atmosphere=self.atmosphere,
            wind=self.wind,
        )
        return solver, rifle, load

    def handle(self, text: str) -> str:
        t = text.strip()
        if not t:
            return ""
        low = t.lower()

        if low in ("help", "?"):
            return HELP_TEXT
        if low in ("quit", "exit"):
            raise SystemExit(0)
        if low == "status":
            return self._status()
        if low == "list rifles":
            return "\n".join(self.store.rifles.keys()) or "No rifles configured."
        if low == "list loads":
            rifle = self.store.get_active_rifle()
            return "\n".join(rifle.loads.keys()) or "No loads configured."

        m = re.search(r"drop at ([\d.]+)\s*(?:yd|yard|yards)", low)
        if m:
            return self._drop_at(float(m.group(1)))

        m = re.search(r"^table(?:\s+to\s+([\d.]+)\s*(?:yd|yard|yards))?"
                       r"(?:\s+every\s+([\d.]+)\s*(?:yd|yard|yards))?", low)
        if low.startswith("table"):
            max_range = float(m.group(1)) if m and m.group(1) else 500.0
            step = float(m.group(2)) if m and m.group(2) else 100.0
            return self._table(max_range, step)

        m = re.search(r"switch rifle to (.+)", low)
        if m:
            rifle = self.store.set_active_rifle(m.group(1).strip())
            self.store.save()
            return f"Active rifle: {rifle.name}"

        m = re.search(r"switch to (.+)", low)
        if m:
            load = self.store.set_active_load(m.group(1).strip())
            self.store.save()
            return f"Active load: {load.name} ({load.muzzle_velocity_fps:.0f} fps)"

        m = re.search(r"what zero minimizes my spread out to ([\d.]+)\s*(?:yd|yard|yards)", low)
        if m:
            return self._minimum_spread_zero(float(m.group(1)))

        m = re.search(
            r"seeing (-?[\d.]+)\s*clicks?(?:\s*(?:difference|diff))?\s*at\s*([\d.]+)\s*(?:yd|yard|yards)"
            r"(?:,?\s*from\s*([\d.]+)\s*(?:yd|yard|yards))?", low)
        if m:
            observed = float(m.group(1))
            los = float(m.group(2))
            ref = float(m.group(3)) if m.group(3) else 100.0
            return self._solve_angle(observed, los, ref)

        m = re.search(
            r"set conditions temp (-?[\d.]+)\s*pressure ([\d.]+)\s*altitude (-?[\d.]+)\s*humidity ([\d.]+)",
            low)
        if m:
            self.atmosphere = AtmosphereConditions(
                temp_f=float(m.group(1)), pressure_inhg=float(m.group(2)),
                altitude_ft=float(m.group(3)), humidity_pct=float(m.group(4)),
            )
            return (f"Conditions set: {self.atmosphere.temp_f:.0f} degrees, "
                    f"{self.atmosphere.pressure_inhg:.2f} inches mercury, "
                    f"{self.atmosphere.humidity_pct:.0f} percent humidity, "
                    f"{self.atmosphere.altitude_ft:.0f} feet.")

        m = re.search(r"set wind ([\d.]+)\s*mph from ([\d.]+)\s*o.?clock", low)
        if m:
            speed = float(m.group(1))
            clock_hours = float(m.group(2))
            self.wind = WindCondition(speed_mph=speed, clock_deg=clock_hours * 30.0)
            return f"Wind set: {speed:.0f} mph from {clock_hours:g} o'clock"

        # Fallback for natural phrasing that doesn't match "drop at X
        # yards" literally -- "set range for 400 yard and give solution",
        # "range 400 yards", "give me a solution for 400 yards" all land
        # here. A range number is by far the most common thing anyone
        # actually says, spoken or typed, so once nothing more specific
        # matched, any bare "<number> yd/yard/yards/yrd" is treated as a
        # drop-at-range request rather than forcing one exact phrasing.
        m = re.search(r"(\d+\.?\d*)\s*(?:yd|yrd|yard|yards)\b", low)
        if m:
            return self._drop_at(float(m.group(1)))

        return "Didn't understand that. Type 'help' for supported commands."

    def _status(self) -> str:
        try:
            solver, rifle, load = self.solver()
        except ValueError as exc:
            return str(exc)
        return (
            f"Rifle: {rifle.name} (scope height {rifle.scope_height_in}in, "
            f"click value {rifle.click_value_mrad} mrad)\n"
            f"Load: {load.name} -- {load.bullet_weight_gr}gr, BC {load.bc} {load.drag_model}, "
            f"{load.muzzle_velocity_fps:.0f} fps, zero {load.zero_distance_yd}yd\n"
            f"Conditions: {self.atmosphere.temp_f:.0f}F, {self.atmosphere.pressure_inhg:.2f}inHg, "
            f"{self.atmosphere.humidity_pct:.0f}% RH, {self.atmosphere.altitude_ft:.0f}ft\n"
            f"Wind: {self.wind.speed_mph:.0f} mph @ {self.wind.clock_deg / 30:g} o'clock"
        )

    def _drop_at(self, range_yd: float) -> str:
        solver, rifle, load = self.solver()
        point = solver.at_range(load.zero_distance_yd, range_yd)
        r = report_for_point(point, rifle.click_value_mrad)

        # Speak in whatever unit the rifle's own turrets/reticle actually
        # use -- not inches, not clicks, not "give three numbers and let
        # the shooter do the conversion in their head at the line." Rick's
        # feedback after the first live wake-word test: too much at once,
        # wrong units for what he actually dials.
        if rifle.reticle_unit == "MOA":
            elev_val, wind_val, unit_word = r.drop_moa, r.windage_moa, "M O A"
        else:
            elev_val, wind_val, unit_word = r.drop_mrad, r.windage_mrad, "mils"

        elev_dir = "up" if elev_val >= 0 else "down"
        wind_dir = "left" if wind_val >= 0 else "right"

        # Two full sentences, not one comma-separated run-on: the period
        # gives TTS a natural pause between elevation and windage instead
        # of both numbers running together.
        return (f"Solution, {r.range_yd:.0f} yards. "
                f"Elevation, {elev_dir} {abs(elev_val):.1f} {unit_word}. "
                f"Windage, {wind_dir} {abs(wind_val):.1f} {unit_word}.")

    def _table(self, max_range_yd: float, step_yd: float) -> str:
        solver, rifle, load = self.solver()
        points = solver.drop_table(load.zero_distance_yd, max_range_yd, step_yd)
        reports = report_table(points, rifle.click_value_mrad)
        return format_table_text(reports)

    def _minimum_spread_zero(self, max_range_yd: float) -> str:
        solver, rifle, load = self.solver()
        result = find_minimum_spread_zero(solver, max_range_yd)
        return (f"Zero solution confirmed. {result.zero_distance_yd:.0f} yards minimizes spread "
                f"out to {max_range_yd:.0f} yards. Peak rise {result.max_height_in:.1f} inches, "
                f"terminal drop {result.min_height_in:.1f} inches. "
                f"Total spread {result.spread_in:.1f} inches.")

    def _solve_angle(self, observed_clicks: float, los_yd: float, ref_yd: float) -> str:
        solver, rifle, load = self.solver()
        try:
            result = solve_incline_angle(
                solver, load.zero_distance_yd, ref_yd, los_yd,
                observed_clicks, rifle.click_value_mrad,
            )
        except ValueError as exc:
            return str(exc)
        holdover = result.corrected_holdover_clicks(solver, load.zero_distance_yd, los_yd, rifle.click_value_mrad)
        return (f"Angle confirmed, {result.angle_deg:.1f} degrees. "
                f"Shoot-to distance {result.shoot_to_distance_yd:.0f} yards. "
                f"Corrected holdover {holdover:.1f} clicks. Solution locked.")


def main() -> None:
    store = ProfileStore()
    if not store.rifles:
        bootstrap_default_profile(store)
        print("No profiles found -- loaded the AR-15 20in Faxon / 77gr SMK test case.")

    cli = BallisticaCLI(store)
    print("Ballistica. Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        try:
            reply = cli.handle(line)
        except SystemExit:
            break
        if reply:
            print(reply)


if __name__ == "__main__":
    sys.exit(main() or 0)
