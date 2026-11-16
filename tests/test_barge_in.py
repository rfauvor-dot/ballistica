"""Barge-in (Rick, 2026-11-16: "she should stop talking the moment I speak").

Runs the page's own watchForBargeIn() / playAudioBlob() from
ballistica/web/index.html under Node, with a fake microphone level and a fake
audio element: her own voice leaking back into the mic must not cut her off,
his voice must, and only when the caller asked for barge-in.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "ballistica" / "web" / "index.html"
NODE = shutil.which("node")

HARNESS = r"""
const URL = { createObjectURL: () => 'blob:x', revokeObjectURL() {} };
const requestAnimationFrame = (cb) => setTimeout(cb, 16);
let sharedAudioEl = null;
let sharedMicStream = {};
let micLevel = () => 0;
let sharedAnalyser = {
  frequencyBinCount: 8,
  getByteFrequencyData(arr) { arr.fill(micLevel()); },
};
class Audio {
  play() {
    this.paused = false;
    this.endTimer = setTimeout(() => { if (!this.paused && this.onended) this.onended(); }, LENGTH_MS);
    return Promise.resolve();
  }
  pause() { this.paused = true; clearTimeout(this.endTimer); }
}
__CODE__
async function run(levelAt, withBargeIn) {
  const t0 = Date.now();
  micLevel = () => levelAt(Date.now() - t0);
  let called = false;
  const interrupted = await playAudioBlob({}, { onBargeIn: withBargeIn ? () => { called = true; } : null });
  return { interrupted, called, ms: Date.now() - t0, paused: sharedAudioEl.paused };
}
(async () => {
  const out = {};
  out.echoOnly = await run(() => 15, true);                          // her own voice leaking back
  out.loudEcho = await run((t) => (t < 400 ? 30 : 38), true);        // louder leak, steady
  out.heSpeaks = await run((t) => (t < 300 ? 12 : 70), true);        // he talks over her
  out.notAsked = await run((t) => (t < 300 ? 12 : 70), false);       // no barge-in requested
  out.blip = await run((t) => (t > 300 && t < 330 ? 80 : 10), true); // a click, not speech
  console.log(JSON.stringify(out));
})();
"""


def _page_code() -> str:
    page = PAGE.read_text(encoding="utf-8")
    start = page.index("const BARGE_IN_LEVEL")
    end = page.index("// Reverted to full speed", start)
    return page[start:end]


@pytest.mark.skipif(NODE is None, reason="needs Node to run the page's JavaScript")
def test_barge_in_stops_her_for_his_voice_but_not_her_own(tmp_path):
    script = tmp_path / "barge_in.js"
    script.write_text("const LENGTH_MS = 900;\n" + HARNESS.replace("__CODE__", _page_code()), encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["echoOnly"]["interrupted"] is False and out["echoOnly"]["ms"] >= 850
    assert out["loudEcho"]["interrupted"] is False
    he = out["heSpeaks"]
    assert he["interrupted"] is True and he["called"] is True and he["paused"] is True
    assert he["ms"] < 600                                     # stopped well before the reply's end
    assert out["notAsked"]["interrupted"] is False and out["notAsked"]["called"] is False
    assert out["blip"]["interrupted"] is False


def test_the_voice_loop_takes_a_barged_in_recording_as_the_next_turn():
    page = PAGE.read_text(encoding="utf-8")
    turn = page[page.index("async function runConversationTurn"):page.index("async function handleWakeWord")]
    # The recording started when he talked over her is the next turn's input...
    assert re.search(r"takePendingBargeIn\(\);\s*\(\{ blob, hadSpeech \} = await \(bargedIn \|\| recordCommand\(\)\)\)", turn)
    # ...every reply in the turn can be talked over, and doing so keeps the conversation going.
    assert turn.count("{ bargeIn: true }") == 4
    assert "!!queryData.awaiting_response || cutOff" in turn
    # Releasing the mic drops a barged-in recording nobody will pick up.
    release = page[page.index("function releaseMicStream"):page.index("async function recordCommand")]
    assert "pendingBargeIn = null" in release
