/*
 * Browser version of the shot tracker's frame-to-frame bookkeeping
 * (ballistica/shot_tracker.py: ShotTracker.process_views, _pick_track,
 * _update_track), 2026-11-16. With detector.js it is the whole live detector
 * on the phone: detector.js finds the sheet views and the dark dots on them in
 * each frame; this decides which dots are new holes and when a new hole has
 * been seen often enough to call it as a shot.
 *
 * Pure JavaScript, no OpenCV. Every rule, constant and tie-break follows the
 * Python line for line; tests/test_shot_caller.py feeds both the per-frame
 * views recorded from the whole November 13 video
 * (data/shot_benchmark/*.views.jsonl.gz) and checks they call the same shots at
 * the same times and places.
 *
 *     const caller = new ShotCaller();
 *     for each frame:  const shots = caller.processViews(views, t);
 *
 * A view is one usable sheet in the frame: {number (null if unnumbered),
 * org: [x, y] (the sheet's top-left in the picture, pixels), s (corner-square
 * size, pixels), dots: [[x_in, y_in], ...]}, plus anything else the caller
 * wants handed back with a shot: `snapshot` (e.g. an image of the sheet) is
 * kept from the frame the hole was first seen and returned on the shot.
 * detector.js gives `used` sheet entries in exactly this shape.
 *
 * Coordinates: inches from the sheet's printed crosshair, x right, y up.
 */
