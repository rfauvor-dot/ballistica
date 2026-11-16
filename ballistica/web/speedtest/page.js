/*
 * The on-device speed test page (/speed-test): loads OpenCV.js and the browser
 * port of the detector, times it on sample frames from the November 13 range
 * video, and checks its answers against what the Python detector found
 * (manifest.json).
 *
 * Two sets of frames:
 *   - 12 single frames from across the video: every one searched in full,
 *     checked against Python (the first version of this test timed only these);
 *   - a continuous 10-second stretch (2 frames a second, as live): timed with a
 *     full search on every frame AND with the patch search (detector.js), the
 *     way live detection would run. The headline is the patch search.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const PASSES = 3;              // timed passes over the stretch, per mode, after an untimed warm-up pass
  const LIVE_FPS = 2;            // what live detection samples at
  // Shown on the page, so a result can be tied to the version that produced it.
  const TEST_VERSION = "Test version 3 (patch search + faster flattening, 2026-11-15)";

  let cv = null, detector = null, manifest = null, spot = [], stretch = [];

  function setStatus(text) { $("status").textContent = text; }

  async function loadOpenCV() {
    let obj = window.cv;
    if (!obj) throw new Error("the detector library didn't load (no connection, or it was blocked)");
    if (obj instanceof Promise) obj = await obj;
    if (!obj.Mat) await new Promise((resolve) => { obj.onRuntimeInitialized = resolve; });
    return obj;
  }

  async function loadImages(list) {
    return Promise.all(list.map(async (f) => {
      // no-cache: check with the server each time (an unchanged frame costs a quick "not modified").
      const blob = await (await fetch("/speedtest/" + f.file, { cache: "no-cache" })).blob();
      return { info: f, bitmap: await createImageBitmap(blob) };
    }));
  }

  async function loadFrames() {
    const res = await fetch("/speedtest/manifest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("couldn't load the sample frames list");
    manifest = await res.json();
    [spot, stretch] = await Promise.all([loadImages(manifest.frames), loadImages(manifest.sequence || [])]);
  }

  /** Frame -> RGBA Mat, the way a live camera frame would be read from a <video>. */
  function toMat(bitmap, canvas, ctx) {
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    ctx.drawImage(bitmap, 0, 0);
    return cv.matFromImageData(ctx.getImageData(0, 0, bitmap.width, bitmap.height));
  }

  function pct(values, p) {
    const s = values.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor(p / 100 * s.length))];
  }
  const mean = (v) => v.reduce((a, b) => a + b, 0) / v.length;
  const fmt = (ms) => ms >= 100 ? ms.toFixed(0) + " ms" : ms.toFixed(1) + " ms";
  const tick = () => new Promise((r) => setTimeout(r, 0));     // let the page repaint

  function timed(frame, canvas, ctx, track) {
    const t0 = performance.now();
    const mat = toMat(frame.bitmap, canvas, ctx);
    const t1 = performance.now();
    const result = detector.process(mat, { track });
    mat.delete();
    return { result, grab: t1 - t0 };
  }

  async function run() {
    $("runBtn").disabled = true;
    $("results").hidden = true;
    const prog = $("progress");
    prog.hidden = false;
    prog.max = spot.length + stretch.length * (1 + 2 * PASSES); prog.value = 0;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });

    // 1. Spot frames: full search each, checked against Python (also warms everything up).
    const spotChecks = [];
    for (const f of spot) {
      setStatus("Warming up and checking answers...");
      await tick();
      detector.resetTracking();
      const { result } = timed(f, canvas, ctx, false);
      spotChecks.push({ f, check: BallisticaFrameDetector.compareWithExpected(result.sheets, f.info.sheets) });
      prog.value += 1;
    }

    // 2. The stretch with patch search, untimed, checked against Python frame by frame.
    const stretchChecks = [];
    detector.resetTracking();
    for (const f of stretch) {
      await tick();
      const { result } = timed(f, canvas, ctx, true);
      stretchChecks.push({ f, search: result.search,
                           check: BallisticaFrameDetector.compareWithExpected(result.sheets, f.info.sheets) });
      prog.value += 1;
    }

    // 3. Timing: the stretch, full search on every frame vs patch search, alternating passes.
    const modes = { full: { grab: [], markers: [], flatten: [] }, patch: { grab: [], markers: [], flatten: [] } };
    let searchStats = null;
    for (let pass = 1; pass <= PASSES; pass++) {
      for (const mode of ["full", "patch"]) {
        detector.resetTracking();
        for (const f of stretch) {
          setStatus(`Timing: pass ${pass} of ${PASSES}, ${mode === "full" ? "full search" : "patch search"}`);
          await tick();
          const { result, grab } = timed(f, canvas, ctx, mode === "patch");
          const m = modes[mode];
          m.grab.push(grab); m.markers.push(result.ms.markers); m.flatten.push(result.ms.flatten);
          prog.value += 1;
        }
        if (mode === "patch") searchStats = Object.assign({}, detector.stats);
      }
    }
    prog.hidden = true;
    show(modes, spotChecks, stretchChecks, searchStats);
    $("runBtn").disabled = false;
    $("runBtn").textContent = "Run speed test again";
    setStatus("");
  }

  function perFrame(m) {
    return m.grab.map((g, i) => g + m.markers[i] + m.flatten[i]);
  }

  function show(modes, spotChecks, stretchChecks, searchStats) {
    const patchMs = mean(perFrame(modes.patch)), fullMs = mean(perFrame(modes.full));
    const fps = 1000 / patchMs, fullFps = 1000 / fullMs;
    $("avgMs").textContent = fmt(patchMs);
    $("fps").textContent = fps.toFixed(1);
    const headroom = fps / LIVE_FPS;
    $("verdict").innerHTML = (headroom >= 2
      ? `<span class="ok">Fast enough.</span> Live detection needs ${LIVE_FPS} frames a second; with the patch search this device does ${fps.toFixed(1)}, about ${headroom.toFixed(1)} times what's needed.`
      : headroom >= 1
        ? `<span class="ok">Just fast enough.</span> Live detection needs ${LIVE_FPS} frames a second; with the patch search this device does ${fps.toFixed(1)}, with little to spare once the camera and screen are also working.`
        : `<span class="no">Not fast enough on its own.</span> Live detection needs ${LIVE_FPS} frames a second; even with the patch search this device does ${fps.toFixed(1)}.`) +
      ` <span class="muted">Searching the whole frame every time: ${fmt(fullMs)} a frame, ${fullFps.toFixed(1)} frames a second -- the patch search is ${(fullMs / patchMs).toFixed(1)} times faster.</span>`;

    const rows = (m) => [
      ["Reading the frame", m.grab], ["Finding the corner squares", m.markers],
      ["Flattening the sheet and finding holes", m.flatten], ["Total per frame", perFrame(m)],
    ];
    const p = rows(modes.patch), f = rows(modes.full);
    $("detail").innerHTML = "<tr><th></th><th>Patch search</th><th>Full search</th></tr>" +
      p.map(([name, v], i) => `<tr><td>${name}</td><td>${fmt(mean(v))}<br><span class="muted">slowest 5%: ${fmt(pct(v, 95))}</span></td><td>${fmt(mean(f[i][1]))}<br><span class="muted">slowest 5%: ${fmt(pct(f[i][1], 95))}</span></td></tr>`).join("") +
      `<tr><td colspan="3" class="muted">${stretch.length} consecutive frames (${stretch[0].bitmap.width}x${stretch[0].bitmap.height}, 10 seconds at 2 a second, including a shot and the jolt it caused), ${PASSES} passes each way, after a warm-up. ` +
      (searchStats ? `In each patch-search pass, ${searchStats.fullSearches} of ${searchStats.frames} frames needed a full search (the first frame, ${searchStats.patchFallbacks} where the patches lost the sheet, and one every ${BallisticaFrameDetector.CONFIG.fullSearchEvery} frames).` : "") + "</td></tr>";

    const spotGood = spotChecks.filter((c) => c.check.ok).length, stretchGood = stretchChecks.filter((c) => c.check.ok).length;
    const allGood = spotGood === spotChecks.length && stretchGood === stretchChecks.length;
    $("parity").innerHTML = allGood
      ? `<span class="ok">Yes, on all ${spotGood + stretchGood} frames.</span> Same sheet views used and the same holes found as the server's detector, with the patch search on the 10-second stretch.`
      : `<span class="no">On ${spotGood} of ${spotChecks.length} single frames and ${stretchGood} of ${stretchChecks.length} stretch frames.</span> The differences are listed below.`;
    const line = (c, extra) => `<tr><td>${c.f.info.video_s}s</td><td class="${c.check.ok ? "ok" : "no"}">${c.check.ok ? "match" : "differs"}</td><td class="muted">${extra ? extra + ", " : ""}${c.check.why}</td></tr>`;
    $("parityTable").innerHTML =
      `<tr><th colspan="3">Single frames, full search</th></tr>` + spotChecks.map((c) => line(c)).join("") +
      `<tr><th colspan="3">10-second stretch, patch search</th></tr>` + stretchChecks.map((c) => line(c, c.search + " search")).join("");

    const cores = navigator.hardwareConcurrency ? `${navigator.hardwareConcurrency} processor cores reported. ` : "";
    $("device").textContent = `${TEST_VERSION}. ${cores}The detector runs on one core here. ${navigator.userAgent}`;
    $("results").hidden = false;
    window.speedTestResult = {
      version: TEST_VERSION, patch: { msPerFrame: patchMs, fps }, full: { msPerFrame: fullMs, fps: fullFps },
      parity: { spot: `${spotGood}/${spotChecks.length}`, stretch: `${stretchGood}/${stretchChecks.length}` },
      searchStats, stages: { patch: Object.fromEntries(p.map(([n, v]) => [n, mean(v)])), full: Object.fromEntries(f.map(([n, v]) => [n, mean(v)])) },
      differences: spotChecks.concat(stretchChecks).filter((c) => !c.check.ok).map((c) => `${c.f.info.video_s}s: ${c.check.why}`),
    };
  }

  async function init() {
    const btn = $("runBtn");
    try {
      $("version").textContent = TEST_VERSION;
      setStatus("Downloading the detector and sample frames...");
      const t0 = performance.now();
      [cv] = await Promise.all([loadOpenCV(), loadFrames()]);
      if (!stretch.length) throw new Error("the 10-second sample stretch is missing");
      detector = new BallisticaFrameDetector.FrameDetector(cv);
      setStatus(`Ready (loaded in ${((performance.now() - t0) / 1000).toFixed(1)} s).`);
      btn.textContent = "Run speed test";
      btn.disabled = false;
      btn.addEventListener("click", () => run().catch((e) => {
        setStatus("The test stopped: " + e.message); btn.disabled = false;
      }));
    } catch (e) {
      btn.textContent = "Couldn't load";
      setStatus("Couldn't start: " + e.message);
    }
  }
  init();
})();
