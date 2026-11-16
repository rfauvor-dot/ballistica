/*
 * The on-device speed test page (/speed-test): loads OpenCV.js and the browser
 * port of the detector, times it on the November 13 sample frames, and checks
 * its answers against what the Python detector found (manifest.json).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const PASSES = 3;              // timed passes over all frames, after one untimed warm-up pass
  const LIVE_FPS = 2;            // what live detection samples at (the November 13 test used 2 frames/s)

  let cv = null, detector = null, manifest = null, frames = [];

  function setStatus(text) { $("status").textContent = text; }

  async function loadOpenCV() {
    let obj = window.cv;
    if (!obj) throw new Error("the detector library didn't load (no connection, or it was blocked)");
    if (obj instanceof Promise) obj = await obj;
    if (!obj.Mat) await new Promise((resolve) => { obj.onRuntimeInitialized = resolve; });
    return obj;
  }

  async function loadFrames() {
    const res = await fetch("/speedtest/manifest.json");
    if (!res.ok) throw new Error("couldn't load the sample frames list");
    manifest = await res.json();
    frames = await Promise.all(manifest.frames.map(async (f) => {
      const blob = await (await fetch("/speedtest/" + f.file)).blob();
      return { info: f, bitmap: await createImageBitmap(blob) };
    }));
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

  async function run() {
    $("runBtn").disabled = true;
    $("results").hidden = true;
    const prog = $("progress");
    prog.hidden = false; prog.max = (PASSES + 1) * frames.length; prog.value = 0;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const grab = [], markers = [], flatten = [], total = [];
    const parity = [];
    for (let pass = 0; pass <= PASSES; pass++) {
      for (const f of frames) {
        setStatus(pass === 0 ? "Warming up..." : `Timing: pass ${pass} of ${PASSES}`);
        await new Promise((r) => setTimeout(r, 0));          // let the page repaint
        const t0 = performance.now();
        const mat = toMat(f.bitmap, canvas, ctx);
        const t1 = performance.now();
        const result = detector.process(mat);
        mat.delete();
        if (pass === 0) parity.push({ f, check: BallisticaFrameDetector.compareWithExpected(result.sheets, f.info.sheets) });
        else {
          grab.push(t1 - t0); markers.push(result.ms.markers); flatten.push(result.ms.flatten);
          total.push(result.ms.markers + result.ms.flatten);
        }
        prog.value += 1;
      }
    }
    prog.hidden = true;
    show({ grab, markers, flatten, total, parity });
    $("runBtn").disabled = false;
    $("runBtn").textContent = "Run speed test again";
    setStatus("");
  }

  function show(r) {
    const avg = mean(r.total), withGrab = avg + mean(r.grab), fps = 1000 / withGrab;
    $("avgMs").textContent = fmt(withGrab);
    $("fps").textContent = fps.toFixed(1);
    const headroom = fps / LIVE_FPS;
    $("verdict").innerHTML = headroom >= 2
      ? `<span class="ok">Fast enough.</span> Live detection needs ${LIVE_FPS} frames a second; this device does ${fps.toFixed(1)}, about ${headroom.toFixed(1)} times what's needed.`
      : headroom >= 1
        ? `<span class="ok">Just fast enough.</span> Live detection needs ${LIVE_FPS} frames a second; this device does ${fps.toFixed(1)}, with little to spare once the camera and screen are also working.`
        : `<span class="no">Not fast enough on its own.</span> Live detection needs ${LIVE_FPS} frames a second; this device does ${fps.toFixed(1)}. The server, or only checking the sheet just after each shot is heard, would be needed.`;
    const rows = [
      ["Reading the frame", r.grab], ["Finding the corner squares", r.markers],
      ["Flattening the sheet and finding holes", r.flatten], ["Detector total (without reading)", r.total],
    ];
    $("detail").innerHTML = "<tr><th></th><th>Average</th><th>Slowest 5%</th></tr>" +
      rows.map(([name, v]) => `<tr><td>${name}</td><td>${fmt(mean(v))}</td><td>${fmt(pct(v, 95))}</td></tr>`).join("") +
      `<tr><td colspan="3" class="muted">${r.total.length} frames timed (${PASSES} passes over ${frames.length} frames of ${frames[0].bitmap.width}x${frames[0].bitmap.height}), after one warm-up pass.</td></tr>`;
    const good = r.parity.filter((p) => p.check.ok).length;
    $("parity").innerHTML = good === r.parity.length
      ? `<span class="ok">Yes, on all ${good} frames.</span> Same sheet views used and the same holes found as the server's detector.`
      : `<span class="no">On ${good} of ${r.parity.length} frames.</span> The rest are listed below.`;
    $("parityTable").innerHTML = r.parity.map((p) =>
      `<tr><td>${p.f.info.video_s}s</td><td class="${p.check.ok ? "ok" : "no"}">${p.check.ok ? "match" : "differs"}</td><td class="muted">${p.check.why}</td></tr>`).join("");
    const cores = navigator.hardwareConcurrency ? `${navigator.hardwareConcurrency} processor cores reported. ` : "";
    $("device").textContent = `${cores}The detector runs on one core here. ${navigator.userAgent}`;
    $("results").hidden = false;
    window.speedTestResult = { avgMsPerFrame: withGrab, fps, parityOk: good, parityTotal: r.parity.length,
                               stages: Object.fromEntries(rows.map(([n, v]) => [n, mean(v)])) };
  }

  async function init() {
    const btn = $("runBtn");
    try {
      setStatus("Downloading the detector and sample frames...");
      const t0 = performance.now();
      [cv] = await Promise.all([loadOpenCV(), loadFrames()]);
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
