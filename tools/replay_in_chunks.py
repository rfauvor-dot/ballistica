"""Replay a long range video through the shot detector GENTLY: a few threads,
low priority, in short chunks with a pause between them, saving progress after
every chunk so an interrupted run picks up where it stopped.

Same detector and same result as tools/live_shot_replay.py; use this one when
the machine shouldn't be worked hard for half an hour, or when a run might be
cut off.

    python tools/replay_in_chunks.py VIDEO OUTDIR [--chunk-s 60] [--pause-s 15] [--threads 2]
                                     [--fps 2] [--scale 1.0] [--ppi 100] [--n-confirm 3] [--restart]

Run the same command again to resume. OUTDIR holds:
    progress.json   where it has got to (updated after every chunk; human-readable)
    progress.pkl    the detector's memory at that point (what a resume loads)
    shotNNN_*.jpg   the stills for each shot called so far
    summary.json    written only when the whole video is done
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time


def _limit_threads(threads: int) -> None:
    """Must run before OpenCV is imported: these are read at load time."""
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(threads)
    os.environ["OPENCV_FFMPEG_THREADS"] = str(threads)                       # video decoding
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"threads;{threads}"


def _lower_priority() -> None:
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(10)
    except Exception:
        pass   # running at normal priority is fine


def _write_atomic(path: str, data: bytes) -> None:
    """Never leaves a half-written progress file behind if the run is cut off mid-write."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--chunk-s", type=float, default=60.0, help="seconds of VIDEO per chunk")
    ap.add_argument("--pause-s", type=float, default=15.0, help="rest between chunks")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--ppi", type=float, default=100.0)
    ap.add_argument("--n-confirm", type=int, default=3)
    ap.add_argument("--restart", action="store_true", help="ignore saved progress and start over")
    args = ap.parse_args()

    _limit_threads(args.threads)
    _lower_priority()
    import cv2
    import numpy as np
    cv2.setNumThreads(args.threads)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ballistica.shot_tracker import ShotTracker, TrackerConfig, _marker_detector

    os.makedirs(args.out_dir, exist_ok=True)
    pkl_path = os.path.join(args.out_dir, "progress.pkl")
    json_path = os.path.join(args.out_dir, "progress.json")
    settings = dict(video=os.path.abspath(args.video), fps=args.fps, scale=args.scale, ppi=args.ppi, n_confirm=args.n_confirm)

    tracker = ShotTracker(TrackerConfig(ppi=args.ppi, n_confirm=args.n_confirm))
    next_idx, decode, wall_before, chunks_done = 0, [], 0.0, 0
    if os.path.exists(pkl_path) and not args.restart:
        with open(pkl_path, "rb") as fh:
            saved = pickle.load(fh)
        if saved["settings"] != settings:
            print("Saved progress in this folder is for different settings or a different video. "
                  "Use another folder, or --restart.")
            return 2
        tracker.__dict__.update(saved["tracker"])
        tracker._detector = _marker_detector()
        next_idx, decode, wall_before, chunks_done = saved["next_idx"], saved["decode"], saved["wall_s"], saved["chunks_done"]
        print(f"Resuming at {next_idx / saved['video_fps']:.0f}s of video ({len(tracker.shots)} shots so far).", flush=True)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"Could not open video: {args.video}")
        return 2
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(round(video_fps / args.fps)))
    frames_per_chunk = max(step, int(round(args.chunk_s * video_fps)))
    if next_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, next_idx)
    idx = next_idx
    wall0 = time.time()
    finished = False

    def save_progress(done: bool) -> None:
        state = {k: v for k, v in tracker.__dict__.items() if k != "_detector"}
        _write_atomic(pkl_path, pickle.dumps(dict(
            settings=settings, tracker=state, next_idx=idx, decode=decode, video_fps=video_fps,
            wall_s=wall_before + time.time() - wall0, chunks_done=chunks_done), protocol=pickle.HIGHEST_PROTOCOL))
        _write_atomic(json_path, json.dumps(dict(
            updated=time.strftime("%Y-%m-%d %H:%M:%S"), finished=done, chunks_done=chunks_done,
            video_seconds_done=round(idx / video_fps, 1), video_seconds_total=round(total_frames / video_fps, 1),
            shots_so_far=len(tracker.shots), frames_processed=tracker.frames_processed,
            working_minutes=round((wall_before + time.time() - wall0) / 60, 1)), indent=1).encode())

    while not finished:
        chunk_end = idx + frames_per_chunk
        while idx < chunk_end:
            t_dec = time.time()
            if not cap.grab():
                finished = True
                break
            if idx % step:
                idx += 1
                continue
            ok, frame = cap.retrieve()
            decode.append(time.time() - t_dec)
            t = idx / video_fps
            idx += 1
            if not ok:
                finished = True
                break
            if args.scale != 1.0:
                frame = cv2.resize(frame, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
            for shot in tracker.process_frame(frame, t):
                sheet = cv2.resize(shot.sheet_image(), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(args.out_dir, "shot%03d_sheet.jpg" % shot.n), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
                cv2.imwrite(os.path.join(args.out_dir, "shot%03d_frame.jpg" % shot.n), shot.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                # The stills are on disk; don't carry two full images per shot in memory and in every save.
                shot.rect = shot.frame = np.zeros((1, 1, 3), np.uint8)
                print("SHOT %d  track %d  first %.1fs  called %.1fs  x=%+.2f y=%+.2f"
                      % (shot.n, shot.sheet_track, shot.t_first, t, shot.x_in, shot.y_in), flush=True)
        chunks_done += 1
        save_progress(finished)
        print("chunk %d done: %.0fs of %.0fs video, %d shots" % (
            chunks_done, idx / video_fps, total_frames / video_fps, len(tracker.shots)), flush=True)
        if not finished and args.pause_s > 0:
            time.sleep(args.pause_s)

    def stat(values):
        if not values:
            return {}
        return {"mean_ms": round(1000 * float(np.mean(values)), 1), "p95_ms": round(1000 * float(np.percentile(values, 95)), 1)}

    summary = dict(
        fps=args.fps, scale=args.scale, ppi=args.ppi, n_conf=args.n_confirm,
        frames_processed=tracker.frames_processed, sheet_frames_used=tracker.sheet_frames_used,
        sheet_frames_gated=tracker.sheet_frames_gated, wall_s=round(wall_before + time.time() - wall0, 1),
        decode=stat(decode), markers=stat(tracker.timing["markers"]),
        rectify=stat(tracker.timing["rectify"]), total=stat(tracker.timing["total"]),
        shots=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.as_dict().items()} for s in tracker.shots],
        tracks=tracker.track_summary(), threads=args.threads, chunks=chunks_done,
    )
    _write_atomic(os.path.join(args.out_dir, "summary.json"), json.dumps(summary, indent=1).encode())
    print("DONE", json.dumps({k: v for k, v in summary.items() if k != "shots"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
