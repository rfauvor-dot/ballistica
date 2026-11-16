"""The on-device speed test page (/speed-test, ballistica/web/speedtest/).

The browser detector itself is checked against these same expected results
by the page, in the browser (it reported 12 of 12 frames matching when
built). Here: the page and its files are served correctly, and the stored
expected results still match what the Python detector finds today -- if the
detector changes, scripts/build_speed_test_frames.py must be re-run so the
page keeps comparing against the right answers.
"""
from __future__ import annotations

import json
import os
import re

import cv2
from fastapi.testclient import TestClient

import ballistica.api as api_module
from ballistica.shot_tracker import ShotTracker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEEDTEST = os.path.join(ROOT, "ballistica", "web", "speedtest")
client = TestClient(api_module.app)


def _manifest() -> dict:
    with open(os.path.join(SPEEDTEST, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_page_is_served_with_its_own_security_policy():
    r = client.get("/speed-test")
    assert r.status_code == 200 and "<h1>Ballistica speed test</h1>" in r.text
    csp = r.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in csp and "'wasm-unsafe-eval'" in csp
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp


def test_opencv_is_pinned_by_integrity_hash():
    html = client.get("/speed-test").text
    tag = re.search(r'<script src="(https://cdn\.jsdelivr\.net/[^"]+)"\s+integrity="(sha384-[^"]+)"\s+crossorigin="anonymous"', html)
    assert tag, "OpenCV.js must be loaded with an integrity hash"
    assert "@5.0.0-release.1/" in tag.group(1)          # an exact version, never "latest"


def test_page_files_are_served():
    for path in ("/speedtest/detector.js", "/speedtest/page.js", "/speedtest/manifest.json"):
        assert client.get(path).status_code == 200, path
    m = _manifest()
    assert len(m["frames"]) == 12
    first = client.get("/speedtest/" + m["frames"][0]["file"])
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"


def test_main_app_page_is_unchanged_by_the_speed_test_policy():
    csp = client.get("/").headers["content-security-policy"]
    assert "jsdelivr" not in csp and "unsafe-eval" not in csp


def test_expected_results_still_match_the_python_detector():
    m = _manifest()
    tracker = ShotTracker()
    c = tracker.config
    for f in m["frames"]:
        frame = cv2.imread(os.path.join(SPEEDTEST, f["file"]))
        assert frame.shape[:2] == (f["height"], f["width"])
        used = []
        for sheet in tracker._sheets_in(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)):
            good = ((sheet["found"] >= 3 and sheet["rms"] < c.max_rms_3_markers_in)
                    or (sheet["found"] == 2 and sheet["rms"] < c.max_rms_2_markers_in))
            if good:
                _, cover, dots = tracker._dots_of(frame, sheet["Hm"])
                if cover >= c.min_cover:
                    used.append(sorted([round(x, 3), round(y, 3)] for x, y in dots))
        expected = [s["dots"] for s in f["sheets"] if s["used"]]
        assert used == expected, f["file"]
