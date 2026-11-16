"""The browser port of the shot calling (ballistica/web/speedtest/shot_caller.js)
must call exactly the shots the Python tracker calls (ShotTracker.process_views)
from the same per-frame sheet views.

Two checks, both run in Node against Python:
  - the views recorded from the WHOLE November 13 range video
    (tools/replay_in_chunks.py --record-views): same shots, same times, same
    places, and the same score against the hand-measured table;
  - random view streams built to hit every branch (numbered and unnumbered
    sheets, holes already there, known holes after a restart, candidates that
    flicker, sheets out of view for a long time, ties between sheets).
"""
from __future__ import annotations

import gzip
import json
import os
import random
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ballistica.shot_scoring import load_truth, score  # noqa: E402
from ballistica.shot_tracker import ShotTracker  # noqa: E402

CALLER_JS = os.path.join(ROOT, "ballistica", "web", "speedtest", "shot_caller.js")
BENCH = os.path.join(ROOT, "data", "shot_benchmark")
CLIP = "2026-11-13-table-tripod"
VIEWS = os.path.join(BENCH, CLIP + ".views.jsonl.gz")
_NODE = shutil.which("node")

_NODE_RUNNER = r"""
const fs = require("fs");
const { ShotCaller } = require(process.argv[1]);
const runs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = runs.map((run) => {
  const caller = new ShotCaller(null, run.known);
  for (const f of run.frames) caller.processViews(f.views, f.t);
  return { shots: caller.shots.map(({ snapshot, ...s }) => s), holes: caller.sheetHoles(),
           tracks: caller.tracks.length };
});
fs.writeFileSync(process.argv[3], JSON.stringify(out));
"""


def _python(frames: list[dict], known: dict | None = None) -> dict:
    tracker = ShotTracker(known_holes={int(k): v for k, v in (known or {}).items()})
    for f in frames:
        tracker.process_views(f["views"], f["t"])
    shots = [dict(n=s.n, sheetTrack=s.sheet_track, sheetNumber=s.sheet_number, tFirst=s.t_first, tCall=s.t_call,
                  x=s.x_in, y=s.y_in, offCameraS=s.off_camera_s) for s in tracker.shots]
    holes = {str(k): [list(h) for h in v] for k, v in tracker.sheet_holes().items()}
    return dict(shots=shots, holes=holes, tracks=len(tracker.tracks))


