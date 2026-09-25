"""Target photo measurement (2026-09-24). Everything here uses SYNTHETIC
photos: render the real printable sheet, punch known holes into it, then put
it through a simulated camera (perspective, rotation, lighting gradient, blur,
noise). Truth is known exactly, so accuracy is checkable. Real photos are the
next validation."""
import math

import cv2
import numpy as np
import pytest

from ballistica import target

BULLET = 0.224  # inches, a .223/5.56 bullet -- also the drawn hole diameter


def _punch(sheet_img, dpi, holes_in, diameter_in=BULLET, color=45):
    img = sheet_img.copy()
    for x, y in holes_in:
        cv2.circle(img, (int(round(x * dpi)), int(round(y * dpi))), int(round(diameter_in * dpi / 2)), color, -1, cv2.LINE_AA)
    return img


def _photograph(sheet_gray, dpi, *, rot_deg=0.0, tilt=0.0, scale=0.30, seed=1, blur=1.2, noise=4.0,
                gradient=0.25, canvas=(1600, 2000)):
    """Simulate a phone photo of the printed sheet: place it on a wall-colored
    background, rotate, apply a perspective tilt, light gradient, blur, noise."""
    rng = np.random.default_rng(seed)
    h, w = sheet_gray.shape
    W, H = canvas
    s = scale * dpi / 300.0
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    cx, cy = W / 2, H / 2
    dst = np.float32([[-w * s / 2, -h * s / 2], [w * s / 2, -h * s / 2], [w * s / 2, h * s / 2], [-w * s / 2, h * s / 2]])
    t = math.radians(rot_deg)
    R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    dst = (dst @ R.T)
    dst[:, 0] *= 1 + tilt * np.array([1, 1, -1, -1]) * 0.15      # keystone: top wider than bottom (or reverse)
    dst[:, 1] *= 1 - tilt * 0.05
    dst += [cx, cy]
    M = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
    wall = np.full((H, W), 90, np.uint8)
    img = cv2.warpPerspective(sheet_gray, M, (W, H), dst=wall, borderMode=cv2.BORDER_TRANSPARENT).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    light = 1.0 - gradient * (xx / W * 0.7 + yy / H * 0.3)         # one side darker
    img = img * light
    img = cv2.GaussianBlur(img, (0, 0), blur)
    img += rng.normal(0, noise, img.shape)
    return cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


@pytest.fixture(scope="module")
def sheet300():
    return target.render_sheet(300)


def _truth_group(n=5, spread=0.5, offset=(0.3, 0.2), seed=3):
    rng = np.random.default_rng(seed)
    ax, ay = target.AIM_IN
    pts = []
    while len(pts) < n:
        p = (ax + offset[0] + rng.normal(0, spread / 2.5), ay + offset[1] + rng.normal(0, spread / 2.5))
        if all(math.dist(p, q) > 0.35 for q in pts) and abs(math.dist(p, (ax, ay)) - 1.0) > 0.3:   # not on the bull ring
            pts.append(p)
    return pts


def _match(found, truth, tol):
    """Each truth point must have a detected hole within tol; returns worst error."""
    worst = 0.0
    for t in truth:
        d = min(math.dist((h.x_in, h.y_in), t) for h in found)
        worst = max(worst, d)
    return worst


# --------------------------------------------------------------- the sheet
def test_sheet_renders_letter_size_with_four_detectable_markers(sheet300):
    assert sheet300.shape == (3300, 2550)
    det = cv2.aruco.ArucoDetector(target._aruco_dict(), cv2.aruco.DetectorParameters())
    _, ids, _ = det.detectMarkers(sheet300)
    assert sorted(ids.ravel().tolist()) == [0, 1, 2, 3]


