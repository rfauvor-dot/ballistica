"""Replay a range video through the live shot detector (ballistica/shot_tracker.py).

Frames are fed one at a time in order, so the result is what a live camera
feed would have produced. For every shot called it saves the camera frame and
the flattened sheet with the new hole ringed -- the still photos, pulled from
the video -- and writes summary.json (shots, timing, sheet tracks) to OUTDIR.

usage: python tools/live_shot_replay.py VIDEO OUTDIR [FPS] [SCALE] [PPI] [N_CONF] [START_S] [END_S]
e.g.   python tools/live_shot_replay.py range.mp4 out 2 1.0 100 3

To score a run against a hand-measured table, or to run every benchmark clip,
see tools/shot_benchmark.py.
"""
from __future__ import annotations

import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballistica.shot_tracker import ShotTracker, TrackerConfig  # noqa: E402


def _stat(values: list[float]) -> dict:
    if not values:
        return {}
    return {"mean_ms": round(1000 * float(np.mean(values)), 1), "p95_ms": round(1000 * float(np.percentile(values, 95)), 1)}


def replay(video: str, out_dir: str | None, fps: float = 2.0, scale: float = 1.0, ppi: float = 100.0,
           n_confirm: int = 3, start_s: float = 0.0, end_s: float = 1e9, verbose: bool = True) -> dict:
    """Runs the detector over `video`, sampling `fps` frames per second.
    Returns the summary dict (also written to out_dir/summary.json, with the
    per-shot stills, when out_dir is given)."""
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tracker = ShotTracker(TrackerConfig(ppi=ppi, n_confirm=n_confirm))
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {video}")
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    step = max(1, int(round(video_fps / fps)))
    if start_s > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000)
    idx = int(start_s * video_fps)
    decode: list[float] = []
    wall0 = time.time()
    while True:
        t_dec = time.time()
        if not cap.grab():
            break
        if idx % step:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        decode.append(time.time() - t_dec)
        t = idx / video_fps
        idx += 1
        if t > end_s or not ok:
            break
        if scale != 1.0:
            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        for shot in tracker.process_frame(frame, t):
            if out_dir:
                sheet = cv2.resize(shot.sheet_image(), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(out_dir, "shot%03d_sheet.jpg" % shot.n), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
                cv2.imwrite(os.path.join(out_dir, "shot%03d_frame.jpg" % shot.n), shot.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if verbose:
                print("SHOT %d  track %d  first %.1fs  called %.1fs  x=%+.2f y=%+.2f"
                      % (shot.n, shot.sheet_track, shot.t_first, t, shot.x_in, shot.y_in), flush=True)
        if verbose and int(t) % 120 == 0 and tracker.frames_processed % int(max(1, fps) * 2) == 0:
            print("t=%ds  shots=%d  tracks=%d" % (int(t), len(tracker.shots), len(tracker.tracks)), flush=True)
    summary = dict(
        fps=fps, scale=scale, ppi=ppi, n_conf=n_confirm,
        frames_processed=tracker.frames_processed, sheet_frames_used=tracker.sheet_frames_used,
        sheet_frames_gated=tracker.sheet_frames_gated, wall_s=round(time.time() - wall0, 1),
        decode=_stat(decode), markers=_stat(tracker.timing["markers"]),
        rectify=_stat(tracker.timing["rectify"]), total=_stat(tracker.timing["total"]),
        shots=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.as_dict().items()} for s in tracker.shots],
        tracks=tracker.track_summary(),
    )
    if out_dir:
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
    return summary


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    num = [float(a) for a in argv[3:]]
    defaults = [2.0, 1.0, 100.0, 3, 0.0, 1e9]
    fps, scale, ppi, n_conf, start_s, end_s = num + defaults[len(num):]
    summary = replay(argv[1], argv[2], fps, scale, ppi, int(n_conf), start_s, end_s)
    print("DONE", json.dumps({k: v for k, v in summary.items() if k != "shots"}), flush=True)


if __name__ == "__main__":
    main(sys.argv)
