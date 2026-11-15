"""The live shot tracker (ballistica/shot_tracker.py) on synthetic camera
frames, plus the scoring used by the benchmark. No video and no network, so
this runs in CI.

Two kinds of test:
  - what the tracker does correctly today, which must keep working;
  - what it gets WRONG today, marked `xfail(strict=True)` and labelled with
    the step of the build plan that is meant to fix it. Strict means the test
    starts failing loudly the moment the behaviour is fixed, so the marker has
    to be removed in the same change -- the list below is always the true
    list of known gaps.

The real-video check is tools/shot_benchmark.py (the videos are too large for
the repo); test_stored_baseline_scores_as_recorded covers its scoring here.
"""
from __future__ import annotations

import json
import math
import os

import pytest

from ballistica.shot_scoring import TrueShot, load_truth, score
from ballistica.shot_tracker import ShotTracker, TrackerConfig

from synthetic_range import Session

# Clear of the printed ring (radius 1.0 in) and of each other.
A, B, C = (1.6, 0.5), (-0.8, -1.6), (0.3, 2.2)
MOVED = {"offset_px": (480, 330)}        # camera nudged: sheet shifts ~2.7 marker widths
SWUNG = {"offset_px": (550, 400)}        # camera re-aimed: sheet shifts ~3.7 marker widths
TILTED = {"offset_px": (300, 250), "scale": 0.9, "rotate_deg": 2}
PRINTED = {"contrast": 1.0}              # the sheet at full printed contrast (a crisp, well-exposed view)


def _positions(shots):
    return [(round(s.x_in, 2), round(s.y_in, 2)) for s in shots]


def _near(shot, point, tol=0.05):
    return math.dist((shot.x_in, shot.y_in), point) < tol


@pytest.fixture
def session():
    return Session(ShotTracker())


# =============================================================== works today

def test_blank_sheet_calls_nothing(session):
    session.show([], frames=6)
    assert session.called == []
    assert session.tracker.sheet_frames_used == 6


def test_each_new_hole_is_called_once_in_order_at_the_right_place(session):
    session.show([], 3)
    first = session.show([A], 4)
    second = session.show([A, B], 4)
    third = session.show([A, B, C], 4)
    assert [len(first), len(second), len(third)] == [1, 1, 1]
    for shot, where in zip(session.called, (A, B, C)):
        assert _near(shot, where), (shot, where)
    assert [s.n for s in session.called] == [1, 2, 3]
    assert len(session.tracker.tracks) == 1
    session.show([A, B, C], 6)               # nothing new: nothing more called
    assert len(session.called) == 3


def test_a_shot_is_called_after_n_confirm_frames(session):
    session.show([], 2)
    session.show([A], 5)
    shot, = session.called
    assert shot.t_first == pytest.approx(1.0)                 # frame 3 of a 2 fps feed
    assert shot.t_call - shot.t_first == pytest.approx(1.0)   # seen in 3 frames: 2 intervals later


def test_n_confirm_is_configurable():
    s = Session(ShotTracker(TrackerConfig(n_confirm=5)))
    s.show([A], 4)
    assert s.called == []
    s.show([A], 1)
    assert len(s.called) == 1


def test_a_dot_seen_for_only_two_frames_is_not_a_shot(session):
    session.show([], 2)
    session.show([A], 2)     # a fly, a shadow, a glitch
    session.show([], 5)
    assert session.called == []


@pytest.mark.parametrize("view", [MOVED, SWUNG, TILTED])
def test_camera_move_with_two_or_more_holes_makes_no_duplicates(session, view):
    session.show([A], 4)
    session.show([A, B], 4)
    session.show([A, B], 6, **view)
    assert _positions(session.called) == [A, B]
    assert len(session.tracker.tracks) == 1


def test_one_hole_sheet_survives_the_camera_being_re_aimed(session):
    """One confirmed hole is enough to recognise a sheet again."""
    session.show([A], 4)
    session.show([A], 6, **SWUNG)
    assert len(session.called) == 1 and len(session.tracker.tracks) == 1


