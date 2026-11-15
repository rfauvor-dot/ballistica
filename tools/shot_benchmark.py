"""Score the live shot detector against the labeled range clips.

    python tools/shot_benchmark.py                      # every clip whose video is available
    python tools/shot_benchmark.py --clip NAME          # one clip
    python tools/shot_benchmark.py --score SUMMARY.json --clip NAME   # score a run already made
    python tools/shot_benchmark.py --baseline           # score the stored baseline results (no video needed)

Clips are listed in data/shot_benchmark/clips.json. The videos are not in the
repo: pass --clips-dir, or set BALLISTICA_CLIPS_DIR, to the folder holding them.
Exits 1 if any clip does worse than its 'expected' numbers, so this can gate a
change. A full run of the 22-minute clip takes about half an hour.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ballistica.shot_scoring import Score, load_truth, score  # noqa: E402

BENCH_DIR = os.path.join(ROOT, "data", "shot_benchmark")
# Lower is better for these; a run may not exceed the clip's expected value.
_MUST_NOT_RISE = ("missed", "late", "duplicates", "false_calls")


def load_clips() -> list[dict]:
    with open(os.path.join(BENCH_DIR, "clips.json"), encoding="utf-8") as fh:
        return json.load(fh)["clips"]


def regressions(result: Score, expected: dict) -> list[str]:
    """What got worse than the clip's expected numbers (empty = fine)."""
    problems = [f"{key}: {getattr(result, key)} (expected at most {expected[key]})"
                for key in _MUST_NOT_RISE if key in expected and getattr(result, key) > expected[key]]
    if "matched" in expected and result.matched < expected["matched"]:
        problems.append(f"matched: {result.matched} (expected at least {expected['matched']})")
    return problems


def report(name: str, result: Score, expected: dict) -> list[str]:
    h = result.headline()
    print(f"\n== {name}")
    print(f"   real shots {h['true_shots']}: matched {h['matched']}, missed {h['missed']}, late {h['late']}"
          f"   |   duplicates {h['duplicates']}, false calls {h['false_calls']}")
    print(f"   position error mean {h['mean_position_error_in']} in, max {h['max_position_error_in']} in"
          f"   |   median call delay {h['median_call_delay_s']} s")
    for row in result.rows:
        where = "sheet %-9s %6.0fs (%+.2f,%+.2f)" % (row["sheet"], row["t"], row["x"], row["y"])
        if row["detection"] is None:
            print("   " + where + "  MISSED")
        else:
            print("   " + where + "  shot#%-3d err %.2f in, first seen %+.1fs, called +%.1fs%s" % (
                row["detection"], row["position_error_in"], row["first_seen_after_s"], row["call_delay_s"],
                "  LATE" if row["late"] else ""))
    for extra in result.extras:
        print("   EXTRA shot#%-3d first %.0fs (%+.2f,%+.2f)  %s" % (
            extra["detection"], extra["t_first"], extra["x"], extra["y"], extra["kind"].upper()))
    problems = regressions(result, expected)
    print("   " + ("WORSE THAN EXPECTED: " + "; ".join(problems) if problems else "as expected or better"))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips-dir", default=os.environ.get("BALLISTICA_CLIPS_DIR", ""))
    ap.add_argument("--clip", help="run only this clip")
    ap.add_argument("--out", default=os.path.join(ROOT, "bench_out"), help="where stills and summary.json go")
    ap.add_argument("--score", metavar="SUMMARY_JSON", help="score an existing run instead of replaying (needs --clip)")
    ap.add_argument("--baseline", action="store_true", help="score each clip's stored baseline results")
    args = ap.parse_args()

    clips = [c for c in load_clips() if not args.clip or c["name"] == args.clip]
    if not clips:
        print(f"No clip named {args.clip!r} in clips.json.")
        return 2
    if args.score and len(clips) != 1:
        print("--score needs --clip NAME.")
        return 2

    failed = ran = 0
    for clip in clips:
        truth = load_truth(os.path.join(BENCH_DIR, clip["truth"]))
        if args.score or args.baseline:
            path = args.score or os.path.join(BENCH_DIR, clip["baseline"])
            with open(path, encoding="utf-8") as fh:
                detections = json.load(fh)["shots"]
        else:
            video = os.path.join(args.clips_dir, clip["video"]) if args.clips_dir else ""
            if not video or not os.path.exists(video):
                print(f"\n== {clip['name']}: SKIPPED, video {clip['video']} not found"
                      + (f" in {args.clips_dir}" if args.clips_dir else " (no --clips-dir / BALLISTICA_CLIPS_DIR)"))
                continue
            from live_shot_replay import replay
            s = clip["settings"]
            summary = replay(video, os.path.join(args.out, clip["name"]), s["fps"], s["scale"], s["ppi"], s["n_confirm"])
            detections = summary["shots"]
        ran += 1
        failed += bool(report(clip["name"], score(detections, truth), clip["expected"]))
    if not ran:
        print("\nNothing was scored.")
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
