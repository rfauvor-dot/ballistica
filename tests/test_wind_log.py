"""Anemometer log + wind video pairing (ballistica/wind_log.py, tools/wind_pairs.py)."""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from ballistica import wind_log
from ballistica.wind_log import Clip, Reading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import wind_pairs  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "iAnemometer_2026-11-18-20-16-54.xls")
MDT = timezone(timedelta(hours=-6))     # May
MST = timezone(timedelta(hours=-7))     # November
HEADER = ["ID", "Time", "Wind Value(mph)", "Temp Value(�F)"]


def test_real_export_from_rick_s_meter():
    pytest.importorskip("xlrd")
    rs = wind_log.read_log(FIXTURE)
    assert len(rs) == 65
    assert rs[0].at == datetime(2026, 11, 18, 20, 15, 40) and rs[-1].at == datetime(2026, 11, 18, 20, 16, 54)
    peak = max(rs, key=lambda r: r.mph)
    assert (peak.at.strftime("%H:%M:%S"), peak.mph) == ("20:15:53", 5.8)
    assert rs[0].temp_f == 71.4


def test_log_needs_its_dated_file_name():
    assert wind_log.log_saved_at("x/iAnemometer_2026-11-18-20-16-54.xls") == datetime(2026, 11, 18, 20, 16, 54)
    with pytest.raises(ValueError, match="date lives only in the name"):
        wind_log.log_saved_at("wind.xls")


def test_log_running_past_midnight_rolls_the_date():
    rows = [["path"], HEADER, ["1", "23:59:58", "1.0", "50"], ["2", "23:59:59", "2.0", "50"],
            ["3", "00:00:00", "3.0", "50"], ["4", "00:00:01", "4.0", "50"]]
    rs = wind_log.readings_from_rows(rows, datetime(2026, 11, 19, 0, 0, 5))
    assert [r.at for r in rs] == [datetime(2026, 11, 18, 23, 59, 58), datetime(2026, 11, 18, 23, 59, 59),
                                  datetime(2026, 11, 19, 0, 0, 0), datetime(2026, 11, 19, 0, 0, 1)]


def test_log_saved_just_after_midnight_belongs_to_the_day_before():
    rows = [HEADER, ["1", "23:58:00", "1.0", "50"], ["2", "23:58:01", "1.5", "50"]]
    rs = wind_log.readings_from_rows(rows, datetime(2026, 11, 19, 0, 0, 30))
    assert rs[0].at == datetime(2026, 11, 18, 23, 58, 0)


def test_other_units_are_converted_to_mph_and_fahrenheit():
    rows = [["ID", "Time", "Wind Value(m/s)", "Temp Value(°C)"], ["1", "10:00:00", "10", "20"]]
    r = wind_log.readings_from_rows(rows, datetime(2026, 11, 18, 10, 1))[0]
    assert r.mph == pytest.approx(22.37, abs=0.01) and r.temp_f == pytest.approx(68.0)
    with pytest.raises(ValueError, match="unknown wind unit"):
        wind_log.readings_from_rows([["Time", "Wind Value(furlongs)"], ["10:00:00", "1"]],
                                    datetime(2026, 11, 18, 10, 1))


def test_overlapping_logs_merge_without_doubles():
    a = [Reading(datetime(2026, 1, 1, 10, 0, s), 1.0, None) for s in (0, 1, 2)]
    b = [Reading(datetime(2026, 1, 1, 10, 0, s), 1.0, None) for s in (2, 3)]
    assert [r.at.second for r in wind_log.merge_logs([b, a])] == [0, 1, 2, 3]


# ------------------------------------------------------------------ video timing
def test_samsung_name_is_the_start_and_embedded_time_is_the_end():
    """Numbers from Rick's own 20260509_150306.mp4: 132.56 s long, embedded
    21:05:21Z = 15:05:21 MDT, which is the name's 15:03:06 plus the length."""
    clip = wind_log.clip_timing("20260509_150306.mp4", 132.56,
                                datetime(2026, 5, 9, 21, 5, 21, tzinfo=timezone.utc), MDT)
    assert clip.start == datetime(2026, 5, 9, 15, 3, 6)
    assert clip.timing_from == "file name" and clip.warning is None


def test_disagreeing_name_and_embedded_time_is_flagged():
    clip = wind_log.clip_timing("20260509_150306.mp4", 30.0,
                                datetime(2026, 5, 9, 21, 5, 21, tzinfo=timezone.utc), MDT)
    assert clip.start == datetime(2026, 5, 9, 15, 3, 6)
    assert "embedded end time" in clip.warning


def test_without_a_dated_name_the_embedded_end_time_is_used():
    clip = wind_log.clip_timing("trees.mp4", 60.0, datetime(2026, 11, 18, 22, 0, 0, tzinfo=timezone.utc), MST)
    assert clip.start == datetime(2026, 11, 18, 14, 59, 0) and clip.timing_from == "embedded end time"
    with pytest.raises(ValueError):
        wind_log.clip_timing("trees.mp4", 60.0, None, MDT)


# ------------------------------------------------------------------ pairing
T0 = datetime(2026, 11, 18, 14, 0, 0)


