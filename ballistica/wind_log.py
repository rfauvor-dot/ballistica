"""Anemometer logs paired with wind video -- the ground truth for camera wind
reading (BACKLOG: "Camera-based wind reading").

Rick films trees, flags and grass with the HoldPeak HP-866B-APP anemometer
logging on the S25. This module lines the two up by clock time and gives, for
each slice of each clip, what the meter measured while the camera watched.

The anemometer log (Intelligent Anemometer app 1.4.3, Save -> .xls):
    row 0  the file's path on the phone
    row 1  ID | Time | Wind Value(mph) | Temp Value(°F)
    rows   1 | 20:15:40 | 0.0 | 71.4       about one reading a second
The rows carry only the time of day. The date comes from the file name
(iAnemometer_YYYY-MM-DD-HH-MM-SS.xls), which is when Save was tapped -- the
end of the log -- so don't rename the files.

Samsung video timing (checked on Rick's own clips, 2026-11-18): the file name
(YYYYMMDD_HHMMSS.mp4) is the LOCAL time recording STARTED; the embedded
creation_time is the time it ENDED, in UTC. The name wins; the embedded time,
minus the duration, is the cross-check.

The meter's fan spins down slowly: a 5.8 mph puff took ~8 s to read zero
again. Rises are prompt, falls lag -- prefer window averages and peaks over
reading single falling values.

The camera has to stand still for leaf movement to be measurable (a
handheld clip shifted 10-280 px between keyframes; leaves in a 3 mph breeze
move a few). Rick starts each clip on the meter's screen as a clock check,
walks back and sets the phone down, so only the longest still stretch of a
clip is used -- still_span() finds it from keyframe-to-keyframe shifts.
"""
from __future__ import annotations

import csv
import math
import os
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

LOG_NAME_RE = re.compile(r"iAnemometer_(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})")
VIDEO_NAME_RE = re.compile(r"(?<!\d)(\d{8})_(\d{6})(?!\d)")
NAME_VS_EMBEDDED_TOLERANCE_S = 3.0
# A log whose last reading is later than its save time by more than this was
# recorded on the day before the save (saved just after midnight).
_SAVE_SLACK_S = 120.0

_TO_MPH = {"mph": 1.0, "m/s": 2.236936, "km/h": 0.621371, "kmh": 0.621371,
           "knots": 1.150779, "kn": 1.150779, "kt": 1.150779, "ft/min": 0.0113636}


@dataclass(frozen=True)
class Reading:
    at: datetime            # naive, the phone's local clock
    mph: float
    temp_f: float | None


@dataclass(frozen=True)
class Clip:
    path: str
    start: datetime         # naive local time recording started
    duration_s: float
    timing_from: str        # "file name" or "embedded end time"
    warning: str | None = None


# ------------------------------------------------------------------ logs
def log_saved_at(path: str) -> datetime:
    m = LOG_NAME_RE.search(os.path.basename(path))
    if not m:
        raise ValueError(
            f"{os.path.basename(path)}: not an iAnemometer_YYYY-MM-DD-HH-MM-SS file name -- "
            "the date lives only in the name, so the log can't be placed in time")
    return datetime(*map(int, m.groups()))


def _wind_factor(header: str) -> float:
    h = header.lower().replace(" ", "")
    for unit, factor in sorted(_TO_MPH.items(), key=lambda kv: -len(kv[0])):
        if f"({unit})" in h or h.endswith(unit):
            return factor
    raise ValueError(f"unknown wind unit in column {header!r}")


