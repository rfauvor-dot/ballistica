"""The range rig: the camera phone on the spotting scope and the device at the
bench (2026-11-16), for the voice equipment check ("Ballistica, equipment
check") and the Check framing button.

Three parts:

  - SHARPNESS. measure_blur() reads how smeared the corner squares' black
    edges are; frame_check() adds that to shot_tracker.check_framing().

  - THE LINK between the camera phone and the bench device, through the
    server. While the range camera is on, the phone checks in every few
    seconds (POST /v2/rig/camera/checkin); a phone that checked in within
    CAMERA_FRESH_S is "connected". When the equipment check needs a look
    through the camera, it leaves a request; the phone sees it on its next
    check-in, sends a short burst of frames (POST /v2/rig/camera/frames), and
    the waiting voice turn gets the result. No video is streamed or stored.

    Kept in this process's memory, like the rate limiter's counts: the app
    runs as ONE process on Render. Running several processes (or a restart
    mid-check) would lose it -- the check then says the camera didn't answer.
    See RISK_REGISTER.md.

  - LIVE SHOT CALLS (2026-11-16). The live page on the camera phone
    (/live-shots) finds and calls shots itself, in its browser; each shot it
    calls is posted here (POST /v2/rig/shots), and the bench device collects
    new ones (GET /v2/rig/shots?after=N) and speaks them. Same in-memory
    meeting point, so the same one-process caveat; only the last SHOTS_KEPT
    shots are kept, and nothing is written to the database.

  - CameraLink: what the voice code (cli.py) uses -- one user's view of the
    link, so cli.py never touches the relay or HTTP directly, and tests can
    hand it a fake.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import target
from .shot_tracker import _marker_detector, check_framing

# ------------------------------------------------------------------ sharpness
# Sharpness as blur_ratio: how far a corner square's black edge is smeared
# (the blur's sigma, in pixels) as a share of one cell of the square's 6x6
# pattern -- so it doesn't depend on how far the camera is zoomed. A crisp
# view is well under 0.1. The November 13 footage, which the tracker handled
# but which looked soft, measured 0.17-0.22; blurring those frames by a
# further 2 px took them to ~0.27-0.31. The bars below are a judgment from
# that one clip, to revisit with ground-tripod footage. Frame to frame the
# reading wanders by about +-0.02 (shimmer, compression), so a check takes
# the middle of a few frames, and focus coaching only calls a change better
# or worse beyond FOCUS_CHANGE.
SHARPNESS_GOOD = 0.15
SHARPNESS_SOFT = 0.25
FOCUS_CHANGE = 0.02


def measure_blur(gray: np.ndarray, corners) -> tuple[float, float] | None:
    """How smeared the corner squares' outer edges are. `corners`: the
    squares' corner points, as detectMarkers gives them. Returns (blur sigma
    in pixels, blur_ratio = sigma / pattern-cell width), or None if no edge
    could be read. At 7 points along each edge it reads the brightness
    across the edge, from the paper outside into the square's black border,
    and takes contrast / steepest slope -- for a blurred step edge that is
    sigma * sqrt(2 pi)."""
    g = gray.astype(np.float32)
    sigmas, cells = [], []
    for corner in corners:
        c = np.asarray(corner, np.float64).reshape(4, 2)
        side = float(np.mean([np.linalg.norm(c[k] - c[(k + 1) % 4]) for k in range(4)]))
        cell = side / 6.0          # a 4x4 square is 6 cells across, counting its black border
        if cell < 2:
            continue
        cells.append(cell)
        centre = c.mean(0)
        step = 0.25
        offsets = np.arange(-1.2 * cell, 0.8 * cell + 1e-9, step)   # outside (paper) -> inside (border)
        outside, inside = offsets < -0.9 * cell, offsets > 0.2 * cell
        for k in range(4):
            a, b = c[k], c[(k + 1) % 4]
            along = (b - a) / np.linalg.norm(b - a)
            normal = np.array([-along[1], along[0]])
            if np.dot(normal, centre - (a + b) / 2) < 0:
                normal = -normal                                       # pointing into the square
            for f in np.linspace(0.2, 0.8, 7):
                p = a + (b - a) * f
                xs = (p[0] + offsets * normal[0]).astype(np.float32).reshape(1, -1)
                ys = (p[1] + offsets * normal[1]).astype(np.float32).reshape(1, -1)
                profile = cv2.remap(g, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()
                contrast = float(np.median(profile[outside]) - profile[inside].min())
                if contrast < 40:          # no clean paper-to-black edge here (glare, edge of the picture)
                    continue
                slope = float((-np.gradient(profile, step)).max())
                if slope > 0:
                    sigmas.append(contrast / (slope * math.sqrt(2 * math.pi)))
    if not sigmas:
        return None
    sigma = float(np.median(sigmas))
    return sigma, sigma / float(np.median(cells))


def sharpness_verdict(blur_ratio: float) -> str:
    if blur_ratio < SHARPNESS_GOOD:
        return "good"
    return "soft" if blur_ratio >= SHARPNESS_SOFT else "a little soft"


def frame_check(frames: list[np.ndarray]) -> dict:
    """check_framing() on the first frame, plus `sharpness`: {blur_ratio,
    verdict ("good" | "a little soft" | "soft"), frames (how many readings
    went into it)}, the middle reading over every frame where the same sheet's
    squares were found -- or None. A soft picture adds a message and makes ok
    False; a little soft adds a note only."""
    result = check_framing(frames[0])
    result["sharpness"] = None
    number = result["sheet_number"]
    if not result["corners_found"]:
        return result
    detector = _marker_detector()
    ratios = []
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _ = detector.detectMarkers(gray)
        if ids is None:
            continue
        mine = [c for c, i in zip(corners, ids.ravel().tolist())
                if (target.marker_layout(i) or (False, None))[0] == number]
        blur = measure_blur(gray, mine)
        if blur:
            ratios.append(blur[1])
    if not ratios:
        return result
    ratio = float(np.median(ratios))
    verdict = sharpness_verdict(ratio)
    result["sharpness"] = dict(blur_ratio=round(ratio, 3), verdict=verdict, frames=len(ratios))
    if verdict == "soft":
        result["messages"].append("The picture is soft -- turn the spotting scope's focus slowly until the corner "
                                  "squares' edges look crisp.")
        if result["ok"]:
            result["ok"] = False
            result["messages"][0] = result["messages"][0].replace("Good to go.", "Not ready yet.").split(" Corner")[0]
    elif verdict == "a little soft":
        result["messages"].append("The picture is a little soft: a small focus touch-up would help.")
    return result


# ------------------------------------------------------------------ the link
CAMERA_FRESH_S = 10.0     # a camera that checked in this recently is "connected"
CHECKIN_SLOW_MS = 3000    # how often an idle camera phone checks in
CHECKIN_FAST_MS = 1000    # ...and while an equipment check is running (focus coaching wants quick answers)
FAST_WINDOW_S = 120.0
REQUEST_EXPIRE_S = 20.0   # a request the phone never picked up is dropped


@dataclass
class _UserRig:
    camera_device: str | None = None
    camera_seen: float = 0.0
    request_id: str | None = None
    request_at: float = 0.0
    last_request_at: float = 0.0
    results: dict = field(default_factory=dict)     # request_id -> (result, device_id)
    calling: bool = False                           # the camera phone is calling shots live
    shots: list = field(default_factory=list)       # the last SHOTS_KEPT shots called, oldest first
    shot_seq: int = 0                               # number of the last shot posted (never reused)


class RigRelay:
    """Per-user meeting point for the camera phone and the voice turn. Thread
    safe: voice turns wait in worker threads while the phone's requests
    arrive on others."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Condition()
        self._users: dict[str, _UserRig] = {}

    def _user(self, user_id: str) -> _UserRig:
        rig = self._users.get(user_id)
        if rig is None:
            rig = self._users[user_id] = _UserRig()
        return rig

    def camera_checkin(self, user_id: str, device_id: str, calling: bool = False) -> dict:
        """The camera phone saying "still here" (and whether it is calling
        shots live). Returns {check_request: id or None, next_checkin_ms}."""
        now = self._clock()
        with self._lock:
            rig = self._user(user_id)
            rig.camera_device, rig.camera_seen, rig.calling = device_id, now, bool(calling)
            if rig.request_id and now - rig.request_at > REQUEST_EXPIRE_S:
                rig.request_id = None
            fast = now - rig.last_request_at < FAST_WINDOW_S
            return {"check_request": rig.request_id,
                    "next_checkin_ms": CHECKIN_FAST_MS if fast else CHECKIN_SLOW_MS}

    def camera_status(self, user_id: str) -> dict:
        with self._lock:
            rig = self._users.get(user_id)
            if rig is None or rig.camera_device is None:
                return {"connected": False, "calling": False, "device_id": None, "seen_s_ago": None}
            age = self._clock() - rig.camera_seen
            connected = age <= CAMERA_FRESH_S
            return {"connected": connected, "calling": connected and rig.calling,
                    "device_id": rig.camera_device, "seen_s_ago": round(age, 1)}

    def request_check(self, user_id: str) -> str:
        now = self._clock()
        with self._lock:
            rig = self._user(user_id)
            rig.request_id, rig.request_at, rig.last_request_at = uuid.uuid4().hex, now, now
            return rig.request_id

    def deliver(self, user_id: str, device_id: str, request_id: str, result: dict) -> bool:
        """The phone's answer to a request. False if no such request is open."""
        with self._lock:
            rig = self._users.get(user_id)
            if rig is None or rig.request_id != request_id:
                return False
            rig.request_id = None
            rig.camera_device, rig.camera_seen = device_id, self._clock()
            rig.results = {request_id: (result, device_id)}
            self._lock.notify_all()
            return True

    def wait_result(self, user_id: str, request_id: str, timeout_s: float) -> dict | None:
        deadline = time.monotonic() + timeout_s
        with self._lock:
            while True:
                rig = self._users.get(user_id)
                if rig and request_id in rig.results:
                    return rig.results.pop(request_id)[0]
                left = deadline - time.monotonic()
                if left <= 0:
                    if rig and rig.request_id == request_id:
                        rig.request_id = None
                    return None
                self._lock.wait(left)


    # ------------------------------------------------------ live shot calls
    def post_shot(self, user_id: str, device_id: str, session_id: str, shot: dict) -> int:
        """A shot the camera phone called. A repeat of one already posted
        (same session, same shot number -- the phone resends after a dropped
        connection) is ignored. Returns its sequence number."""
        with self._lock:
            rig = self._user(user_id)
            rig.camera_device, rig.camera_seen = device_id, self._clock()
            for old in rig.shots:
                if old["session_id"] == session_id and old["n"] == shot["n"]:
                    return old["seq"]
            rig.shot_seq += 1
            entry = dict(shot, seq=rig.shot_seq, session_id=session_id, say=shot_sentence(shot))
            rig.shots = (rig.shots + [entry])[-SHOTS_KEPT:]
            return rig.shot_seq

    def shots_since(self, user_id: str, after: int) -> dict:
        """Shots posted after sequence number `after` (-1: none, just the
        current number -- where a newly opened bench device starts from)."""
        with self._lock:
            rig = self._users.get(user_id)
            last = rig.shot_seq if rig else 0
            shots = [] if rig is None or after < 0 else [dict(s) for s in rig.shots if s["seq"] > after]
            return {"shots": shots, "last": last}


