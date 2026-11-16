"""Test bang detection (ballistica/shot_sound.py) against a range video whose
shots are already known.

    python tools/shot_sound_test.py VIDEO SHOTS_JSON [--out REPORT.json] [--ffmpeg PATH]

SHOTS_JSON is a detector result (summary.json from a replay, or a baseline in
data/shot_benchmark/): each shot's t_first is when its hole was first SEEN, so
the bang that fired it must come a little before that. For every shot this
reports the bang it most likely came from, how long before the hole was seen,
and how loud it was compared with every other bang in the recording.

Needs ffmpeg (to pull the sound out of the video). Uses one thread.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballistica.shot_sound import find_bangs, to_mono_float  # noqa: E402

RATE = 16000
# A hole is first seen at most one sampled frame (0.5 s at 2 fps) after it is
# made, plus however many frames a jolt hid the sheet for. The bang must come
# before the sighting, and a bullet reaches 100 yd in about a tenth of a second.
LOOK_BACK_S, LOOK_AHEAD_S = 4.0, 0.15


def _ffmpeg(path_hint: str | None) -> str:
    found = path_hint or shutil.which("ffmpeg")
    if not found:
        winget = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
        for root, _, files in os.walk(winget) if os.path.isdir(winget) else []:
            if "ffmpeg.exe" in files:
                found = os.path.join(root, "ffmpeg.exe")
                break
    if not found:
        raise SystemExit("ffmpeg not found -- install it or pass --ffmpeg PATH")
    return found


def load_audio(video: str, ffmpeg: str) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-threads", "1", "-i", video, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    return to_mono_float(np.frombuffer(raw, np.int16), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("shots_json")
    ap.add_argument("--out")
    ap.add_argument("--ffmpeg")
    args = ap.parse_args()

    samples = load_audio(args.video, _ffmpeg(args.ffmpeg))
    duration = len(samples) / RATE
    bangs = find_bangs(samples, RATE)
    with open(args.shots_json, encoding="utf-8") as fh:
        shots = json.load(fh)["shots"]

    loudness_rank = {id(b): r for r, b in enumerate(sorted(bangs, key=lambda b: -b.peak_db), start=1)}
    rows, used = [], set()
    for s in shots:
        window = [b for b in bangs if s["t_first"] - LOOK_BACK_S <= b.t_s <= s["t_first"] + LOOK_AHEAD_S]
        # The shooter's own rifle is the closest gun, so the loudest bang in the window.
        best = max(window, key=lambda b: b.peak_db) if window else None
        if best is not None:
            used.add(id(best))
        rows.append(dict(
            shot=s["n"], hole_first_seen_s=s["t_first"], bangs_in_window=len(window),
            bang_t_s=best.t_s if best else None,
            seen_after_bang_s=round(s["t_first"] - best.t_s, 2) if best else None,
            peak_db=best.peak_db if best else None, loudness_rank=loudness_rank[id(best)] if best else None,
        ))
    matched = [r for r in rows if r["bang_t_s"] is not None]
    shot_peaks = sorted(r["peak_db"] for r in matched)
    others = [b for b in bangs if id(b) not in used]
    quietest_shot = shot_peaks[0] if shot_peaks else None
    summary = dict(
        audio_minutes=round(duration / 60, 1), bangs_found=len(bangs), shots=len(shots),
        shots_with_a_bang=len(matched),
        shot_peak_db=dict(min=shot_peaks[0], median=shot_peaks[len(shot_peaks) // 2], max=shot_peaks[-1]) if shot_peaks else None,
        other_bangs=len(others),
        other_bangs_as_loud_as_quietest_shot=(sum(1 for b in others if b.peak_db >= quietest_shot)
                                              if quietest_shot is not None else None),
        other_bangs_per_minute=round(len(others) / (duration / 60), 2) if duration else None,
    )

    print(json.dumps(summary, indent=1))
    print("\nshot  hole seen   bang at    seen after   peak dB  rank  bangs in window")
    for r in rows:
        if r["bang_t_s"] is None:
            print("%4d  %8.1fs   -- no bang found --" % (r["shot"], r["hole_first_seen_s"]))
        else:
            print("%4d  %8.1fs  %8.2fs   %6.2fs   %7.1f  %4d  %d" % (
                r["shot"], r["hole_first_seen_s"], r["bang_t_s"], r["seen_after_bang_s"], r["peak_db"],
                r["loudness_rank"], r["bangs_in_window"]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(dict(summary=summary, shots=rows,
                           bangs=[dict(t_s=b.t_s, peak_db=b.peak_db, rise_db=b.rise_db, above_db=b.above_db)
                                  for b in bangs]), fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
