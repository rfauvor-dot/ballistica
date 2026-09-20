"""Session Mode tracker (component 2, 2026-09-19): narration about which
rifle/load is being shot and chronograph readings gets logged into a
session log -- never straight into a saved load. The LLM is stubbed
throughout (it only REPORTS what was said); these tests pin the
deterministic side that has the final say."""
import time

import pytest

import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI, _SessionLog
from ballistica.profiles import Load, ProfileStore, Rifle


def _load(name, fps=2500, powder=""):
    return Load(name=name, bullet_weight_gr=110, bc=0.3, drag_model="G1",
                muzzle_velocity_fps=fps, zero_distance_yd=100, powder=powder)


def _make_cli(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    ar15 = Rifle(name="AR-15", scope_height_in=2.5, click_value_mrad=0.1)
    ar15.add_load(_load("75gr ELD", 2787))
    store.add_rifle(ar15)
    sbr = Rifle(name="300 Blackout SBR", scope_height_in=2.2, click_value_mrad=0.1)
    sbr.add_load(_load("110gr Lil Gun", 1150, powder="Lil Gun"))
    sbr.add_load(_load("125gr H110", 1050, powder="H110"))
    store.add_rifle(sbr, make_active=False)
    pistol = Rifle(name="300 Blackout Pistol", scope_height_in=2.0, click_value_mrad=0.1)
    pistol.add_load(_load("110gr Lil Gun", 1000, powder="Lil Gun"))
    store.add_rifle(pistol, make_active=False)
    cli = BallisticaCLI(store)
    cli._session_mode = True
    return cli, store


def _observe(monkeypatch, **args):
    monkeypatch.setattr(
        cli_module, "extract_intent",
        lambda text, history=None, session_context=None: ("log_session_observation", args),
    )


def test_session_context_only_passed_in_session_mode(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    seen = {}

    def fake(text, history=None, **kwargs):
        seen.update(kwargs)
        return ("converse", {"reply": "ok"})

    monkeypatch.setattr(cli_module, "extract_intent", fake)
    cli.handle("just chatting about the range")
    ctx = seen["session_context"]
    assert ctx["active_rifle"] == "AR-15"
    assert "300 Blackout SBR" in ctx["rifles"]
    assert ctx["rifles"]["300 Blackout SBR"] == ["110gr Lil Gun", "125gr H110"]

    # Outside Session Mode the call must be byte-for-byte what it was
    # before the tracker existed: no session_context kwarg at all.
    seen.clear()
    cli._session_mode = False
    cli.handle("just chatting about the range again")
    assert "session_context" not in seen


def test_readings_are_logged_against_the_active_rifle_and_load(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2795, 2790])
    reply = cli.handle("twenty seven eighty, twenty seven ninety five, twenty seven ninety")
    assert "2780, shot 1" in reply and "2790, shot 3" in reply
    assert cli._session_log.grouped() == {("AR-15", "75gr ELD"): [2780.0, 2795.0, 2790.0]}
    # Logging never touches the saved load.
    assert store.rifles["AR-15"].loads["75gr ELD"].muzzle_velocity_fps == 2787


def test_implicit_context_switch_from_narration(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="the SBR", load_query="110 Lil Gun", velocities_fps=[1150, 1162])
    reply = cli.handle("okay now the SBR with the 110 Lil Gun, 1150, 1162")
    assert store.active_rifle_name == "300 Blackout SBR"
    assert store.rifles["300 Blackout SBR"].active_load_name == "110gr Lil Gun"
    assert cli._session_log.grouped() == {("300 Blackout SBR", "110gr Lil Gun"): [1150.0, 1162.0]}
    assert "300 Blackout SBR." in reply and "1162, shot 2" in reply


def test_switching_only_the_load_keeps_the_rifle(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    store.set_active_rifle("300 Blackout SBR")
    _observe(monkeypatch, load_query="125 H110", velocities_fps=[1040])
    cli.handle("now the H110 load, 1040")
    assert store.active_rifle_name == "300 Blackout SBR"
    assert cli._session_log.grouped() == {("300 Blackout SBR", "125gr H110"): [1040.0]}


@pytest.mark.parametrize("bad", [150.0, 11.5, 99999.0])
def test_implausible_velocity_is_never_logged(monkeypatch, tmp_path, bad):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[bad])
    reply = cli.handle("that last one read weird")
    assert "doesn't sound like a velocity" in reply
    assert not cli._session_log.readings


def test_chrono_narration_with_a_number_reaches_the_tracker_not_calibration(monkeypatch, tmp_path):
    """Without the session_reading guard, "chrono says 1150" hit the
    calibration fast path first and started a modal calibration session
    before the tracker ever saw it."""
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780])
    reply = cli.handle("chrono says 2780")
    assert cli._calibration is None
    assert "2780, shot 1" in reply

    # Explicit commands with no reading in them are untouched.
    assert "Calibration started" in cli.handle("start calibration")
    cli._calibration = None

    # And outside Session Mode nothing changed at all.
    cli._session_mode = False
    assert "Calibration started" in cli.handle("chrono says 2780")