def test_sheet_pdf_is_a_valid_single_letter_page_at_actual_size():
    pdf = target.render_sheet_pdf(300)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/MediaBox [0 0 612 792]" in pdf
    assert b"/Width 2550 /Height 3300" in pdf
    # xref offsets must point at "N 0 obj" -- a wrong offset makes viewers reject or "repair" the file
    start = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert pdf[start:start + 4] == b"xref"
    lines = pdf[start:].split(b"\n")
    offsets = [int(l[:10]) for l in lines[3:8]]
    for i, off in enumerate(offsets, start=1):
        assert pdf[off:off + len(b"%d 0 obj" % i)] == b"%d 0 obj" % i
    # and the image stream must actually decode back to the sheet
    import re, zlib
    m = re.search(rb"/Length (\d+) >>\nstream\n", pdf[pdf.index(b"/Subtype /Image"):])
    body_start = pdf.index(b"/Subtype /Image") + m.end()
    raw = zlib.decompress(pdf[body_start:body_start + int(m.group(1))])
    assert len(raw) == 2550 * 3300


# ---------------------------------------------------------- sheet mode
@pytest.mark.parametrize("rot,tilt", [(0, 0.0), (7, 0.0), (-12, 0.3), (180, 0.0), (4, -0.4)])
def test_sheet_mode_finds_every_hole_within_a_few_hundredths_of_an_inch(sheet300, rot, tilt):
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet300, 300, truth), 300, rot_deg=rot, tilt=tilt)
    r = target.analyze_photo(photo, distance_yd=36, bullet_diameter_in=BULLET)
    assert r.mode == "sheet" and r.markers_found == 4 and r.scale_ppi == target.DETECT_PPI
    assert len(r.holes) == 5, r.warnings
    assert _match(r.holes, truth, 0.05) < 0.05
    assert all(0.15 < h.diameter_in < 0.32 for h in r.holes)     # measured hole size lands near the bullet
    assert not [w for w in r.warnings if "warped" in w or "average" in w]


def test_group_measurements_match_the_known_truth(sheet300):
    truth = _truth_group(5, spread=0.6, offset=(0.4, -0.25))
    photo = _photograph(_punch(sheet300, 300, truth), 300, rot_deg=3)
    r = target.analyze_photo(photo, 36, BULLET)
    got = target.group_stats([(h.x_in, h.y_in) for h in r.holes], 36, r.aim_in, BULLET)
    want = target.group_stats(truth, 36, target.AIM_IN, BULLET)
    assert abs(got["extreme_spread_in"] - want["extreme_spread_in"]) < 0.05
    assert abs(got["offset_from_aim_in"]["right_in"] - want["offset_from_aim_in"]["right_in"]) < 0.05
    assert abs(got["offset_from_aim_in"]["down_in"] - want["offset_from_aim_in"]["down_in"]) < 0.05


def test_a_hole_overlapping_the_crosshair_is_still_found(sheet300):
    ax, ay = target.AIM_IN
    truth = [(ax + 0.02, ay - 0.01), (ax + 1.7, ay + 0.9), (ax - 1.5, ay + 1.2)]
    photo = _photograph(_punch(sheet300, 300, truth), 300)
    r = target.analyze_photo(photo, 36, BULLET)
    assert len(r.holes) == 3 and _match(r.holes, truth, 0.06) < 0.06


def test_the_printed_ring_and_crosshair_are_not_mistaken_for_holes(sheet300):
    photo = _photograph(sheet300, 300)   # an empty sheet
    r = target.analyze_photo(photo, 36, BULLET)
    assert r.mode == "sheet" and r.holes == []


def test_partially_visible_markers_still_work_but_warn(sheet300):
    truth = _truth_group(4)
    photo = _photograph(_punch(sheet300, 300, truth), 300)
    # cover one corner marker (bottom-right) with wall color
    H, W = photo.shape[:2]
    det = cv2.aruco.ArucoDetector(target._aruco_dict(), cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY))
    for c, i in zip(corners, ids.ravel()):
        if i == 3:
            cv2.fillConvexPoly(photo, c.reshape(4, 2).astype(np.int32), (90, 90, 90))
    r = target.analyze_photo(photo, 36, BULLET)
    assert r.mode == "sheet" and r.markers_found == 3 and len(r.holes) == 4
    assert any("3 of the 4" in w for w in r.warnings)


def test_a_sheet_printed_at_the_wrong_size_is_caught_by_the_hole_size_check(sheet300):
    """If the print scale is wrong (or the caliber given is wrong) the holes measure far from the
    bullet size: still counted correctly, but the shooter is warned."""
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet300, 300, truth), 300)
    r = target.analyze_photo(photo, 36, 0.13)    # pretend the bullet is far smaller than the marks
    assert len(r.holes) == 5
    assert any("average" in w for w in r.warnings)


