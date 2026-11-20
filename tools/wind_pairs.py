"""Pair anemometer logs with wind video: what the meter read during each slice
of each clip. Ground truth for camera wind reading (see ballistica/wind_log.py).

    python tools/wind_pairs.py --logs iAnemometer_*.xls --videos 2026*.mp4
                               [--heading 270 | --heading NAME=270 ...]
                               [--meter-heading 300 | --meter-heading NAME=300 ...]
                               [--window 10] [--clock-offset 0] [--out wind_pairs.csv]
                               [--no-trim | --start S --end S]

Only the part of each clip where the camera held still is measured: the
clock-check close-up of the meter, the walk back, and the reach to stop
recording are cut off automatically (camera motion between keyframes).
--start/--end (seconds into the clip) set it by hand; --no-trim uses it all.

--heading        compass degrees the CAMERA points: one number for every
                 clip, or VIDEO_NAME=DEG per clip (repeatable)
--meter-heading  compass degrees the meter takes wind FROM, same forms: wind
                 from the west through the meter (screen facing east) = 270
--clock-offset   seconds the camera's clock is AHEAD of the meter phone's; 0
                 when the S25 films and logs at once
Needs ffmpeg and ffprobe on the PATH, and OpenCV for the trimming.
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

COLUMNS = ["video", "heading_deg", "meter_heading_deg", "window", "start_s", "end_s", "local_time",
           "readings", "mean_mph", "min_mph", "max_mph", "sd_mph", "temp_f", "note"]
_MOTION_LONG_SIDE = 480


def probe(path: str) -> tuple[float, datetime | None]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    fmt = json.loads(out).get("format", {})
    created = (fmt.get("tags") or {}).get("creation_time")
    when = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
    return float(fmt["duration"]), when


def _display_size(path: str) -> tuple[int, int]:
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout)["streams"][0]
    rot = next((float(sd.get("rotation", 0)) for sd in j.get("side_data_list", [])), 0.0)
    w, h = j["width"], j["height"]
    return (h, w) if abs(int(rot)) % 180 == 90 else (w, h)


def _keyframe_times(path: str) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    times = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0] not in ("", "N/A"):
            times.append(float(parts[0]))
    return sorted(times)


def motion_steps(path: str) -> list[tuple[float, float, float, float]]:
    """Camera shift between consecutive keyframes: [(t_prev, t, shift_px, response)],
    shift in pixels on a 1920-px long side. Decodes keyframes only (~1 a second),
    about 25 s for a 3-minute 1080p clip."""
    import cv2
    import numpy as np

    w, h = _display_size(path)
    scale = _MOTION_LONG_SIDE / max(w, h)
    W, H = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", path, "-vf", f"scale={W}:{H},format=gray",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    times = _keyframe_times(path)
    if len(times) != len(frames):          # can't place the frames in time: don't guess
        raise RuntimeError(f"{len(frames)} keyframes decoded but {len(times)} timestamps")
    window = cv2.createHanningWindow((W, H), cv2.CV_32F)
    to_1920 = 1920 / max(W, H)
    steps = []
    for i in range(1, len(frames)):
        (dx, dy), response = cv2.phaseCorrelate(frames[i - 1], frames[i], window)
        steps.append((times[i - 1], times[i], float(np.hypot(dx, dy)) * to_1920, float(response)))
    return steps


def parse_headings(values: list[str]) -> tuple[float | None, dict[str, float]]:
    default, per = None, {}
    for v in values or []:
        if "=" in v:
            name, deg = v.rsplit("=", 1)
            per[os.path.basename(name)] = float(deg) % 360
        else:
            default = float(v) % 360
    return default, per


def _heading_for(video: str, parsed) -> float | None:
    default, per = parsed
    return per.get(os.path.basename(video), default)


def run(logs: list[str], videos: list[str], window_s: float, headings: list[str],
        clock_offset_s: float, out_path: str, probe_fn=probe, local_tz=None,
        meter_headings: list[str] = (), trim: bool = True, motion_fn=motion_steps,
        start_s: float | None = None, end_s: float | None = None) -> list[dict]:
    readings = wind_log.merge_logs(wind_log.read_log(p) for p in logs)
    if not readings:
        raise SystemExit("the logs hold no readings")
    print(f"{len(readings)} readings, {readings[0].at:%Y-%m-%d %H:%M:%S} to {readings[-1].at:%H:%M:%S}")
    cam_h, meter_h = parse_headings(headings), parse_headings(list(meter_headings))
    rows = []
    for v in videos:
        duration, embedded = probe_fn(v)
        clip = wind_log.clip_timing(v, duration, embedded, local_tz)
        spans, note, how = [(0.0, duration)], "", "whole clip"
        if start_s is not None or end_s is not None:
            spans = [(max(0.0, start_s or 0.0), min(duration, end_s if end_s is not None else duration))]
            how = "set by hand"
        elif trim:
            found = wind_log.still_spans(motion_fn(v))
            if found:
                spans, how = found, "camera still"
            else:
                note = "camera not still"
                how = f"WHOLE CLIP -- the camera never held still for {wind_log.STILL_MIN_S:.0f} s"
        clip_rows = wind_log.window_stats(readings, clip, window_s, clock_offset_s, note=note, spans=spans)
        for r in clip_rows:
            r["heading_deg"] = _heading_for(v, cam_h)
            r["meter_heading_deg"] = _heading_for(v, meter_h)
        rows.extend(clip_rows)
        whole = clip_rows[-1]
        said = (f"{whole['mean_mph']} mph average, {whole['max_mph']} peak, {whole['readings']} readings"
                if whole["readings"] else "NO READINGS during this clip")
        print(f"{os.path.basename(v)}: {clip.start:%H:%M:%S} +{duration:.0f} s "
              f"(timing from {clip.timing_from})")
        measured = ", ".join(f"{a:.1f}-{b:.1f}" for a, b in spans)
        print(f"  measured {measured} s ({how}, {sum(b - a for a, b in spans):.0f} s): {said}")
        if clip.warning:
            print(f"  warning: {clip.warning}")
        gaps = [r for r in clip_rows[:-1] if "readings" in r["note"]]
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
    ap.add_argument("--meter-heading", action="append", default=[],
                    help="compass degrees the meter takes wind FROM (screen faces the opposite way)")
    ap.add_argument("--clock-offset", type=float, default=0.0)
    ap.add_argument("--out", default="wind_pairs.csv")
    ap.add_argument("--no-trim", action="store_true", help="measure the whole clip")
    ap.add_argument("--start", type=float, help="seconds into the clip to start measuring")
    ap.add_argument("--end", type=float, help="seconds into the clip to stop measuring")
    a = ap.parse_args(argv)
    run(a.logs, a.videos, a.window, a.heading, a.clock_offset, a.out,
        meter_headings=a.meter_heading, trim=not a.no_trim, start_s=a.start, end_s=a.end)


if __name__ == "__main__":
    main()