def test_again_with_a_reading_is_narration_in_session_mode(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2790])
    reply = cli.handle("and again, 2790")
    assert "2790, shot 1" in reply
    cli._session_mode = False
    # Outside Session Mode "again" is still the repeat-last-solution command.
    assert "2790, shot" not in cli.handle("repeat elevation")


def test_ambiguous_rifle_holds_readings_until_answered(monkeypatch, tmp_path):
    """The SBR has two loads and none was named, so even after the rifle
    answer the readings must NOT land on whichever load happens to be
    active -- the tracker asks which load first."""
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="300 blackout", velocities_fps=[1150, 1155])
    reply = cli.handle("switching to the 300 blackout, 1150, 1155")
    assert "Which one do you mean" in reply and "holding those 2 readings" in reply
    assert cli._pending_rifle_switch is not None
    assert cli._session_log.unassigned == [1150.0, 1155.0]
    assert not cli._session_log.readings  # held, not guessed onto a pairing
    assert store.active_rifle_name == "AR-15"

    answer = cli.handle("the SBR one")
    assert store.active_rifle_name == "300 Blackout SBR"
    assert "Which load are the 2 held readings on" in answer
    assert not cli._session_log.readings  # still not guessed
    assert cli._pending_load_switch is not None

    final = cli.handle("the Lil Gun")
    assert "Logged the 2 held readings on the 110gr Lil Gun" in final
    assert not cli._session_log.unassigned
    assert cli._session_log.grouped() == {("300 Blackout SBR", "110gr Lil Gun"): [1150.0, 1155.0]}


def test_held_readings_auto_attach_when_the_rifle_has_only_one_load(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="300 blackout", velocities_fps=[1000, 1004])
    cli.handle("300 blackout, 1000, 1004")
    answer = cli.handle("the pistol")
    assert store.active_rifle_name == "300 Blackout Pistol"
    assert "Logged the 2 held readings" in answer
    assert cli._session_log.grouped() == {("300 Blackout Pistol", "110gr Lil Gun"): [1000.0, 1004.0]}


def test_switching_rifles_with_readings_but_no_load_asks_which_load(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="the SBR", velocities_fps=[1150])
    reply = cli.handle("okay the SBR, 1150")
    assert store.active_rifle_name == "300 Blackout SBR"
    assert "Which load are those 1 readings on" in reply
    assert not cli._session_log.readings
    assert cli._session_log.unassigned == [1150.0]
    cli.handle("the H110")
    assert cli._session_log.grouped() == {("300 Blackout SBR", "125gr H110"): [1150.0]}


def test_readings_with_no_rifle_change_log_against_the_active_load_directly(monkeypatch, tmp_path):
    """Already on the rifle: the active load is established context, not a
    guess, so no question is asked."""
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780])
    assert "which load" not in cli.handle("2780").lower()
    assert cli._session_log.readings


def test_stale_held_readings_are_dropped(tmp_path):
    cli, _ = _make_cli(tmp_path)
    cli._session_log = _SessionLog()
    cli._session_log.hold([1150.0])
    cli._session_log.unassigned_at = time.time() - 600
    cli._expire_stale_sessions()
    assert cli._session_log.unassigned == []


def test_ambiguous_rifle_with_a_named_load_does_not_guess_where_readings_go(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="300 blackout", load_query="125 H110", velocities_fps=[1040])
    reply = cli.handle("300 blackout, the H110, 1040")
    assert "didn't log those 1 readings" in reply
    assert not cli._session_log.unassigned and not cli._session_log.readings


def test_unknown_rifle_offers_setup_and_says_readings_were_not_logged(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, rifle_query="6.5 creedmoor tikka", velocities_fps=[2700])
    reply = cli.handle("now the 6.5 creedmoor tikka, 2700")
    assert "don't have a rifle saved" in reply
    assert "didn't log those 1 readings" in reply
    assert "Set up a new rifle" in reply
    assert cli._pending_setup_kind == "rifle"
    assert not cli._session_log.readings


