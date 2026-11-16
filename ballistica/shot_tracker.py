"""Live shot detection from video: frames in, shots out.

Feed it camera frames one at a time, in order, with their timestamps. It finds
the Ballistica target sheet in each frame, flattens it, and reports a shot the
first time a new bullet hole has been seen in enough frames to be trusted.
Strictly causal -- a frame is only ever compared with what came before it --
so replaying a recorded video through it behaves exactly as a live camera feed
would.

    tracker = ShotTracker()
    for frame, t in frames:                 # BGR image, seconds
        for shot in tracker.process_frame(frame, t):
            ...                             # shot.x_in, shot.y_in, shot.sheet_image()

Two kinds of sheet:
  - NUMBERED sheets (printed with a number 1-11, each with its own corner
    markers; see target.render_sheet). Identity comes from the markers in
    every frame, so a bump, a swing away and back, or a restart (pass the
    saved hole lists back in as `known_holes`) can never make an old hole
    look new. A numbered view is only used with at least three of its corner
    markers in sight, and holes already on a sheet the first time it is seen
    are taken as already there, not called as shots.
  - UNNUMBERED sheets (the original printout, all with the same four
    markers). Handled exactly as the 2026-11-13 prototype did, which is what
    the November 13 benchmark checks: a sheet is recognised by the holes
    already on it (one is enough), or -- while still blank -- by where it sits
    in the picture.

Known limits, each pinned by a test in tests/test_shot_tracker.py marked as an
expected failure until the step that fixes it:
  - Unnumbered sheets: a blank one gets a new identity whenever the camera is
    re-aimed, and two with a hole in a similar place are taken for one sheet.
  - Tight groups. Touching holes merge into one blob too big to count as a
    hole, and both vanish. On an unnumbered sheet that can hide enough known
    holes that the sheet stops being recognised and its other holes are
    called again (numbered sheets are immune to that part).
  - Close holes. A new hole within `tol_known_in` of an old one is taken for
    the old one.
  - Printed features. At the flat contrast of the November 13 footage the
    light-gray ring and crosshair stay under the darkness threshold. In a
    crisp, well-exposed view they do not: the crosshair is called as a shot at
    dead centre, a hole on the ring is lost, and the scale bar at the bottom
    edge can be called when the view is tilted.

Coordinates: inches from the sheet's printed crosshair, x to the RIGHT, y UP.
(target.py's photo analysis uses y DOWN from the sheet's top-left corner.)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import target


@dataclass(frozen=True)
class TrackerConfig:
    ppi: float = 100.0            # working resolution of the flattened sheet, pixels per inch
    n_confirm: int = 3            # frames a new dot must be seen in before it is called
    tol_known_in: float = 0.30    # a dot this close to a confirmed hole IS that hole
    tol_candidate_in: float = 0.20  # frame-to-frame matching of a not-yet-confirmed dot
    darkness_threshold: int = 45  # how much darker than the surrounding paper counts as a dot
    min_cover: float = 0.9        # share of the shot area that must be inside the camera frame
    # Marker-fit quality gates, in inches of RMS error: (3+ markers, exactly 2 markers).
    max_rms_3_markers_in: float = 0.12
    max_rms_2_markers_in: float = 0.05
    # Sheet identity (see ShotTracker._pick_track).
    pattern_match_in: float = 0.35     # a known hole counts as "visible" within this distance
    pattern_min_fraction: float = 0.7  # share of a sheet's holes that must be visible to recognise it
    position_match_markers: float = 3.0  # image-position fallback, in marker widths
    long_gap_s: float = 20.0      # a hole first seen after the sheet was out of view this long is flagged
    min_markers_numbered: int = 3  # a numbered sheet is only used with this many of its markers in view


@dataclass
class Shot:
    n: int                 # 1-based order in which this tracker called it
    sheet_track: int       # the tracker's internal sheet id
    t_first: float         # when the hole was first seen, seconds
    t_call: float          # when it was confirmed and reported
    x_in: float            # right of the crosshair
    y_in: float            # above the crosshair
    off_camera_s: float    # >0 if the sheet had been out of view that long just before; else 0
    rect: np.ndarray = field(repr=False)    # flattened sheet at first sighting
    frame: np.ndarray = field(repr=False)   # camera frame at first sighting
    ppi: float = field(default=100.0, repr=False)
    sheet_number: int | None = None   # printed sheet number (None: an unnumbered sheet)

    def sheet_image(self) -> np.ndarray:
        """The flattened sheet with this hole ringed in red."""
        out = self.rect.copy()
        px = int(4.25 * self.ppi + self.x_in * self.ppi)
        py = int(5.5 * self.ppi - self.y_in * self.ppi)
        cv2.circle(out, (px, py), int(0.45 * self.ppi), (0, 0, 255), max(2, int(self.ppi / 50)))
        return out

    def as_dict(self) -> dict:
        """The summary.json record (same keys the prototype wrote)."""
        return {"sheet_track": self.sheet_track, "sheet_number": self.sheet_number, "t_first": self.t_first,
                "t_call": self.t_call, "x": self.x_in, "y": self.y_in, "off_camera_s": self.off_camera_s,
                "n": self.n}


def _marker_detector() -> "cv2.aruco.ArucoDetector":
    """Marker detection loosened for video. The still-photo defaults decoded
    only 2 of 4 markers at ~55 px per marker on the November 13 footage."""
    p = cv2.aruco.DetectorParameters()
    p.polygonalApproxAccuracyRate = 0.1
    p.adaptiveThreshWinSizeMax = 83
    p.adaptiveThreshWinSizeStep = 4
    p.errorCorrectionRate = 1.0
    p.perspectiveRemovePixelPerCell = 8
    p.perspectiveRemoveIgnoredMarginPerCell = 0.2
    p.minMarkerDistanceRate = 0.02
    p.minCornerDistanceRate = 0.02
    return cv2.aruco.ArucoDetector(target._aruco_dict(), p)


class ShotTracker:
    def __init__(self, config: TrackerConfig | None = None,
                 known_holes: dict[int, list[tuple[float, float]]] | None = None) -> None:
        """known_holes: {sheet number: [(x_in, y_in), ...]} -- holes already on
        numbered sheets, e.g. what sheet_holes() returned before a restart.
        They are never called as shots."""
        self.config = c = config or TrackerConfig()
        ppi = c.ppi
        self._detector = _marker_detector()
        self._origins = target._MARKER_ORIGINS
        self._w, self._h = int(8.5 * ppi), int(11 * ppi)
        self._aim = (4.25 * ppi, 5.5 * ppi)
        # Where a hole may be: the body of the sheet, minus a margin round each corner marker.
        mask = np.zeros((self._h, self._w), np.uint8)
        mask[int(1.55 * ppi):int(10.2 * ppi), int(0.3 * ppi):int(8.2 * ppi)] = 255
        for ox, oy in self._origins.values():
            mask[int((oy - 0.35) * ppi):int((oy + 1.35) * ppi), int((ox - 0.35) * ppi):int((ox + 1.35) * ppi)] = 0
        self._mask = mask
        self._blur_k = int(ppi * 101 / 200) | 1                 # median-blur kernel, scaled
        self._area_lo, self._area_hi = 400 * (ppi / 200) ** 2, 2500 * (ppi / 200) ** 2
        self._morph = max(3, int(25 * ppi / 200))

        self.tracks: list[dict] = []       # one per sheet the tracker believes it has seen
        for number, holes in (known_holes or {}).items():
            track = self._new_track(org=None, t=None, number=int(number))
            track["holes"] = [dict(x=float(x), y=float(y), first=None, obs=[]) for x, y in holes]
        self.shots: list[Shot] = []        # every shot called so far, in order
        self.frames_processed = 0
        self.sheet_frames_used = 0
        self.sheet_frames_gated = 0
        # Seconds spent per frame, for sizing what can run live.
        self.timing: dict[str, list[float]] = {"markers": [], "rectify": [], "total": []}

    # ------------------------------------------------------------ public
    def process_frame(self, frame_bgr: np.ndarray, t: float) -> list[Shot]:
        """Process one camera frame taken at time `t` (seconds). Returns the
        shots this frame confirmed -- usually none."""
        c = self.config
        new_shots: list[Shot] = []
        t_a = time.time()
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        sheets = self._sheets_in(gray)
        t_b = time.time()
        for sheet in sheets:
            good = ((sheet["found"] >= 3 and sheet["rms"] < c.max_rms_3_markers_in)
                    or (sheet["found"] == 2 and sheet["rms"] < c.max_rms_2_markers_in))
            if sheet["number"] is not None and sheet["found"] < c.min_markers_numbered:
                good = False
            if not good:
                self.sheet_frames_gated += 1
                continue
            rect, cover, dots = self._dots_of(frame_bgr, sheet["Hm"])
            if cover < c.min_cover:
                self.sheet_frames_gated += 1
                continue
            track = self._pick_track(sheet, dots, t)
            track["gap"] = t - track["last_t"] if track["last_t"] is not None else 0.0
            track["last_t"] = t
            track["seen_frames"] += 1
            new_shots.extend(self._update_track(track, dots, t, rect, frame_bgr))
            self.sheet_frames_used += 1
        t_c = time.time()
        self.frames_processed += 1
        self.timing["markers"].append(t_b - t_a)
        self.timing["rectify"].append(t_c - t_b)
        self.timing["total"].append(t_c - t_a)
        return new_shots

    def track_summary(self) -> list[dict]:
        out = []
        for tr in self.tracks:
            row = {"id": tr["id"], "holes": len(tr["holes"]), "frames": tr["seen_frames"]}
            if tr["number"] is not None:
                row["number"] = tr["number"]
            out.append(row)
        return out

    def sheet_holes(self) -> dict[int, list[tuple[float, float]]]:
        """Every hole on every NUMBERED sheet seen so far, including holes that
        were already there when the sheet was first seen -- the list to save,
        and to pass back as `known_holes` after a restart."""
        return {tr["number"]: [(round(h["x"], 3), round(h["y"], 3)) for h in tr["holes"]]
                for tr in self.tracks if tr["number"] is not None}

    def _new_track(self, org, t, number: int | None = None) -> dict:
        track = dict(id=len(self.tracks) + 1, number=number, org=org, holes=[], cands=[], seen_frames=0,
                     last_t=t, adopted=0)
        self.tracks.append(track)
        return track

    # ------------------------------------------------------ sheet finding
    def _sheets_in(self, gray: np.ndarray) -> list[dict]:
        """Every sheet visible in the frame: its printed number (None if
        unnumbered), its image-to-inches transform and how well the markers
        fit it. Markers are grouped by sheet number, then -- since every copy
        of a sheet shares its marker ids -- by where each one says the sheet's
        corner is."""
        O = self._origins
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        votes = []
        for corner, marker_id in zip(corners, ids.ravel().tolist()):
            layout = target.marker_layout(marker_id)
            if layout is None:
                continue
            number, corner_index = layout
            corner = corner.reshape(4, 2)
            s = float(np.mean([np.linalg.norm(corner[k] - corner[(k + 1) % 4]) for k in range(4)]))
            ctr = corner.mean(0)
            ox, oy = O[corner_index]
            votes.append(dict(id=corner_index, number=number, c=corner, s=s,
                              org=(ctr[0] - (ox + 0.5) * s, ctr[1] - (oy + 0.5) * s)))
        clusters: list[list[dict]] = []
        for v in sorted(votes, key=lambda v: v["org"][0]):
            for cl in clusters:
                if cl[0]["number"] != v["number"]:
                    continue
                m = cl[0]
                if abs(v["org"][0] - m["org"][0]) < 1.3 * m["s"] and abs(v["org"][1] - m["org"][1]) < 1.3 * m["s"]:
                    cl.append(v)
                    break
            else:
                clusters.append([v])
        sheets = []
        for cl in clusters:
            by_id: dict[int, dict] = {}
            for v in cl:
                by_id.setdefault(v["id"], v)
            if len(by_id) < 2:
                continue
            src, dst = [], []
            for marker_id, v in by_id.items():
                ox, oy = O[marker_id]
                src.extend(v["c"].tolist())
                dst.extend([(ox, oy), (ox + 1, oy), (ox + 1, oy + 1), (ox, oy + 1)])
            src = np.array(src, np.float32)
            dst = np.array(dst, np.float32)
            if len(by_id) >= 3:
                Hm, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.05)
            else:   # two markers can't fix perspective: a scale+rotation+shift fit only
                A, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
                Hm = None if A is None else np.vstack([A, [0, 0, 1]])
            if Hm is None:
                continue
            proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
            rms = float(np.sqrt(((proj - dst) ** 2).sum(1).mean()))
            sheets.append(dict(
                Hm=Hm, rms=rms, found=len(by_id), number=cl[0]["number"],
                org=(float(np.mean([v["org"][0] for v in cl])), float(np.mean([v["org"][1] for v in cl]))),
                s=float(np.mean([v["s"] for v in cl])),
            ))
        return sheets

    def _dots_of(self, frame: np.ndarray, Hm: np.ndarray):
        """Flatten the sheet and find dark dots on it. Returns (flattened
        image, share of the shot area in view, dot centres in inches)."""
        ppi, W, H = self.config.ppi, self._w, self._h
        M = np.diag([ppi, ppi, 1.0]) @ Hm
        rect = cv2.warpPerspective(frame, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
        g = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        diff = cv2.subtract(cv2.medianBlur(g, self._blur_k), g)
        in_view = cv2.warpPerspective(np.full(frame.shape[:2], 255, np.uint8), M, (W, H),
                                      flags=cv2.INTER_NEAREST, borderValue=0)
        valid = cv2.erode(in_view, np.ones((self._morph, self._morph), np.uint8))
        cover = float((valid[self._mask > 0] > 0).mean())
        bw = ((diff > self.config.darkness_threshold) & (self._mask > 0) & (valid > 0)).astype(np.uint8)
        n, _, stats, cent = cv2.connectedComponentsWithStats(bw)
        dots = [((cent[i][0] - self._aim[0]) / ppi, (self._aim[1] - cent[i][1]) / ppi)
                for i in range(1, n) if self._area_lo < stats[i, cv2.CC_STAT_AREA] < self._area_hi]
        return rect, cover, dots

    # ------------------------------------------------------ sheet identity
    def _pick_track(self, sheet: dict, dots: list, t: float) -> dict:
        """Which known sheet this view is. 1) a track whose confirmed holes
        are mostly visible here IS this sheet (works wherever the camera
        points); 2) else follow image position to a track with too few holes
        to contradict it; 3) else a new sheet. A NUMBERED sheet is simply the
        track with that number -- none of the guessing applies, and an
        unnumbered view is never matched to a numbered sheet."""
        c = self.config
        if sheet["number"] is not None:
            for tr in self.tracks:
                if tr["number"] == sheet["number"]:
                    tr["org"] = sheet["org"]
                    return tr
            return self._new_track(sheet["org"], t, sheet["number"])
        unnumbered = [tr for tr in self.tracks if tr["number"] is None]

        def hits(tr):
            return sum(1 for h in tr["holes"]
                       if any(math.dist((h["x"], h["y"]), d) < c.pattern_match_in for d in dots))

        best, best_fraction = None, 0.0
        for tr in unnumbered:
            n = len(tr["holes"])
            if n == 0:
                continue
            h = hits(tr)
            fraction = h / n
            if h >= min(n, 2) and fraction >= c.pattern_min_fraction and (fraction, h) > (best_fraction, 0):
                best, best_fraction = tr, fraction
        if best is None:
            near, near_d = None, 1e9
            for tr in unnumbered:
                d = math.hypot(sheet["org"][0] - tr["org"][0], sheet["org"][1] - tr["org"][1]) / sheet["s"]
                if d < c.position_match_markers and d < near_d:
                    near, near_d = tr, d
            if near is not None and (len(near["holes"]) == 0 or hits(near) >= 0.5 * len(near["holes"])):
                best = near
        if best is None:
            best = self._new_track(sheet["org"], t)
        best["org"] = sheet["org"]
        return best

    # ---------------------------------------------------------- new holes
    def _update_track(self, tr: dict, dots: list, t: float, rect: np.ndarray, frame: np.ndarray) -> list[Shot]:
        c = self.config
        seen: set[int] = set()
        for d in dots:
            known = [h for h in tr["holes"] if math.dist((h["x"], h["y"]), d) < c.tol_known_in]
            if known:
                known[0]["obs"].append(d)
                continue
            hit = None
            for ci, cand in enumerate(tr["cands"]):
                if math.dist((cand["x"], cand["y"]), d) < c.tol_candidate_in:
                    hit = ci
                    break
            if hit is None:
                # On a numbered sheet, a hole already there the first time the
                # sheet is seen is not a new shot (recording started late, or
                # a shot landed while the app was closed).
                already_there = tr["number"] is not None and tr["seen_frames"] <= 1
                tr["cands"].append(dict(x=d[0], y=d[1], first=t, seen=1, miss=0, pts=[d], already=already_there,
                                        rect=rect.copy(), frame=frame, gap=tr.get("gap", 0)))
                seen.add(len(tr["cands"]) - 1)
            else:
                cand = tr["cands"][hit]
                cand["seen"] += 1
                cand["miss"] = 0
                cand["pts"].append(d)
                seen.add(hit)
                cand["x"] = float(np.median([p[0] for p in cand["pts"]]))
                cand["y"] = float(np.median([p[1] for p in cand["pts"]]))
        new_shots, keep = [], []
        for ci, cand in enumerate(tr["cands"]):
            if ci not in seen:
                cand["miss"] += 1
            if cand["seen"] >= c.n_confirm:
                tr["holes"].append(dict(x=cand["x"], y=cand["y"], first=cand["first"], obs=list(cand["pts"])))
                if cand["already"]:
                    tr["adopted"] += 1
                    continue
                shot = Shot(
                    n=len(self.shots) + 1, sheet_track=tr["id"], t_first=cand["first"], t_call=t,
                    x_in=cand["x"], y_in=cand["y"],
                    off_camera_s=round(cand["gap"], 1) if cand["gap"] > c.long_gap_s else 0,
                    rect=cand["rect"], frame=cand["frame"], ppi=c.ppi, sheet_number=tr["number"],
                )
                self.shots.append(shot)
                new_shots.append(shot)
            elif cand["miss"] < 2:      # a candidate that vanishes for two frames is dropped
                keep.append(cand)
        tr["cands"] = keep
        return new_shots


# ------------------------------------------------------------ framing check
# Before a session: is the camera set up so the live tracker can work? One
# frame in, plain answers out. Uses the same marker settings as the tracker,
# so "found" here means the tracker will find it too.

_CORNER_NAMES = {0: "top-left", 1: "top-right", 2: "bottom-left", 3: "bottom-right"}
# Corner-square size in the picture, in pixels (average side). One real data
# point so far: the November 13 footage had about 55 px and worked, but only
# with the loosened video settings. Revisit once ground-tripod clips exist.
FRAMING_MARKER_PX_LOW = 45
FRAMING_MARKER_PX_GOOD = 70
# A corner square closer than this share of the frame's shorter side to the
# edge is "near the edge": a small bump can push it out of the picture.
FRAMING_EDGE_MARGIN = 0.03


def check_framing(frame_bgr: np.ndarray) -> dict:
    """What a camera frame looks like to the live tracker. Returns a dict:
    ok (bool), sheet_number (int or None), corners_found / corners_missing /
    corners_near_edge (sheet-corner names), marker_px (float or None), detail
    ("good" | "workable" | "too small" | None), other_sheets (int: other target
    sheets visible), and messages: plain sentences, the first being the
    verdict. Doesn't judge focus or exposure -- there is no real ground-tripod
    footage yet to set a bar from."""
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    corners, ids, _ = _marker_detector().detectMarkers(gray)
    groups: dict[int | None, dict[int, np.ndarray]] = {}
    for corner, marker_id in zip(corners if ids is not None else [], ids.ravel().tolist() if ids is not None else []):
        layout = target.marker_layout(marker_id)
        if layout is not None:
            groups.setdefault(layout[0], {}).setdefault(layout[1], corner.reshape(4, 2))
    result = dict(ok=False, sheet_number=None, corners_found=[], corners_missing=list(_CORNER_NAMES.values()),
                  corners_near_edge=[], marker_px=None, detail=None, other_sheets=0, messages=[])
    if not groups:
        result["messages"] = ["No target sheet found. Point the camera at a Ballistica sheet with all four "
                              "black corner squares in view."]
        return result
    number = max(groups, key=lambda n: (len(groups[n]), n is not None))
    found = groups[number]
    sides = [float(np.mean([np.linalg.norm(c[k] - c[(k + 1) % 4]) for k in range(4)])) for c in found.values()]
    marker_px = float(np.mean(sides))
    margin = FRAMING_EDGE_MARGIN * min(w, h)
    near = [_CORNER_NAMES[k] for k, c in sorted(found.items())
            if c[:, 0].min() < margin or c[:, 1].min() < margin or c[:, 0].max() > w - margin or c[:, 1].max() > h - margin]
    detail = ("good" if marker_px >= FRAMING_MARKER_PX_GOOD
              else "workable" if marker_px >= FRAMING_MARKER_PX_LOW else "too small")
    result.update(
        sheet_number=number, corners_found=[_CORNER_NAMES[k] for k in sorted(found)],
        corners_missing=[name for k, name in _CORNER_NAMES.items() if k not in found],
        corners_near_edge=near, marker_px=round(marker_px, 1), detail=detail,
        # A single stray square is usually a misread, not another sheet.
        other_sheets=sum(1 for n, g in groups.items() if n != number and len(g) >= 2),
    )

    name = f"Sheet {number}" if number is not None else "An unnumbered sheet"
    problems: list[str] = []
    if result["corners_missing"]:
        problems.append(f"Can't see the {_join(result['corners_missing'])} corner square"
                        f"{'s' if len(result['corners_missing']) > 1 else ''} -- adjust so all four are in view.")
    if near:
        problems.append(f"The {_join(near)} corner square{'s are' if len(near) > 1 else ' is'} right at the edge of "
                        f"the picture -- leave a little room so a bump doesn't push "
                        f"{'them' if len(near) > 1 else 'it'} out.")
    if detail == "too small":
        problems.append(f"The sheet is small in the picture (corner squares about {marker_px:.0f} pixels) -- zoom in "
                        "or use higher quality.")
    if result["other_sheets"]:
        problems.append("Another target sheet is in view too -- zoom so only one sheet is in the picture.")
    if number is None:
        problems.append("This sheet has no number, so the camera can't tell it from other sheets -- print a "
                        "numbered sheet.")
    result["ok"] = not problems
    if result["ok"]:
        size_note = (" Corner squares are a good size." if detail == "good"
                     else f" Corner squares are about {marker_px:.0f} pixels: workable, zooming in a little "
                          "more would help.")
        result["messages"] = [f"{name} found, all 4 corners in view. Good to go.{size_note}"]
    else:
        seen = "all 4 corners" if len(found) == 4 else f"{len(found)} of 4 corners"
        result["messages"] = [f"{name} found, {seen} in view. Not ready yet."] + problems
    return result


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

