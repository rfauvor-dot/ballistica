"""The range camera's framing check (shot_tracker.check_framing, POST
/v2/target/framing) and bang detection (shot_sound.find_bangs). Synthetic
frames and synthetic sound only -- no video, no network.

The real-recording check of bang detection is tools/shot_sound_test.py: on
the November 13 range video it found a bang for 15 of the 17 known shots.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from ballistica.shot_sound import SoundConfig, find_bangs, frame_levels_db, to_mono_float
from ballistica.shot_tracker import check_framing

from synthetic_range import blank_frame, camera_frame

# =========================================================== framing check


def test_good_numbered_view_is_ready():
    r = check_framing(camera_frame([], number=3))
    assert r["ok"] and r["sheet_number"] == 3 and r["detail"] == "good"
    assert r["corners_missing"] == [] and r["corners_near_edge"] == [] and r["other_sheets"] == 0
    assert r["messages"][0].startswith("Sheet 3 found, all 4 corners in view. Good to go.")


def test_missing_corners_are_named():
    r = check_framing(camera_frame([], number=3, offset_px=(900, 180)))
    assert not r["ok"]
    assert r["corners_missing"] == ["top-right", "bottom-right"]
    assert "top-right and bottom-right corner squares" in " ".join(r["messages"])


def test_corners_at_the_edge_are_flagged():
    r = check_framing(camera_frame([], number=3, offset_px=(575, 180)))
    assert not r["ok"] and r["corners_missing"] == []
    assert r["corners_near_edge"] == ["top-right", "bottom-right"]
    assert "push them out" in " ".join(r["messages"])


def test_small_sheet_is_flagged():
    r = check_framing(camera_frame([], number=3, scale=0.45))
    assert not r["ok"] and r["detail"] == "too small" and r["other_sheets"] == 0
    assert "zoom in" in " ".join(r["messages"])


def test_two_sheets_in_view_are_flagged():
    two = np.minimum(camera_frame([], number=3, scale=0.5, offset_px=(50, 100)),
                     camera_frame([], number=5, scale=0.5, offset_px=(700, 100)))
    r = check_framing(two)
    assert not r["ok"] and r["other_sheets"] == 1
    assert "only one sheet" in " ".join(r["messages"])


def test_unnumbered_sheet_asks_for_a_numbered_one():
    r = check_framing(camera_frame([]))
    assert not r["ok"] and r["sheet_number"] is None
    assert "print a numbered sheet" in " ".join(r["messages"])


def test_no_sheet():
    r = check_framing(blank_frame())
    assert not r["ok"] and r["sheet_number"] is None and r["marker_px"] is None
    assert r["messages"][0].startswith("No target sheet found")


def test_framing_endpoint():
    import ballistica.api as api_module
    app = api_module.app
    app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    try:
        client = TestClient(app)
        ok, jpg = cv2.imencode(".jpg", camera_frame([], number=6))
        r = client.post("/v2/target/framing", files={"image": ("frame.jpg", jpg.tobytes(), "image/jpeg")})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] and r.json()["sheet_number"] == 6
        bad = client.post("/v2/target/framing", files={"image": ("x.jpg", b"not a picture", "image/jpeg")})
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_framing_endpoint_needs_sign_in():
    import ballistica.api as api_module
    r = TestClient(api_module.app).post("/v2/target/framing", files={"image": ("f.jpg", b"x", "image/jpeg")})
    assert r.status_code in (401, 422)


# ========================================================== bang detection

RATE = 16000
rng = np.random.default_rng(7)


def _noise(seconds: float, dbfs: float = -50.0) -> np.ndarray:
    return rng.normal(0, 10 ** (dbfs / 20), int(seconds * RATE)).astype(np.float32)


def _event(rise_s: float, hold_s: float, peak_dbfs: float, start_dbfs: float = -50.0) -> np.ndarray:
    """Noise whose loudness climbs from start_dbfs to peak_dbfs over rise_s,
    holds, then fades -- the shape of a shot through a phone microphone when
    rise_s is a few tens of milliseconds, of a voice when it is slower."""
    n_rise, n_hold, n_fade = int(rise_s * RATE), int(hold_s * RATE), int(0.3 * RATE)
    db = np.concatenate([np.linspace(start_dbfs, peak_dbfs, n_rise), np.full(n_hold, peak_dbfs),
                         np.linspace(peak_dbfs, start_dbfs, n_fade)])
    return (rng.normal(0, 1, len(db)) * 10 ** (db / 20)).astype(np.float32)


def _shot(peak_dbfs: float = -12.0) -> np.ndarray:
    return _event(0.035, 0.4, peak_dbfs)       # measured on November 13: ~30 dB in 30-40 ms, then held


def _track(*parts) -> np.ndarray:
    return np.concatenate(parts)


def test_a_shot_is_found_at_the_right_time():
    audio = _track(_noise(2.0), _shot(), _noise(2.0))
    bangs = find_bangs(audio, RATE)
    assert len(bangs) == 1
    assert 2.0 <= bangs[0].t_s <= 2.05
    assert -14 <= bangs[0].peak_db <= -10


def test_shots_a_second_apart_are_both_found():
    audio = _track(_noise(1.0), _shot(), _noise(0.3), _shot(), _noise(1.0))
    assert len(find_bangs(audio, RATE)) == 2


def test_a_voice_rising_slowly_is_not_a_bang():
    audio = _track(_noise(1.5), _event(0.4, 1.0, -18.0), _noise(1.0))
    assert find_bangs(audio, RATE) == []


def test_a_distant_quiet_bang_is_ignored():
    audio = _track(_noise(1.5, -65), _event(0.03, 0.2, -36.0, start_dbfs=-65), _noise(1.0, -65))
    assert find_bangs(audio, RATE) == []


def test_steady_loud_noise_is_not_a_bang():
    assert find_bangs(_noise(4.0, -15.0), RATE) == []


def test_detection_is_causal():
    """Live use: a bang must be found the same way whatever comes after it."""
    audio = _track(_noise(1.5), _shot(), _noise(1.0), _shot(-14), _noise(1.5))
    full = find_bangs(audio, RATE)
    cut = find_bangs(audio[: int(3.0 * RATE)], RATE)
    assert len(full) == 2 and cut == full[:1]


def test_an_echo_right_after_a_shot_is_not_a_second_bang():
    audio = _track(_noise(1.0), _shot(), _noise(1.0))
    echo_at = int((1.0 + 0.15) * RATE)            # a second sharp burst 0.15 s in: the berm echo
    audio[echo_at:echo_at + 800] += _event(0.01, 0.03, -11.0)[:800]
    assert len(find_bangs(audio, RATE)) == 1


def test_helpers():
    pcm = np.array([0, 16384, -16384, 0], np.int16)
    assert to_mono_float(pcm, 2).tolist() == [0.25, -0.25]
    assert to_mono_float(pcm, 1).tolist() == [0.0, 0.5, -0.5, 0.0]
    levels = frame_levels_db(np.full(RATE, 0.5, np.float32), RATE, 5.0)
    assert len(levels) == 200 and levels[0] == pytest.approx(-6.02, abs=0.01)
    assert SoundConfig().refractory_s == 0.25
