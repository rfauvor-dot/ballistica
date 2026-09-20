"""Per-user daily spend budget (2026-09-19). The provider clients are faked
throughout, so this suite never makes a paid call."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import ballistica.api as api_module
from ballistica import spend
from ballistica.profiles import Load, ProfileStore, Rifle


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    spend.ledger.reset()
    monkeypatch.setenv("DAILY_BUDGET_USD", "3.00")
    yield
    spend.ledger.reset()


# ------------------------------------------------------------ cost math

def test_llm_cost_prices_input_output_and_cache_tokens():
    usage = SimpleNamespace(input_tokens=332, output_tokens=61, cache_creation_input_tokens=0, cache_read_input_tokens=5623)
    # 332*$1/M + 61*$5/M + 5623*$0.10/M -- the measured cache-hit call from COST_MODEL.md
    assert spend.llm_cost(usage) == pytest.approx(0.00119, abs=2e-5)
    write = SimpleNamespace(input_tokens=332, output_tokens=61, cache_creation_input_tokens=5623, cache_read_input_tokens=0)
    assert spend.llm_cost(write) == pytest.approx(0.00766, abs=2e-5)
    assert spend.llm_cost(SimpleNamespace()) == 0.0  # tolerant of missing fields


def test_stt_cost_prices_audio_text_and_output_separately():
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=20,
        input_token_details=SimpleNamespace(audio_tokens=44, text_tokens=56),
    )
    # the real probe: 44 audio + 56 text-prompt tokens in, 20 out
    assert spend.stt_cost(usage) == pytest.approx(44 * 6 / 1e6 + 56 * 2.5 / 1e6 + 20 * 10 / 1e6)
    # no breakdown: everything priced at the dearer audio rate, never free
    flat = SimpleNamespace(input_tokens=100, output_tokens=20, input_token_details=None)
    assert spend.stt_cost(flat) == pytest.approx(100 * 6 / 1e6 + 20 * 10 / 1e6)


def test_stt_cost_falls_back_to_upload_size_when_usage_is_missing():
    assert spend.stt_cost(None, upload_bytes=200_000) > 0
    assert spend.stt_cost(None, upload_bytes=0) == 0


def test_tts_cost_is_exact_per_character():
    assert spend.tts_cost(1000) == pytest.approx(0.015)


# ---------------------------------------------------------------- ledger

def test_ledger_accumulates_per_user_and_blocks_at_the_budget(monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "1.00")
    spend.ledger.add("u1", 0.6)
    spend.ledger.check("u1")  # under budget
    spend.ledger.add("u1", 0.5)
    with pytest.raises(spend.BudgetExceeded) as exc:
        spend.ledger.check("u1")
    assert exc.value.retry_after >= 1
    spend.ledger.check("u2")  # another user is unaffected
    assert spend.ledger.spent_today("u2") == 0


def test_ledger_resets_at_the_utc_day_boundary():
    now = [1_800_000_000.0]  # any fixed instant
    ledger = spend.SpendLedger(clock=lambda: now[0])
    ledger.add("u1", 9.0)
    assert ledger.spent_today("u1") == 9.0
    remaining = ledger.seconds_until_reset()
    assert 1 <= remaining <= 86_400
    now[0] += remaining + 1  # just past midnight UTC
    assert ledger.spent_today("u1") == 0
    ledger.add("u1", 1.0)
    assert ledger.spent_today("u1") == 1.0


@pytest.mark.parametrize("value", ["", "abc", "0", "-5"])
def test_bad_budget_setting_falls_back_to_the_default_not_to_unlimited(monkeypatch, value):
    monkeypatch.setenv("DAILY_BUDGET_USD", value)
    assert spend.daily_budget_usd() == spend.DEFAULT_DAILY_BUDGET_USD


def test_track_collects_claude_costs_and_never_raises():
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    with spend.track() as box:
        spend.record_llm(usage)
        spend.record_llm(usage)
        spend.record_llm(None)          # tolerated
        spend.record_llm(object())      # tolerated
    assert box[0] == pytest.approx(2 * (1000 * 1 / 1e6 + 100 * 5 / 1e6))
    spend.record_llm(usage)  # outside any request: silently ignored


# ------------------------------------------------------------- endpoints

class _FakeOpenAI:
    def __init__(self):
        outer = self
        outer.tts_calls = outer.stt_calls = 0

        class _Speech:
            def create(self, model, voice, input, speed):
                outer.tts_calls += 1
                return SimpleNamespace(content=b"mp3")

        class _Transcriptions:
            def create(self, **kwargs):
                outer.stt_calls += 1
                return SimpleNamespace(text="drop at four hundred yards", usage=SimpleNamespace(
                    input_tokens=100, output_tokens=20,
                    input_token_details=SimpleNamespace(audio_tokens=44, text_tokens=56)))

        self.audio = SimpleNamespace(speech=_Speech(), transcriptions=_Transcriptions())


@pytest.fixture
def fake_openai(monkeypatch):
    fake = _FakeOpenAI()
    monkeypatch.setattr(api_module, "get_openai_client", lambda: fake)
    return fake


@pytest.fixture
def client():
    return TestClient(api_module.app)


def _as_user(user_id):
    api_module.app.dependency_overrides[api_module._verify_bearer] = lambda: (user_id, "token")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    api_module.app.dependency_overrides.clear()


def test_speak_charges_the_exact_character_cost(client, fake_openai):
    _as_user("alice")
    assert client.post("/voice/speak", json={"text": "Shot 3, 1162."}).status_code == 200
    assert spend.ledger.spent_today("alice") == pytest.approx(spend.tts_cost(len("Shot 3, 1162.")))


def test_transcribe_charges_from_the_reported_usage(client, fake_openai):
    _as_user("alice")
    r = client.post("/voice/transcribe", files={"audio": ("c.webm", b"x" * 50_000, "audio/webm")})
    assert r.status_code == 200
    assert spend.ledger.spent_today("alice") == pytest.approx(44 * 6 / 1e6 + 56 * 2.5 / 1e6 + 20 * 10 / 1e6)


def test_over_budget_user_gets_a_429_with_code_and_retry_after_and_no_paid_call(client, fake_openai, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "0.01")
    _as_user("alice")
    spend.ledger.add("alice", 0.02)
    for r in (
        client.post("/voice/speak", json={"text": "hello"}),
        client.post("/voice/transcribe", files={"audio": ("c.webm", b"x" * 1000, "audio/webm")}),
        client.post("/v2/voice/query", json={"text": "hello"}),
    ):
        assert r.status_code == 429
        body = r.json()["detail"]
        assert body["code"] == "daily_budget_exceeded"
        assert 1 <= body["resets_in_seconds"] <= 86_400
        assert r.headers["retry-after"] == str(body["resets_in_seconds"])
    assert fake_openai.tts_calls == 0 and fake_openai.stt_calls == 0


def test_one_users_budget_never_blocks_another(client, fake_openai, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "0.01")
    spend.ledger.add("alice", 5.0)
    _as_user("bob")
    assert client.post("/voice/speak", json={"text": "hello"}).status_code == 200
    _as_user("alice")
    assert client.post("/voice/speak", json={"text": "hello"}).status_code == 429


def test_a_call_that_crosses_the_budget_still_completes_then_the_next_is_blocked(client, fake_openai, monkeypatch):
    """Check-before / charge-after: overshoot is bounded by one call."""
    monkeypatch.setenv("DAILY_BUDGET_USD", "0.0001")
    _as_user("alice")
    assert client.post("/voice/speak", json={"text": "a" * 100}).status_code == 200  # costs $0.0015
    assert client.post("/voice/speak", json={"text": "hello"}).status_code == 429


def test_usage_endpoint_reports_spend_budget_and_reset(client, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "2.00")
    _as_user("alice")
    spend.ledger.add("alice", 0.5)
    body = client.get("/v2/usage/today").json()
    assert body["spent_usd"] == 0.5 and body["budget_usd"] == 2.0 and body["remaining_usd"] == 1.5
    assert 1 <= body["resets_in_seconds"] <= 86_400
    spend.ledger.add("alice", 5.0)
    assert client.get("/v2/usage/today").json()["remaining_usd"] == 0.0


def test_usage_endpoint_requires_a_login(client):
    assert client.get("/v2/usage/today").status_code in (401, 422)


# ----------------------------------------- voice query (Claude cost path)

class _FakeStore(ProfileStore):
    """Offline stand-in for SupabaseProfileStore: same surface the voice
    endpoint touches, no network."""

    def __init__(self, tmp_path, user_id="alice"):
        super().__init__(tmp_path / "profiles.json")
        self.user_id = user_id
        self.state = {}
        self.hydrated = 0
        r = Rifle(name="AR-15", scope_height_in=2.5, click_value_mrad=0.1)
        r.add_load(Load(name="75gr ELD", bullet_weight_gr=75, bc=0.4, drag_model="G1",
                        muzzle_velocity_fps=2787, zero_distance_yd=100))
        self.add_rifle(r)

    def get_conversation_state(self):
        self.hydrated += 1
        return dict(self.state)

    def set_conversation_state(self, **updates):
        self.state.update(updates)

    def log_conversation_turn(self, *args, **kwargs):
        pass


def _fake_claude(monkeypatch, usage, calls):
    import ballistica.intent as intent

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Roger that.")], usage=usage)

    monkeypatch.setattr(intent, "get_anthropic_client", lambda: SimpleNamespace(messages=_Messages()))


def test_voice_query_records_the_real_claude_cost(client, tmp_path, monkeypatch):
    store = _FakeStore(tmp_path)
    _as_user("alice")
    api_module.app.dependency_overrides[api_module._get_user_store] = lambda: store
    usage = SimpleNamespace(input_tokens=332, output_tokens=61, cache_creation_input_tokens=0, cache_read_input_tokens=5623)
    calls = []
    _fake_claude(monkeypatch, usage, calls)
    r = client.post("/v2/voice/query", json={"text": "this thing is loud today"})
    assert r.status_code == 200 and r.json()["reply"] == "Roger that."
    assert len(calls) == 1
    assert spend.ledger.spent_today("alice") == pytest.approx(spend.llm_cost(usage))


def test_voice_query_fast_path_turns_cost_nothing(client, tmp_path, monkeypatch):
    store = _FakeStore(tmp_path)
    _as_user("alice")
    api_module.app.dependency_overrides[api_module._get_user_store] = lambda: store
    calls = []
    _fake_claude(monkeypatch, SimpleNamespace(input_tokens=1, output_tokens=1), calls)
    assert client.post("/v2/voice/query", json={"text": "status"}).status_code == 200
    assert calls == [] and spend.ledger.spent_today("alice") == 0


def test_over_budget_voice_query_does_not_touch_the_database_or_claude(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "0.01")
    store = _FakeStore(tmp_path)
    _as_user("alice")
    api_module.app.dependency_overrides[api_module._get_user_store] = lambda: store
    spend.ledger.add("alice", 1.0)
    calls = []
    _fake_claude(monkeypatch, SimpleNamespace(input_tokens=1, output_tokens=1), calls)
    assert client.post("/v2/voice/query", json={"text": "hello"}).status_code == 429
    assert store.hydrated == 0 and calls == []


def test_voice_query_cost_is_recorded_even_when_handling_raises(client, tmp_path, monkeypatch):
    store = _FakeStore(tmp_path)
    _as_user("alice")
    api_module.app.dependency_overrides[api_module._get_user_store] = lambda: store
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0)

    def boom(self, text):
        spend.record_llm(usage)
        raise ValueError("engine error")

    monkeypatch.setattr(api_module.BallisticaCLI, "handle", boom)
    assert client.post("/v2/voice/query", json={"text": "hello"}).status_code == 200  # ValueError becomes a spoken reply
    assert spend.ledger.spent_today("alice") == pytest.approx(spend.llm_cost(usage))