def test_marker_size_parameter_rescales_the_result(sheet300):
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet300, 300, truth), 300)
    a = target.analyze_photo(photo, 36, BULLET, marker_size_in=1.0)
    b = target.analyze_photo(photo, 36, BULLET, marker_size_in=0.9)     # printed at 90%
    ea = target.group_stats([(h.x_in, h.y_in) for h in a.holes], 36)["extreme_spread_in"]
    eb = target.group_stats([(h.x_in, h.y_in) for h in b.holes], 36)["extreme_spread_in"]
    assert eb == pytest.approx(ea * 0.9, rel=0.03)


def test_blurry_noisy_low_light_still_works(sheet300):
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet300, 300, truth), 300, blur=2.4, noise=12, gradient=0.45, seed=9)
    r = target.analyze_photo(photo, 36, BULLET)
    assert r.mode == "sheet" and len(r.holes) == 5 and _match(r.holes, truth, 0.08) < 0.08


def test_overlapping_holes_are_split_and_flagged(sheet300):
    ax, ay = target.AIM_IN
    truth = [(ax + 1.5, ay + 0.5), (ax + 1.5 + 0.12, ay + 0.5), (ax + 2.1, ay + 1.4), (ax + 2.9, ay - 0.3),
             (ax - 1.4, ay + 1.6)]      # first two nearly touch
    photo = _photograph(_punch(sheet300, 300, truth), 300)
    r = target.analyze_photo(photo, 36, BULLET)
    assert any(h.merged for h in r.holes)
    assert any("overlapping" in w for w in r.warnings)


# ----------------------------------------------------------------- free mode
def test_free_mode_on_an_unmarked_target_estimates_scale_from_the_bullet(sheet300):
    plain = np.full((3300, 2550), 255, np.uint8)
    truth = [(3.0, 4.0), (3.6, 4.4), (3.2, 4.9), (4.0, 4.1), (3.5, 3.6)]
    photo = _photograph(_punch(plain, 300, truth), 300, rot_deg=2, tilt=0.0)
    r = target.analyze_photo(photo, 36, BULLET)
    assert r.mode == "free" and r.markers_found == 0
    assert len(r.holes) == 5
    assert any("ESTIMATED" in w for w in r.warnings) and any("Print the sheet" in w for w in r.warnings)
    got = target.group_stats([(h.x_in, h.y_in) for h in r.holes], 36)["extreme_spread_in"]
    want = target.group_stats(truth, 36)["extreme_spread_in"]
    assert got == pytest.approx(want, rel=0.25)      # the promised +/-25%


def test_free_mode_without_a_scale_reports_pixels_and_says_so():
    plain = np.full((3300, 2550), 255, np.uint8)
    photo = _photograph(_punch(plain, 300, [(3.0, 4.0), (3.6, 4.4), (3.2, 4.9)]), 300)
    r = target.analyze_photo(photo, 36, None)
    assert r.scale_ppi is None and len(r.holes) == 3
    assert any("No scale" in w for w in r.warnings)


@pytest.mark.parametrize("img", [None, np.zeros((0, 0, 3), np.uint8), np.full((400, 400, 3), 200, np.uint8),
                                 np.random.default_rng(1).integers(0, 255, (500, 500, 3), dtype=np.uint8)])
def test_bad_photos_never_crash_they_warn(img):
    r = target.analyze_photo(img, 36, BULLET)
    assert isinstance(r.warnings, list) and r.warnings


def test_huge_photos_are_downscaled_and_still_measured(sheet300):
    truth = _truth_group(5)
    photo = _photograph(_punch(sheet300, 300, truth), 300, canvas=(3200, 4200), scale=0.62)
    r = target.analyze_photo(photo, 36, BULLET)
    assert r.mode == "sheet" and len(r.holes) == 5


