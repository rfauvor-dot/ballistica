"""Guards the conversation state that has to survive between API requests
(BallisticaCLI.to_state / load_state).

The first test is the important one: it fails when someone adds an attribute
to BallisticaCLI.__init__ without deciding whether it must survive a request.
That gap is how "repeat" stayed broken over the API -- _last_solution was
simply never persisted, and nothing noticed.
"""
from __future__ import annotations

import json
import time

from ballistica.atmosphere import AtmosphereConditions
from ballistica.cli import (
    BallisticaCLI, _CalibrationSession, _SessionLog, _SetupSession, bootstrap_default_profile,
)
from ballistica.profiles import ProfileStore
from ballistica.trajectory import WindCondition


def _cli(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)
    return BallisticaCLI(store)


def test_every_attribute_is_classified_as_persisted_or_request_only(tmp_path):
    cli = _cli(tmp_path)
    declared = BallisticaCLI._PERSISTED_ATTRS | BallisticaCLI._REQUEST_ONLY_ATTRS
    actual = set(vars(cli))
    assert not (BallisticaCLI._PERSISTED_ATTRS & BallisticaCLI._REQUEST_ONLY_ATTRS)
    assert actual - declared == set(), (
        "New BallisticaCLI attribute(s) not named in _PERSISTED_ATTRS or _REQUEST_ONLY_ATTRS. "
        "If it must survive between voice turns, add it to to_state()/load_state() too."
    )
    assert declared - actual == set(), "Declared attribute(s) that __init__ no longer sets."


def _populate(cli):
    now = time.time()
    cli.atmosphere = AtmosphereConditions(temp_f=91, pressure_inhg=24.9, humidity_pct=12, altitude_ft=5200)
    cli.wind = WindCondition(speed_mph=9, clock_deg=120)
    cli._last_solution = {"range_yd": 400.0, "elev_dir": "up", "elev_val": 2.1,
                          "wind_dir": "left", "wind_val": 0.4, "unit_word": "mils"}
    cli._setup = _SetupSession("load")
    cli._setup.draft = {"name": "x", "bc": 0.4}
    cli._setup.skipped = {"notes"}
    cli._calibration = _CalibrationSession("AR-15 20in Faxon", "23.5gr H335")
    cli._calibration.shots = [2750.0, 2761.0]
    cli._pending_delete, cli._pending_delete_load, cli._pending_delete_at = "AR-15 20in Faxon", "21.0gr H335", now
    cli._pending_rifle_switch, cli._pending_rifle_switch_at = ["A", "B"], now
    cli._pending_load_switch, cli._pending_load_switch_at = ["C", "D"], now
    cli._pending_calibration_start, cli._pending_calibration_start_at = True, now
    cli._pending_setup_kind, cli._pending_setup_text, cli._pending_setup_at = "rifle", "new rifle, the 9mm", now
    cli._chat_history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    cli._session_log = _SessionLog()
    cli._session_log.readings = [{"rifle": "A", "load": "C", "fps": 1150.0}]
    cli._session_log.unassigned = [1162.0]
    cli._pending_session_save = [{"rifle": "A", "load": "C", "avg": 1150.0, "n": 3, "spread": 9.0}]
    cli._pending_session_save_at = now
    cli._equipment_check = {"step": "focus", "issues": [], "framing_issues": ["another sheet is in view"],
                            "link_note": "Phone and tablet are connected.", "sharp_verdict": "soft",
                            "focus_last": 0.3, "focus_tries": 1, "asks": 0, "at": now}
    cli._rename = {"queue": ["A", "B"], "stage": "offer", "skipped": [], "maker": "Tikka",
                   "suggestion": "6.5 Creedmoor 24-inch Tikka", "custom": "", "renamed": [["X", "Y"]],
                   "single": False, "at": now}


def test_fully_populated_state_round_trips(tmp_path):
    cli = _cli(tmp_path)
    _populate(cli)
    state = cli.to_state()
    json.dumps(state)                       # it is stored as JSON
    restored = _cli(tmp_path)
    restored.load_state(json.loads(json.dumps(state)))
    assert restored.to_state() == state
    assert restored.atmosphere == cli.atmosphere and restored.wind == cli.wind
    assert restored._last_solution == cli._last_solution
    assert restored._setup.skipped == {"notes"}
    assert restored._pending_delete_load == "21.0gr H335"


def test_every_persisted_attribute_actually_changes_the_state(tmp_path):
    """Populating a persisted attribute must show up in to_state() -- an
    attribute listed as persisted but never written out would pass the
    classification test above while still being lost."""
    blank = _cli(tmp_path).to_state()
    full_cli = _cli(tmp_path)
    _populate(full_cli)
    full = full_cli.to_state()
    assert all(blank[key] != full[key] for key in full), [k for k in full if blank[k] == full[k]]


def test_empty_state_is_a_clean_slate(tmp_path):
    cli = _cli(tmp_path)
    _populate(cli)
    kept_atmosphere, kept_wind = cli.atmosphere, cli.wind
    cli.load_state({})
    assert cli._setup is None and cli._calibration is None and cli._pending_delete is None
    assert cli._pending_delete_load is None and cli._last_solution is None
    assert cli._chat_history == [] and cli._session_log is None
    assert not cli._pending_calibration_start and cli._pending_setup_kind is None
    # atmosphere/wind: a missing key leaves whatever was there.
    assert cli.atmosphere == kept_atmosphere and cli.wind == kept_wind


def test_state_saved_before_load_delete_existed_still_loads(tmp_path):
    """A pending_delete stored before 2026-11-14 has no load_name key."""
    cli = _cli(tmp_path)
    cli.load_state({"pending_delete": {"rifle_name": "AR-15 20in Faxon", "at": time.time()}})
    assert cli._pending_delete == "AR-15 20in Faxon" and cli._pending_delete_load is None
