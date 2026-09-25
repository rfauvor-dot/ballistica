"""Target photo measurement: find bullet holes in a photo of a paper target
and measure the group (2026-09-24).

Built without a scope, on purpose: what Ballistica does with a picture does
not depend on how the picture was taken. A phone photo of a target after a
string works the same as one taken through a spotting scope.

Two modes:
  - SHEET (precise): the shooter prints the Ballistica target sheet (a US
    Letter page with four ArUco corner markers of a known physical size).
    The markers give the paper's exact position and scale, so the photo is
    corrected for camera angle and every hole comes out in real inches.
  - FREE (approximate): any other target. There is no known scale, so it is
    estimated from the hole size vs the bullet diameter, and the result is
    flagged approximate.

Deliberate limits, stated so nobody over-trusts this:
  - Holes are found as DARK blobs against lighter paper (a dark backer shows
    through the hole). A light-backed hole can be missed, and so can most of a
    cluster of heavily overlapping holes: on synthetic photos about 96% of
    random groups come out exactly right when holes are >= 0.2 in apart, ~75%
    when they overlap by more than half. The web UI therefore lets the shooter
    tap to add or remove holes, and the group math takes whatever list the
    shooter confirms. (The printed ring/crosshair are light gray so a hole on
    them still shows; with a solid black ring, 22 of 40 random groups miscounted.)
  - Group size is center-to-center extreme spread. Offsets from the aim point
    are reported neutrally ("right / low of the aim point"); this module does
    NOT turn them into dial directions -- a wrong-way correction at the line is
    worse than none, so that convention is left for the shooter to confirm.
  - Tuned on synthetic photos (tests/test_target.py). Real photos are the next
    validation; expect to tune thresholds.
"""
from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np

# ------------------------------------------------------------------ sheet
PAPER_W_IN, PAPER_H_IN = 8.5, 11.0
MARKER_IN = 1.0
# Top-left corner of each marker, in sheet inches (origin top-left, y down).
_MARKER_ORIGINS = {0: (0.5, 0.5), 1: (7.0, 0.5), 2: (0.5, 9.5), 3: (7.0, 9.5)}
AIM_IN = (4.25, 5.5)
_RING_RADIUS_IN, _RING_WIDTH_IN = 1.0, 0.12
_CROSS_HALF_IN, _CROSS_WIDTH_IN = 0.35, 0.03
# The bull ring and crosshair are printed LIGHT gray, not black: a hole punched through solid black
# ink is dark-on-dark and invisible, but a hole on light gray is still clearly darker than its
# surroundings. Gray 190 is far enough from the ~40-gray hole to detect and far enough from paper
# (~230+) that the detector's darkness threshold never mistakes the ink itself for a hole.
_GUIDE_GRAY = 190
# Region where shots are expected; everything else (title, markers, scale bar) is ignored.
_SHOT_AREA_IN = (0.75, 1.75, 7.75, 9.25)  # x0, y0, x1, y1
_DICT = cv2.aruco.DICT_4X4_50
DETECT_PPI = 200          # working resolution of the rectified sheet
OVERLAY_PPI = 200         # resolution of the picture returned to the UI (the app zooms into the group)


def _aruco_dict():
    return cv2.aruco.getPredefinedDictionary(_DICT)


def _draw_sheet_features(img: np.ndarray, dpi: int) -> None:
    """The printed bull ring and crosshair (light gray, see _GUIDE_GRAY)."""
    px = lambda inches: int(round(inches * dpi))
    center = (px(AIM_IN[0]), px(AIM_IN[1]))
    cv2.circle(img, center, px(_RING_RADIUS_IN), _GUIDE_GRAY, max(1, px(_RING_WIDTH_IN)), cv2.LINE_AA)
    t = max(1, px(_CROSS_WIDTH_IN))
    cv2.line(img, (center[0] - px(_CROSS_HALF_IN), center[1]), (center[0] + px(_CROSS_HALF_IN), center[1]), _GUIDE_GRAY, t)
    cv2.line(img, (center[0], center[1] - px(_CROSS_HALF_IN)), (center[0], center[1] + px(_CROSS_HALF_IN)), _GUIDE_GRAY, t)


