"""Builds the sample frames and expected results for the on-device speed test
page (/speed-test, ballistica/web/speedtest/).

    python -m scripts.build_speed_test_frames VIDEO [--times 20,75,...]

Pulls one frame at each time (seconds) from the video, saves it as a JPEG in
ballistica/web/speedtest/frames/, and writes manifest.json with what the
Python shot tracker finds in each frame: every target sheet it sees, whether
the tracker would use that view, and the dark dots on it. The page runs the
browser version of the same steps on the same frames and compares, so the
speed it reports is for work that gives the same answer.

Already run for the November 13 range video; the frames are in the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ballistica.shot_tracker import ShotTracker  # noqa: E402

OUT_DIR = os.path.join(ROOT, "ballistica", "web", "speedtest")
DEFAULT_TIMES = [20, 75, 135, 210, 300, 380, 430, 560, 760, 1000, 1290, 1340]


def expected_for(tracker: ShotTracker, frame) -> list[dict]:
    """The Python tracker's view of one frame, in the manifest's shape."""
    c = tracker.config
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    out = []
    for sheet in tracker._sheets_in(gray):
        good = ((sheet["found"] >= 3 and sheet["rms"] < c.max_rms_3_markers_in)
                or (sheet["found"] == 2 and sheet["rms"] < c.max_rms_2_markers_in))
        if sheet["number"] is not None and sheet["found"] < c.min_markers_numbered:
            good = False
        entry = dict(number=sheet["number"], markers=sheet["found"], rms=round(sheet["rms"], 4), used=False)
        if good:
            _, cover, dots = tracker._dots_of(frame, sheet["Hm"])
            entry["cover"] = round(cover, 4)
            if cover >= c.min_cover:
                entry["used"] = True
                entry["dots"] = sorted([round(x, 3), round(y, 3)] for x, y in dots)
        out.append(entry)
    return sorted(out, key=lambda e: (not e["used"], -e["markers"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--times", default=",".join(str(t) for t in DEFAULT_TIMES))
    args = ap.parse_args()
    cv2.setNumThreads(2)
    tracker = ShotTracker()
    os.makedirs(os.path.join(OUT_DIR, "frames"), exist_ok=True)
    cap = cv2.VideoCapture(args.video)
    frames = []
    for t in [float(x) for x in args.times.split(",")]:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            print(f"no frame at {t}s")
            continue
        name = f"f{int(t):04d}.jpg"
        path = os.path.join(OUT_DIR, "frames", name)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        # Expected results are computed from the saved JPEG, exactly what the page will load.
        saved = cv2.imread(path)
        frames.append(dict(file=f"frames/{name}", video_s=t, width=saved.shape[1], height=saved.shape[0],
                           sheets=expected_for(tracker, saved)))
        print(name, [(s["number"], s["markers"], s["used"], len(s.get("dots", []))) for s in frames[-1]["sheets"]])
    manifest = dict(
        source=os.path.basename(args.video), opencv_python=cv2.__version__,
        settings=dict(ppi=tracker.config.ppi, darkness_threshold=tracker.config.darkness_threshold,
                      max_rms_3_markers_in=tracker.config.max_rms_3_markers_in,
                      max_rms_2_markers_in=tracker.config.max_rms_2_markers_in,
                      min_cover=tracker.config.min_cover, min_markers_numbered=tracker.config.min_markers_numbered),
        frames=frames,
    )
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