# ------------------------------------------------------------------ group math
def test_group_stats_known_answers():
    pts = [(0.0, 0.0), (3.0, 4.0)]
    g = target.group_stats(pts, 100, aim_in=(0.0, 0.0), bullet_in=0.224)
    assert g["extreme_spread_in"] == 5.0 and g["mean_radius_in"] == 2.5
    assert g["outside_spread_in"] == pytest.approx(5.224)
    assert g["extreme_spread_moa"] == pytest.approx(5.0 / 1.0472, abs=0.01)      # 1 MOA = 1.0472 in at 100 yd
    assert g["extreme_spread_mil"] == pytest.approx(5.0 / 3.6, abs=0.01)         # 1 mil = 3.6 in at 100 yd
    assert g["center_in"] == [1.5, 2.0]
    assert g["offset_from_aim_in"]["description"] == "1.50 in right, 2.00 in low of the aim point"


def test_group_stats_edge_cases():
    assert target.group_stats([], 36)["count"] == 0
    one = target.group_stats([(1.0, 1.0)], 36, aim_in=(1.5, 0.5))
    assert one["count"] == 1 and "extreme_spread_in" not in one
    assert one["offset_from_aim_in"]["description"] == "0.50 in left, 0.50 in low of the aim point"
    assert "extreme_spread_moa" not in target.group_stats([(0, 0), (1, 0)], 0)   # no distance -> inches only


def test_offsets_are_reported_neutrally_never_as_dial_directions():
    g = target.group_stats([(1, 1), (2, 2)], 36, aim_in=(0, 0))
    text = str(g).lower()
    assert "dial" not in text and "adjust" not in text and "click" not in text


@pytest.mark.parametrize("text,expected", [
    ("6mm ARC", 0.243), (".223 Wylde", 0.224), ("300 Blackout", 0.308), ("9mm PCC", 0.355), ("5.7x28", 0.224),
    ("22 LR", 0.223), (".308 Win", 0.308), ("6.5 Creedmoor", 0.264), (".45 ACP", 0.451), (".257", 0.257),
    ("", None), (None, None), ("banana", None),
])
def test_caliber_lookup(text, expected):
    assert target.caliber_inches(text) == expected


# ------------------------------------------------ regression: the printed guides
def test_holes_on_the_bull_ring_are_visible_because_the_ring_is_light_gray(sheet300):
    """A hole through solid-black ink would be dark-on-dark and invisible; the ring and crosshair
    are printed light gray for exactly this reason (found by random-group testing: 22/40 miscounts
    with a black ring, 0/40 with the gray one)."""
    ax, ay = target.AIM_IN
    truth = [(ax + 1.0, ay), (ax, ay - 1.0), (ax - 0.71, ay + 0.71), (ax + 0.02, ay + 0.01)]   # 3 on the ring, 1 on the crosshair
    photo = _photograph(_punch(sheet300, 300, truth), 300, rot_deg=5)
    r = target.analyze_photo(photo, 36, BULLET)
    assert len(r.holes) == 4 and _match(r.holes, truth, 0.05) < 0.05


def test_guides_are_light_enough_never_to_read_as_holes(sheet300):
    assert target._GUIDE_GRAY >= 170
    assert sheet300[sheet300.shape[0] // 2:, :].min() == 0   # sanity: markers/text are still black


@pytest.mark.parametrize("seed", range(8))
def test_random_groups_are_counted_and_placed_correctly(sheet300, seed):
    """Randomized groups (3-8 holes, holes >= 0.25 in apart, random tilt/blur/noise/lighting): every
    hole found, within 0.05 in. (Tighter overlap is a documented limit -- see the module docstring.)"""
    rng = np.random.default_rng(1000 + seed)
    ax, ay = target.AIM_IN
    n = int(rng.integers(3, 9))
    pts = []
    while len(pts) < n:
        p = (ax + rng.normal(0, 0.6), ay + rng.normal(0, 0.6))
        if all(math.dist(p, q) >= 0.25 for q in pts):
            pts.append(p)
    photo = _photograph(_punch(sheet300, 300, pts), 300, rot_deg=float(rng.uniform(-15, 15)),
                        tilt=float(rng.uniform(-0.4, 0.4)), blur=float(rng.uniform(0.8, 2.2)),
                        noise=float(rng.uniform(2, 10)), gradient=float(rng.uniform(0, 0.4)), seed=seed)
    r = target.analyze_photo(photo, 36, BULLET)
    assert len(r.holes) == n, r.warnings
    assert _match(r.holes, pts, 0.05) < 0.05
