"""The voice equipment check ("Ballistica, equipment check", 2026-11-16) and
what it stands on: the sharpness reading (rig.measure_blur / frame_check), the
camera phone link (rig.RigRelay, the /v2/rig endpoints), and the
conversation itself (cli.py), with a fake camera.

Synthetic frames come from tests/synthetic_range.py: the real printable sheet,
blurred by a known amount.
"""
from __future__ import annotations

import threading

import cv2
import pytest

from ballistica import rig
from ballistica.cli import BallisticaCLI
from ballistica.profiles import Load, ProfileStore, Rifle
from synthetic_range import blank_frame, camera_frame


def _blurred(sigma: float, **kw):
    frame = camera_frame([], number=3, sharp=True, **kw)
    return frame if sigma == 0 else cv2.GaussianBlur(frame, (0, 0), sigma)


# ------------------------------------------------------------------ sharpness
def test_blur_reading_rises_with_blur_and_ignores_zoom():
    near = [rig.frame_check([_blurred(s)])["sharpness"]["blur_ratio"] for s in (0, 1, 2, 3)]
    assert near == sorted(near) and near[0] < 0.05
    # The same blur relative to the corner squares' size reads the same at half the size.
    far = rig.frame_check([_blurred(1.0, scale=0.5)])["sharpness"]["blur_ratio"]
    assert abs(far - rig.frame_check([_blurred(2.0)])["sharpness"]["blur_ratio"]) < 0.03


def test_sharpness_verdicts_and_their_effect_on_the_framing_check():
    good = rig.frame_check([_blurred(0)])
    assert good["sharpness"]["verdict"] == "good" and good["ok"]
    little = rig.frame_check([_blurred(3)])
    assert little["sharpness"]["verdict"] == "a little soft" and little["ok"]
    assert any("a little soft" in m for m in little["messages"])
    soft = rig.frame_check([_blurred(5)])
    assert soft["sharpness"]["verdict"] == "soft" and not soft["ok"]
    assert any("soft" in m and "focus" in m for m in soft["messages"])
    assert rig.frame_check([blank_frame()])["sharpness"] is None


def test_sharpness_is_the_middle_of_a_burst():
    one_bad = rig.frame_check([_blurred(0), _blurred(0.5), _blurred(6)])
    assert one_bad["sharpness"]["verdict"] == "good" and one_bad["sharpness"]["frames"] == 3


# ------------------------------------------------------------------ the link
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_camera_counts_as_connected_only_while_checking_in():
    clock = Clock()
    relay = rig.RigRelay(clock)
    assert relay.camera_status("u")["connected"] is False
    assert relay.camera_checkin("u", "phone-1") == {"check_request": None, "next_checkin_ms": rig.CHECKIN_SLOW_MS}
    assert relay.camera_status("u")["connected"] is True
    clock.t += rig.CAMERA_FRESH_S + 1
    assert relay.camera_status("u")["connected"] is False
    assert relay.camera_status("someone else")["connected"] is False


def test_a_check_request_reaches_the_phone_and_the_answer_reaches_the_voice_turn():
    relay = rig.RigRelay()
    relay.camera_checkin("u", "phone-1")
    link = rig.CameraLink(relay, "u", "tablet-1", wait_s=5)
    answer = {"ok": True}

    def phone():
        while True:
            reply = relay.camera_checkin("u", "phone-1")
            if reply["check_request"]:
                assert reply["next_checkin_ms"] == rig.CHECKIN_FAST_MS      # hurry while a check runs
                assert relay.deliver("u", "phone-1", reply["check_request"], answer)
                return

    t = threading.Thread(target=phone)
    t.start()
    assert link.look() == answer
    t.join()
    assert link.status() == {"connected": True, "calling": False, "device_id": "phone-1",
                             "seen_s_ago": pytest.approx(0, abs=1), "same_device": False}
    assert rig.CameraLink(relay, "u", "phone-1").status()["same_device"] is True


def test_no_answer_times_out_and_a_late_answer_is_refused():
    relay = rig.RigRelay()
    link = rig.CameraLink(relay, "u", "tablet-1", wait_s=0.05)
    assert link.look() is None
    request = relay.camera_checkin("u", "phone-1")["check_request"]
    assert request is None                                   # the expired request isn't handed out
    assert relay.deliver("u", "phone-1", "stale-id", {}) is False


# ------------------------------------------------------------------ the conversation
class FakeCamera:
    """Stands in for rig.CameraLink: connected or not, and a queue of
    frame_check() results to hand back, one per look."""

    def __init__(self, looks, connected=True, same_device=False):
        self.looks, self.connected, self.same_device = list(looks), connected, same_device
        self.taken = 0

    def status(self):
        return {"connected": self.connected, "device_id": "phone", "seen_s_ago": 1.0, "same_device": self.same_device}

    def look(self):
        self.taken += 1
        return self.looks.pop(0) if self.looks else None


