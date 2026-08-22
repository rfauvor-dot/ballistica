"""Text-based stand-in for voice interaction.

Real speech I/O (ElevenLabs STT/TTS, matching Rick's Pearl/Lucia stack)
is not wired up here -- that needs API keys and a decision on the
runtime. This REPL exists to exercise the same query patterns the
voice interface will need to support, and to make the engine usable
today from a terminal. Reply tone is mode-aware: live-fire/calibration
answers stay terse and numeric, setup/status/small-talk replies are
more conversational -- see the handler methods below for the split.
"""
from __future__ import annotations

from pathlib import Path
import random
import re
import sys

from dotenv import load_dotenv

# Idempotent: api.py also loads this, but cli.py needs its own copy so
# the LLM intent fallback works when this module is used standalone
# (python -m ballistica.cli), not just through the API server.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .angle import solve_incline_angle
from .atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE
from .intent import extract_intent, generate_warm_reply
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
  repeat solution / repeat elevation / repeat windage -- re-speak the last drop-at solution
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
        self._last_solution: dict | None = None

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
        # Fast, free path for the small talk that's common enough not to
        # burn an LLM call on -- everything else genuinely conversational
        # ("rough day at the range", "you're the best") falls through to
        # generate_warm_reply() via the no_match branch below.
        if low in ("hi", "hello", "hey"):
            return random.choice(["Hey, Rick.", "Hey there.", "What's up?"])
        if re.search(r"\b(thanks|thank you|good job|nice work|well done)\b", low):
            return random.choice(["Anytime.", "You got it.", "That's what I'm here for."])
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

        # "repeat windage/elevation/solution" -- re-speaks the last drop-at
        # solution from memory rather than recalculating, so it works even
        # right after a load/condition switch that hasn't been re-queried
        # yet. Checked with word boundaries so it doesn't fire on unrelated
        # phrases that happen to contain "again".
        if re.search(r"\b(repeat|again)\b", low):
            if re.search(r"\bwindage\b", low):
                return self._repeat("windage")
            if re.search(r"\belevation\b", low):
                return self._repeat("elevation")
            return self._repeat("solution")

        m = re.search(r"switch rifle to (.+)", low)
        if m:
            return self._switch_rifle(m.group(1).strip())

        m = re.search(r"switch to (.+)", low)
        if m:
            return self._switch_load(m.group(1).strip())

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
            return self._apply_conditions_update(
                temp_f=float(m.group(1)), pressure_inhg=float(m.group(2)),
                altitude_ft=float(m.group(3)), humidity_pct=float(m.group(4)),
            )

        m = re.search(r"set wind ([\d.]+)\s*mph from ([\d.]+)\s*o.?clock", low)
        if m:
            return self._set_wind(float(m.group(1)), float(m.group(2)))

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

        # Last resort: every fast, free, exact pattern above missed.
        # Rather than keep discovering and patching one rigid regex at a
        # time as new phrasings turn up, hand this off to an LLM that
        # only decides *which* command was meant and *what* the
        # parameters are -- the actual math still runs through the same
        # deterministic functions every other path above uses.
        result = extract_intent(t)
        if result is None:
            return "Didn't understand that. Type 'help' for supported commands."
        return self._dispatch_intent(*result, original_text=t)

    def _dispatch_intent(self, name: str, args: dict, original_text: str) -> str:
        # Two different failure modes need two different responses: the
        # LLM giving back malformed/missing arguments (its fault, a
        # generic "didn't understand" is honest) versus a well-formed,
        # correctly-understood request that just doesn't match anything
        # (e.g. a genuinely unknown load name) -- that one should surface
        # its real message, same as every deterministic regex path above
        # already does by not catching KeyError/ValueError at all.
        try:
            if name == "get_drop_at_range":
                range_yd = float(args["range_yd"])
            elif name == "switch_load":
                load_query = str(args["query"])
            elif name == "switch_rifle":
                rifle_query = str(args["query"])
            elif name == "get_minimum_spread_zero":
                max_range_yd = float(args["max_range_yd"])
            elif name == "solve_incline_angle":
                observed = float(args["observed_diff_clicks"])
                los = float(args["line_of_sight_distance_yd"])
                ref = float(args.get("reference_distance_yd") or 100.0)
            elif name == "set_wind":
                speed = float(args["speed_mph"])
                clock_hours = float(args["clock_hours"])
            elif name == "repeat_last_solution":
                part = str(args.get("part") or "solution")
            elif name not in ("set_conditions", "get_status", "no_match"):
                return "Didn't understand that. Type 'help' for supported commands."
        except (KeyError, ValueError, TypeError):
            return "Didn't understand that. Type 'help' for supported commands."

        if name == "get_drop_at_range":
            return self._drop_at(range_yd)
        if name == "switch_load":
            return self._switch_load(load_query)
        if name == "switch_rifle":
            return self._switch_rifle(rifle_query)
        if name == "get_minimum_spread_zero":
            return self._minimum_spread_zero(max_range_yd)
        if name == "solve_incline_angle":
            return self._solve_angle(observed, los, ref)
        if name == "set_conditions":
            return self._apply_conditions_update(
                temp_f=args.get("temp_f"), pressure_inhg=args.get("pressure_inhg"),
                altitude_ft=args.get("altitude_ft"), humidity_pct=args.get("humidity_pct"),
            )
        if name == "set_wind":
            return self._set_wind(speed, clock_hours)
        if name == "repeat_last_solution":
            return self._repeat(part if part in ("elevation", "windage") else "solution")
        if name == "no_match":
            warm = generate_warm_reply(original_text)
            return warm or "Didn't understand that. Type 'help' for supported commands."
        return self._status()  # only get_status left

    # Setup-tone helpers: switching load/rifle/wind happens at the bench,
    # between strings -- not mid-shot -- so these read as a conversational
    # confirmation rather than the clipped numeric callouts _drop_at() and
    # _solve_angle() use for in-the-moment, live-fire solutions.

    def _switch_load(self, query: str) -> str:
        load = self.store.set_active_load(query)
        self.store.save()
        return f"Alright, you're on the {load.name} now -- {load.muzzle_velocity_fps:.0f} feet per second."

    def _switch_rifle(self, query: str) -> str:
        rifle = self.store.set_active_rifle(query)
        self.store.save()
        return f"Switched you over to the {rifle.name}."

    def _set_wind(self, speed_mph: float, clock_hours: float) -> str:
        self.wind = WindCondition(speed_mph=speed_mph, clock_deg=clock_hours * 30.0)
        return f"Got it -- wind's {speed_mph:.0f} mph out of {clock_hours:g} o'clock."

    def _apply_conditions_update(
        self, temp_f: float | None = None, pressure_inhg: float | None = None,
        altitude_ft: float | None = None, humidity_pct: float | None = None,
    ) -> str:
        """Merges into the currently-set conditions rather than requiring
        every field restated -- "it's about 90 out" should work without
        also having to repeat pressure/altitude/humidity that haven't
        changed."""
        self.atmosphere = AtmosphereConditions(
            temp_f=temp_f if temp_f is not None else self.atmosphere.temp_f,
            pressure_inhg=pressure_inhg if pressure_inhg is not None else self.atmosphere.pressure_inhg,
            altitude_ft=altitude_ft if altitude_ft is not None else self.atmosphere.altitude_ft,
            humidity_pct=humidity_pct if humidity_pct is not None else self.atmosphere.humidity_pct,
        )
        return (f"Conditions updated -- {self.atmosphere.temp_f:.0f} degrees, "
                f"{self.atmosphere.pressure_inhg:.2f} inches of mercury, "
                f"{self.atmosphere.humidity_pct:.0f} percent humidity, "
                f"{self.atmosphere.altitude_ft:.0f} feet.")

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

        # Remembered so "repeat windage/elevation/solution" can re-speak
        # this without recalculating -- see _repeat().
        self._last_solution = {
            "range_yd": r.range_yd, "elev_dir": elev_dir, "elev_val": abs(elev_val),
            "wind_dir": wind_dir, "wind_val": abs(wind_val), "unit_word": unit_word,
        }

        # Two full sentences, not one comma-separated run-on: the period
        # gives TTS a natural pause between elevation and windage instead
        # of both numbers running together.
        return (f"Solution, {r.range_yd:.0f} yards. "
                f"Elevation, {elev_dir} {abs(elev_val):.1f} {unit_word}. "
                f"Windage, {wind_dir} {abs(wind_val):.1f} {unit_word}.")

    def _repeat(self, part: str) -> str:
        s = self._last_solution
        if s is None:
            return "No solution given yet -- ask for a range first."
        if part == "elevation":
            return f"Elevation, {s['elev_dir']} {s['elev_val']:.1f} {s['unit_word']}."
        if part == "windage":
            return f"Windage, {s['wind_dir']} {s['wind_val']:.1f} {s['unit_word']}."
        return (f"Solution, {s['range_yd']:.0f} yards. "
                f"Elevation, {s['elev_dir']} {s['elev_val']:.1f} {s['unit_word']}. "
                f"Windage, {s['wind_dir']} {s['wind_val']:.1f} {s['unit_word']}.")

    def _table(self, max_range_yd: float, step_yd: float) -> str:
        solver, rifle, load = self.solver()
        points = solver.drop_table(load.zero_distance_yd, max_range_yd, step_yd)
        reports = report_table(points, rifle.click_value_mrad)
        return format_table_text(reports)

    def _minimum_spread_zero(self, max_range_yd: float) -> str:
        solver, rifle, load = self.solver()
        result = find_minimum_spread_zero(solver, max_range_yd)
        return (f"Your {result.zero_distance_yd:.0f} yard zero minimizes spread out to "
                f"{max_range_yd:.0f} yards -- peak rise {result.max_height_in:.1f} inches, "
                f"terminal drop {result.min_height_in:.1f} inches, "
                f"total spread {result.spread_in:.1f} inches.")

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