def test_swinging_away_and_back_makes_no_duplicates(session):
    session.show([A, B, C], 4)
    session.look_away(5)
    session.show([A, B, C], 6, **TILTED)
    assert sorted(_positions(session.called)) == sorted([A, B, C])
    assert len(session.tracker.tracks) == 1


def test_new_hole_after_a_camera_move_is_called_on_the_same_sheet(session):
    session.show([A, B], 4)
    new = session.show([A, B, C], 5, **SWUNG)
    assert len(new) == 1 and _near(new[0], C)
    assert {s.sheet_track for s in session.called} == {1}


def test_sheet_partly_out_of_frame_is_not_used(session):
    session.show([A], 5, offset_px=(900, 180))     # right-hand markers cut off
    assert session.called == []
    assert session.tracker.sheet_frames_used == 0 and session.tracker.sheet_frames_gated == 5


def test_frame_with_no_sheet_is_ignored(session):
    session.look_away(4)
    assert session.called == [] and session.tracker.tracks == []
    assert session.tracker.frames_processed == 4


def test_holes_a_third_of_an_inch_apart_are_both_called(session):
    session.show([A], 4)
    session.show([A, (A[0] + 0.35, A[1])], 5)
    assert len(session.called) == 2


def test_hole_on_the_printed_ring_is_found_in_footage_like_contrast(session):
    session.show([(1.0, 0.0)], 5)
    assert len(session.called) == 1 and _near(session.called[0], (1.0, 0.0))


def test_shot_carries_its_stills(session):
    session.show([A], 4)
    shot, = session.called
    assert shot.frame.shape[:2] == (1500, 1400)
    sheet = shot.sheet_image()
    assert sheet.shape[:2] == (1100, 850)
    assert (sheet != shot.rect).any()             # the ring was drawn on a copy, not on the stored image
    assert set(shot.as_dict()) == {"sheet_track", "sheet_number", "t_first", "t_call", "x", "y", "off_camera_s", "n"}


# ==================================================== known gaps (must fail)

@pytest.mark.xfail(strict=True, reason="UNNUMBERED sheets only (numbered sheets fix this, see below): they carry no identity, "
                                       "so a second sheet with a hole near where the first sheet's hole is "
                                       "gets taken for the first sheet, and its shot is never called")
def test_a_different_sheet_with_a_hole_in_a_similar_place_is_a_different_sheet(session):
    session.show([A], 4)                       # sheet 1, one hole
    session.look_away(4)                       # camera re-aimed at sheet 2
    session.show([], 3, **SWUNG)               # sheet 2 is blank...
    session.show([(A[0] + 0.15, A[1] - 0.1)], 6, **SWUNG)   # ...then takes a shot near the same spot
    assert len(session.called) == 2
    assert len({s.sheet_track for s in session.called}) == 2


@pytest.mark.xfail(strict=True, reason="UNNUMBERED sheets only (numbered sheets fix this, see below): blank ones are indistinguishable")
def test_blank_sheet_keeps_its_identity_when_the_camera_is_re_aimed(session):
    session.show([], 3)
    session.show([], 3, **SWUNG)
    assert len(session.tracker.tracks) == 1


@pytest.mark.xfail(strict=True, reason="plan step 3 (compare with before): two touching holes merge into one "
                                       "blob that is too big to count, so the new hole is never called")
def test_a_hole_touching_an_old_one_is_called(session):
    session.show([A, B], 4)
    session.show([A, B, (A[0] + 0.18, A[1])], 6)
    assert len(session.called) == 3


@pytest.mark.xfail(strict=True, reason="plan step 3 (compare with before): a hole within tol_known_in of an "
                                       "old one is taken for the old one")
def test_a_hole_a_quarter_inch_from_an_old_one_is_called(session):
    session.show([A, B], 4)
    session.show([A, B, (A[0] + 0.25, A[1])], 6)
    assert len(session.called) == 3


@pytest.mark.xfail(strict=True, reason="UNNUMBERED sheets (numbered ones fix this, see below): when a sheet's only hole is swallowed by a touching one "
                                       "the sheet stops being recognised, a new empty sheet is started every "
                                       "frame, and later shots never accumulate enough frames to be called")