def test_observation_tool_ignored_outside_session_mode(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    cli._session_mode = False
    _observe(monkeypatch, velocities_fps=[2780])
    reply = cli.handle("twenty seven eighty")
    assert "Didn't understand" in reply
    assert cli._session_log is None


def test_correction_discard_and_replace(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2795])
    cli.handle("2780 2795")
    _observe(monkeypatch, discard_last_reading=True)
    assert "Tossed 2795" in cli.handle("scratch that")
    assert [r["fps"] for r in cli._session_log.readings] == [2780.0]
    _observe(monkeypatch, replace_last_reading_fps=2785)
    assert "Changed 2780 to 2785" in cli.handle("no that was 2785")
    assert [r["fps"] for r in cli._session_log.readings] == [2785.0]
    _observe(monkeypatch, replace_last_reading_fps=12)
    reply = cli.handle("no that was twelve")
    assert "left the last reading alone" in reply
    assert [r["fps"] for r in cli._session_log.readings] == [2785.0]


def test_outlier_reading_is_flagged_but_still_logged(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2785, 2790, 2782])
    cli.handle("four readings")
    _observe(monkeypatch, velocities_fps=[2600])
    reply = cli.handle("2600")
    assert "outlier" in reply
    assert cli._session_log.readings[-1]["fps"] == 2600.0


def test_save_velocities_needs_enough_readings_then_confirms(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    assert "Nothing logged" in cli.handle("save velocities")

    _observe(monkeypatch, velocities_fps=[2780, 2790])
    cli.handle("2780 2790")
    assert "at least 3" in cli.handle("save velocities")

    _observe(monkeypatch, velocities_fps=[2800])
    cli.handle("2800")
    ask = cli.handle("save the velocities")
    assert "Say yes to save" in ask and "2790 over 3 shots" in ask
    # Asking must not write anything.
    assert store.rifles["AR-15"].loads["75gr ELD"].muzzle_velocity_fps == 2787

    reply = cli.handle("yes")
    load = store.rifles["AR-15"].loads["75gr ELD"]
    assert load.muzzle_velocity_fps == pytest.approx(2790.0)
    assert "Chrono-verified: 3 shots" in load.notes
    assert "75gr ELD is now 2790" in reply
    assert not cli._session_log.readings  # saved pairings leave the log


def test_declining_the_save_keeps_readings_and_changes_nothing(monkeypatch, tmp_path):
    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2790, 2800])
    cli.handle("three readings")
    cli.handle("save velocities")
    reply = cli.handle("no")
    assert "not saving" in reply.lower()
    assert store.rifles["AR-15"].loads["75gr ELD"].muzzle_velocity_fps == 2787
    assert len(cli._session_log.readings) == 3
    assert cli._pending_session_save is None


def test_save_velocity_with_a_number_is_a_field_correction_not_a_session_save(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2790, 2800])
    cli.handle("three readings")
    monkeypatch.setattr(
        cli_module, "extract_intent",
        lambda text, history=None, session_context=None: ("update_load_field", {"muzzle_velocity_fps": 2800}),
    )
    cli.handle("save the velocity as 2800")
    assert cli._pending_session_save is None


def test_end_session_summary_recaps_and_keeps_the_log(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    assert cli.end_session_summary() == ""
    cli._session_mode = True
    _observe(monkeypatch, velocities_fps=[2780, 2790, 2800])
    cli.handle("three readings")
    summary = cli.end_session_summary()
    assert "3 readings logged" in summary and "average 2790" in summary
    assert "Say save velocities" in summary
    assert cli._session_mode is False
    assert len(cli._session_log.readings) == 3


def test_stale_session_log_is_cleared(tmp_path):
    cli, _ = _make_cli(tmp_path)
    cli._session_log = _SessionLog()
    cli._session_log.readings.append({"rifle": "AR-15", "load": "75gr ELD", "fps": 2780.0})
    cli._session_log.last_activity = time.time() - (13 * 3600)
    cli._expire_stale_sessions()
    assert cli._session_log is None


def test_session_state_round_trips_through_hydrate_dehydrate(monkeypatch, tmp_path):
    import ballistica.api as api_module

    cli, store = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2790, 2800])
    cli.handle("three readings")
    cli.handle("save velocities")  # leaves a pending save gate open
    state = api_module._dehydrate_cli(cli)

    restored = BallisticaCLI(store)
    api_module._hydrate_cli(restored, state)
    assert restored._session_log.grouped() == cli._session_log.grouped()
    assert restored._pending_session_save == cli._pending_session_save
    assert "75gr ELD is now 2790" in restored.handle("yes")

    fresh = BallisticaCLI(store)
    api_module._hydrate_cli(fresh, {})
    assert fresh._session_log is None and fresh._pending_session_save is None