def _look(blur=0, **kw):
    return rig.frame_check([_blurred(blur, **kw)])


def _load(name):
    return Load(name=name, bullet_weight_gr=140, bc=0.6, drag_model="G7", muzzle_velocity_fps=2700, zero_distance_yd=100)


def _cli(tmp_path, camera, rifles=True):
    store = ProfileStore(tmp_path / "profiles.json")
    if rifles:
        tikka = Rifle(name="Tikka T3x 6.5 Creedmoor", scope_height_in=2.0, click_value_mrad=0.1)
        tikka.add_load(_load("Hornady 140 ELD-M"))
        tikka.add_load(_load("Handload 41.5gr H4350"))
        store.add_rifle(tikka)
        ar = Rifle(name="AR-15 20-inch Faxon", scope_height_in=2.5, click_value_mrad=0.1)
        ar.add_load(_load("Sierra MatchKing 77"))
        store.add_rifle(ar)                      # active
    cli = BallisticaCLI(store)
    cli._camera = camera
    return cli, store


def _say(cli, text):
    """One voice turn the way the API runs it: state saved and restored in between."""
    state = cli.to_state()
    cli.load_state(state)
    return cli.handle(text)


def test_everything_good_ends_all_set_and_sets_the_rifle_and_ammunition(tmp_path):
    cli, store = _cli(tmp_path, FakeCamera([_look(0)]))
    first = _say(cli, "Ballistica, equipment check")
    assert "I see sheet 3, all four corners." in first and "The picture's sharp." in first
    assert "Phone and tablet are connected." in first
    assert first.index("sharp") < first.index("connected") < first.index("What caliber are you shooting?")
    assert "Tikka" not in first                              # asked by caliber, not by reading a list
    second = _say(cli, "I'm shooting the Tikka")              # "shooting" must not end the check
    assert second.startswith("The Tikka T3x 6.5 Creedmoor.") and "which ammunition" in second
    assert store.active_rifle_name == "Tikka T3x 6.5 Creedmoor"
    third = _say(cli, "the hand load")
    assert third.endswith("All set.")
    assert store.get_active_rifle().active_load_name == "Handload 41.5gr H4350"
    assert cli._equipment_check is None


def test_camera_on_the_same_device_is_said_so(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(0)], same_device=True))
    assert "The camera's running on this device." in _say(cli, "equipment check")


def test_soft_picture_is_coached_until_sharp(tmp_path):
    # blur readings: 8 -> 0.39, 5 -> 0.30, 7 -> 0.36, 3.5 -> 0.23 (a little soft), 0.5 -> sharp
    camera = FakeCamera([_look(8), _look(5), _look(7), _look(3.5), _look(0.5)])
    cli, _ = _cli(tmp_path, camera)
    first = _say(cli, "equipment check")
    assert "The picture's soft." in first and "say check focus" in first and "What caliber" not in first
    assert "Better." in _say(cli, "check focus")
    assert "softer" in _say(cli, "how's that").lower()
    assert "Better, close now." in _say(cli, "check focus")
    done = _say(cli, "check focus")
    assert done.startswith("That's sharp now.") and "What caliber" in done
    assert camera.taken == 5
    _say(cli, "the AR")
    assert _say(cli, "first one").endswith("All set.")


def test_a_corner_hidden_by_blur_is_not_left_on_the_list_once_sharp(tmp_path):
    soft_missing = _look(4)          # at this blur one corner square isn't found
    assert soft_missing["corners_missing"]
    cli, _ = _cli(tmp_path, FakeCamera([soft_missing, _look(0)]))
    first = _say(cli, "equipment check")
    assert "but not the" in first
    done = _say(cli, "check focus")
    assert done.startswith("That's sharp now.") and "But" not in done.split("Phone")[0]
    assert _say(cli, "AR-15") == "The AR-15 20-inch Faxon -- right?"
    _say(cli, "yes")
    assert _say(cli, "the MatchKing").endswith("All set.")


def test_good_enough_moves_on_and_the_softness_is_listed_at_the_end(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(3)]))
    assert "The picture's a little soft." in _say(cli, "equipment check")
    moved = _say(cli, "that's good enough")
    assert moved.startswith("Okay, leaving the focus there.") and "What caliber" in moved
    _say(cli, "the AR")
    assert _say(cli, "MatchKing").endswith("Still needs fixing: the picture is still a little soft.")