def test_sheet_is_not_lost_when_its_only_hole_gets_a_touching_neighbour(session):
    session.show([A], 4)
    pair = [A, (A[0] + 0.18, A[1])]
    session.show(pair, 4)
    later = session.show(pair + [B], 6)
    assert len(session.tracker.tracks) == 1
    assert any(_near(s, B) for s in later)


@pytest.mark.xfail(strict=True, reason="UNNUMBERED sheets (numbered ones fix this, see below): a tight cluster hides enough known holes that the "
                                       "sheet isn't recognised after a camera move, and its other holes are "
                                       "called again")
def test_tight_group_plus_a_camera_move_makes_no_duplicates(session):
    session.show([A, B, C], 4)
    cluster = [A, B, C, (A[0] + 0.18, A[1]), (B[0] + 0.18, B[1])]
    session.show(cluster, 4)
    session.show(cluster, 6, **SWUNG)
    assert len(session.called) <= 5
    assert len(session.tracker.tracks) == 1


@pytest.mark.xfail(strict=True, reason="plan step 3: at full printed contrast the light-gray crosshair is "
                                       "darker than the threshold and is called as a shot at dead centre")
def test_printed_crosshair_is_not_called_as_a_shot(session):
    session.show([], 6, **PRINTED)
    assert session.called == []


@pytest.mark.xfail(strict=True, reason="plan step 3: at full printed contrast a hole on the ring merges with "
                                       "the ring and is lost")
def test_hole_on_the_printed_ring_is_found_at_full_contrast(session):
    session.show([(1.0, 0.0)], 6, **PRINTED)
    assert any(_near(s, (1.0, 0.0)) for s in session.called)


@pytest.mark.xfail(strict=True, reason="plan step 3: the printed scale bar sits on the edge of the area "
                                       "searched for holes and is called as a shot when the view is tilted")
def test_printed_scale_bar_is_not_called_as_a_shot(session):
    session.show([A, B, C], 6, contrast=1.0, **TILTED)
    assert not [s for s in session.called if s.y_in < -4.0]


# ============================================================ numbered sheets

def _shows(session, number, holes, frames=4, **view):
    return session.show(holes, frames, number=number, **view)


def test_numbered_sheet_shots_carry_the_sheet_number(session):
    _shows(session, 3, [], 3)
    _shows(session, 3, [A], 4)
    shot, = session.called
    assert shot.sheet_number == 3 and _near(shot, A)
    assert session.tracker.track_summary() == [{"id": 1, "holes": 1, "frames": 7, "number": 3}]


def test_blank_numbered_sheet_keeps_its_identity_when_the_camera_is_re_aimed(session):
    _shows(session, 2, [], 3)
    _shows(session, 2, [], 3, **SWUNG)
    _shows(session, 2, [A], 5, **TILTED)
    assert len(session.tracker.tracks) == 1 and len(session.called) == 1


def test_two_numbered_sheets_with_holes_in_similar_places_stay_separate(session):
    _shows(session, 1, [], 2)
    _shows(session, 1, [A], 4)
    session.look_away(4)
    _shows(session, 2, [], 3, **SWUNG)
    _shows(session, 2, [(A[0] + 0.15, A[1] - 0.1)], 6, **SWUNG)
    session.look_away(3)
    _shows(session, 1, [A], 5, **MOVED)              # back to sheet 1: its hole is not called again
    assert [s.sheet_number for s in session.called] == [1, 2]
    holes = session.tracker.sheet_holes()
    assert sorted(holes) == [1, 2] and len(holes[1]) == 1 and len(holes[2]) == 1


def test_numbered_sheet_tight_group_plus_a_camera_move_makes_no_duplicates(session):
    _shows(session, 4, [], 2)
    _shows(session, 4, [A, B, C], 4)
    cluster = [A, B, C, (A[0] + 0.18, A[1]), (B[0] + 0.18, B[1])]
    _shows(session, 4, cluster, 4)
    _shows(session, 4, cluster, 6, **SWUNG)
    assert len(session.called) <= 3      # the touching pair is still missed (step 3), but nothing repeats
    assert len(session.tracker.tracks) == 1