def render_sheet(dpi: int = 300) -> np.ndarray:
    """The printable target sheet as a grayscale image (US Letter at `dpi`)."""
    px = lambda inches: int(round(inches * dpi))
    img = np.full((px(PAPER_H_IN), px(PAPER_W_IN)), 255, np.uint8)
    d = _aruco_dict()
    for marker_id, (ox, oy) in _MARKER_ORIGINS.items():
        m = cv2.aruco.generateImageMarker(d, marker_id, px(MARKER_IN))
        img[px(oy):px(oy) + m.shape[0], px(ox):px(ox) + m.shape[1]] = m
    cv2.putText(img, "BALLISTICA TARGET SHEET", (px(1.7), px(0.85)), cv2.FONT_HERSHEY_SIMPLEX,
                dpi / 150.0, 0, max(1, dpi // 100), cv2.LINE_AA)
    cv2.putText(img, "Print at 100% (Actual size). Hang with this edge UP.", (px(1.7), px(1.2)),
                cv2.FONT_HERSHEY_SIMPLEX, dpi / 330.0, 0, max(1, dpi // 150), cv2.LINE_AA)
    _draw_sheet_features(img, dpi)
    # 2-inch scale bar with half-inch ticks: check it measures 2.00" after printing.
    y = px(10.2)
    cv2.line(img, (px(3.25), y), (px(5.25), y), 0, max(1, dpi // 100))
    for k in range(5):
        x = px(3.25 + 0.5 * k)
        cv2.line(img, (x, y - px(0.08)), (x, y + px(0.08)), 0, max(1, dpi // 100))
    cv2.putText(img, "2 in", (px(5.4), y + px(0.05)), cv2.FONT_HERSHEY_SIMPLEX, dpi / 330.0, 0,
                max(1, dpi // 150), cv2.LINE_AA)
    return img


def render_sheet_pdf(dpi: int = 300) -> bytes:
    """The sheet as a single-page PDF at exactly Letter size, so it prints at
    100% (a bare PNG gets scaled to fit by many print dialogs, which would
    silently wreck the scale the whole measurement depends on)."""
    img = render_sheet(dpi)
    h, w = img.shape
    data = zlib.compress(img.tobytes(), 9)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /XObject << /Im0 5 0 R >> >> >>",
    ]
    content = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(data))
        + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# --------------------------------------------------------------- calibers
_CALIBER_TABLE = [
    (r"22 ?lr|\.22 ?long|22 long rifle|\.22$|^22$", 0.223), (r"17 ?hmr|\.17", 0.172),
    (r"6 ?mm ?arc|6mm|\.243|243", 0.243), (r"6\.5|\.264", 0.264), (r"5\.56|\.223|223|22-250|5\.7", 0.224),
    (r"300 ?b(lackout|lk)|\.308|308|7\.62|300 win|\.30", 0.308), (r"9 ?mm|\.355|380", 0.355),
    (r"\.45|45 acp|45 auto", 0.451), (r"\.270|270", 0.277), (r"7 ?mm|\.284", 0.284),
    (r"\.338|338", 0.338), (r"\.40|40 s&w|10 ?mm", 0.400), (r"50 ?bmg|\.50", 0.510),
]


def caliber_inches(text: str | None) -> float | None:
    """Best-effort bullet diameter (inches) from a caliber string like
    "6mm ARC" or ".223 Wylde". None if unrecognized -- callers must then ask.
    Only used to sanity-check the scale (SHEET) or estimate it (FREE)."""
    if not text:
        return None
    low = text.strip().lower()
    for pattern, diameter in _CALIBER_TABLE:
        if re.search(pattern, low):
            return diameter
    m = re.search(r"0?\.(\d{2,3})\b", low)
    if m:
        value = float("0." + m.group(1))
        if 0.15 <= value <= 0.6:
            return value
    return None


# ------------------------------------------------------------------ math
def group_stats(points_in: list[tuple[float, float]], distance_yd: float,
                aim_in: tuple[float, float] | None = None, bullet_in: float | None = None) -> dict:
    """Group measurements from hole centers in inches (x right, y DOWN).
    Extreme spread is center-to-center, the usual way to compare loads."""
    n = len(points_in)
    out: dict = {"count": n, "distance_yd": distance_yd}
    if n == 0:
        return out
    pts = np.asarray(points_in, dtype=float)
    cx, cy = pts.mean(axis=0)
    out["center_in"] = [round(float(cx), 3), round(float(cy), 3)]
    if distance_yd and distance_yd > 0:
        in_per_moa = 1.0472 * distance_yd / 100.0
        in_per_mil = 0.036 * distance_yd
    else:
        in_per_moa = in_per_mil = None
    if n >= 2:
        diffs = pts[:, None, :] - pts[None, :, :]
        es = float(np.sqrt((diffs ** 2).sum(axis=2)).max())
        radii = np.sqrt(((pts - [cx, cy]) ** 2).sum(axis=1))
        out["extreme_spread_in"] = round(es, 3)
        out["mean_radius_in"] = round(float(radii.mean()), 3)
        out["width_in"] = round(float(pts[:, 0].max() - pts[:, 0].min()), 3)
        out["height_in"] = round(float(pts[:, 1].max() - pts[:, 1].min()), 3)
        if bullet_in:
            out["outside_spread_in"] = round(es + bullet_in, 3)
        if in_per_moa:
            out["extreme_spread_moa"] = round(es / in_per_moa, 2)
            out["extreme_spread_mil"] = round(es / in_per_mil, 2)
            out["mean_radius_moa"] = round(float(radii.mean()) / in_per_moa, 2)
    if aim_in is not None:
        dx, dy = float(cx - aim_in[0]), float(cy - aim_in[1])
        out["offset_from_aim_in"] = {
            "right_in": round(dx, 3), "down_in": round(dy, 3),   # negative = left / up
            "description": f"{abs(dx):.2f} in {'right' if dx >= 0 else 'left'}, "
                           f"{abs(dy):.2f} in {'low' if dy >= 0 else 'high'} of the aim point",
        }
        if in_per_moa:
            out["offset_from_aim_moa"] = {"right_moa": round(dx / in_per_moa, 2), "down_moa": round(dy / in_per_moa, 2)}
    return out


# ------------------------------------------------------------- detection
@dataclass
class Hole:
    x_in: float
    y_in: float
    diameter_in: float
    merged: bool = False   # split out of a blob that looked like overlapping holes


@dataclass
class TargetAnalysis:
    mode: str                                  # "sheet" | "free"
    scale_ppi: float | None                    # pixels per inch of the rectified/working image
    holes: list[Hole] = field(default_factory=list)
    aim_in: tuple[float, float] | None = None
    warnings: list[str] = field(default_factory=list)
    markers_found: int = 0
    overlay_jpeg: bytes = b""                  # clean picture (no markings) for the UI to draw on
    overlay_ppi: float | None = None           # overlay pixels per inch


def _odd(n: int) -> int:
    n = max(3, int(n))
    return n if n % 2 else n + 1


def _dark_blobs(gray: np.ndarray, ppi: float | None, expected_d_in: float | None,
                ignore: np.ndarray | None) -> list[tuple]:
    """Dark blobs as (cx_px, cy_px, diameter_px, circularity, area_px, pts_xy).
    Background = a large median of a downsampled copy (holes are far smaller
    than the window, so the median is the paper); a blob is a place clearly
    darker than that paper. Regions the caller marks `ignore` (title, corner
    markers) are treated as paper. pts_xy holds the
    blob's pixels so overlapping holes can be pulled apart later."""
    g = cv2.GaussianBlur(gray, (0, 0), 1.2)
    ign = ignore.astype(bool) if ignore is not None and ignore.any() else None
    h, w = g.shape
    down = 4
    small = cv2.resize(g, (max(8, w // down), max(8, h // down)), interpolation=cv2.INTER_AREA)
    window = _odd(min(small.shape) // 6 if ppi is None else int(0.9 * ppi / down))
    bg = cv2.resize(cv2.medianBlur(small, min(window, 101)), (w, h), interpolation=cv2.INTER_LINEAR)
    g_use = g.copy()
    if ign is not None:
        g_use[ign] = bg[ign]          # areas the caller knows are not target (markers, title): never dark
    dark = bg.astype(np.int16) - g_use.astype(np.int16)
    thr = np.maximum(40, (0.30 * bg).astype(np.int16))
    mask = (dark > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = []
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < 12:
            continue
        x0, y0 = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        sel = labels[y0:y0 + bh, x0:x0 + bw] == i
        contours, _ = cv2.findContours(sel.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        perim = cv2.arcLength(c, True)
        circ = 4 * math.pi * area / (perim * perim) if perim > 0 else 0.0
        ys, xs = np.nonzero(sel)
        weights = dark[y0:y0 + bh, x0:x0 + bw][sel].astype(np.float64).clip(min=1)
        cx = float((xs * weights).sum() / weights.sum()) + x0
        cy = float((ys * weights).sum() / weights.sum()) + y0
        pts = np.column_stack([xs + x0, ys + y0]).astype(np.float32)
        blobs.append((cx, cy, 2.0 * math.sqrt(area / math.pi), circ, area, pts))
    return blobs


def _elongation(pts: np.ndarray) -> float:
    """Major/minor axis ratio of a blob's pixels (1.0 = round)."""
    if len(pts) < 5:
        return 1.0
    (_, _), (w, h), _ = cv2.minAreaRect(pts)
    return max(w, h) / max(1.0, min(w, h))


def _select_holes(blobs, ppi: float | None, expected_d_in: float | None, image_w_px: int):
    """Keep blobs of a plausible hole size; pull apart ones that look like
    overlapping holes (a tight group makes these common). Returns
    (list of (cx, cy, d_px, merged), warnings)."""
    warnings: list[str] = []
    if not blobs:
        return [], warnings
    if ppi and expected_d_in:
        lo, hi = 0.5 * expected_d_in * ppi, 3.4 * expected_d_in * ppi
    elif ppi:
        lo, hi = 0.10 * ppi, 1.6 * ppi
    else:  # no scale at all: hole diameter is roughly 1-6% of the picture width
        lo, hi = 0.008 * image_w_px, 0.10 * image_w_px
    cand = [b for b in blobs if lo <= b[2] <= hi]   # irregular blobs are kept: a merged cluster is never round
    if not cand:
        return [], warnings
    # Typical single-hole size: the round, plausibly-sized blobs; else the expected bullet.
    # measured holes run ~15% larger than the bullet (blur + threshold), so calibrate the nominal area
    exp_area = 1.15 * math.pi / 4 * (expected_d_in * ppi) ** 2 if (ppi and expected_d_in) else None
    singles = [b for b in cand if b[3] >= 0.7 and (exp_area is None or 0.5 * exp_area <= b[4] <= 1.6 * exp_area)]
    rounds = [b for b in cand if b[3] >= 0.7]
    if singles:
        ref = float(np.median([b[4] for b in singles]))
    elif rounds:      # nothing matches the stated bullet (wrong print scale or caliber): trust the round blobs
        ref = float(np.median([b[4] for b in rounds]))
    else:
        ref = exp_area or float(np.median([b[4] for b in cand]))
    out = []
    n_split = 0
    for cx, cy, d, circ, area, pts in cand:
        ratio = area / ref if ref > 0 else 1.0
        many = ratio >= 1.6 or (ratio >= 1.25 and _elongation(pts) >= 1.3)
        if many and ratio <= 7:
            k = max(2, int(ratio + 0.3))
            out.extend(_split_blob(pts, k, math.sqrt(ref / math.pi) * 2.0))
            n_split += 1
        elif circ >= 0.5:
            out.append((cx, cy, d, False))
    if n_split:
        warnings.append(f"{n_split} overlapping-hole cluster{'s were' if n_split != 1 else ' was'} pulled "
                        "apart automatically -- check the marked holes.")
    return out, warnings


def _split_blob(pts: np.ndarray, k: int, single_d_px: float):
    """k hole centers for one merged blob: k-means on its pixels. Each comes
    back flagged merged so the UI can mark it for a check."""
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2)
    _, _, centers = cv2.kmeans(pts, k, None, crit, 5, cv2.KMEANS_PP_CENTERS)
    return [(float(cx), float(cy), single_d_px, True) for cx, cy in centers]


def _decode_rectified(img_bgr: np.ndarray, marker_in: float):
    """Detect the sheet's markers and return (H_image_to_inches, markers_found,
    rms_error_in) or None. Uses every corner of every detected marker."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    detector = cv2.aruco.ArucoDetector(_aruco_dict(), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return None
    s = marker_in / MARKER_IN
    src, dst = [], []
    found = 0
    for c, marker_id in zip(corners, ids.ravel().tolist()):
        if marker_id not in _MARKER_ORIGINS:
            continue
        ox, oy = _MARKER_ORIGINS[marker_id]
        m = MARKER_IN
        phys = [(ox, oy), (ox + m, oy), (ox + m, oy + m), (ox, oy + m)]   # TL, TR, BR, BL of the printed marker
        src.extend(c.reshape(4, 2).tolist())
        dst.extend([(px * s, py * s) for px, py in phys])
        found += 1
    if found < 3:
        return None
    H, inliers = cv2.findHomography(np.array(src, np.float32), np.array(dst, np.float32), cv2.RANSAC, 0.05 * s)
    if H is None:
        return None
    proj = cv2.perspectiveTransform(np.array(src, np.float32).reshape(-1, 1, 2), H).reshape(-1, 2)
    rms = float(np.sqrt(((proj - np.array(dst)) ** 2).sum(axis=1).mean()))
    return H, found, rms


def _sheet_ignore_mask(shape: tuple[int, int], ppi: int, s: float) -> np.ndarray:
    """Pixels of the rectified sheet outside the shot area (title, corner
    markers, scale bar) -- never holes. The bull ring and crosshair are light
    gray on purpose and need no masking."""
    h, w = shape
    x0, y0, x1, y1 = (v * s * ppi for v in _SHOT_AREA_IN)
    outside = np.ones((h, w), np.uint8)
    outside[int(y0):int(y1), int(x0):int(x1)] = 0
    return outside


MAX_DECODE_PIXELS = 40_000_000     # a 200 MP phone photo would expand to >500 MB; the app downsizes first


_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")
_JPEG_MAGIC = bytes.fromhex("ffd8")


def image_dimensions(raw: bytes) -> tuple[int, int] | None:
    """(width, height) read from a JPEG or PNG header without decoding it."""
    if raw[:8] == _PNG_MAGIC and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if raw[:2] == _JPEG_MAGIC:
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seglen = int.from_bytes(raw[i + 2:i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(raw[i + 7:i + 9], "big"), int.from_bytes(raw[i + 5:i + 7], "big")
            i += 2 + max(2, seglen)
    return None


def decode_image(raw: bytes, max_pixels: int = MAX_DECODE_PIXELS) -> np.ndarray:
    """Decode an uploaded photo to BGR. Raises ValueError with a shooter-readable
    message for anything unusable or unreasonably large (checked BEFORE decoding)."""
    dims = image_dimensions(raw)
    if dims is not None and dims[0] * dims[1] > max_pixels:
        raise ValueError(f"That photo is {dims[0]}x{dims[1]} pixels -- too large. Use a smaller size "
                         "(the app shrinks photos automatically; if you're seeing this, resize it first).")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Couldn't read that as a photo (use JPEG or PNG).")
    if img.shape[0] * img.shape[1] > max_pixels:
        raise ValueError("That photo is too large. Use a smaller size.")
    return img


def _jpeg(img: np.ndarray, quality: int = 82) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return bytes(buf) if ok else b""


def analyze_photo(img_bgr: np.ndarray, distance_yd: float, bullet_diameter_in: float | None = None,
                  marker_size_in: float = MARKER_IN) -> TargetAnalysis:
    """Find the holes in a photo of a target. Never raises on a bad photo:
    problems come back as warnings on a (possibly empty) result."""
    warnings: list[str] = []
    if img_bgr is None or img_bgr.size == 0:
        return TargetAnalysis("free", None, warnings=["Couldn't read that image."])
    h0, w0 = img_bgr.shape[:2]
    longest = max(h0, w0)
    if longest > 4000:                       # keep processing time bounded on 50-200 MP phone photos
        f = 4000.0 / longest
        img_bgr = cv2.resize(img_bgr, (int(w0 * f), int(h0 * f)), interpolation=cv2.INTER_AREA)

    sheet = _decode_rectified(img_bgr, marker_size_in)
    if sheet is not None:
        H, found, rms = sheet
        s = marker_size_in / MARKER_IN
        w_px, h_px = int(PAPER_W_IN * s * DETECT_PPI), int(PAPER_H_IN * s * DETECT_PPI)
        M = np.diag([DETECT_PPI, DETECT_PPI, 1.0]) @ H
        rect = cv2.warpPerspective(img_bgr, M, (w_px, h_px), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
        gray = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        ignore = _sheet_ignore_mask(gray.shape, DETECT_PPI, s)
        blobs = _dark_blobs(gray, DETECT_PPI, bullet_diameter_in, ignore)
        picked, split_warn = _select_holes(blobs, DETECT_PPI, bullet_diameter_in, w_px)
        warnings += split_warn
        holes = [Hole(round(cx / DETECT_PPI, 4), round(cy / DETECT_PPI, 4), round(d / DETECT_PPI, 4), m)
                 for cx, cy, d, m in picked]
        holes.sort(key=lambda hole: (hole.y_in, hole.x_in))
        if found == 3:
            warnings.append("Only 3 of the 4 corner markers were visible; the scale is still solid, "
                            "but a shot with all 4 in frame is best.")
        if rms > 0.03 * s:
            warnings.append(f"The sheet looks warped or badly printed (marker fit error {rms:.3f} in) -- "
                            "measurements may be off. Print at 100% on flat paper.")
        _check_scale(holes, bullet_diameter_in, warnings)
        overlay = cv2.resize(rect, (int(w_px * OVERLAY_PPI / DETECT_PPI), int(h_px * OVERLAY_PPI / DETECT_PPI)),
                             interpolation=cv2.INTER_AREA)
        return TargetAnalysis("sheet", float(DETECT_PPI), holes, (AIM_IN[0] * s, AIM_IN[1] * s), warnings,
                              found, _jpeg(overlay), float(OVERLAY_PPI))

    # FREE mode ---------------------------------------------------------------
    warnings.append("Didn't find the Ballistica sheet's corner markers, so this is an approximate "
                    "reading. Print the sheet for exact measurements.")
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    hh, ww = gray.shape
    blobs = _dark_blobs(gray, None, None, None)
    picked, split_warn = _select_holes(blobs, None, None, ww)
    warnings += split_warn
    if not picked:
        warnings.append("No bullet holes found. Try a sharper, well-lit photo straight on to the target.")
        return TargetAnalysis("free", None, [], None, warnings, 0, _jpeg(gray), None)
    ppi = None
    if bullet_diameter_in:
        median_d_px = float(np.median([d for _, _, d, _ in picked]))
        ppi = median_d_px / bullet_diameter_in
        warnings.append("Scale is ESTIMATED from the bullet size (hole size is only roughly the bullet "
                        "diameter) -- treat inches as +/-25%. The sheet gives exact numbers.")
    else:
        warnings.append("No scale: give the bullet diameter, or print the sheet, to get inches.")
    holes = []
    for cx, cy, d, m in picked:
        if ppi:
            holes.append(Hole(round(cx / ppi, 4), round(cy / ppi, 4), round(d / ppi, 4), m))
        else:
            holes.append(Hole(round(cx, 2), round(cy, 2), round(d, 2), m))   # pixels, not inches
    holes.sort(key=lambda hole: (hole.y_in, hole.x_in))
    scale = 1200.0 / max(ww, hh) if max(ww, hh) > 1200 else 1.0
    overlay = cv2.resize(img_bgr, (int(ww * scale), int(hh * scale)), interpolation=cv2.INTER_AREA) if scale != 1.0 else img_bgr
    overlay_ppi = (ppi * scale) if ppi else scale     # overlay pixels per inch (or per original pixel if unscaled)
    return TargetAnalysis("free", ppi, holes, None, warnings, 0, _jpeg(overlay), overlay_ppi)


def _check_scale(holes: list[Hole], bullet_in: float | None, warnings: list[str]) -> None:
    """The markers fix the scale, so measured hole size should land near the
    bullet diameter. If it doesn't, either the print scale is wrong or the
    detections aren't holes."""
    if not bullet_in or len(holes) < 2:
        return
    med = float(np.median([h.diameter_in for h in holes]))
    if not (0.6 * bullet_in <= med <= 1.7 * bullet_in):
        warnings.append(f"Detected marks average {med:.2f} in across but this bullet is about {bullet_in:.2f} in -- "
                        "either the sheet wasn't printed at 100% or some marks aren't bullet holes.")