SHOTS_KEPT = 200


def _inches(v: float) -> str:
    v = abs(v)
    return "less than a tenth of an inch" if v < 0.05 else f"{v:.1f} inch" + ("" if round(v, 1) == 1.0 else "es")


def shot_sentence(shot: dict) -> str:
    """What the bench device says for a shot: where the hole is from the
    sheet's crosshair. Plain offsets only -- never which way to turn a knob."""
    parts = [f"Shot {shot['n']}"]
    if shot.get("sheet_number"):
        parts.append(f"sheet {shot['sheet_number']}")
    x, y = float(shot["x_in"]), float(shot["y_in"])
    if abs(x) < 0.05 and abs(y) < 0.05:
        where = "dead center"
    else:
        bits = []
        if abs(y) >= 0.05:
            bits.append(f"{_inches(y)} {'high' if y > 0 else 'low'}")
        if abs(x) >= 0.05:
            bits.append(f"{_inches(x)} {'right' if x > 0 else 'left'}")
        where = ", ".join(bits)
    sentence = ", ".join(parts) + ": " + where + "."
    if shot.get("off_camera_s"):
        sentence += " Found after the sheet was out of view, so it may be older."
    return sentence


relay = RigRelay()


class CameraLink:
    """One user's view of the rig, for cli.py's equipment check."""

    def __init__(self, relay: RigRelay, user_id: str, this_device: str | None, wait_s: float = 12.0) -> None:
        self._relay, self._user_id, self.this_device, self._wait_s = relay, user_id, this_device, wait_s

    def status(self) -> dict:
        """{connected, same_device (the camera is this very device), seen_s_ago}."""
        s = self._relay.camera_status(self._user_id)
        s["same_device"] = bool(s["device_id"] and self.this_device and s["device_id"] == self.this_device)
        return s

    def look(self) -> dict | None:
        """Ask the camera phone for frames and wait for frame_check()'s result.
        None if it didn't answer in time."""
        request_id = self._relay.request_check(self._user_id)
        return self._relay.wait_result(self._user_id, request_id, self._wait_s)