(function (root) {
  "use strict";

  // shot_tracker.TrackerConfig defaults (the calling part).
  const CALL_CONFIG = {
    nConfirm: 3,                 // frames a new dot must be seen in before it is called
    tolKnownIn: 0.30,            // a dot this close to a confirmed hole IS that hole
    tolCandidateIn: 0.20,        // frame-to-frame matching of a not-yet-confirmed dot
    patternMatchIn: 0.35,        // a known hole counts as "visible" within this distance
    patternMinFraction: 0.7,     // share of a sheet's holes that must be visible to recognise it
    positionMatchMarkers: 3.0,   // image-position fallback, in marker widths
    longGapS: 20.0,              // a hole first seen after the sheet was out of view this long is flagged
  };

  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  function median(values) {     // numpy.median
    const s = values.slice().sort((a, b) => a - b), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  class ShotCaller {
    /** knownHoles: {sheetNumber: [[x_in, y_in], ...]} -- holes already on
     *  numbered sheets (what sheetHoles() returned before a restart); never
     *  called as shots. */
    constructor(config, knownHoles) {
      this.c = Object.assign({}, CALL_CONFIG, config || {});
      this.tracks = [];
      for (const [number, holes] of Object.entries(knownHoles || {})) {
        const track = this._newTrack(null, null, Number(number));
        track.holes = holes.map(([x, y]) => ({ x, y, first: null, obs: [] }));
      }
      this.shots = [];
      this.framesProcessed = 0;
      this.sheetFramesUsed = 0;
    }

    /** One frame's usable sheet views, taken at time t (seconds). Returns the
     *  shots this frame confirmed -- usually none. */
    processViews(views, t) {
      const newShots = [];
      for (const view of views) {
        const track = this._pickTrack(view, view.dots, t);
        track.gap = track.lastT !== null ? t - track.lastT : 0.0;
        track.lastT = t;
        track.seenFrames += 1;
        newShots.push(...this._updateTrack(track, view, t));
        this.sheetFramesUsed += 1;
      }
      this.framesProcessed += 1;
      return newShots;
    }

    /** Every hole on every NUMBERED sheet seen so far (including those already
     *  there when it was first seen): the list to save, and to pass back as
     *  knownHoles after a restart. */
    sheetHoles() {
      const out = {};
      for (const tr of this.tracks) {
        if (tr.number !== null) out[tr.number] = tr.holes.map((h) => [Math.round(h.x * 1000) / 1000, Math.round(h.y * 1000) / 1000]);
      }
      return out;
    }

    _newTrack(org, t, number) {
      const track = { id: this.tracks.length + 1, number: number === undefined ? null : number, org, holes: [],
                      cands: [], seenFrames: 0, lastT: t, adopted: 0, gap: 0 };
      this.tracks.push(track);
      return track;
    }

    /** Which known sheet this view is -- see ShotTracker._pick_track. */
    _pickTrack(view, dots, t) {
      const c = this.c;
      if (view.number !== null && view.number !== undefined) {
        for (const tr of this.tracks) {
          if (tr.number === view.number) { tr.org = view.org; return tr; }
        }
        return this._newTrack(view.org, t, view.number);
      }
      const unnumbered = this.tracks.filter((tr) => tr.number === null);
      const hits = (tr) => tr.holes.filter((h) => dots.some((d) => dist([h.x, h.y], d) < c.patternMatchIn)).length;

      let best = null, bestFraction = 0.0;
      for (const tr of unnumbered) {
        const n = tr.holes.length;
        if (n === 0) continue;
        const h = hits(tr), fraction = h / n;
        // Python: (fraction, h) > (best_fraction, 0)
        if (h >= Math.min(n, 2) && fraction >= c.patternMinFraction &&
            (fraction > bestFraction || (fraction === bestFraction && h > 0))) {
          best = tr; bestFraction = fraction;
        }
      }
      if (best === null) {
        let near = null, nearD = 1e9;
        for (const tr of unnumbered) {
          const d = Math.hypot(view.org[0] - tr.org[0], view.org[1] - tr.org[1]) / view.s;
          if (d < c.positionMatchMarkers && d < nearD) { near = tr; nearD = d; }
        }
        if (near !== null && (near.holes.length === 0 || hits(near) >= 0.5 * near.holes.length)) best = near;
      }
      if (best === null) best = this._newTrack(view.org, t);
      best.org = view.org;
      return best;
    }

    /** New holes on one sheet -- see ShotTracker._update_track. */
    _updateTrack(tr, view, t) {
      const c = this.c, seen = new Set();
      for (const d of view.dots) {
        const known = tr.holes.filter((h) => dist([h.x, h.y], d) < c.tolKnownIn);
        if (known.length) { known[0].obs.push(d); continue; }
        let hit = -1;
        for (let ci = 0; ci < tr.cands.length; ci++) {
          if (dist([tr.cands[ci].x, tr.cands[ci].y], d) < c.tolCandidateIn) { hit = ci; break; }
        }
        if (hit < 0) {
          // On a numbered sheet, a hole already there the first time the sheet
          // is seen is not a new shot.
          const already = tr.number !== null && tr.seenFrames <= 1;
          tr.cands.push({ x: d[0], y: d[1], first: t, seen: 1, miss: 0, pts: [d], already,
                          snapshot: view.snapshot === undefined ? null : view.snapshot, gap: tr.gap || 0 });
          seen.add(tr.cands.length - 1);
        } else {
          const cand = tr.cands[hit];
          cand.seen += 1; cand.miss = 0; cand.pts.push(d); seen.add(hit);
          cand.x = median(cand.pts.map((p) => p[0]));
          cand.y = median(cand.pts.map((p) => p[1]));
        }
      }
      const newShots = [], keep = [];
      tr.cands.forEach((cand, ci) => {
        if (!seen.has(ci)) cand.miss += 1;
        if (cand.seen >= c.nConfirm) {
          tr.holes.push({ x: cand.x, y: cand.y, first: cand.first, obs: cand.pts.slice() });
          if (cand.already) { tr.adopted += 1; return; }
          const shot = {
            n: this.shots.length + 1, sheetTrack: tr.id, sheetNumber: tr.number, tFirst: cand.first, tCall: t,
            x: cand.x, y: cand.y, offCameraS: cand.gap > c.longGapS ? Math.round(cand.gap * 10) / 10 : 0,
            snapshot: cand.snapshot,
          };
          this.shots.push(shot);
          newShots.push(shot);
        } else if (cand.miss < 2) {    // a candidate that vanishes for two frames is dropped
          keep.push(cand);
        }
      });
      tr.cands = keep;
      return newShots;
    }
  }

  const api = { ShotCaller, CALL_CONFIG, median };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BallisticaShotCaller = api;
})(typeof window !== "undefined" ? window : globalThis);
