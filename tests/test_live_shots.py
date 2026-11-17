"""Live shot calling (2026-11-16): the camera phone's page (/live-shots) calls
shots in its own browser and posts them; the bench device collects them from
the server and speaks them. Tests the server's half (rig.py relay and the
/v2/rig/shots endpoints), the sentence the bench device says, and that the
page is served the way the speed test is (own security policy, pinned
OpenCV, scripts addressed by content)."""
from __future__ import annotations

import hashlib
import os
import re

import pytest
from fastapi.testclient import TestClient

import ballistica.api as api
from ballistica import rig

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ballistica", "web")


def _shot(n, **kw):
    return dict(dict(n=n, sheet_number=2, x_in=0.4, y_in=1.2, t_first=10.0, t_call=11.5, off_camera_s=0), **kw)


# ------------------------------------------------------------------ the sentence
@pytest.mark.parametrize("shot, said", [
    (_shot(3, x_in=-0.42, y_in=1.21), "Shot 3, sheet 2: 1.2 inches high, 0.4 inches left."),
    (_shot(1, sheet_number=None, x_in=0.02, y_in=-1.0), "Shot 1: 1.0 inch low."),
    (_shot(4, x_in=0.01, y_in=0.03), "Shot 4, sheet 2: dead center."),
    (_shot(5, x_in=2.3, y_in=0.0), "Shot 5, sheet 2: 2.3 inches right."),
    (_shot(6, off_camera_s=31.0), "Shot 6, sheet 2: 1.2 inches high, 0.4 inches right. "
                                  "Found after the sheet was out of view, so it may be older."),
])
def test_what_the_bench_device_says(shot, said):
    assert rig.shot_sentence(shot) == said


def test_the_sentence_never_says_which_way_to_dial():
    said = rig.shot_sentence(_shot(1, x_in=-3, y_in=-2)).lower()
    for word in ("click", "moa", "mil", "turn", "dial", "adjust"):
        assert word not in said


# ------------------------------------------------------------------ the relay
def test_shots_are_collected_in_order_and_repeats_ignored():
    relay = rig.RigRelay()
    assert relay.shots_since("u", -1) == {"shots": [], "last": 0}
    assert relay.post_shot("u", "phone-1", "sess-1", _shot(1)) == 1
    assert relay.post_shot("u", "phone-1", "sess-1", _shot(2)) == 2
    assert relay.post_shot("u", "phone-1", "sess-1", _shot(1)) == 1          # resent after a dropped connection
    got = relay.shots_since("u", 0)
    assert [s["n"] for s in got["shots"]] == [1, 2] and got["last"] == 2
    assert [s["n"] for s in relay.shots_since("u", 1)["shots"]] == [2]
    assert relay.shots_since("u", -1)["shots"] == []                           # a new listener starts from now
    assert relay.post_shot("u", "phone-1", "sess-2", _shot(1)) == 3          # same number, new session: a new shot
    assert relay.shots_since("someone else", 0) == {"shots": [], "last": 0}


def test_only_the_last_shots_are_kept():
    relay = rig.RigRelay()
    for n in range(1, rig.SHOTS_KEPT + 11):
        relay.post_shot("u", "phone-1", "s", _shot(n))
    got = relay.shots_since("u", 0)
    assert len(got["shots"]) == rig.SHOTS_KEPT and got["shots"][0]["n"] == 11 and got["last"] == rig.SHOTS_KEPT + 10


def test_calling_status_follows_the_phones_check_ins():
    now = [1000.0]
    relay = rig.RigRelay(lambda: now[0])
    relay.camera_checkin("u", "phone-1", calling=True)
    assert relay.camera_status("u")["calling"] is True
    relay.camera_checkin("u", "phone-1")
    assert relay.camera_status("u")["calling"] is False
    relay.camera_checkin("u", "phone-1", calling=True)
    now[0] += rig.CAMERA_FRESH_S + 1
    assert relay.camera_status("u")["calling"] is False                      # gone quiet: not calling


