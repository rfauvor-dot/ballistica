/*
 * Browser version of the shot tracker's per-frame work (ballistica/shot_tracker.py,
 * ShotTracker._sheets_in and _dots_of), for measuring whether a phone or tablet can
 * run the detector itself instead of the server (2026-11-15).
 *
 * Covers only the per-frame image work -- find the sheet's corner markers, flatten
 * the sheet, find the dark dots on it -- which is nearly all of the cost. The
 * frame-to-frame bookkeeping (which dots are new, when to call a shot) is a few
 * comparisons per frame and is not ported yet.
 *
 * Every step, constant and rounding follows the Python line for line, so the
 * speed measured is for work that gives the same answer; the page checks that
 * against the Python results stored in manifest.json. One step is approximated:
 * OpenCV.js has no estimateAffinePartial2D, which Python uses (robustly) for a
 * view with only two corner markers; here it is an ordinary least-squares fit of
 * the same kind of transform. Such views are rarely used (they need a very small
 * fit error), so this can only move a borderline two-marker view across the line.
 *
 * Needs OpenCV.js with the aruco module (4.10 from jsdelivr has it).
 */
(function (root) {
  "use strict";

  // target.py: corner squares' top-left corners in sheet inches, by corner 0-3.
  const MARKER_ORIGINS = [[0.5, 0.5], [7.0, 0.5], [0.5, 9.5], [7.0, 9.5]];
  const MAX_SHEET_NUMBER = 11;

  // shot_tracker.TrackerConfig defaults.
  const CONFIG = {
    ppi: 100.0, darknessThreshold: 45, minCover: 0.9,
    maxRms3: 0.12, maxRms2: 0.05, minMarkersNumbered: 3,
  };

  function markerLayout(id) {
    const number = Math.floor(id / 4), corner = id % 4;
    if (number > MAX_SHEET_NUMBER) return null;
    return { number: number || null, corner };
  }

  class FrameDetector {
    constructor(cv, config) {
      this.cv = cv;
      this.c = Object.assign({}, CONFIG, config || {});
      const ppi = this.c.ppi;
      this.W = Math.trunc(8.5 * ppi); this.H = Math.trunc(11 * ppi);
      this.aim = [4.25 * ppi, 5.5 * ppi];
      this.blurK = Math.trunc(ppi * 101 / 200) | 1;
      this.areaLo = 400 * Math.pow(ppi / 200, 2); this.areaHi = 2500 * Math.pow(ppi / 200, 2);
      this.morph = Math.max(3, Math.trunc(25 * ppi / 200));

      // shot_tracker._marker_detector(): loosened for video.
      const p = new cv.aruco_DetectorParameters();
      p.polygonalApproxAccuracyRate = 0.1; p.adaptiveThreshWinSizeMax = 83; p.adaptiveThreshWinSizeStep = 4;
      p.errorCorrectionRate = 1.0; p.perspectiveRemovePixelPerCell = 8; p.perspectiveRemoveIgnoredMarginPerCell = 0.2;
      p.minMarkerDistanceRate = 0.02; p.minCornerDistanceRate = 0.02;
      this.detector = new cv.aruco_ArucoDetector(cv.getPredefinedDictionary(cv.DICT_4X4_50), p,
                                                 new cv.aruco_RefineParameters(10, 3, true));

      // Where a hole may be (same rectangles, same float-then-truncate as Python's int()).
      this.mask = new cv.Mat(this.H, this.W, cv.CV_8UC1, new cv.Scalar(0));
      const y0 = Math.trunc(1.55 * ppi), y1 = Math.trunc(10.2 * ppi), x0 = Math.trunc(0.3 * ppi), x1 = Math.trunc(8.2 * ppi);
      this.mask.roi(new cv.Rect(x0, y0, x1 - x0, y1 - y0)).setTo(new cv.Scalar(255));
      for (const [ox, oy] of MARKER_ORIGINS) {
        const ya = Math.max(0, Math.trunc((oy - 0.35) * ppi)), yb = Math.min(this.H, Math.trunc((oy + 1.35) * ppi));
        const xa = Math.max(0, Math.trunc((ox - 0.35) * ppi)), xb = Math.min(this.W, Math.trunc((ox + 1.35) * ppi));
        this.mask.roi(new cv.Rect(xa, ya, xb - xa, yb - ya)).setTo(new cv.Scalar(0));
      }
      this.maskCount = cv.countNonZero(this.mask);
      this.kernel = cv.Mat.ones(this.morph, this.morph, cv.CV_8U);
    }

    /** One RGBA frame (cv.Mat) -> {sheets: [...], ms: {markers, flatten}} */
    process(rgba) {
      const cv = this.cv;
      const t0 = performance.now();
      const gray = new cv.Mat();
      cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
      const sheets = this._sheetsIn(gray);
      gray.delete();
      const t1 = performance.now();
      const out = [];
      for (const sheet of sheets) {
        let good = (sheet.found >= 3 && sheet.rms < this.c.maxRms3) || (sheet.found === 2 && sheet.rms < this.c.maxRms2);
        if (sheet.number !== null && sheet.found < this.c.minMarkersNumbered) good = false;
        const entry = { number: sheet.number, markers: sheet.found, rms: sheet.rms, used: false };
        if (good) {
          const r = this._dotsOf(rgba, sheet.Hm);
          entry.cover = r.cover;
          if (r.cover >= this.c.minCover) { entry.used = true; entry.dots = r.dots; }
        }
        out.push(entry);
      }
      const t2 = performance.now();
      return { sheets: out, ms: { markers: t1 - t0, flatten: t2 - t1 } };
    }

    _sheetsIn(gray) {
      const cv = this.cv;
      const corners = new cv.MatVector(), ids = new cv.Mat(), rejected = new cv.MatVector();
      this.detector.detectMarkers(gray, corners, ids, rejected);
      const votes = [];
      // OpenCV.js returns the ids as ONE ROW (1 x n), where Python gets a column.
      const idList = ids.empty() ? [] : Array.from(ids.data32S);
      for (let k = 0; k < idList.length; k++) {
        const layout = markerLayout(idList[k]);
        if (!layout) continue;
        const d = corners.get(k).data32F;
        const c = [[d[0], d[1]], [d[2], d[3]], [d[4], d[5]], [d[6], d[7]]];
        let s = 0;
        for (let j = 0; j < 4; j++) s += Math.hypot(c[j][0] - c[(j + 1) % 4][0], c[j][1] - c[(j + 1) % 4][1]);
        s /= 4;
        const cx = (c[0][0] + c[1][0] + c[2][0] + c[3][0]) / 4, cy = (c[0][1] + c[1][1] + c[2][1] + c[3][1]) / 4;
        const [ox, oy] = MARKER_ORIGINS[layout.corner];
        votes.push({ id: layout.corner, number: layout.number, c, s, org: [cx - (ox + 0.5) * s, cy - (oy + 0.5) * s] });
      }
      corners.delete(); ids.delete(); rejected.delete();

      votes.sort((a, b) => a.org[0] - b.org[0]);   // stable, like Python's sorted()
      const clusters = [];
      for (const v of votes) {
        let placed = false;
        for (const cl of clusters) {
          if (cl[0].number !== v.number) continue;
          const m = cl[0];
          if (Math.abs(v.org[0] - m.org[0]) < 1.3 * m.s && Math.abs(v.org[1] - m.org[1]) < 1.3 * m.s) { cl.push(v); placed = true; break; }
        }
        if (!placed) clusters.push([v]);
      }

      const sheets = [];
      for (const cl of clusters) {
        const byId = new Map();
        for (const v of cl) if (!byId.has(v.id)) byId.set(v.id, v);
        if (byId.size < 2) continue;
        const src = [], dst = [];
        for (const [id, v] of byId) {
          const [ox, oy] = MARKER_ORIGINS[id];
          src.push(...v.c);
          dst.push([ox, oy], [ox + 1, oy], [ox + 1, oy + 1], [ox, oy + 1]);
        }
        const Hm = byId.size >= 3 ? this._homography(src, dst) : similarityFit(src, dst);
        if (!Hm) continue;
        let sq = 0;
        for (let i = 0; i < src.length; i++) {
          const p = applyH(Hm, src[i]);
          sq += Math.pow(p[0] - dst[i][0], 2) + Math.pow(p[1] - dst[i][1], 2);
        }
        sheets.push({ Hm, rms: Math.sqrt(sq / src.length), found: byId.size, number: cl[0].number });
      }
      return sheets;
    }

    _homography(src, dst) {
      const cv = this.cv;
      const s = cv.matFromArray(src.length, 1, cv.CV_32FC2, src.flat());
      const d = cv.matFromArray(dst.length, 1, cv.CV_32FC2, dst.flat());
      const H = cv.findHomography(s, d, cv.RANSAC, 0.05);
      s.delete(); d.delete();
      if (H.empty()) { H.delete(); return null; }
      const h = Array.from(H.data64F);
      H.delete();
      return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], h[8]]];
    }

    _dotsOf(rgba, Hm) {
      const cv = this.cv, ppi = this.c.ppi, W = this.W, H = this.H;
      const M = cv.matFromArray(3, 3, cv.CV_64F, [
        ppi * Hm[0][0], ppi * Hm[0][1], ppi * Hm[0][2],
        ppi * Hm[1][0], ppi * Hm[1][1], ppi * Hm[1][2],
        Hm[2][0], Hm[2][1], Hm[2][2]]);
      const size = new cv.Size(W, H);
      const rect = new cv.Mat();
      cv.warpPerspective(rgba, rect, M, size, cv.INTER_LINEAR, cv.BORDER_CONSTANT, new cv.Scalar(255, 255, 255, 255));
      const g = new cv.Mat(), med = new cv.Mat(), diff = new cv.Mat();
      cv.cvtColor(rect, g, cv.COLOR_RGBA2GRAY);
      cv.medianBlur(g, med, this.blurK);
      cv.subtract(med, g, diff);
      const full = new cv.Mat(rgba.rows, rgba.cols, cv.CV_8UC1, new cv.Scalar(255));
      const inView = new cv.Mat(), valid = new cv.Mat();
      cv.warpPerspective(full, inView, M, size, cv.INTER_NEAREST, cv.BORDER_CONSTANT, new cv.Scalar(0));
      cv.erode(inView, valid, this.kernel);
      const validInMask = new cv.Mat();
      cv.bitwise_and(valid, this.mask, validInMask);
      const cover = cv.countNonZero(validInMask) / this.maskCount;
      // bw = (diff > threshold) & mask & valid
      const dark = new cv.Mat(), bw = new cv.Mat();
      cv.threshold(diff, dark, this.c.darknessThreshold, 255, cv.THRESH_BINARY);
      cv.bitwise_and(dark, validInMask, bw);
      const labels = new cv.Mat(), stats = new cv.Mat(), cent = new cv.Mat();
      const n = cv.connectedComponentsWithStats(bw, labels, stats, cent, 8, cv.CV_32S);
      const dots = [];
      for (let i = 1; i < n; i++) {
        const area = stats.intAt(i, cv.CC_STAT_AREA);
        if (area > this.areaLo && area < this.areaHi) {
          dots.push([(cent.doubleAt(i, 0) - this.aim[0]) / ppi, (this.aim[1] - cent.doubleAt(i, 1)) / ppi]);
        }
      }
      for (const m of [M, rect, g, med, diff, full, inView, valid, validInMask, dark, bw, labels, stats, cent]) m.delete();
      return { cover, dots };
    }
  }

  function applyH(H, p) {
    const w = H[2][0] * p[0] + H[2][1] * p[1] + H[2][2];
    return [(H[0][0] * p[0] + H[0][1] * p[1] + H[0][2]) / w, (H[1][0] * p[0] + H[1][1] * p[1] + H[1][2]) / w];
  }

  /** Least-squares scale + rotation + shift taking src onto dst (see the header note). */
  function similarityFit(src, dst) {
    const n = src.length;
    let sx = 0, sy = 0, dx = 0, dy = 0;
    for (let i = 0; i < n; i++) { sx += src[i][0]; sy += src[i][1]; dx += dst[i][0]; dy += dst[i][1]; }
    sx /= n; sy /= n; dx /= n; dy /= n;
    let a = 0, b = 0, den = 0;
    for (let i = 0; i < n; i++) {
      const px = src[i][0] - sx, py = src[i][1] - sy, qx = dst[i][0] - dx, qy = dst[i][1] - dy;
      a += px * qx + py * qy; b += px * qy - py * qx; den += px * px + py * py;
    }
    if (den === 0) return null;
    a /= den; b /= den;
    return [[a, -b, dx - (a * sx - b * sy)], [b, a, dy - (b * sx + a * sy)], [0, 0, 1]];
  }

  /** Does a browser result match the Python result for the same frame? */
  function compareWithExpected(got, expected, tolIn) {
    tolIn = tolIn || 0.05;
    const used = (list) => list.filter((s) => s.used);
    const g = used(got), e = used(expected);
    if (g.length !== e.length) return { ok: false, why: `used ${g.length} sheet view(s), expected ${e.length}` };
    for (let k = 0; k < e.length; k++) {
      const gd = g[k].dots, ed = e[k].dots;
      if (gd.length !== ed.length) return { ok: false, why: `${gd.length} dots, expected ${ed.length}` };
      const left = gd.slice();
      for (const [x, y] of ed) {
        let best = -1, bestD = Infinity;
        left.forEach(([gx, gy], i) => { const d = Math.hypot(gx - x, gy - y); if (d < bestD) { bestD = d; best = i; } });
        if (bestD > tolIn) return { ok: false, why: `a dot is ${bestD.toFixed(3)} in from where Python put it` };
        left.splice(best, 1);
      }
    }
    const n = e.length ? e[0].dots.length : 0;
    return { ok: true, why: e.length ? `${n} hole${n === 1 ? "" : "s"} on the sheet` : "no usable view of a sheet" };
  }

  const api = { FrameDetector, markerLayout, similarityFit, compareWithExpected, CONFIG };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BallisticaFrameDetector = api;
})(typeof window !== "undefined" ? window : globalThis);
