"""Replay a range video through the BROWSER detector -- detector.js (corner
squares with the patch search, flattening, holes) and shot_caller.js (which
holes are new shots) -- frame by frame, the way it would run live on the
phone, and score it the same way the Python tracker is scored (2026-11-16).

Three steps:

    python tools/browser_replay.py export VIDEO FRAMES_DIR [--fps 2] [--quality 95]
        Saves exactly the frames the Python replay samples (same frame numbers,
        same times) as JPEGs, plus frames.json. Low priority, two threads.

    python tools/browser_replay.py serve FRAMES_DIR [--port 8431]
        Serves a local page at http://localhost:PORT/ that loads OpenCV.js and
        the two browser scripts straight from ballistica/web/speedtest/, runs
        every frame in order, and posts the result back here: it is written to
        FRAMES_DIR/browser_result.json. Open the page in a browser and leave it.

    python tools/browser_replay.py score FRAMES_DIR --clip NAME [--views VIEWS.jsonl[.gz]]
        Scores browser_result.json against the clip's hand-measured table (as
        tools/shot_benchmark.py does), lists any shot that differs from the
        Python tracker's, and -- given the views the Python replay recorded
        (tools/replay_in_chunks.py --record-views) -- counts the frames where
        the two detectors saw different holes.

The frames are JPEG, not the decoded video, so a few pixels differ from what
the Python replay saw; the score is what matters, not bit-for-bit equality.
"""
from __future__ import annotations

import argparse
import gzip
import http.server
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEEDTEST = os.path.join(ROOT, "ballistica", "web", "speedtest")
BENCH_DIR = os.path.join(ROOT, "data", "shot_benchmark")
sys.path.insert(0, ROOT)

