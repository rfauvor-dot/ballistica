"""Synthetic range-camera frames for testing the shot tracker without a video.

Renders the real printable sheet, punches holes in it at known positions, and
places it in a camera frame at a chosen position, size and tilt. Soft by
default (a slight blur, like footage through a spotting scope); `sharp=True`
gives a crisp frame.

Hole positions are inches from the crosshair, x right, y up -- the same
convention the tracker reports in.
"""
from __future__ import annotations

import cv2
import numpy as np

from ballistica import target

PPI = 100
FRAME_SIZE = (1500, 1400)          # (height, width) of the camera frame, pixels
BACKGROUND_GRAY = 120
HOLE_DIAMETER_IN = 0.224
# Real footage through the spotting scope is flatter than the printed sheet:
# the light-gray ring and crosshair sit well under the detector's darkness
# threshold there. At full contrast (1.0) they sit above it.
VIDEO_CONTRAST = 0.6


def sheet_with_holes(holes: list[tuple[float, float]], diameter_in: float = HOLE_DIAMETER_IN,
                     number: int | None = None) -> np.ndarray:
    sheet = target.render_sheet(dpi=PPI, number=number).copy()
    for x, y in holes:
        centre = (int(round((4.25 + x) * PPI)), int(round((5.5 - y) * PPI)))
        cv2.circle(sheet, centre, int(round(diameter_in / 2 * PPI)), 25, -1, cv2.LINE_AA)
    return sheet


def camera_frame(holes: list[tuple[float, float]], offset_px: tuple[float, float] = (250, 180),
                 scale: float = 1.0, rotate_deg: float = 0.0, sharp: bool = False,
                 contrast: float = VIDEO_CONTRAST, diameter_in: float = HOLE_DIAMETER_IN,
                 number: int | None = None) -> np.ndarray:
    """A BGR camera frame showing the sheet with its top-left corner at
    `offset_px`, drawn at `scale` camera pixels per sheet pixel. `contrast`
    1.0 is the sheet exactly as printed (white paper, full-strength gray ring
    and crosshair); the default mimics the flatter look of real footage.
    `number` renders that numbered sheet (None: the original unnumbered one)."""
    sheet = sheet_with_holes(holes, diameter_in, number)
    h, w = sheet.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, scale)
    M[0, 2] += offset_px[0] - (w / 2 - scale * w / 2)
    M[1, 2] += offset_px[1] - (h / 2 - scale * h / 2)
    frame = cv2.warpAffine(sheet, M, (FRAME_SIZE[1], FRAME_SIZE[0]), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
                           borderValue=BACKGROUND_GRAY)
    if not sharp:
        frame = cv2.GaussianBlur(frame, (0, 0), 1.8)
    if contrast != 1.0:
        frame = cv2.convertScaleAbs(frame, alpha=contrast, beta=255 * (1 - contrast) * 0.6)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


def blank_frame() -> np.ndarray:
    """A frame with no sheet in it (the camera swung away)."""
    return np.full((FRAME_SIZE[0], FRAME_SIZE[1], 3), BACKGROUND_GRAY, np.uint8)


class Session:
    """Feeds a tracker a scripted sequence at a steady frame rate and keeps
    every shot it called."""

    def __init__(self, tracker, fps: float = 2.0) -> None:
        self.tracker, self.dt, self.t = tracker, 1.0 / fps, 0.0
        self.called = []

    def show(self, holes, frames: int = 4, **view) -> list:
        """Show the sheet with these holes for `frames` frames; returns the
        shots called during them."""
        new = []
        for _ in range(frames):
            new += self.tracker.process_frame(camera_frame(list(holes), **view), self.t)
            self.t += self.dt
        self.called += new
        return new

    def look_away(self, frames: int = 4) -> None:
        for _ in range(frames):
            self.called += self.tracker.process_frame(blank_frame(), self.t)
            self.t += self.dt
