"""HTTP layer for the target-photo feature (2026-09-24). Auth is overridden
(no Supabase needed) and photos are synthetic, rendered from the real sheet."""
import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import ballistica.api as api_module
from ballistica import target
from tests.test_target import BULLET, _photograph, _punch, _truth_group


@pytest.fixture
def client():
    return TestClient(api_module.app)


@pytest.fixture
def signed_in():
    api_module.app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    yield
    api_module.app.dependency_overrides.pop(api_module._verify_bearer, None)


@pytest.fixture(scope="module")
def sheet_photo_jpeg():
    sheet = target.render_sheet(300)
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet, 300, truth), 300, rot_deg=4)
    ok, buf = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return bytes(buf), truth


def test_sheet_pdf_is_public_and_a_real_pdf(client):
    r = client.get("/target-sheet.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-1.4")


def test_analyze_and_group_require_login(client):
    files = {"image": ("t.jpg", b"x" * 100, "image/jpeg")}
    assert client.post("/v2/target/analyze", files=files, data={"distance_yd": "36"}).status_code in (401, 422)
    assert client.post("/v2/target/group", json={"holes": [[1, 1]], "distance_yd": 36}).status_code in (401, 422)


def test_analyze_a_sheet_photo_end_to_end(client, signed_in, sheet_photo_jpeg):
    raw, truth = sheet_photo_jpeg
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", raw, "image/jpeg")},
                    data={"distance_yd": "36", "caliber": ".223 Rem"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["mode"] == "sheet" and j["markers_found"] == 4 and j["bullet_diameter_in"] == BULLET
    assert len(j["holes"]) == 5 and j["stats"]["count"] == 5
    assert j["stats"]["extreme_spread_in"] > 0 and "moa" in str(j["stats"]).lower()
    assert j["overlay_ppi"] == 200.0
    jpeg = base64.b64decode(j["overlay_jpeg_b64"])
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (2200, 1700)


def test_explicit_bullet_diameter_wins_over_caliber_text(client, signed_in, sheet_photo_jpeg):
    raw, _ = sheet_photo_jpeg
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", raw, "image/jpeg")},
                    data={"distance_yd": "36", "caliber": ".308", "bullet_diameter_in": "0.224"})
    assert r.json()["bullet_diameter_in"] == 0.224


def test_free_mode_without_scale_gives_no_group_math(client, signed_in):
    plain = np.full((3300, 2550), 255, np.uint8)
    photo = _photograph(_punch(plain, 300, [(3.0, 4.0), (3.6, 4.4), (3.2, 4.9)]), 300)
    ok, buf = cv2.imencode(".jpg", photo)
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", bytes(buf), "image/jpeg")}, data={"distance_yd": "36"})
    j = r.json()
    assert r.status_code == 200 and j["mode"] == "free" and j["scale_ppi"] is None
    assert j["stats"] is None      # pixel coordinates must never be presented as inches
    assert any("No scale" in w for w in j["warnings"])


def test_a_non_image_is_a_clean_422_not_a_crash(client, signed_in):
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", b"definitely not a jpeg", "image/jpeg")},
                    data={"distance_yd": "36"})
    assert r.status_code == 422 and "read" in r.json()["detail"].lower()


def test_oversized_upload_is_refused_before_decoding(client, signed_in, monkeypatch):
    monkeypatch.setattr(api_module, "_TARGET_MAX_UPLOAD_BYTES", 1000)
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", b"\xff\xd8" + b"0" * 5000, "image/jpeg")},
                    data={"distance_yd": "36"})
    assert r.status_code == 413


def test_a_huge_pixel_count_is_refused_from_the_header_without_decoding():
    """A 200 MP phone photo is ~576 MB decoded; the header says so, so it never gets decoded."""
    # minimal JPEG header: SOI, SOF0 with height 12000, width 16000
    hdr = bytes.fromhex("ffd8") + bytes.fromhex("ffc0") + (17).to_bytes(2, "big") + bytes([8]) \
        + (12000).to_bytes(2, "big") + (16000).to_bytes(2, "big") + bytes([3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    assert target.image_dimensions(hdr) == (16000, 12000)
    with pytest.raises(ValueError, match="too large"):
        target.decode_image(hdr)


def test_png_dimensions_are_read_from_the_header():
    ok, buf = cv2.imencode(".png", np.zeros((30, 50, 3), np.uint8))
    assert target.image_dimensions(bytes(buf)) == (50, 30)


@pytest.mark.parametrize("data", [{"distance_yd": "0"}, {"distance_yd": "99999"},
                                  {"distance_yd": "36", "bullet_diameter_in": "5"},
                                  {"distance_yd": "36", "marker_size_in": "9"}])
def test_bad_parameters_are_rejected(client, signed_in, sheet_photo_jpeg, data):
    r = client.post("/v2/target/analyze", files={"image": ("t.jpg", sheet_photo_jpeg[0], "image/jpeg")}, data=data)
    assert r.status_code == 422


def test_group_endpoint_recomputes_from_the_shooters_corrected_holes(client, signed_in):
    r = client.post("/v2/target/group", json={"holes": [[0, 0], [3, 4]], "distance_yd": 100,
                                              "aim_in": [0, 0], "bullet_diameter_in": 0.224})
    j = r.json()
    assert r.status_code == 200 and j["extreme_spread_in"] == 5.0 and j["outside_spread_in"] == 5.224


def test_group_endpoint_bounds_its_input(client, signed_in):
    assert client.post("/v2/target/group", json={"holes": [[0, 0]] * 201, "distance_yd": 36}).status_code == 422
    assert client.post("/v2/target/group", json={"holes": [[0, 0]], "distance_yd": -1}).status_code == 422
    assert client.post("/v2/target/group", json={"holes": [], "distance_yd": 36}).json()["count"] == 0
