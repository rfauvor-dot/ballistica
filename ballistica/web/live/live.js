/*
 * Live shot calling on the camera phone (/live-shots), 2026-11-16.
 *
 * Camera -> every half second a frame -> detector.js (finds the sheet and the
 * holes, with the patch search between frames) -> shot_caller.js (decides
 * which holes are new shots) -> each called shot is posted to the server
 * (POST /v2/rig/shots), where the bench device picks it up and speaks it.
 * Everything that looks at the picture runs here, on the phone; only the
 * calls go over the network, and a call that can't be sent is kept and sent
 * when the connection comes back.
 *
 * Also keeps the rig link the main app's range camera keeps (check-ins, and
 * frames for the voice equipment check), so "Ballistica, equipment check"
 * works with this page open instead of the range camera panel.
 *
 * Remembered on this phone between page loads (localStorage, per session):
 * the holes on every numbered sheet, so a reload or a phone restart doesn't
 * call old holes again. Unnumbered sheets can't be told apart, so their holes
 * aren't carried over.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const LIVE_FPS = 2;                  // frames looked at per second
  const MAX_WIDTH = 1920;              // frames are scaled down to this (the detector was tuned on 1080p)
  const STATE_KEY = "ballistica_live_session";
  const SESSION_KEY = "ballistica_session";          // the main app's saved sign-in
  const SESSION_MAX_AGE_MS = 16 * 3600 * 1000;       // a remembered session older than this starts fresh
  const meta = (name) => (document.querySelector(`meta[name="${name}"]`) || {}).content || "";
  const SUPABASE_URL = meta("ballistica-supabase-url"), SUPABASE_ANON_KEY = meta("ballistica-supabase-anon-key");

  let cv = null, detector = null, caller = null;
  let stream = null, calling = false, fromFile = false, wakeLock = null;
  let session = null;        // {id, startedAt, nOffset, holes}
  let shotsShown = [];
  const pending = [];        // calls not yet accepted by the server
  const recent = [];         // timestamps of processed frames, for the rate shown

  // ------------------------------------------------------------ storage
  function load(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode: fine */ } }
  function newId() {
    return (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^A-Za-z0-9_-]/g, "");
  }
  function deviceId() {      // the same id the main app gives this phone
    try {
      let id = localStorage.getItem("ballisticaDeviceId");
      if (!id) { id = newId(); localStorage.setItem("ballisticaDeviceId", id); }
      return id;
    } catch (e) {
      window.ballisticaPageDeviceId = window.ballisticaPageDeviceId || ("p" + Math.random().toString(36).slice(2, 14));
      return window.ballisticaPageDeviceId;
    }
  }

  // ------------------------------------------------------------ sign-in (the main app's, refreshed the same way)
  async function accessToken() {
    let s = load(SESSION_KEY);
    if (!s) return null;
    if (s.expires_at * 1000 - Date.now() < 60000) {
      try {
        const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=refresh_token`, {
          method: "POST", headers: { apikey: SUPABASE_ANON_KEY, "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: s.refresh_token }),
        });
        if (!res.ok) return null;
        const d = await res.json();
        s = { access_token: d.access_token, refresh_token: d.refresh_token, expires_at: d.expires_at,
              email: (d.user && d.user.email) || s.email };
        save(SESSION_KEY, s);
      } catch (e) { return null; }
    }
    return s.access_token;
  }

  async function api(path, options) {
    const token = await accessToken();
    if (!token) throw new Error("not signed in");
    const headers = Object.assign({ Authorization: `Bearer ${token}` }, options && options.json ? { "Content-Type": "application/json" } : {});
    return fetch(path, Object.assign({}, options, { headers, body: options && options.json ? JSON.stringify(options.json) : options && options.body }));
  }

  function showSignin() {
    const s = load(SESSION_KEY);
    $("signin").innerHTML = s
      ? `<span class="ok">Signed in${s.email ? " as " + escapeHtml(s.email) : ""}.</span>`
      : `<span class="no">Not signed in on this phone.</span> Open <a href="/">Ballistica</a>, sign in, then come back here.`;
  }

  function escapeHtml(t) { return String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  // ------------------------------------------------------------ the session (shot numbers, remembered holes)
  function startSession(fresh) {
    const old = load(STATE_KEY);
    if (!fresh && old && Date.now() - old.startedAt < SESSION_MAX_AGE_MS) {
      session = old;
    } else {
      session = { id: newId(), startedAt: Date.now(), nOffset: 0, holes: {} };
      shotsShown = [];
    }
    session.nOffsetBase = session.nOffset;     // shots this caller calls are numbered on from here
    if (!fromFile) save(STATE_KEY, session);
    caller = new BallisticaShotCaller.ShotCaller(null, session.holes);
    detector && detector.resetTracking();
    $("sessionNote").textContent = session.nOffset ? `(continuing: ${session.nOffset} called before)` : "";
    renderShots();
  }

  function remember() {
    if (fromFile || !session) return;
    session.holes = caller.sheetHoles();
    session.nOffset = session.nOffsetBase + caller.shots.length;
    save(STATE_KEY, session);
  }

  // ------------------------------------------------------------ camera
  async function fillCameras() {
    try {
      const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      const sel = $("camSel"), current = sel.value;
      sel.innerHTML = '<option value="">Back camera (default)</option>' +
        devs.map((d, i) => `<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || "Camera " + (i + 1))}</option>`).join("");
      sel.value = current;
    } catch (e) { /* the default is enough */ }
  }

  function stopSource() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    const v = $("video");
    v.srcObject = null;
    if (v.src) { URL.revokeObjectURL(v.src); v.removeAttribute("src"); v.load(); }
    v.hidden = true; $("zoomWrap").hidden = true;
  }

  async function startCamera() {
    stopCalling();
    stopSource();
    fromFile = false;
    const id = $("camSel").value;
    const video = Object.assign({ width: { ideal: 1920 }, height: { ideal: 1080 } },
      id ? { deviceId: { exact: id } } : { facingMode: { ideal: "environment" } });
    stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    const v = $("video");
    v.srcObject = stream; v.hidden = false;
    try { await v.play(); } catch (e) { /* autoplay is requested */ }
    await fillCameras();
    const track = stream.getVideoTracks()[0], caps = track.getCapabilities ? track.getCapabilities() : {};
    if (caps.zoom) {
      const z = $("zoom");
      z.min = caps.zoom.min; z.max = caps.zoom.max; z.step = caps.zoom.step || 0.1;
      z.value = track.getSettings().zoom || caps.zoom.min;
      $("zoomWrap").hidden = false;
    }
    $("camBtn").textContent = "Restart camera";
    $("callBtn").disabled = false;
    startSession(false);
    startLink();
  }

  // ------------------------------------------------------------ calling
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  function frameTime() {
    return fromFile ? $("video").currentTime : (Date.now() - session.startedAt) / 1000;
  }

  function lookOnce() {
    const v = $("video");
    if (!v.videoWidth || v.readyState < 2 || (fromFile && (v.paused || v.ended))) return null;
    const k = Math.min(1, MAX_WIDTH / Math.max(v.videoWidth, v.videoHeight));
    canvas.width = Math.round(v.videoWidth * k); canvas.height = Math.round(v.videoHeight * k);
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    const t = frameTime();
    const mat = cv.matFromImageData(ctx.getImageData(0, 0, canvas.width, canvas.height));
    let r;
    try { r = detector.process(mat, { track: true }); } finally { mat.delete(); }
    const views = r.sheets.filter((s) => s.used).map((s) => ({ number: s.number, org: s.org, s: s.s, dots: s.dots }));
    const shots = caller.processViews(views, t);
    return { r, views, shots };
  }

  function describeView(r, views) {
    if (views.length) {
      const names = views.map((v) => (v.number ? `sheet ${v.number}` : "a sheet with no number"));
      return `<span class="ok">Watching ${names.join(" and ")}.</span>`;
    }
    if (r.sheets.length) return `<span class="no">A sheet is in view but not all of it can be used</span> -- check that all four corner squares are in the picture.`;
    return `<span class="no">No sheet in view.</span>`;
  }

  async function tick() {
    if (!calling) return;
    const started = performance.now();
    try {
      const out = lookOnce();
      if (out) {
        recent.push(started);
        while (recent.length && recent[0] < started - 10000) recent.shift();
        $("status").innerHTML = describeView(out.r, out.views);
        $("rate").textContent = `${(recent.length / Math.min(10, (started - recent[0]) / 1000 || 1)).toFixed(1)} frames a second looked at` +
          (fromFile ? ` -- video at ${Math.floor($("video").currentTime)} s` : "");
        for (const shot of out.shots) called(shot);
      } else if (fromFile && $("video").ended) {
        $("status").textContent = "The video has finished.";
        stopCalling();
        return;
      }
    } catch (e) {
      $("status").textContent = "Something went wrong looking at the picture: " + (e.message || e);
    }
    setTimeout(tick, Math.max(0, 1000 / LIVE_FPS - (performance.now() - started)));
  }

  function called(shot) {
    const entry = {
      n: session.nOffsetBase + shot.n, sheet_number: shot.sheetNumber, x_in: round(shot.x, 3), y_in: round(shot.y, 3),
      t_first: round(Math.max(0, shot.tFirst), 2), t_call: round(Math.max(0, shot.tCall), 2), off_camera_s: shot.offCameraS || 0,
    };
    shotsShown.unshift(entry);
    renderShots();
    remember();
    pending.push(entry);
    flush();
  }

  function round(v, d) { const k = Math.pow(10, d); return Math.round(v * k) / k; }

  function renderShots() {
    $("shots").innerHTML = shotsShown.length
      ? shotsShown.slice(0, 30).map((s) => `<div>Shot ${s.n}${s.sheet_number ? ", sheet " + s.sheet_number : ""}: ` +
          `${Math.abs(s.y_in).toFixed(1)} in ${s.y_in >= 0 ? "high" : "low"}, ${Math.abs(s.x_in).toFixed(1)} in ${s.x_in >= 0 ? "right" : "left"}` +
          `${s.sent ? "" : ' <span class="muted">(not sent yet)</span>'}</div>`).join("")
      : "None yet.";
  }

  let flushing = false;
  async function flush() {
    if (flushing) return;
    flushing = true;
    try {
      while (pending.length) {
        const shot = pending[0];
        const res = await api("/v2/rig/shots", { method: "POST", json: { device_id: deviceId(), session_id: session.id, shot } });
        if (!res.ok) break;
        pending.shift();
        shot.sent = true;
        renderShots();
      }
    } catch (e) { /* no connection: kept, tried again with the next check-in */ }
    flushing = false;
  }

  async function startCalling() {
    if (!stream && !fromFile) return;
    calling = true;
    $("callBtn").textContent = "Stop calling shots";
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { /* not offered: keep the screen on by hand */ }
    tick();
    pingLink();
  }

  function stopCalling() {
    calling = false;
    $("callBtn").textContent = "Start calling shots";
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    pingLink();
  }

  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible" && calling && !wakeLock) {
      try { wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { /* fine */ }
    }
  });

  // ------------------------------------------------------------ rig link (check-ins, equipment check frames)
  let linkTimer = null, linkBusy = false;
  function startLink() { clearTimeout(linkTimer); linkTimer = setTimeout(checkin, 0); }
  function pingLink() { if (linkTimer) startLink(); }

  async function grabJpeg(maxWidth) {
    const v = $("video");
    if (!v.videoWidth) return null;
    const k = maxWidth ? Math.min(1, maxWidth / v.videoWidth) : 1;
    const c = document.createElement("canvas");
    c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return new Promise((resolve) => c.toBlob(resolve, "image/jpeg", 0.85));
  }

  async function sendFrames(requestId) {
    linkBusy = true;
    try {
      const fd = new FormData();
      fd.append("device_id", deviceId()); fd.append("request_id", requestId);
      for (let i = 0; i < 3; i++) {
        if (i) await new Promise((r) => setTimeout(r, 150));
        const blob = await grabJpeg(i ? 1920 : 0);
        if (blob) fd.append("images", blob, `frame${i}.jpg`);
      }
      $("link").textContent = "Linked -- sending a look for the equipment check...";
      await api("/v2/rig/camera/frames", { method: "POST", body: fd });
    } catch (e) { /* the voice check says the camera didn't answer */ }
    linkBusy = false;
  }

  async function checkin() {
    linkTimer = null;
    if (!stream && !fromFile) return;
    let next = 3000;
    try {
      const res = await api("/v2/rig/camera/checkin", { method: "POST", json: { device_id: deviceId(), calling } });
      if (res.ok) {
        const r = await res.json();
        next = r.next_checkin_ms || next;
        if (!linkBusy) $("link").innerHTML = `<span class="ok">Linked to the bench device.</span>` +
          (calling ? " Calls are spoken there (tap “Listen for shots” on the tablet)." : "");
        if (r.check_request && !linkBusy && !fromFile) sendFrames(r.check_request);
        if (pending.length) flush();
      } else {
        $("link").textContent = res.status === 401 ? "Link: sign in again in the main app." : `Link: the server said ${res.status}.`;
      }
    } catch (e) {
      $("link").innerHTML = `<span class="no">No connection.</span> Shots are still called here and sent when it comes back.`;
    }
    if ((stream || fromFile) && !linkTimer) linkTimer = setTimeout(checkin, next);
  }

  // ------------------------------------------------------------ start-up
  async function loadOpenCV() {
    let obj = window.cv;
    if (!obj) throw new Error("the detector library didn't load (no connection, or it was blocked)");
    if (obj instanceof Promise) obj = await obj;
    if (!obj.Mat) await new Promise((resolve) => { obj.onRuntimeInitialized = resolve; });
    return obj;
  }

  async function init() {
    showSignin();
    try {
      cv = await loadOpenCV();
      detector = new BallisticaFrameDetector.FrameDetector(cv);
    } catch (e) {
      $("camBtn").textContent = "Couldn't load the detector";
      $("status").textContent = e.message;
      return;
    }
    $("camBtn").textContent = "Start camera";
    $("camBtn").disabled = false;
    startSession(false);
  }

  $("camBtn").addEventListener("click", () => startCamera().catch((e) => {
    $("status").textContent = "Couldn't start the camera -- " + (e.message || e.name || "permission denied?");
  }));
  $("callBtn").addEventListener("click", () => (calling ? stopCalling() : startCalling()));
  $("newBtn").addEventListener("click", () => {
    const wasCalling = calling;
    stopCalling();
    startSession(true);
    if (wasCalling) startCalling();
  });
  $("zoom").addEventListener("input", async () => {
    const track = stream && stream.getVideoTracks()[0];
    if (track) { try { await track.applyConstraints({ advanced: [{ zoom: Number($("zoom").value) }] }); } catch (e) { /* ignore */ } }
  });
  $("fileIn").addEventListener("change", async () => {
    const file = $("fileIn").files[0];
    if (!file || !cv) return;
    stopCalling();
    stopSource();
    fromFile = true;
    const v = $("video");
    v.src = URL.createObjectURL(file); v.hidden = false; v.muted = true;
    session = { id: newId(), startedAt: Date.now(), nOffset: 0, nOffsetBase: 0, holes: {} };   // a replay never touches the live session
    caller = new BallisticaShotCaller.ShotCaller();
    detector.resetTracking();
    shotsShown = []; renderShots();
    $("sessionNote").textContent = "(replaying " + file.name + ")";
    $("callBtn").disabled = false;
    try { await v.play(); } catch (e) { /* the play button on the video works too */ }
    startLink();
    startCalling();
  });

  window.ballisticaLive = { state: () => ({ calling, fromFile, session, shots: shotsShown.slice(), pending: pending.length }) };
  init();
})();