def test_framing_problems_are_said_and_listed(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(0, offset_px=(575, 180))]))
    first = _say(cli, "equipment check")
    assert "right at the edge of the picture" in first
    _say(cli, "AR")
    end = _say(cli, "MatchKing")
    assert "Still needs fixing:" in end and "edge of the picture" in end


def test_no_sheet_in_view(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([rig.frame_check([blank_frame()])]))
    assert "doesn't see a target sheet" in _say(cli, "equipment check")


def test_camera_phone_not_connected_still_runs_the_rest(tmp_path):
    camera = FakeCamera([], connected=False)
    cli, _ = _cli(tmp_path, camera)
    first = _say(cli, "Ballistica, equipment check")
    assert "not hearing from the camera phone" in first and "What caliber" in first
    assert camera.taken == 0
    _say(cli, "Tikka")
    end = _say(cli, "the factory ammo, the ELD-M")
    assert end.endswith("Still needs fixing: the camera phone isn't connected: on the phone, open Range camera "
                        "and tap Start camera.")


def test_camera_that_does_not_answer(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([None]))
    assert "didn't send me a picture in time" in _say(cli, "equipment check")


def test_no_camera_link_at_all(tmp_path):
    cli, _ = _cli(tmp_path, None)
    assert "can't reach a camera from here" in _say(cli, "equipment check")


def test_no_rifles_saved(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(0)]), rifles=False)
    reply = _say(cli, "equipment check")
    assert "Still needs fixing: no rifle is saved yet" in reply and cli._equipment_check is None


def test_unclear_answers_are_asked_again_then_listed(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(0)]))
    _say(cli, "equipment check")
    assert _say(cli, "the blue one").startswith("Didn't catch which rifle")
    assert _say(cli, "umm").endswith("Still needs fixing: I didn't get which rifle.")


def test_stop_ends_the_check(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(5)]))
    _say(cli, "equipment check")
    assert _say(cli, "stop the equipment check") == "Okay, stopped the equipment check."
    assert cli._equipment_check is None


def test_starting_a_check_drops_a_half_asked_question(tmp_path):
    cli, _ = _cli(tmp_path, FakeCamera([_look(0)]))
    cli._pending_rifle_switch = ["Tikka T3x 6.5 Creedmoor", "AR-15 20-inch Faxon"]
    assert "Equipment check." in _say(cli, "equipment check")
    assert cli._pending_rifle_switch is None


# ------------------------------------------------------------------ the API
@pytest.fixture
def api_client(monkeypatch):
    from fastapi.testclient import TestClient
    import ballistica.api as api
    relay = rig.RigRelay()
    monkeypatch.setattr(rig, "relay", relay)
    api.app.dependency_overrides[api._verify_bearer] = lambda: ("user-1", "token")
    try:
        yield TestClient(api.app), relay
    finally:
        api.app.dependency_overrides.pop(api._verify_bearer, None)


def test_api_checkin_and_frames(api_client):
    client, relay = api_client
    r = client.post("/v2/rig/camera/checkin", json={"device_id": "phone-abc123"})
    assert r.status_code == 200 and r.json()["check_request"] is None
    request_id = relay.request_check("user-1")
    assert client.post("/v2/rig/camera/checkin", json={"device_id": "phone-abc123"}).json()["check_request"] == request_id
    ok, jpeg = cv2.imencode(".jpg", _blurred(0))
    files = [("images", ("f0.jpg", jpeg.tobytes(), "image/jpeg")), ("images", ("f1.jpg", jpeg.tobytes(), "image/jpeg"))]
    r = client.post("/v2/rig/camera/frames", data={"device_id": "phone-abc123", "request_id": request_id}, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["sheet_number"] == 3 and r.json()["sharpness"]["frames"] == 2
    assert relay.wait_result("user-1", request_id, 1)["sheet_number"] == 3
    # The same request again: nobody is waiting for it any more.
    r = client.post("/v2/rig/camera/frames", data={"device_id": "phone-abc123", "request_id": request_id}, files=files)
    assert r.status_code == 409


def test_api_rejects_bad_device_ids_and_too_many_frames(api_client):
    client, relay = api_client
    assert client.post("/v2/rig/camera/checkin", json={"device_id": "../../etc"}).status_code == 422
    request_id = relay.request_check("user-1")
    ok, jpeg = cv2.imencode(".jpg", _blurred(0))
    files = [("images", (f"f{i}.jpg", jpeg.tobytes(), "image/jpeg")) for i in range(5)]
    r = client.post("/v2/rig/camera/frames", data={"device_id": "phone-abc123", "request_id": request_id}, files=files)
    assert r.status_code == 422