def _steady(seconds, mph_of=lambda s: float(s)):
    return [Reading(T0 + timedelta(seconds=s), mph_of(s), 60.0) for s in range(seconds)]


def test_windows_cover_the_clip_with_a_short_last_one_and_a_whole_clip_row():
    clip = Clip("20261118_140000.mp4", T0, 25.0, "file name")
    rows = wind_log.window_stats(_steady(40), clip, window_s=10)
    assert [r["window"] for r in rows] == ["1", "2", "3", "all"]
    assert [(r["start_s"], r["end_s"], r["readings"]) for r in rows] == [
        (0, 10, 10), (10, 20, 10), (20, 25, 5), (0, 25, 25)]
    assert rows[0]["mean_mph"] == 4.5 and rows[0]["min_mph"] == 0 and rows[0]["max_mph"] == 9
    assert rows[1]["local_time"] == "2026-11-18 14:00:10" and rows[0]["temp_f"] == 60.0
    assert all(r["note"] == "" for r in rows)


def test_gaps_and_clips_outside_the_log_are_marked():
    readings = [r for r in _steady(30) if not 10 <= (r.at - T0).seconds < 18]
    rows = wind_log.window_stats(readings, Clip("v.mp4", T0, 30.0, "file name"), window_s=10)
    assert rows[1]["note"].startswith("gap: 2 of ~10")
    later = wind_log.window_stats(readings, Clip("v.mp4", T0 + timedelta(hours=1), 20.0, "file name"))
    assert later[-1]["readings"] == 0 and later[-1]["note"] == "no readings" and later[-1]["mean_mph"] is None


def test_clock_offset_moves_the_readings_onto_the_camera_clock():
    """Camera clock 5 s ahead: the meter's 14:00:05 reading is camera second 10."""
    readings = _steady(30, mph_of=lambda s: 9.0 if s == 5 else 0.0)
    clip = Clip("v.mp4", T0, 30.0, "file name")
    rows = wind_log.window_stats(readings, clip, window_s=10, clock_offset_s=5.0)
    assert rows[0]["max_mph"] == 0.0 and rows[1]["max_mph"] == 9.0


# ------------------------------------------------------------------ the tool
def test_tool_writes_the_csv(tmp_path, capsys):
    pytest.importorskip("xlrd")
    video = "20261118_201545.mp4"
    out = tmp_path / "pairs.csv"
    end = datetime(2026, 11, 18, 20, 16, 15, tzinfo=MST).astimezone(timezone.utc)
    rows = wind_pairs.run([FIXTURE], [video], 10.0, ["270"], 0.0, str(out),
                          probe_fn=lambda p: (30.0, end), local_tz=MST, trim=False,
                          meter_headings=["20261118_201545.mp4=300"])
    with open(out, newline="", encoding="utf-8") as fh:
        saved = list(csv.DictReader(fh))
    assert len(saved) == len(rows) == 4
    assert saved[0]["heading_deg"] == "270.0" and saved[0]["meter_heading_deg"] == "300.0"
    assert saved[0]["max_mph"] == "5.8"                                          # the puff at :52-:53
    assert saved[-1]["window"] == "all"
    said = capsys.readouterr().out
    assert "65 readings" in said and "20261118_201545.mp4: 20:15:45 +30 s" in said and "warning" not in said


def _steps(shifts, response=0.85):
    return [(float(i), float(i + 1), sh, response) for i, sh in enumerate(shifts)]


def test_still_span_skips_the_walk_back_and_the_reach_to_stop():
    walk, still, bump = [80, 40, 120, 30, 90], [0.1] * 60, [150]
    assert wind_log.still_span(_steps(walk + still + bump)) == (6.0, 64.0)


def test_still_span_takes_the_longest_still_stretch():
    steps = _steps([0.1] * 25 + [60] + [0.2] * 40 + [70] + [0.1] * 22)
    assert wind_log.still_span(steps) == (27.0, 65.0)


def test_a_lone_wobble_does_not_end_a_still_stretch():
    """Rick's 12:40 clip: still on the table for ~5 minutes, with the odd 3-4 px
    wobble in gusts. That used to cut the measured part to 2 minutes 19."""
    steps = _steps([90, 40] + [0.2] * 50 + [4] + [0.5] * 40 + [3.5, 4] + [0.3] * 60 + [140])
    assert wind_log.still_span(steps) == (3.0, 154.0)


def test_a_real_move_still_ends_it():
    steps = _steps([0.2] * 30 + [25] + [0.2] * 40)                     # nudged the phone: 25 px
    assert wind_log.still_span(steps) == (32.0, 70.0)


def test_a_slow_creep_splits_the_clip_and_both_halves_are_kept():
    """Rick's 12:40 clip at 79-83 s: 3-4 px every second for four seconds
    (3.01, 3.39, 4.11, 2.98 -- the phone settling, typical shift just over
    3 px). Before and after are both good footage, so both are kept; a creep
    right on the line costs at most its own few seconds."""
    steps = _steps([90, 40] + [0.3] * 50 + [3.5, 3.8, 4.1, 3.6] + [0.4] * 60)
    assert wind_log.still_spans(steps) == [(3.0, 51.0), (57.0, 115.0)]
    assert wind_log.still_span(steps) == (57.0, 115.0)