def test_numbered_sheet_is_not_lost_when_its_only_hole_gets_a_touching_neighbour(session):
    _shows(session, 5, [], 2)
    _shows(session, 5, [A], 4)
    pair = [A, (A[0] + 0.18, A[1])]
    _shows(session, 5, pair, 4)
    later = _shows(session, 5, pair + [B], 6)
    assert len(session.tracker.tracks) == 1
    assert len(later) == 1 and _near(later[0], B)


def test_holes_already_on_a_numbered_sheet_when_first_seen_are_not_shots(session):
    _shows(session, 6, [A, B], 5)                    # recording started after two shots
    assert session.called == []
    assert len(session.tracker.sheet_holes()[6]) == 2
    new = _shows(session, 6, [A, B, C], 5)
    assert len(new) == 1 and _near(new[0], C)


def test_saved_holes_survive_a_restart():
    first = Session(ShotTracker())
    first.show([], 2, number=7)
    first.show([A], 4, number=7)
    saved = first.tracker.sheet_holes()
    assert len(first.called) == 1
    second = Session(ShotTracker(known_holes=saved))          # app restarted
    second.show([A], 2, number=7)
    second.show([A], 4, number=7, **SWUNG)
    assert second.called == []
    new = second.show([A, B], 5, number=7)
    assert len(new) == 1 and _near(new[0], B) and new[0].sheet_number == 7


def test_numbered_sheet_needs_three_markers(session):
    _shows(session, 8, [], 2)
    _shows(session, 8, [A], 5, offset_px=(900, 180))     # right-hand markers cut off: only two visible
    assert session.called == []


def test_unnumbered_view_is_never_matched_to_a_numbered_sheet(session):
    _shows(session, 9, [], 2)
    _shows(session, 9, [A], 4)
    session.show([], 2)                               # an unnumbered printout...
    session.show([A], 5)                              # ...with a hole in the same place
    assert [s.sheet_number for s in session.called] == [9, None]


# =================================================================== scoring

def _det(n, t, x, y, call_after=1.0):
    return {"n": n, "t_first": t, "t_call": t + call_after, "x": x, "y": y}


def test_scoring_classifies_every_detection():
    truth = [TrueShot("1", 10, 0.0, 0.0), TrueShot("1", 20, 1.0, 1.0), TrueShot("1", 30, -1.0, 2.0)]
    detections = [
        _det(1, 10.5, 0.03, 0.0),      # matched
        _det(2, 28.0, 1.0, 1.05),      # matched, but first seen 8 s after it was fired: late
        _det(3, 60.0, 0.02, 0.01),     # the first hole, called again: duplicate
        _det(4, 70.0, 3.0, -3.0),      # nothing was ever there: false call
    ]
    result = score(detections, truth)
    assert result.headline() == {
        "true_shots": 3, "matched": 2, "missed": 1, "late": 1, "duplicates": 1, "false_calls": 1,
        "mean_position_error_in": 0.04, "max_position_error_in": 0.05, "median_call_delay_s": 1.0,
    }
    assert [r["detection"] for r in result.rows] == [1, 2, None]
    assert [e["kind"] for e in result.extras] == ["duplicate", "false call"]


def test_scoring_a_perfect_and_an_empty_run():
    truth = [TrueShot("1", 10, 0.0, 0.0)]
    assert score([_det(1, 10, 0.0, 0.0)], truth).headline()["matched"] == 1
    empty = score([], truth)
    assert (empty.matched, empty.missed, empty.mean_position_error_in) == (0, 1, None)


def test_stored_baseline_scores_as_recorded():
    """The November 13 prototype result, kept in the repo, scored against the
    hand-measured table: this is the number every later change is compared
    with, and it pins the scoring rules themselves."""
    bench = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "shot_benchmark")
    with open(os.path.join(bench, "clips.json"), encoding="utf-8") as fh:
        clip, = [c for c in json.load(fh)["clips"] if c["name"] == "2026-11-13-table-tripod"]
    with open(os.path.join(bench, clip["baseline"]), encoding="utf-8") as fh:
        detections = json.load(fh)["shots"]
    result = score(detections, load_truth(os.path.join(bench, clip["truth"])))
    assert {k: getattr(result, k) for k in clip["expected"]} == clip["expected"]
    assert result.max_position_error_in < 0.13