def readings_from_rows(rows, saved_at: datetime) -> list[Reading]:
    """rows: the sheet as lists of cell values, header row included."""
    rows = [[str(c).strip() for c in r] for r in rows]
    header_i = next((i for i, r in enumerate(rows)
                     if any(c.lower() == "time" for c in r)), None)
    if header_i is None:
        raise ValueError("no header row with a Time column")
    header = [c.lower() for c in rows[header_i]]
    t_col = header.index("time")
    w_col = next(i for i, c in enumerate(header) if c.startswith("wind"))
    temp_col = next((i for i, c in enumerate(header) if c.startswith("temp")), None)
    factor = _wind_factor(rows[header_i][w_col])
    celsius = temp_col is not None and "c)" in header[temp_col] and "f)" not in header[temp_col]

    out: list[Reading] = []
    day = saved_at.date()
    prev = None
    for r in rows[header_i + 1:]:
        if len(r) <= max(t_col, w_col) or not r[t_col]:
            continue
        hh, mm, ss = (int(x) for x in r[t_col].split(":"))
        at = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm, second=ss)
        if prev is not None and at < prev - timedelta(hours=1):     # past midnight
            day += timedelta(days=1)
            at += timedelta(days=1)
        prev = at
        temp = None
        if temp_col is not None and len(r) > temp_col and r[temp_col]:
            temp = float(r[temp_col])
            if celsius:
                temp = temp * 9 / 5 + 32
        out.append(Reading(at, round(float(r[w_col]) * factor, 2), temp))

    if out and (out[-1].at - saved_at).total_seconds() > _SAVE_SLACK_S:
        out = [Reading(x.at - timedelta(days=1), x.mph, x.temp_f) for x in out]
    return out


def read_log(path: str) -> list[Reading]:
    """An iAnemometer .xls (or the same sheet saved as .csv)."""
    saved_at = log_saved_at(path)
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rows = list(csv.reader(fh))
    else:
        import xlrd                                    # dev-only dependency
        sheet = xlrd.open_workbook(path).sheet_by_index(0)
        rows = [sheet.row_values(i) for i in range(sheet.nrows)]
    return readings_from_rows(rows, saved_at)


def merge_logs(logs) -> list[Reading]:
    seen, out = set(), []
    for r in sorted((r for log in logs for r in log), key=lambda r: r.at):
        if r.at not in seen:
            seen.add(r.at)
            out.append(r)
    return out


# ------------------------------------------------------------------ video
def name_start(path: str) -> datetime | None:
    m = VIDEO_NAME_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def clip_timing(path: str, duration_s: float, embedded_utc: datetime | None,
                local_tz: tzinfo | None = None) -> Clip:
    """local_tz None = this computer's time zone (the phone's, at the range)."""
    embedded_start = None
    if embedded_utc is not None:
        if embedded_utc.tzinfo is None:
            embedded_utc = embedded_utc.replace(tzinfo=timezone.utc)
        end_local = embedded_utc.astimezone(local_tz).replace(tzinfo=None)
        embedded_start = end_local - timedelta(seconds=duration_s)
    named = name_start(path)
    if named is not None:
        warning = None
        if embedded_start is not None:
            off = (embedded_start - named).total_seconds()
            if abs(off) > NAME_VS_EMBEDDED_TOLERANCE_S:
                warning = (f"file name says it started {named:%H:%M:%S}, the embedded end time "
                           f"says {embedded_start:%H:%M:%S} ({off:+.0f} s) -- renamed, edited, "
                           "or a different time zone? Check the app screen at the clip's start")
        return Clip(path, named, duration_s, "file name", warning)
    if embedded_start is not None:
        return Clip(path, embedded_start, duration_s, "embedded end time")
    raise ValueError(f"{os.path.basename(path)}: no start time in the file name or the video")


# ------------------------------------------------------------------ camera stillness
# Shift between consecutive keyframes (~1 s apart), in pixels on a 1920-px
# long side. A phone resting on a table moves 0-1 px, with the odd 3-4 px
# wobble when a gust rocks it (Rick's 12:40 clip, 2026-11-20); handheld moves
# 10-30 px typically and up to ~300. So a keyframe step is still when the
# typical shift around it (median of 5 steps) is <= 3 px and it isn't itself
# a jolt over 10 px -- a lone wobble doesn't end a still stretch, a real
# move does.
STILL_SHIFT_PX = 3.0
STILL_JOLT_PX = 10.0
STILL_MEDIAN_STEPS = 5
STILL_MIN_RESPONSE = 0.3        # phase-correlation confidence; blurry walking frames are lower
STILL_MIN_S = 20.0
STILL_MARGIN_S = 1.0            # trimmed off both ends: setting the phone down, reaching to stop