def test_windows_never_cross_the_gap_between_still_stretches():
    clip = Clip("v.mp4", T0, 60.0, "file name")
    rows = wind_log.window_stats(_steady(60), clip, window_s=10, spans=[(2.0, 17.0), (30.0, 45.0)])
    assert [(r["window"], r["start_s"], r["end_s"], r["readings"]) for r in rows] == [
        ("1", 2.0, 12.0, 10), ("2", 12.0, 17.0, 5), ("3", 30.0, 40.0, 10), ("4", 40.0, 45.0, 5),
        ("all", 2.0, 45.0, 30)]
    assert rows[-1]["mean_mph"] == pytest.approx((sum(range(2, 17)) + sum(range(30, 45))) / 30)
    assert rows[-1]["note"] == ""


def test_handheld_or_blurry_never_counts_as_still():
    assert wind_log.still_span(_steps([15, 28, 40, 2.0, 22] * 20)) is None   # one lucky keyframe isn't a stretch
    assert wind_log.still_span(_steps([0.5] * 60, response=0.05)) is None   # tiny shift but no real match
    assert wind_log.still_span(_steps([0.1] * 15)) is None                   # still, but under 20 s


def test_windows_follow_the_still_span():
    clip = Clip("v.mp4", T0, 60.0, "file name")
    rows = wind_log.window_stats(_steady(60), clip, window_s=10, span=(6.0, 31.0))
    assert [(r["window"], r["start_s"], r["end_s"], r["readings"]) for r in rows] == [
        ("1", 6.0, 16.0, 10), ("2", 16.0, 26.0, 10), ("3", 26.0, 31.0, 5), ("all", 6.0, 31.0, 25)]
    assert rows[0]["local_time"] == "2026-11-18 14:00:06"
    flagged = wind_log.window_stats(_steady(60), clip, window_s=30, note="camera not still")
    assert all(r["note"] == "camera not still" for r in flagged)


def test_tool_measures_only_the_still_part_and_flags_a_moving_camera(tmp_path, capsys):
    pytest.importorskip("xlrd")
    end = datetime(2026, 11, 18, 20, 16, 25, tzinfo=MST).astimezone(timezone.utc)
    still = _steps([90, 50] + [0.1] * 30 + [120])          # keyframes 0..33 s
    rows = wind_pairs.run([FIXTURE], ["20261118_201545.mp4"], 10.0, [], 0.0, str(tmp_path / "a.csv"),
                          probe_fn=lambda p: (40.0, end), local_tz=MST, motion_fn=lambda p: still)
    assert (rows[-1]["start_s"], rows[-1]["end_s"], rows[-1]["note"]) == (3.0, 31.0, "")
    moving = _steps([30, 12, 50] * 13)
    rows = wind_pairs.run([FIXTURE], ["20261118_201545.mp4"], 10.0, [], 0.0, str(tmp_path / "b.csv"),
                          probe_fn=lambda p: (40.0, end), local_tz=MST, motion_fn=lambda p: moving)
    assert rows[-1]["start_s"] == 0.0 and all(r["note"].startswith("camera not still") for r in rows)
    assert "never held still" in capsys.readouterr().out
    rows = wind_pairs.run([FIXTURE], ["20261118_201545.mp4"], 10.0, [], 0.0, str(tmp_path / "c.csv"),
                          probe_fn=lambda p: (40.0, end), local_tz=MST, start_s=12, end_s=32,
                          motion_fn=lambda p: pytest.fail("set by hand: no motion analysis"))
    assert (rows[-1]["start_s"], rows[-1]["end_s"]) == (12.0, 32.0)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_motion_from_a_real_video_walk_still_bump(tmp_path):
    """Moving crop for 5 s (walking back), a still 33 s with flicker noise
    (leaves, sensor noise), then a 2 s bump (reaching to stop)."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    rng = np.random.default_rng(7)
    scene = cv2.GaussianBlur(rng.integers(0, 255, (540, 1000, 3), dtype=np.uint8), (0, 0), 3)
    cv2.imwrite(str(tmp_path / "scene.png"), scene)
    video = tmp_path / "20261120_100000.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(tmp_path / "scene.png"),
                    "-t", "40", "-vf",
                    "crop=w=800:h=450:x='if(lt(t,5),t*30+8*sin(t*9),150)+if(gt(t,38),(t-38)*60,0)'"
                    ":y='if(lt(t,5),20+10*sin(t*7),20)',noise=alls=12:allf=t,format=yuv420p",
                    "-c:v", "libx264", "-g", "30", "-preset", "veryfast", str(video)], check=True)
    span = wind_log.still_span(wind_pairs.motion_steps(str(video)))
    assert span is not None and 5.0 <= span[0] <= 8.0 and 35.0 <= span[1] <= 38.0, span


def test_per_clip_headings():
    assert wind_pairs.parse_headings(["90", "dir/a.mp4=370"]) == (90.0, {"a.mp4": 10.0})