def _node(runs: list[dict], tmp_path) -> list[dict]:
    src, dst = tmp_path / "runs.json", tmp_path / "out.json"
    src.write_text(json.dumps(runs), encoding="utf-8")
    res = subprocess.run([_NODE, "-e", _NODE_RUNNER, CALLER_JS, str(src), str(dst)],
                         capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr
    return json.loads(dst.read_text(encoding="utf-8"))


def _assert_same(py: dict, js: dict, label: str) -> None:
    assert js["tracks"] == py["tracks"], f"{label}: {js['tracks']} sheets vs Python's {py['tracks']}"
    assert len(js["shots"]) == len(py["shots"]), f"{label}: {len(js['shots'])} shots vs Python's {len(py['shots'])}"
    for a, b in zip(py["shots"], js["shots"]):
        for key in ("n", "sheetTrack", "sheetNumber", "tFirst", "tCall"):
            assert a[key] == b[key], f"{label}: shot {a['n']} {key} {b[key]} vs Python's {a[key]}"
        assert abs(a["x"] - b["x"]) < 1e-9 and abs(a["y"] - b["y"]) < 1e-9, f"{label}: shot {a['n']} position"
        assert abs(a["offCameraS"] - b["offCameraS"]) < 1e-9, f"{label}: shot {a['n']} off-camera time"
    assert js["holes"] == py["holes"], f"{label}: saved hole lists differ"


def _load_views() -> list[dict]:
    with gzip.open(VIEWS, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


# ------------------------------------------------------------ November 13 video
def test_recorded_views_cover_the_whole_video():
    frames = _load_views()
    assert len(frames) > 2600                       # 22 minutes at 2 frames a second
    assert all(b["t"] > a["t"] for a, b in zip(frames, frames[1:]))
    assert sum(1 for f in frames if f["views"]) > 1500


def test_recorded_views_give_the_benchmark_result():
    """Replaying the recorded views through the Python calling gives the
    benchmark's result -- so the recording is faithful, and the browser port
    is checked against the real thing."""
    frames = _load_views()
    py = _python(frames)
    with open(os.path.join(BENCH, "clips.json"), encoding="utf-8") as fh:
        clip = next(c for c in json.load(fh)["clips"] if c["name"] == CLIP)
    detections = [dict(sheet_track=s["sheetTrack"], sheet_number=s["sheetNumber"], t_first=s["tFirst"],
                       t_call=s["tCall"], x=s["x"], y=s["y"], off_camera_s=s["offCameraS"], n=s["n"])
                  for s in py["shots"]]
    result = score(detections, load_truth(os.path.join(BENCH, clip["truth"])))
    for key, want in clip["expected"].items():
        assert getattr(result, key) == want, f"{key}: {getattr(result, key)} vs expected {want}"
    with open(os.path.join(BENCH, clip["baseline"]), encoding="utf-8") as fh:
        baseline = json.load(fh)["shots"]
    assert [(b["sheet_track"], b["t_first"], b["t_call"], b["x"], b["y"]) for b in baseline] == \
           [(d["sheet_track"], round(d["t_first"], 2), round(d["t_call"], 2), round(d["x"], 2), round(d["y"], 2))
            for d in detections]


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_browser_calls_the_same_shots_on_the_november_13_video(tmp_path):
    frames = _load_views()
    js = _node([dict(frames=frames, known={})], tmp_path)[0]
    _assert_same(_python(frames), js, "November 13")


# ------------------------------------------------------------ random streams
def _random_stream(rng: random.Random) -> tuple[list[dict], dict]:
    """A made-up session: a few sheets, numbered or not, shots landing over
    time, dots that flicker, jitter, and spells out of view."""
    sheets = []
    for k in range(rng.randint(1, 3)):
        number = rng.choice([None, None, rng.randint(1, 11)])
        if number is not None and any(s["number"] == number for s in sheets):
            number = None
        holes = [(rng.uniform(-3.5, 3.5), rng.uniform(-4, 4)) for _ in range(rng.randint(0, 2))]  # already there
        sheets.append(dict(number=number, org=[200.0 + 600 * k + rng.uniform(-20, 20), 300.0 + rng.uniform(-20, 20)],
                           s=rng.uniform(45, 90), holes=holes, away_until=-1.0))
    known = {}
    for s in sheets:
        if s["number"] is not None and rng.random() < 0.4:
            known[str(s["number"])] = [[round(x, 3), round(y, 3)] for x, y in s["holes"]]
    frames = []
    t = 0.0
    for _ in range(rng.randint(40, 160)):
        t = round(t + rng.choice([0.5, 0.5, 0.5, 1.0]), 3)
        if rng.random() < 0.02:
            t += rng.choice([5.0, 25.0, 40.0])       # app paused / long gap
        for s in sheets:
            if rng.random() < 0.06:
                # A shot; sometimes right next to an old hole.
                if s["holes"] and rng.random() < 0.3:
                    hx, hy = rng.choice(s["holes"])
                    s["holes"].append((hx + rng.uniform(-0.4, 0.4), hy + rng.uniform(-0.4, 0.4)))
                else:
                    s["holes"].append((rng.uniform(-3.5, 3.5), rng.uniform(-4, 4)))
            if rng.random() < 0.03:
                s["away_until"] = t + rng.choice([2.0, 30.0])
        views = []
        for s in sheets:
            if t < s["away_until"] or rng.random() < 0.08:
                continue
            dots = [[x + rng.gauss(0, 0.03), y + rng.gauss(0, 0.03)] for x, y in s["holes"] if rng.random() > 0.1]
            if rng.random() < 0.05:
                dots.append([rng.uniform(-3.5, 3.5), rng.uniform(-4, 4)])      # a speck for one frame
            rng.shuffle(dots)
            shift = rng.uniform(-30, 30) if rng.random() < 0.05 else rng.uniform(-2, 2)
            views.append(dict(number=s["number"], org=[s["org"][0] + shift, s["org"][1] + shift], s=s["s"], dots=dots))
        frames.append(dict(t=t, views=views))
    return frames, known


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_browser_calls_the_same_shots_on_random_streams(tmp_path):
    rng = random.Random(20261116)
    runs = [dict(zip(("frames", "known"), _random_stream(rng))) for _ in range(300)]
    out = _node(runs, tmp_path)
    total = 0
    for i, (run, js) in enumerate(zip(runs, out)):
        py = _python(run["frames"], run["known"])
        _assert_same(py, js, f"stream {i}")
        total += len(py["shots"])
    assert total > 300          # the streams really exercise shot calling


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_median_matches_numpy(tmp_path):
    import numpy as np
    script = ("const { median } = require(process.argv[1]);"
              "console.log(JSON.stringify([[3],[1,2],[5,1,4],[2,9,1,7],[0.1,0.3,0.2,0.25,0.15]].map(median)));")
    res = subprocess.run([_NODE, "-e", script, CALLER_JS], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    want = [float(np.median(v)) for v in ([3], [1, 2], [5, 1, 4], [2, 9, 1, 7], [0.1, 0.3, 0.2, 0.25, 0.15])]
    assert got == pytest.approx(want, abs=1e-12)