# ------------------------------------------------------------------ the API
@pytest.fixture
def client(monkeypatch):
    relay = rig.RigRelay()
    monkeypatch.setattr(rig, "relay", relay)
    api.app.dependency_overrides[api._verify_bearer] = lambda: ("user-1", "token")
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(api._verify_bearer, None)


def test_api_post_and_collect(client):
    start = client.get("/v2/rig/shots")
    assert start.status_code == 200 and start.json()["shots"] == [] and start.json()["camera"]["connected"] is False
    assert client.post("/v2/rig/camera/checkin", json={"device_id": "phone-abc123", "calling": True}).status_code == 200
    body = {"device_id": "phone-abc123", "session_id": "session-abc123", "shot": _shot(1, x_in=-0.42, y_in=1.21)}
    r = client.post("/v2/rig/shots", json=body)
    assert r.status_code == 200 and r.json() == {"seq": 1}
    got = client.get("/v2/rig/shots", params={"after": start.json()["last"]}).json()
    assert got["camera"] == {"connected": True, "calling": True}
    assert [s["say"] for s in got["shots"]] == ["Shot 1, sheet 2: 1.2 inches high, 0.4 inches left."]
    assert client.get("/v2/rig/shots", params={"after": got["last"]}).json()["shots"] == []


@pytest.mark.parametrize("change", [
    {"device_id": "../etc/passwd"}, {"session_id": "x"},
    {"shot": _shot(0)}, {"shot": _shot(1, sheet_number=12)}, {"shot": _shot(1, x_in=50)},
])
def test_api_rejects_bad_shots(client, change):
    body = dict({"device_id": "phone-abc123", "session_id": "session-abc123", "shot": _shot(1)}, **change)
    assert client.post("/v2/rig/shots", json=body).status_code == 422


def test_shots_need_sign_in(monkeypatch):
    # Reject the token the way a real Supabase check would, so this doesn't
    # depend on SUPABASE_URL being set (CI has no credentials; without it the
    # real verify_token raises RuntimeError, a deliberate fail-closed 500).
    import jwt

    def reject(token):
        raise jwt.InvalidTokenError("not a real token")
    monkeypatch.setattr(api, "verify_token", reject)
    plain = TestClient(api.app)
    assert plain.get("/v2/rig/shots").status_code in (401, 403, 422)     # no Authorization header: 422
    assert plain.get("/v2/rig/shots", headers={"Authorization": "Bearer nonsense"}).status_code in (401, 403)
    assert plain.post("/v2/rig/shots", json={}).status_code in (401, 403, 422)


# ------------------------------------------------------------------ the page
def test_live_page_is_served_with_its_own_policy_and_fresh_scripts():
    r = TestClient(api.app).get("/live-shots")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in csp and "'wasm-unsafe-eval'" in csp and "frame-ancestors 'none'" in csp
    assert "no-store" in r.headers["cache-control"]
    assert "__SUPABASE_URL__" not in r.text and "__SUPABASE_ANON_KEY__" not in r.text
    assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]+"', r.text)
    for folder, name in (("speedtest", "detector.js"), ("speedtest", "shot_caller.js"), ("live", "live.js")):
        with open(os.path.join(WEB, folder, name), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:12]
        assert f'src="/{folder}/{name}?v={digest}"' in r.text
        assert TestClient(api.app).get(f"/{folder}/{name}").status_code == 200


def test_main_app_keeps_its_strict_policy():
    csp = TestClient(api.app).get("/").headers["content-security-policy"]
    assert "unsafe-eval" not in csp and "cdn.jsdelivr.net" not in csp


def test_main_app_has_the_bench_panel():
    html = TestClient(api.app).get("/").text
    assert 'id="liveCallsDetails"' in html and "/v2/rig/shots?after=" in html