def still_spans(steps, shift_px: float = STILL_SHIFT_PX, min_response: float = STILL_MIN_RESPONSE,
                min_s: float = STILL_MIN_S, margin_s: float = STILL_MARGIN_S,
                jolt_px: float = STILL_JOLT_PX, median_steps: int = STILL_MEDIAN_STEPS,
                ) -> list[tuple[float, float]]:
    """steps: [(t_prev, t, shift_px, response)] between consecutive keyframes.
    Every stretch where the camera held still for min_s or more (after
    margin_s off each end), in clip seconds, in order. A phone that creeps
    or is nudged mid-clip gives two stretches; the move between is skipped."""
    steps = list(steps)
    shifts = [s[2] for s in steps]
    half = median_steps // 2

    def still(i: int) -> bool:
        _, _, shift, response = steps[i]
        if shift > jolt_px or response < min_response:
            return False
        return statistics.median(shifts[max(0, i - half):i + half + 1]) <= shift_px

    runs, run_start, run_end = [], None, None
    for i, (t_prev, t, _, _) in enumerate(steps):
        if still(i):
            if run_start is None:
                run_start = t_prev
            run_end = t
        elif run_start is not None:
            runs.append((run_start, run_end))
            run_start = None
    if run_start is not None:
        runs.append((run_start, run_end))
    trimmed = [(lo + margin_s, hi - margin_s) for lo, hi in runs]
    return [(lo, hi) for lo, hi in trimmed if hi - lo >= min_s]


def still_span(steps, **kw) -> tuple[float, float] | None:
    """The longest still stretch, or None."""
    spans = still_spans(steps, **kw)
    return max(spans, key=lambda sp: sp[1] - sp[0]) if spans else None


# ------------------------------------------------------------------ pairing
def _typical_interval_s(readings: list[Reading]) -> float:
    gaps = [(b.at - a.at).total_seconds() for a, b in zip(readings, readings[1:])]
    gaps = [g for g in gaps if 0 < g < 30]
    return statistics.median(gaps) if gaps else 1.0


def window_stats(readings: list[Reading], clip: Clip, window_s: float = 10.0,
                 clock_offset_s: float = 0.0, span: tuple[float, float] | None = None,
                 note: str = "", spans: list[tuple[float, float]] | None = None) -> list[dict]:
    """One row per window_s slice of the clip -- or of each of spans (clip
    seconds: the still stretches), slices never crossing a gap between them --
    plus a row "all" over everything measured. note goes on every row.

    clock_offset_s: how far the camera's clock is AHEAD of the meter phone's
    (measured by filming the app screen at the start of a clip); 0 when the
    S25 is both camera and logger.
    """
    interval = _typical_interval_s(readings)
    shifted = [((r.at - clip.start).total_seconds() + clock_offset_s, r) for r in readings]

    def stats(lo: float, hi: float, label: str, parts=None) -> dict:
        parts = parts or [(lo, hi)]
        inside = [r for t, r in shifted if any(a <= t < b for a, b in parts)]
        mph = [r.mph for r in inside]
        temps = [r.temp_f for r in inside if r.temp_f is not None]
        expected = sum(b - a for a, b in parts) / interval
        notes = [note] if note else []
        if not inside:
            notes.append("no readings")
        elif len(inside) < 0.5 * expected:
            notes.append(f"gap: {len(inside)} of ~{expected:.0f} readings")
        return {
            "video": os.path.basename(clip.path), "window": label,
            "start_s": round(lo, 1), "end_s": round(hi, 1),
            "local_time": (clip.start + timedelta(seconds=lo)).strftime("%Y-%m-%d %H:%M:%S"),
            "readings": len(inside),
            "mean_mph": round(statistics.fmean(mph), 2) if mph else None,
            "min_mph": min(mph) if mph else None,
            "max_mph": max(mph) if mph else None,
            "sd_mph": round(statistics.pstdev(mph), 2) if len(mph) > 1 else None,
            "temp_f": round(statistics.fmean(temps), 1) if temps else None,
            "note": "; ".join(notes),
        }

    if spans is None:
        spans = [span] if span else [(0.0, clip.duration_s)]
    rows = []
    for first, last in spans:
        n = max(1, math.ceil((last - first) / window_s - 1e-9))
        for k in range(n):
            lo, hi = first + k * window_s, min(first + (k + 1) * window_s, last)
            rows.append(stats(lo, hi, str(len(rows) + 1)))
    rows.append(stats(spans[0][0], spans[-1][1], "all", spans))
    return rows
