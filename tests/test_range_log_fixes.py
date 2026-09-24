"""Regression tests replaying the real turns from Rick's 2026-09-23 range-
style test log (the per-turn conversation log, downloaded from the app).
Every scenario below happened in that session; the timestamps in each
docstring are from it."""
import pytest

import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI, _normalize_spoken_numbers, _SetupSession
from ballistica.profiles import Load, ProfileStore, Rifle


def _load(name, fps=2750, weight=107):
    return Load(name=name, bullet_weight_gr=weight, bc=0.547, drag_model="G1",
                muzzle_velocity_fps=fps, zero_distance_yd=100)


def _make(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    ar = Rifle(name="AR-15 20-inch Faxon", scope_height_in=2.5, click_value_mrad=0.1)
    ar.add_load(_load("Sierra MatchKing", 2787, 77))
    store.add_rifle(ar, make_active=False)
    arc = Rifle(name="6mm ARC test rifle", scope_height_in=3.0, click_value_mrad=0.1)
    arc.add_load(_load("test 6mm arc"))
    store.add_rifle(arc)  # active
    return BallisticaCLI(store), store


def _no_llm(monkeypatch, setup_prefill=False):
    """Fails the test if the turn reaches the intent/calibration LLM.
    Starting a setup legitimately pre-fills from the trigger utterance with
    extract_setup_fields (returns nothing here), so tests that start a setup
    pass setup_prefill=True."""
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the LLM")

    monkeypatch.setattr(cli_module, "extract_intent", boom)
    monkeypatch.setattr(cli_module, "classify_calibration_turn", boom)
    if setup_prefill:
        monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {})
    else:
        monkeypatch.setattr(cli_module, "extract_setup_fields", boom)


# ------------------------------------------------ 6:21 PM  "1,000 yards"

def test_thousands_commas_are_joined_but_lists_are_left_alone():
    assert _normalize_spoken_numbers("Distance 1,000 yards, get solution.") == "Distance 1000 yards, get solution."
    assert _normalize_spoken_numbers("2,750") == "2750"
    assert _normalize_spoken_numbers("1,000,000") == "1000000"
    assert _normalize_spoken_numbers("1150, 1162, 1148") == "1150, 1162, 1148"   # a spoken list
    assert _normalize_spoken_numbers("1150,1162") == "1150,1162"                 # 4-digit groups: not thousands
    assert _normalize_spoken_numbers("wind 10, then 3 o'clock") == "wind 10, then 3 o'clock"


def test_a_comma_in_a_range_no_longer_becomes_a_zero_yard_solution(tmp_path, monkeypatch):
    """Heard "Distance 1,000 yards, get solution." and answered "Solution, 0
    yards. Elevation, up 0.0 mils" -- a fake answer that looked real."""
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    reply = cli.handle("Distance 1,000 yards, get solution.")
    assert reply.startswith("Solution, 1000 yards.")


