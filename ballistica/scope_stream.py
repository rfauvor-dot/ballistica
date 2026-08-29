"""Receiving end for a GoPro's local WiFi preview stream -- the low-
latency (~210ms) mechanism, not RTMP (2-13s, unusable for real-time use).
See SCOPE_CAMERA_DESIGN.md for the full decision record.

This targets a camera the device is DIRECTLY connected to over WiFi (the
GoPro runs its own access point; joining it is a manual step done before
any of this runs, and typically replaces normal internet connectivity on
that device for the duration -- there is no code-level way around that,
it's how the camera's hotspot works). Once connected, the camera is
reachable at a fixed local IP.

Two unrelated command APIs exist depending on camera generation --
confirmed against real GoPro/community documentation, not assumed:
  - HERO4/5/6/7 (Rick's own HERO4, the first hardware this gets tested
    against): the older "gpControl" API, plain HTTP, port 80.
  - HERO9/10/11 (the target hardware once purchased): GoPro's newer,
    officially-documented Open GoPro HTTP API, port 8080.
Both land on the same UDP video port (8554) once started.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

CAMERA_IP = "10.5.5.9"
STREAM_URL = f"udp://{CAMERA_IP}:8554"

_START_STREAM_URLS = {
    "legacy": f"http://{CAMERA_IP}/gp/gpControl/execute?p1=gpStream&a1=proto_v2&c1=restart",  # HERO4-7
    "open_gopro": f"http://{CAMERA_IP}:8080/gopro/camera/stream/start",  # HERO9+
}


def start_preview_stream(generation: str = "legacy", timeout: float = 5.0) -> bool:
    """Tells the camera to start pushing its UDP preview stream. Returns
    whether the HTTP request itself succeeded -- not proof video is
    actually flowing yet (see verify_stream() for that); the camera can
    accept this command and still fail to produce frames for other
    reasons (wrong mode, low battery, etc.), same as any device command."""
    if generation not in _START_STREAM_URLS:
        raise ValueError(f"generation must be one of {list(_START_STREAM_URLS)}")
    try:
        resp = httpx.get(_START_STREAM_URLS[generation], timeout=timeout)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@dataclass
class StreamCheckResult:
    ok: bool
    frames_read: int
    width: int | None
    height: int | None
    elapsed_s: float
    detail: str


def verify_stream(duration_s: float = 5.0, warn_after_s: float = 8.0) -> StreamCheckResult:
    """Opens the UDP stream and reads real frames for `duration_s`,
    proving actual video is arriving (not just that the start command
    was accepted). Requires opencv-python (bundles its own FFmpeg
    build -- no separate ffmpeg install needed on the machine running
    this). `warn_after_s` bounds how long this will wait for the very
    first frame before giving up -- a camera that never got the start
    command, isn't in the right mode, or isn't actually reachable on
    this network will otherwise hang here indefinitely."""
    import cv2  # local import -- this module's only user of opencv, no need to pay its ~40MB import cost elsewhere

    cap = cv2.VideoCapture(STREAM_URL, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return StreamCheckResult(False, 0, None, None, 0.0, f"Could not open {STREAM_URL} at all.")

    start = time.monotonic()
    frames_read = 0
    width = height = None
    try:
        while True:
            elapsed = time.monotonic() - start
            if frames_read == 0 and elapsed > warn_after_s:
                return StreamCheckResult(
                    False, 0, None, None, elapsed,
                    f"No frames after {warn_after_s:.0f}s -- camera may not be in preview mode, "
                    f"the start command may not have reached it, or this device isn't actually "
                    f"on the camera's own WiFi network.",
                )
            if elapsed > duration_s and frames_read > 0:
                break
            ok, frame = cap.read()
            if ok and frame is not None:
                frames_read += 1
                if width is None:
                    height, width = frame.shape[:2]
    finally:
        cap.release()

    fps = frames_read / max(elapsed, 0.001)
    return StreamCheckResult(
        True, frames_read, width, height, elapsed,
        f"{frames_read} real frames decoded in {elapsed:.1f}s (~{fps:.1f} fps), {width}x{height}.",
    )


def save_sample_frame(out_path: str, timeout_s: float = 8.0) -> bool:
    """Grabs one frame and writes it as a JPEG -- the simplest possible
    real proof of a working pipeline: a human looking at an actual photo
    pulled live off the camera, not just a log line claiming success."""
    import cv2

    cap = cv2.VideoCapture(STREAM_URL, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False
    start = time.monotonic()
    try:
        while time.monotonic() - start < timeout_s:
            ok, frame = cap.read()
            if ok and frame is not None:
                return bool(cv2.imwrite(out_path, frame))
        return False
    finally:
        cap.release()