def test_voice_query_model_defaults_session_mode_off():
    import ballistica.api as api_module

    assert api_module.VoiceQueryIn(text="hello").session_mode is False
    assert api_module.VoiceQueryIn(text="hello", session_mode=True).session_mode is True


def test_session_context_carries_the_last_logged_reading(monkeypatch, tmp_path):
    """Found in the live-model check: without knowing a reading was just
    logged, a bare "scratch that" gets a clarifying question instead of
    the discard it obviously means."""
    cli, _ = _make_cli(tmp_path)
    assert cli._session_context()["last_reading"] is None
    _observe(monkeypatch, velocities_fps=[2780, 2790])
    cli.handle("2780 2790")
    last = cli._session_context()["last_reading"]
    assert last == {"fps": 2790.0, "rifle": "AR-15", "load": "75gr ELD", "count": 2}


@pytest.mark.parametrize("utterance", [
    "no wait, that first one was 1152",
    "the second shot was actually 1152",
    "earlier I said 1150, it was 1152",
    "the one two shots ago was 1152",
])
def test_correction_pointing_at_an_earlier_reading_is_refused_not_misapplied(monkeypatch, tmp_path, utterance):
    """Confirmed live: the model maps "that first one was 1152" onto
    replace-the-LAST-reading despite being told not to, silently changing
    the wrong number. The guard is in code, not the prompt."""
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2790, 2800])
    cli.handle("three readings")
    _observe(monkeypatch, replace_last_reading_fps=1152)
    reply = cli.handle(utterance)
    assert "only correct the most recent reading" in reply
    assert [r["fps"] for r in cli._session_log.readings] == [2780.0, 2790.0, 2800.0]
    _observe(monkeypatch, discard_last_reading=True)
    assert "only correct the most recent reading" in cli.handle(utterance.replace("was 1152", "was bad"))
    assert len(cli._session_log.readings) == 3


def test_last_reading_corrections_still_work(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780, 2790])
    cli.handle("two readings")
    _observe(monkeypatch, replace_last_reading_fps=2795)
    assert "Changed 2790 to 2795" in cli.handle("no that last one was 2795")
    _observe(monkeypatch, discard_last_reading=True)
    assert "Tossed 2795" in cli.handle("scratch that")


def test_earlier_reference_refusal_does_not_block_new_readings_in_the_same_breath(monkeypatch, tmp_path):
    cli, _ = _make_cli(tmp_path)
    _observe(monkeypatch, velocities_fps=[2780])
    cli.handle("2780")
    _observe(monkeypatch, replace_last_reading_fps=1152, velocities_fps=[2790])
    reply = cli.handle("the first one was 1152 and the next is 2790")
    assert "only correct the most recent reading" in reply and "2790, shot 2" in reply
    assert [r["fps"] for r in cli._session_log.readings] == [2780.0, 2790.0]


def test_intent_request_marks_the_static_prompt_cacheable_and_keeps_the_dynamic_suffix_outside_it(monkeypatch):
    """Prompt caching only pays if the cached prefix is byte-identical
    call to call. The session suffix (rifle list, last reading) changes
    every turn, so it must sit in its own block AFTER the cache
    breakpoint -- folding it into the cached text would silently turn the
    cache off (cost deep dive, 2026-09-19)."""
    import ballistica.intent as intent

    sent = []

    class _Messages:
        def create(self, **kwargs):
            sent.append(kwargs)
            return type("R", (), {"content": [type("B", (), {"type": "text", "text": "ok"})()]})()

    monkeypatch.setattr(intent, "get_anthropic_client", lambda: type("C", (), {"messages": _Messages()})())

    intent.extract_intent("hello")
    plain = sent[-1]
    assert plain["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert len(plain["system"]) == 1 and plain["system"][0]["text"] == intent._SYSTEM_PROMPT

    ctx = {"rifles": {"AR-15": ["75gr ELD"]}, "active_rifle": "AR-15", "active_load": "75gr ELD", "last_reading": None}
    intent.extract_intent("hello", session_context=ctx)
    session_a = sent[-1]
    ctx["last_reading"] = {"fps": 2780.0, "rifle": "AR-15", "load": "75gr ELD", "count": 1}
    intent.extract_intent("hello", session_context=ctx)
    session_b = sent[-1]

    for req in (session_a, session_b):
        assert req["system"][0] == plain["system"][0]          # identical cached block every turn
        assert "cache_control" not in req["system"][1]         # dynamic block is never cached
        assert "SESSION MODE IS ON" in req["system"][1]["text"]
        assert req["tools"][-1]["name"] == "log_session_observation"
    assert session_a["system"][1]["text"] != session_b["system"][1]["text"]  # it really does vary per turn
