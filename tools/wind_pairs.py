"""Pair anemometer logs with wind video: what the meter read during each slice
of each clip. Ground truth for camera wind reading (see ballistica/wind_log.py).

    python tools/wind_pairs.py --logs iAnemometer_*.xls --videos 2026*.mp4
                               [--window 10] [--heading 270 | --heading NAME=270 ...]
                               [--clock-offset 0] [--out wind_pairs.csv]

--heading       which way the camera pointed, compass degrees: one number for
                every clip, or VIDEO_NAME=DEG per clip (repeatable)
--clock-offset  seconds the camera's clock is AHEAD of the meter phone's; 0
                when the S25 films and logs at once
Needs ffprobe on the PATH (it reads each clip's length and embedded time).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballistica import wind_log  # noqa: E402

COLUMNS = ["video", "heading_deg", "window", "start_s", "end_s", "local_time", "readings",
           "mean_mph", "min_mph", "max_mph", "sd_mph", "temp_f", "note"]


def probe(path: str) -> tuple[float, datetime | None]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    fmt = json.loads(out).get("format", {})
    created = (fmt.get("tags") or {}).get("creation_time")
    when = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
    return float(fmt["duration"]), when


def parse_headings(values: list[str]) -> tuple[float | None, dict[str, float]]:
    default, per = None, {}
    for v in values or []:
        if "=" in v:
            name, deg = v.rsplit("=", 1)
            per[os.path.basename(name)] = float(deg) % 360
        else:
            default = float(v) % 360
    return default, per


def run(logs: list[str], videos: list[str], window_s: float, headings: list[str],
        clock_offset_s: float, out_path: str, probe_fn=probe, local_tz=None) -> list[dict]:
    readings = wind_log.merge_logs(wind_log.read_log(p) for p in logs)
    if not readings:
        raise SystemExit("the logs hold no readings")
    print(f"{len(readings)} readings, {readings[0].at:%Y-%m-%d %H:%M:%S} to {readings[-1].at:%H:%M:%S}")
    default_heading, per_heading = parse_headings(headings)
    rows = []
    for v in videos:
        duration, embedded = probe_fn(v)
        clip = wind_log.clip_timing(v, duration, embedded, local_tz)
        heading = per_heading.get(os.path.basename(v), default_heading)
        clip_rows = wind_log.window_stats(readings, clip, window_s, clock_offset_s)
        for r in clip_rows:
            r["heading_deg"] = heading
        rows.extend(clip_rows)
        whole = clip_rows[-1]
        said = (f"{whole['mean_mph']} mph average, {whole['max_mph']} peak, {whole['readings']} readings"
                if whole["readings"] else "NO READINGS during this clip")
        print(f"{os.path.basename(v)}: {clip.start:%H:%M:%S} +{duration:.0f} s "
              f"(timing from {clip.timing_from}) -- {said}")
        if clip.warning:
            print(f"  warning: {clip.warning}")
        gaps = [r for r in clip_rows[:-1] if r["note"]]
        if gaps and whole["readings"]:
            print(f"  {len(gaps)} of {len(clip_rows) - 1} windows short of readings")
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path}")
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--window", type=float, default=10.0, help="seconds per slice (default 10)")
    ap.add_argument("--heading", action="append", default=[])
    ap.add_argument("--clock-offset", type=float, default=0.0)
    ap.add_argument("--out", default="wind_pairs.csv")
    a = ap.parse_args(argv)
    run(a.logs, a.videos, a.window, a.heading, a.clock_offset, a.out)


if __name__ == "__main__":
    main()