def test_a_zero_or_negative_range_is_refused_not_answered(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    assert "didn't catch a distance" in cli.handle("drop at 0 yards")
    assert cli._last_solution is None


def test_a_comma_in_a_calibration_shot_no_longer_logs_a_fragment(tmp_path, monkeypatch):
    """"2,750" used to log as a 750 fps shot."""
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    cli.handle("start calibration")
    assert "Shot 1, 2750" in cli.handle("Shot one, 2,750")


# ------------------------------- 6:25 PM  "Distance 400 yards" saved as a shot

def _calibrating(cli, monkeypatch, shots):
    _no_llm(monkeypatch)
    cli.handle("start calibration")
    for s in shots:
        cli.handle(str(s))


def test_a_distance_said_mid_calibration_is_not_a_shot(tmp_path, monkeypatch):
    """The real failure: "Distance 400 yards" was logged as "Shot 6, 400", and
    the 2,358 average was then saved as the load's muzzle velocity."""
    cli, store = _make(tmp_path)
    _calibrating(cli, monkeypatch, [2775, 2750, 2725, 2775, 2725])
    reply = cli.handle("Distance 400 yards")
    assert "distance, not a velocity" in reply
    assert cli._calibration.shots == [2775.0, 2750.0, 2725.0, 2775.0, 2725.0]
    assert "400" not in cli.handle("Finish velocity test.").split("average")[0]
    saved = cli.handle("Yes, it's working really good.")
    assert store.rifles["6mm ARC test rifle"].loads["test 6mm arc"].muzzle_velocity_fps == pytest.approx(2750.0)
    assert "2750" in saved


def test_a_bare_implausible_reading_is_refused_even_without_the_word_distance(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _calibrating(cli, monkeypatch, [2775, 2750])
    reply = cli.handle("400")
    assert "way off" in reply and cli._calibration.shots == [2775.0, 2750.0]
    assert "doesn't sound like a velocity" in cli.handle("99999")   # outside any plausible range


def test_a_wide_spread_is_warned_about_and_refused_at_save(tmp_path, monkeypatch):
    """The real session's summary said "spread 2375" and it was saved anyway."""
    cli, store = _make(tmp_path)
    _calibrating(cli, monkeypatch, [2750, 2775, 2000])
    ask = cli.handle("finish")
    assert "spread is wide" in ask.lower()
    reply = cli.handle("yes")
    assert "Not saving" in reply
    assert store.rifles["6mm ARC test rifle"].loads["test 6mm arc"].muzzle_velocity_fps == 2750  # untouched
    assert cli._calibration is not None  # session still open so the bad shot can be discarded
    assert "Tossed 2000" in cli.handle("discard that")
    assert "Save as the new velocity" in cli.handle("finish")
    assert "Saved" in cli.handle("yes")


# --------------------- 6:23-6:25 PM  "twenty-seven fifty" / "27-25" not understood

@pytest.mark.parametrize("spoken", ["Shot two, twenty-seven fifty.", "Shot three, 27-25", "Shot five, twenty-seven twenty-five."])
def test_spoken_or_split_readings_go_through_the_model_fallback(tmp_path, monkeypatch, spoken):
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    cli.handle("start calibration")
    monkeypatch.setattr(cli_module, "classify_calibration_turn", lambda text: ("record_shot", {"velocity_fps": 2725}))
    assert "Shot 1, 2725" in cli.handle(spoken)


def test_a_spoken_reading_still_goes_through_the_plausibility_gate(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    cli.handle("start calibration")
    monkeypatch.setattr(cli_module, "classify_calibration_turn", lambda text: ("record_shot", {"velocity_fps": 275}))
    assert "doesn't sound like a velocity" in cli.handle("twenty-seven fifty")
    assert cli._calibration.shots == []


def test_old_style_string_classifications_still_work(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch)
    cli.handle("start calibration")
    cli.handle("2750")
    monkeypatch.setattr(cli_module, "classify_calibration_turn", lambda text: "end_calibration")
    assert "Save as the new velocity" in cli.handle("I think that's good")


# --------------------------- 6:16 PM  "1.07" saved as a bullet weight

def _in_load_setup(cli, monkeypatch, extracted):
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: dict(extracted))
    cli.handle("new load")
    cli._setup.draft = {"name": "test 6mm arc"}
    return cli


def test_a_mis_transcribed_bullet_weight_is_asked_about_not_saved(tmp_path, monkeypatch):
    """Heard "one oh seven" as "1.07"; the read-back said "1 grain"; it was
    confirmed and a 1.07-grain load was saved."""
    cli, _ = _make(tmp_path)
    _in_load_setup(cli, monkeypatch, {"bullet_weight_gr": 1.07})
    reply = cli.handle("1.07")
    assert "doesn't sound like a bullet weight" in reply and reply.endswith("Say it again?")
    assert "bullet_weight_gr" not in cli._setup.draft
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {"bullet_weight_gr": 107})
    assert "ballistic coefficient" in cli.handle("107")
    assert cli._setup.draft["bullet_weight_gr"] == 107


def test_a_bad_number_alongside_good_ones_drops_only_the_bad_one(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _in_load_setup(cli, monkeypatch, {"bullet_weight_gr": 1.07, "bc": 0.547})
    cli.handle("1.07 grain, BC point five four seven")
    assert cli._setup.draft.get("bc") == 0.547 and "bullet_weight_gr" not in cli._setup.draft


@pytest.mark.parametrize("field,value", [("muzzle_velocity_fps", 27), ("bc", 5.47), ("zero_distance_yd", 0),
                                          ("powder_charge_gr", 3000), ("scope_height_in", 30)])
def test_other_setup_numbers_are_bounded_too(tmp_path, monkeypatch, field, value):
    cli, _ = _make(tmp_path)
    _in_load_setup(cli, monkeypatch, {field: value})
    cli.handle("whatever")
    assert field not in cli._setup.draft


def test_unusual_but_real_values_are_accepted(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _in_load_setup(cli, monkeypatch, {"bullet_weight_gr": 17, "muzzle_velocity_fps": 2550, "bc": 0.125})
    cli.handle("a 17 grain HMR")
    assert cli._setup.draft["bullet_weight_gr"] == 17
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, kind, asking_about=None: {"bullet_weight_gr": 750})
    cli.handle("actually 750 grain")
    assert cli._setup.draft["bullet_weight_gr"] == 750


def test_the_readback_speaks_the_exact_weight_not_a_rounded_one(tmp_path):
    cli, _ = _make(tmp_path)
    cli._setup = _SetupSession("load")
    cli._setup.draft = {"name": "x", "bullet_weight_gr": 107.5, "bc": 0.547, "drag_model": "G1",
                        "muzzle_velocity_fps": 2812, "zero_distance_yd": 100}
    summary = cli._setup_summary()
    assert "107.5 grain" in summary and "zeroed at 100 yards" in summary


def test_correcting_a_saved_load_by_voice_is_bounded_too(tmp_path):
    cli, store = _make(tmp_path)
    reply = cli._update_load_fields({"bullet_weight_gr": 1.07})
    assert "doesn't sound like a bullet weight" in reply and "changed nothing" in reply
    assert store.rifles["6mm ARC test rifle"].loads["test 6mm arc"].bullet_weight_gr == 107


# --------------- 6:14 PM  couldn't back out at "Sound right?"

def _at_confirm(cli, monkeypatch, kind="load"):
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, k, asking_about=None: {})
    cli.handle(f"new {kind}")
    cli._setup.draft = {"name": "n", "bullet_weight_gr": 107, "bc": 0.5, "drag_model": "G1",
                        "muzzle_velocity_fps": 2750, "zero_distance_yd": 100} if kind == "load" else {}
    cli._setup.confirming = True


@pytest.mark.parametrize("phrase", ["Delete that load. Cancel it.", "scrap that", "forget it, cancel", "throw that out"])
def test_natural_ways_of_backing_out_scrap_the_draft(tmp_path, monkeypatch, phrase):
    cli, _ = _make(tmp_path)
    _at_confirm(cli, monkeypatch)
    assert "scrapped the new load" in cli.handle(phrase)
    assert cli._setup is None


@pytest.mark.parametrize("phrase", ["Delete that load and let's start over.", "start over", "New load", "let's do a new load"])
def test_start_over_restarts_the_same_setup(tmp_path, monkeypatch, phrase):
    cli, _ = _make(tmp_path)
    _at_confirm(cli, monkeypatch)
    reply = cli.handle(phrase)
    assert reply.startswith("Okay, starting the load over.") and "call this load" in reply
    assert cli._setup is not None and cli._setup.draft == {} and cli._setup.kind == "load"


def test_a_new_rifle_at_the_load_confirm_step_is_not_treated_as_a_restart_of_the_load(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _at_confirm(cli, monkeypatch)
    cli.handle("new rifle")   # different task: the top-level interrupt starts a rifle setup
    assert cli._setup is not None and cli._setup.kind == "rifle"


def test_a_long_answer_that_merely_contains_a_backout_word_is_not_a_backout(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, k, asking_about=None: {"notes": "x"})
    cli.handle("new load")
    cli.handle("the note is do not cancel or delete this load ever because it is my favorite one")
    assert cli._setup is not None


def test_saying_new_load_while_still_naming_the_load_is_an_answer_not_a_restart(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, k, asking_about=None: {"name": "new load test"})
    cli.handle("new load")
    cli.handle("call it new load test")
    assert cli._setup.draft.get("name") == "new load test"


# ------------------- 6:28 PM  "switch rifles, the AR-15 ..." and "switch to a new rifle"

def test_switch_rifles_followed_by_a_name_switches(tmp_path, monkeypatch):
    """Went to the LLM instead, which answered "You're already on the AR-15"
    from stale memory while the 6mm was actually active."""
    cli, store = _make(tmp_path)
    _no_llm(monkeypatch)
    reply = cli.handle("Now just switch rifles, the AR-15 Faxon 20 inch.")
    assert reply == "Switched you over to the AR-15 20-inch Faxon."
    assert store.active_rifle_name == "AR-15 20-inch Faxon"


@pytest.mark.parametrize("phrase", ["switch rifle to the 6mm", "switch to the rifle 6mm arc", "switch me over to rifle 6mm ARC test"])
def test_other_switch_rifle_phrasings(tmp_path, monkeypatch, phrase):
    cli, store = _make(tmp_path)
    store.set_active_rifle("AR-15 20-inch Faxon")
    _no_llm(monkeypatch)
    assert "6mm ARC test rifle" in cli.handle(phrase)
    assert store.active_rifle_name == "6mm ARC test rifle"


def test_switch_rifles_with_no_name_still_goes_to_the_model_to_ask(tmp_path, monkeypatch):
    cli, store = _make(tmp_path)
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: ("converse", {"reply": "Which rifle?"}))
    assert cli.handle("switch rifles") == "Which rifle?"
    assert store.active_rifle_name == "6mm ARC test rifle"


def test_switch_to_a_new_rifle_asks_instead_of_starting_a_setup(tmp_path, monkeypatch):
    """"Can you switch to a new rifle?" started a brand-new-rifle interview."""
    cli, store = _make(tmp_path)
    _no_llm(monkeypatch)
    reply = cli.handle("Copy that. Can you switch to a new rifle?")
    assert "Which rifle do you want to switch to" in reply
    assert "AR-15 20-inch Faxon" in reply and "6mm ARC test rifle" in reply
    assert cli._setup is None                       # no interview started
    assert cli._pending_rifle_switch is not None    # the next answer resolves against the real list
    assert "Switched you over to the AR-15 20-inch Faxon" in cli.handle("the AR-15")
    assert store.active_rifle_name == "AR-15 20-inch Faxon"


def test_the_setup_phrases_without_switch_still_start_a_setup(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch, setup_prefill=True)
    assert "call this rifle" in cli.handle("new rifle")
    cli._setup = None
    assert "call this load" in cli.handle("add a new load")


# ------------------------------------------------ 6:11 PM  "Load setup"

@pytest.mark.parametrize("phrase,kind", [("Load setup", "load"), ("rifle setup", "rifle")])
def test_noun_order_setup_phrases_start_immediately(tmp_path, monkeypatch, phrase, kind):
    """Skipped the fast path and went through the slower, confirm-gated
    LLM route ("Set up a new load? Say yes to begin.")."""
    cli, _ = _make(tmp_path)
    _no_llm(monkeypatch, setup_prefill=True)
    assert f"call this {kind}" in cli.handle(phrase)
    assert cli._setup.kind == kind


# -------- 6:28 PM  stale "you're already on the AR-15" from 22 minutes earlier

def test_saved_state_changes_clear_the_stale_chat_memory(tmp_path, monkeypatch):
    cli, store = _make(tmp_path)
    stale = [{"role": "user", "content": "rifle"}, {"role": "assistant", "content": "You've got the AR-15 active."}]

    cli._chat_history = list(stale)
    cli.handle("switch rifle to the AR-15")
    assert cli._chat_history == []

    cli._chat_history = list(stale)
    cli.handle("switch to sierra")
    assert cli._chat_history == []

    cli._chat_history = list(stale)
    monkeypatch.setattr(cli_module, "extract_setup_fields", lambda text, k, asking_about=None: {})
    cli.handle("new load")
    cli._setup.draft = {"name": "n2", "bullet_weight_gr": 107, "bc": 0.5, "drag_model": "G1",
                        "muzzle_velocity_fps": 2750, "zero_distance_yd": 100}
    cli._setup.confirming = True
    assert "Saved" in cli.handle("yes")
    assert cli._chat_history == []

    cli._chat_history = list(stale)
    cli.handle("start calibration")
    for s in (2750, 2760):
        cli.handle(str(s))
    cli.handle("finish")
    assert "Saved" in cli.handle("yes")
    assert cli._chat_history == []


def test_ordinary_conversation_still_builds_memory(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: ("converse", {"reply": "Sure thing."}))
    cli.handle("nice day at the range")
    assert len(cli._chat_history) == 2


# ---------------------------------------------- em dashes in spoken replies

def test_converse_replies_are_plain_ascii(tmp_path, monkeypatch):
    cli, _ = _make(tmp_path)
    monkeypatch.setattr(cli_module, "extract_intent", lambda text, history=None: (
        "converse", {"reply": "That’s me — I’m here. “Go ahead”…"}))
    reply = cli.handle("hello there friend")
    assert reply == 'That\'s me -- I\'m here. "Go ahead"...'
    assert reply.isascii()
