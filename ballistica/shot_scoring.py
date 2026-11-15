"""Scoring a shot-detection run against a hand-measured table of the shots
that were really fired (2026-11-14). Used by tools/shot_benchmark.py and the
tests, so "did that change make detection better or worse" is a set of
numbers rather than an impression.

Every detection ends up as exactly one of:
  - matched    the right hole, near the right time
  - duplicate  a hole that was already there, called again
  - false call nothing was there at all
and every real shot is either matched or missed. A matched shot is also
"late" if it was first seen well after it was fired.

Coordinates everywhere: inches from the crosshair, x right, y up.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field

# A detection matches a real shot if it is within this distance of it...
MATCH_DISTANCE_IN = 0.4
# ...and was first seen no earlier than this before / no later than this after
# the time in the table. The table's own times are only good to a couple of
# seconds, and a hole can legitimately be noticed late (see LATE_AFTER_S).
MATCH_EARLY_S, MATCH_LATE_S = 6.0, 30.0
# A matched shot first seen more than this long after it was fired is "late".
LATE_AFTER_S = 5.0


@dataclass
class TrueShot:
    sheet: str
    t: float
    x_in: float
    y_in: float


@dataclass
class Score:
    true_shots: int = 0
    matched: int = 0
    missed: int = 0
    late: int = 0
    duplicates: int = 0
    false_calls: int = 0
    mean_position_error_in: float | None = None
    max_position_error_in: float | None = None
    median_call_delay_s: float | None = None     # hole first seen -> shot called
    rows: list[dict] = field(default_factory=list, repr=False)   # per true shot
    extras: list[dict] = field(default_factory=list, repr=False)  # unmatched detections, classified

    def headline(self) -> dict:
        return {k: getattr(self, k) for k in (
            "true_shots", "matched", "missed", "late", "duplicates", "false_calls",
            "mean_position_error_in", "max_position_error_in", "median_call_delay_s")}


def load_truth(csv_path: str) -> list[TrueShot]:
    """Reads the hand-measured table. Columns: sheet, video_seconds,
    x_in_right_of_crosshair, y_in_above_crosshair (others are ignored). Rows
    whose sheet is blank or "note" are comments."""
    shots = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("sheet") and row["sheet"] != "note" and row.get("video_seconds"):
                shots.append(TrueShot(row["sheet"], float(row["video_seconds"]),
                                      float(row["x_in_right_of_crosshair"]), float(row["y_in_above_crosshair"])))
    return shots


def score(detections: list[dict], truth: list[TrueShot]) -> Score:
    """detections: dicts with t_first, t_call, x, y (the summary.json shot
    records). Each true shot takes the closest unused detection inside the
    time window; what is left over is a duplicate or a false call."""
    result = Score(true_shots=len(truth))
    used: set[int] = set()
    errors, delays = [], []
    for true in truth:
        best = None
        for i, det in enumerate(detections):
            if i in used:
                continue
            dt = det["t_first"] - true.t
            dp = math.dist((det["x"], det["y"]), (true.x_in, true.y_in))
            if -MATCH_EARLY_S <= dt <= MATCH_LATE_S and dp < MATCH_DISTANCE_IN and (best is None or dp < best[1]):
                best = (i, dp, dt)
        row = {"sheet": true.sheet, "t": true.t, "x": true.x_in, "y": true.y_in, "detection": None}
        if best is None:
            result.missed += 1
        else:
            i, dp, dt = best
            used.add(i)
            det = detections[i]
            late = dt > LATE_AFTER_S
            result.matched += 1
            result.late += int(late)
            errors.append(dp)
            delays.append(det["t_call"] - det["t_first"])
            row.update(detection=det.get("n", i + 1), position_error_in=round(dp, 3),
                       first_seen_after_s=round(dt, 1), call_delay_s=round(det["t_call"] - det["t_first"], 1),
                       late=late)
        result.rows.append(row)
    for i, det in enumerate(detections):
        if i in used:
            continue
        # Called again: some real hole was already at this spot before this detection appeared.
        repeat = any(true.t < det["t_first"] and math.dist((det["x"], det["y"]), (true.x_in, true.y_in)) < MATCH_DISTANCE_IN
                     for true in truth)
        result.duplicates += int(repeat)
        result.false_calls += int(not repeat)
        result.extras.append({"detection": det.get("n", i + 1), "t_first": det["t_first"],
                              "x": det["x"], "y": det["y"], "kind": "duplicate" if repeat else "false call"})
    if errors:
        result.mean_position_error_in = round(sum(errors) / len(errors), 3)
        result.max_position_error_in = round(max(errors), 3)
        ordered = sorted(delays)
        result.median_call_delay_s = round(ordered[len(ordered) // 2], 2)
    return result
