/*
 * Browser version of the shot tracker's per-frame work (ballistica/shot_tracker.py,
 * ShotTracker._sheets_in and _dots_of), for measuring whether a phone or tablet can
 * run the detector itself instead of the server (2026-11-15).
 *
 * Covers only the per-frame image work -- find the sheet's corner markers, flatten
 * the sheet, find the dark dots on it -- which is nearly all of the cost. The
 * frame-to-frame bookkeeping (which dots are new, when to call a shot) is a few
 * comparisons per frame and is in shot_caller.js.
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
 *
 * PATCH SEARCH (2026-11-15). Finding the corner squares in the whole frame is
 * most of the cost (on Rick's S25 the full per-frame time was 268 ms). From one
 * frame to the next the camera barely moves, so with `track: true` a frame is
 * searched only in small patches where each sheet's four corner squares should
 * be: where a square was found last frame, or -- for a square that was missed
 * -- where last frame's fit of the sheet puts it. Every sheet seen is
 * followed, not just one. It falls back to a full search on the first frame,
 * when the patches have not found 3+ squares of a sheet that was being USED
 * for `maxPatchMisses` frames running -- one miss is usually a frame blurred by
 * the jolt of a shot, where a full search finds nothing either (measured on
 * the November 13 stretch: the picture moved at most 23 px between frames,
 * against a patch margin of ~57 px), so the same patches are kept and tried
 * again; a half-visible neighbouring sheet flickering in and out doesn't
 * count -- and every
 * `fullSearchEvery` frames anyway, so a newly hung sheet is noticed within a
 * few seconds. A square found in a patch has exactly the corners a full search
 * gives it (checked: 0.00 px apart), and on the 33 sample frames the answers
 * are identical. Over the WHOLE November 13 video (2026-11-16) they are not
 * always: on some frames a patch finds a square the full search missed, or
 * misses one it found, so the sheet is fitted from 3 squares instead of 2 or 4
 * (or the reverse) and the holes move by up to ~0.25 in on that frame. Not
 * consistently worse -- sometimes the patch fit is the closer one -- and the
 * shots called were within 0.1 in of the full-search Python tracker's.
 *
 * FASTER FLATTENING (2026-11-15). Two shortcuts in finding the holes, checked
 * against the Python results on all the sample frames:
 *   - the median blur (the paper's local brightness) runs only over the area a
 *     hole can be in, plus the blur's own reach on every side -- identical
 *     results inside that area;
 *   - "which part of the flattened sheet is inside the picture" is drawn as the
 *     picture's outline mapped onto the sheet, not by warping a whole-frame mask
 *     (the two can disagree on single pixels along that edge).
 * Tried and REJECTED: flattening the grey picture instead of the colour one
 * (half the warp time). Pixels come out up to 1 grey level different, and on
 * the 12-hole sample frame that broke the thin neck between two touching
 * holes, turning one spot into two.
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
    // Patch search: each patch is the square's last position grown by this many
    // square-widths on every side. At 2 frames a second a steady tripod moves the
    // picture by a few pixels; one width (~55-100 px) is a wide margin.
    patchMargin: 1.0,
    fullSearchEvery: 10,          // frames; at 2 frames a second, every 5 s
    minPatchMarkers: 3,
    maxPatchMisses: 2,            // patch searches in a row that lose the sheet before a full search
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
      // The median blur only has to be right inside the mask's bounding box; it
      // looks blurK/2 pixels out, so give it that much more on every side and
      // the values inside the box are exactly what a whole-sheet blur gives.
      const r = (this.blurK - 1) / 2;
      const bx0 = Math.max(0, x0 - r), by0 = Math.max(0, y0 - r);
      this.blurRect = new cv.Rect(bx0, by0, Math.min(this.W, x1 + r) - bx0, Math.min(this.H, y1 + r) - by0);
      this.boxRect = new cv.Rect(x0 - bx0, y0 - by0, x1 - x0, y1 - y0);   // the mask's box, inside blurRect
      this.boxOrigin = [x0, y0];
      this.boxMask = this.mask.roi(new cv.Rect(x0, y0, x1 - x0, y1 - y0)).clone();
      this.kernel = cv.Mat.ones(this.morph, this.morph, cv.CV_8U);
      this.resetTracking();
    }

    /** Forget where the squares were: the next frame gets a full search. */
    resetTracking() {
      this.followed = [];           // per sheet seen last frame: {squares: [{id, c}] x4, used}
      this.sinceFull = 0;
      this.patchMisses = 0;
      this.stats = { frames: 0, fullSearches: 0, patchSearches: 0, patchFallbacks: 0, patchMissesKept: 0 };
    }

    /** One RGBA frame (cv.Mat) -> {sheets: [...], search: "full"|"patch", ms: {markers, flatten}}.
     *  The `used` sheets are the views shot_caller.js takes: {number, org, s, dots}.
     *  options.track: use the patch search between consecutive frames of one feed. */
    process(rgba, options) {
      const cv = this.cv, track = !!(options && options.track);
      const t0 = performance.now();
      const gray = new cv.Mat();
      cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
      let markers = null, search = "full", keepFollowed = false;
      this.stats.frames += 1;
      if (track && this.followed.some((f) => f.used) && this.sinceFull < this.c.fullSearchEvery) {
        const found = this._detectInPatches(gray);
        this.stats.patchSearches += 1;
        if (found.everySheetKept) {
          markers = found.markers; search = "patch"; this.sinceFull += 1; this.patchMisses = 0;
        } else if (this.patchMisses + 1 < this.c.maxPatchMisses) {
          // Probably a blurred frame: accept what the patches found, keep looking in the same places next frame.
          markers = found.markers; search = "patch"; this.sinceFull += 1; this.patchMisses += 1;
          keepFollowed = true; this.stats.patchMissesKept += 1;
        } else {
          this.stats.patchFallbacks += 1;    // lost the sheet: search the whole frame
        }
      }
      if (markers === null) {
        markers = this._detectAll(gray);
        this.stats.fullSearches += 1;
        this.sinceFull = 0;
        this.patchMisses = 0;
      }
      const sheets = this._sheetsFrom(markers);
      const t1 = performance.now();
      const out = [];
      for (const sheet of sheets) {
        let good = (sheet.found >= 3 && sheet.rms < this.c.maxRms3) || (sheet.found === 2 && sheet.rms < this.c.maxRms2);
        if (sheet.number !== null && sheet.found < this.c.minMarkersNumbered) good = false;
        const entry = { number: sheet.number, markers: sheet.found, rms: sheet.rms, used: false, org: sheet.org, s: sheet.s };
        if (good) {
          const r = this._dotsOf(rgba, gray.cols, gray.rows, sheet.Hm);
          entry.cover = r.cover;
          if (r.cover >= this.c.minCover) { entry.used = true; entry.dots = r.dots; }
        }
        out.push(entry);
      }
      // Where each sheet's squares should be next frame: found ones where they
      // were, missing ones where this frame's fit puts them.
      gray.delete();
      if (!keepFollowed) this.followed = sheets.map((sh, k) => ({ squares: this._expectedSquares(sh), used: out[k].used }));
      const t2 = performance.now();
      return { sheets: out, search, ms: { markers: t1 - t0, flatten: t2 - t1 } };
    }

    /** Every marker in the frame: [{id, c: [[x,y] x4]}], in detection order. */
    _detectAll(gray) {
      const cv = this.cv;
      const corners = new cv.MatVector(), ids = new cv.Mat(), rejected = new cv.MatVector();
      this.detector.detectMarkers(gray, corners, ids, rejected);
      // OpenCV.js returns the ids as ONE ROW (1 x n), where Python gets a column.
      const idList = ids.empty() ? [] : Array.from(ids.data32S);
      const out = idList.map((id, k) => {
        const d = corners.get(k).data32F;
        return { id, c: [[d[0], d[1]], [d[2], d[3]], [d[4], d[5]], [d[6], d[7]]] };
      });
      corners.delete(); ids.delete(); rejected.delete();
      return out;
    }

    /** The 4 squares of a sheet, in frame pixels: found ones as found, the rest
     *  projected from the sheet's fit. */
    _expectedSquares(sheet) {
      const inv = invert3(sheet.Hm);
      const base = sheet.number ? 4 * sheet.number : 0;
      return MARKER_ORIGINS.map(([ox, oy], corner) => {
        const id = base + corner;
        const seen = sheet.markers.find((m) => m.id === id);
        if (seen) return { id, c: seen.c };
        return { id, c: [[ox, oy], [ox + 1, oy], [ox + 1, oy + 1], [ox, oy + 1]].map((pt) => applyH(inv, pt)), predicted: true };
      });
    }

    /** Search small patches where the followed sheets' squares should be.
     *  -> {markers: [{id, c}], everySheetKept: bool} (frame coordinates). */
    _detectInPatches(gray) {
      const out = [];
      let everySheetKept = true;
      const sameMarker = (a, b) => a.id === b.id &&
        Math.hypot(a.c[0][0] - b.c[0][0], a.c[0][1] - b.c[0][1]) < 0.5 * side(a.c);
      for (const sheet of this.followed) {
        let kept = 0;
        for (const want of sheet.squares) {
          const xs = want.c.map((q) => q[0]), ys = want.c.map((q) => q[1]);
          const pad = this.c.patchMargin * side(want.c);
          const x0 = Math.max(0, Math.floor(Math.min(...xs) - pad)), y0 = Math.max(0, Math.floor(Math.min(...ys) - pad));
          const x1 = Math.min(gray.cols, Math.ceil(Math.max(...xs) + pad)), y1 = Math.min(gray.rows, Math.ceil(Math.max(...ys) + pad));
          if (x1 - x0 < 16 || y1 - y0 < 16) continue;           // off the edge of the picture
          const patch = gray.roi(new this.cv.Rect(x0, y0, x1 - x0, y1 - y0));
          let hit = false;
          for (const m of this._detectAll(patch)) {
            const g = { id: m.id, c: m.c.map(([x, y]) => [x + x0, y + y0]) };
            if (g.id === want.id) hit = true;
            if (!out.some((o) => sameMarker(o, g))) out.push(g);   // neighbouring patches overlap
          }
          patch.delete();
          if (hit) kept += 1;
        }
        if (sheet.used && kept < this.c.minPatchMarkers) everySheetKept = false;
      }
      return { markers: out, everySheetKept };
    }

    _sheetsFrom(markers) {
      const votes = [];
      for (const m of markers) {
        const layout = markerLayout(m.id);
        if (!layout) continue;
        const c = m.c;
        let s = 0;
        for (let j = 0; j < 4; j++) s += Math.hypot(c[j][0] - c[(j + 1) % 4][0], c[j][1] - c[(j + 1) % 4][1]);
        s /= 4;
        const cx = (c[0][0] + c[1][0] + c[2][0] + c[3][0]) / 4, cy = (c[0][1] + c[1][1] + c[2][1] + c[3][1]) / 4;
        const [ox, oy] = MARKER_ORIGINS[layout.corner];
        votes.push({ id: layout.corner, markerId: m.id, number: layout.number, c, s,
                     org: [cx - (ox + 0.5) * s, cy - (oy + 0.5) * s] });
      }

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
        // Where the sheet sits in the picture and how big its squares are, averaged
        // over every square in the cluster (shot_caller.js uses these to tell
        // unnumbered sheets apart).
        const org = [cl.reduce((a, v) => a + v.org[0], 0) / cl.length, cl.reduce((a, v) => a + v.org[1], 0) / cl.length];
        const s = cl.reduce((a, v) => a + v.s, 0) / cl.length;
        sheets.push({ Hm, rms: Math.sqrt(sq / src.length), found: byId.size, number: cl[0].number, org, s,
                      markers: [...byId.values()].map((v) => ({ id: v.markerId, c: v.c })) });
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

    /** Flatten the sheet from the colour frame and find the dark dots on it.
     *  -> {cover, dots: [[x_in, y_in]]}. Same steps as shot_tracker._dots_of. */
    _dotsOf(rgba, frameW, frameH, Hm) {
      const cv = this.cv, ppi = this.c.ppi, W = this.W, H = this.H;
      const Mv = [[ppi * Hm[0][0], ppi * Hm[0][1], ppi * Hm[0][2]],
                  [ppi * Hm[1][0], ppi * Hm[1][1], ppi * Hm[1][2]], Hm[2].slice()];
      const M = cv.matFromArray(3, 3, cv.CV_64F, Mv.flat());
      const rect = new cv.Mat(), g = new cv.Mat();
      cv.warpPerspective(rgba, rect, M, new cv.Size(W, H), cv.INTER_LINEAR, cv.BORDER_CONSTANT, new cv.Scalar(255, 255, 255, 255));
      cv.cvtColor(rect, g, cv.COLOR_RGBA2GRAY);

      // Paper brightness (median) and darkness below it, inside the mask's box only.
      const gBlur = g.roi(this.blurRect), med = new cv.Mat();
      cv.medianBlur(gBlur, med, this.blurK);
      const medBox = med.roi(this.boxRect), gBox = gBlur.roi(this.boxRect), diff = new cv.Mat();
      cv.subtract(medBox, gBox, diff);

      // Inside the picture: the frame's outline mapped onto the sheet (pixel
      // centres -0.5 .. size-0.5, as nearest-neighbour warping counts them),
      // drawn over the whole sheet and shrunk exactly as Python's erode does
      // (the sheet's own edges are not "outside"), then cut to the box.
      const shift = 4, k = 1 << shift, ox = this.boxOrigin[0], oy = this.boxOrigin[1];
      const outline = [[-0.5, -0.5], [frameW - 0.5, -0.5], [frameW - 0.5, frameH - 0.5], [-0.5, frameH - 0.5]]
        .map((pt) => applyH(Mv, pt)).map(([x, y]) => [Math.round(x * k), Math.round(y * k)]);
      const inView = new cv.Mat(H, W, cv.CV_8UC1, new cv.Scalar(0));
      const poly = cv.matFromArray(4, 1, cv.CV_32SC2, outline.flat()), polys = new cv.MatVector();
      polys.push_back(poly);
      cv.fillPoly(inView, polys, new cv.Scalar(255), cv.LINE_8, shift);
      const validFull = new cv.Mat();
      cv.erode(inView, validFull, this.kernel);
      const valid = validFull.roi(new cv.Rect(ox, oy, this.boxRect.width, this.boxRect.height));
      const validInMask = new cv.Mat();
      cv.bitwise_and(valid, this.boxMask, validInMask);
      const cover = cv.countNonZero(validInMask) / this.maskCount;

      const dark = new cv.Mat(), bw = new cv.Mat();
      cv.threshold(diff, dark, this.c.darknessThreshold, 255, cv.THRESH_BINARY);
      cv.bitwise_and(dark, validInMask, bw);
      const labels = new cv.Mat(), stats = new cv.Mat(), cent = new cv.Mat();
      const n = cv.connectedComponentsWithStats(bw, labels, stats, cent, 8, cv.CV_32S);
      const dots = [];
      for (let i = 1; i < n; i++) {
        const area = stats.intAt(i, cv.CC_STAT_AREA);
        if (area > this.areaLo && area < this.areaHi) {
          dots.push([(cent.doubleAt(i, 0) + ox - this.aim[0]) / ppi, (this.aim[1] - (cent.doubleAt(i, 1) + oy)) / ppi]);
        }
      }
      for (const m of [M, rect, g, gBlur, med, medBox, gBox, diff, inView, poly, polys, validFull, valid, validInMask, dark, bw, labels, stats, cent]) m.delete();
      return { cover, dots };
    }
  }

  function side(c) {
    return Math.max(Math.hypot(c[1][0] - c[0][0], c[1][1] - c[0][1]), Math.hypot(c[3][0] - c[0][0], c[3][1] - c[0][1]));
  }

  function invert3(m) {
    const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
    const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
            [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
            [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]];
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

  /** Does a browser result match the Python result for the same frame? The
   *  used sheet views are paired by content, not by order (the two detectors
   *  can list two views of a frame in a different order). */
  function compareWithExpected(got, expected, tolIn) {
    tolIn = tolIn || 0.05;
    const used = (list) => list.filter((s) => s.used);
    const g = used(got), e = used(expected);
    if (g.length !== e.length) return { ok: false, why: `used ${g.length} sheet view(s), expected ${e.length}` };
    const left = g.slice();
    let firstProblem = null;
    for (const ev of e) {
      const k = left.findIndex((gv) => dotsMatch(gv.dots, ev.dots, tolIn).ok);
      if (k < 0) {
        firstProblem = firstProblem || closest(left, ev.dots, tolIn);
        continue;
      }
      left.splice(k, 1);
    }
    if (left.length) return { ok: false, why: firstProblem || "a sheet view differs" };
    const n = e.reduce((a, v) => a + v.dots.length, 0);
    return { ok: true, why: e.length ? `${n} hole${n === 1 ? "" : "s"} found` + (e.length > 1 ? ` on ${e.length} sheet views` : "") : "no usable view of a sheet" };
  }

  function dotsMatch(gd, ed, tolIn) {
    if (gd.length !== ed.length) return { ok: false, why: `${gd.length} holes, expected ${ed.length}` };
    const left = gd.slice();
    for (const [x, y] of ed) {
      let best = -1, bestD = Infinity;
      left.forEach(([gx, gy], i) => { const d = Math.hypot(gx - x, gy - y); if (d < bestD) { bestD = d; best = i; } });
      if (bestD > tolIn) return { ok: false, why: `a hole is ${bestD.toFixed(3)} in from where the server put it` };
      left.splice(best, 1);
    }
    return { ok: true };
  }

  function closest(views, ed, tolIn) {
    return views.map((gv) => dotsMatch(gv.dots, ed, tolIn).why).find(Boolean);
  }

  const api = { FrameDetector, markerLayout, similarityFit, compareWithExpected, invert3, applyH, CONFIG };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BallisticaFrameDetector = api;
})(typeof window !== "undefined" ? window : globalThis);