# Same OpenCV.js file, pinned the same way, as the speed test page.
_OPENCV = ('<script src="https://cdn.jsdelivr.net/npm/@techstark/opencv-js@5.0.0-release.1/dist/opencv.js" '
           'integrity="sha384-16eddoZUbhJFP+dmyqNO/+vH8UdDOg8MJo+zi1NSy2clHxpKiQaoSQWtQnPHzYvc" '
           'crossorigin="anonymous"></script>')

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Browser replay</title>
<style>body{font:15px system-ui;margin:20px;background:#fff;color:#111} pre{white-space:pre-wrap}</style></head>
<body><h1>Browser replay</h1><div id="status">Loading...</div><pre id="log"></pre>
""" + _OPENCV + """
<script src="/speedtest/detector.js"></script>
<script src="/speedtest/shot_caller.js"></script>
<script>
(async function () {
  const $ = (id) => document.getElementById(id);
  const log = (s) => { $("log").textContent += s + "\\n"; };
  try {
    let cv = window.cv;
    if (cv instanceof Promise) cv = await cv;
    if (!cv.Mat) await new Promise((r) => { cv.onRuntimeInitialized = r; });
    const list = await (await fetch("/frames.json")).json();
    const detector = new BallisticaFrameDetector.FrameDetector(cv);
    const caller = new BallisticaShotCaller.ShotCaller();
    const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
    const frames = [], wall0 = performance.now();
    let next = fetch("/frames/" + list.frames[0].file).then((r) => r.blob());
    for (let i = 0; i < list.frames.length; i++) {
      const f = list.frames[i];
      const blob = await next;
      if (i + 1 < list.frames.length) next = fetch("/frames/" + list.frames[i + 1].file).then((r) => r.blob());
      const bmp = await createImageBitmap(blob);
      canvas.width = bmp.width; canvas.height = bmp.height; ctx.drawImage(bmp, 0, 0); bmp.close();
      const mat = cv.matFromImageData(ctx.getImageData(0, 0, canvas.width, canvas.height));
      const r = detector.process(mat, { track: true });
      mat.delete();
      const views = r.sheets.filter((s) => s.used).map((s) => ({ number: s.number, org: s.org, s: s.s, dots: s.dots }));
      for (const shot of caller.processViews(views, f.t)) {
        log(`SHOT ${shot.n}  track ${shot.sheetTrack}  first ${shot.tFirst.toFixed(1)}s  called ${shot.tCall.toFixed(1)}s  x=${shot.x.toFixed(2)} y=${shot.y.toFixed(2)}`);
      }
      frames.push({ t: f.t, search: r.search, ms: r.ms, views });
      if (i % 20 === 0) $("status").textContent = `Frame ${i + 1} of ${list.frames.length} (${f.t.toFixed(0)} s of video), ${caller.shots.length} shots`;
    }
    const result = { frames, shots: caller.shots.map(({ snapshot, ...s }) => s), stats: detector.stats,
                     tracks: caller.tracks.length, wall_s: (performance.now() - wall0) / 1000, userAgent: navigator.userAgent };
    const res = await fetch("/result", { method: "POST", body: JSON.stringify(result) });
    $("status").textContent = res.ok ? `DONE: ${caller.shots.length} shots, result saved.` : "DONE but saving failed";
    window.replayDone = true;
  } catch (e) {
    $("status").textContent = "FAILED: " + e.message;
    window.replayDone = "failed: " + e.message;
  }
})();
</script></body></html>"""


def export(video: str, out_dir: str, fps: float, quality: int) -> int:
    from replay_in_chunks import _limit_threads, _lower_priority
    _limit_threads(2)
    _lower_priority()
    import cv2
    cv2.setNumThreads(2)
    os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        print(f"Could not open video: {video}")
        return 2
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    step = max(1, int(round(video_fps / fps)))     # exactly as the Python replay samples
    frames, idx = [], 0
    while cap.grab():
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            name = "f%05d.jpg" % idx
            cv2.imwrite(os.path.join(out_dir, "frames", name), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            frames.append({"file": name, "t": idx / video_fps})
            if len(frames) % 200 == 0:
                print(f"{len(frames)} frames ({idx / video_fps:.0f} s)", flush=True)
        idx += 1
    with open(os.path.join(out_dir, "frames.json"), "w", encoding="utf-8") as fh:
        json.dump({"video": os.path.basename(video), "fps": fps, "frames": frames}, fh)
    print(f"Saved {len(frames)} frames.")
    return 0


def serve(frames_dir: str, port: int) -> int:
    frames_dir = os.path.abspath(frames_dir)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = path.split("?", 1)[0]
            if path.startswith("/speedtest/"):
                return os.path.join(SPEEDTEST, os.path.basename(path))
            if path == "/frames.json":
                return os.path.join(frames_dir, "frames.json")
            if path.startswith("/frames/"):
                return os.path.join(frames_dir, "frames", os.path.basename(path))
            return os.path.join(frames_dir, "__nothing__")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = _PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_POST(self):
            if self.path != "/result":
                self.send_error(404)
                return
            data = self.rfile.read(int(self.headers["Content-Length"]))
            with open(os.path.join(frames_dir, "browser_result.json"), "wb") as fh:
                fh.write(data)
            print("Result saved.", flush=True)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    print(f"Open http://localhost:{port}/ in a browser.", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


def _load_jsonl(path: str) -> list[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _same_views(a: list[dict], b: list[dict], tol: float = 0.05) -> bool:
    """Same usable views with the same holes, paired by content."""
    if len(a) != len(b):
        return False
    left = list(b)
    for va in a:
        for k, vb in enumerate(left):
            if va["number"] == vb["number"] and len(va["dots"]) == len(vb["dots"]) and _dots_pair(va["dots"], vb["dots"], tol):
                del left[k]
                break
        else:
            return False
    return True


def _dots_pair(da, db, tol) -> bool:
    rest = [tuple(d) for d in db]
    for x, y in da:
        best = min(rest, key=lambda d: (d[0] - x) ** 2 + (d[1] - y) ** 2)
        if ((best[0] - x) ** 2 + (best[1] - y) ** 2) ** 0.5 > tol:
            return False
        rest.remove(best)
    return True


def score_run(frames_dir: str, clip_name: str, views_path: str | None) -> int:
    from ballistica.shot_scoring import load_truth, score
    from ballistica.shot_tracker import ShotTracker
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from shot_benchmark import load_clips, report

    with open(os.path.join(frames_dir, "browser_result.json"), encoding="utf-8") as fh:
        run = json.load(fh)
    clip = next(c for c in load_clips() if c["name"] == clip_name)
    detections = [dict(sheet_track=s["sheetTrack"], sheet_number=s["sheetNumber"], t_first=s["tFirst"],
                       t_call=s["tCall"], x=s["x"], y=s["y"], off_camera_s=s["offCameraS"], n=s["n"])
                  for s in run["shots"]]
    problems = report(clip_name + " (browser)", score(detections, load_truth(os.path.join(BENCH_DIR, clip["truth"]))),
                      clip["expected"])
    frames = run["frames"]
    full = sum(1 for f in frames if f["search"] == "full")
    ms = [f["ms"]["markers"] + f["ms"]["flatten"] for f in frames]
    print(f"\n   {len(frames)} frames, {full} full searches ({100 * full / len(frames):.1f}%), "
          f"detector {sum(ms) / len(ms):.0f} ms a frame on average in this browser, {run['wall_s'] / 60:.1f} min in all")
    if views_path:
        py_frames = _load_jsonl(views_path)
        py_by_t = {round(f["t"], 4): f["views"] for f in py_frames}
        differ = [f["t"] for f in frames if not _same_views(f["views"], py_by_t.get(round(f["t"], 4), []))]
        print(f"   Frames where the browser saw different holes from the Python replay: {len(differ)} of {len(frames)}"
              + (f" (first at {', '.join('%.1f' % t for t in differ[:8])} s)" if differ else ""))
        tracker = ShotTracker()
        for f in py_frames:
            tracker.process_views(f["views"], f["t"])
        py = [(s.sheet_track, round(s.t_first, 2), round(s.t_call, 2), round(s.x_in, 2), round(s.y_in, 2)) for s in tracker.shots]
        js = [(d["sheet_track"], round(d["t_first"], 2), round(d["t_call"], 2), round(d["x"], 2), round(d["y"], 2))
              for d in detections]
        if py == js:
            print(f"   Same {len(js)} shots as the Python tracker (sheet, times, and position to 0.01 in).")
        else:
            print("   Shots differing from the Python tracker:")
            for s in sorted(set(py) ^ set(js), key=lambda r: r[1]):
                print("     %s  sheet %d first %.1fs called %.1fs (%+.2f, %+.2f)" % ((("python only" if s in py else "browser only"),) + s))
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("video")
    e.add_argument("frames_dir")
    e.add_argument("--fps", type=float, default=2.0)
    e.add_argument("--quality", type=int, default=95)
    s = sub.add_parser("serve")
    s.add_argument("frames_dir")
    s.add_argument("--port", type=int, default=8431)
    c = sub.add_parser("score")
    c.add_argument("frames_dir")
    c.add_argument("--clip", required=True)
    c.add_argument("--views")
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if args.cmd == "export":
        return export(args.video, args.frames_dir, args.fps, args.quality)
    if args.cmd == "serve":
        return serve(args.frames_dir, args.port)
    return score_run(args.frames_dir, args.clip, args.views)


if __name__ == "__main__":
    sys.exit(main())
