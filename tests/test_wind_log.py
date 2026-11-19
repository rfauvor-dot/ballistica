"""Anemometer log + wind video pairing (ballistica/wind_log.py, tools/wind_pairs.py)."""
from __future__ import annotations

import csv
import os
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
                          probe_fn=lambda p: (30.0, end), local_tz=MST)
    with open(out, newline="", encoding="utf-8") as fh:
        saved = list(csv.DictReader(fh))
    assert len(saved) == len(rows) == 4
    assert saved[0]["heading_deg"] == "270.0" and saved[0]["max_mph"] == "5.8"   # the puff at :52-:53
    assert saved[-1]["window"] == "all"
    said = capsys.readouterr().out
    assert "65 readings" in said and "20261118_201545.mp4: 20:15:45 +30 s" in said and "warning" not in said


def test_per_clip_headings():
    assert wind_pairs.parse_headings(["90", "dir/a.mp4=370"]) == (90.0, {"a.mp4": 10.0})
