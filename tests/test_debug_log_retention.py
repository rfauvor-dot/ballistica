"""Conversation debug log: 30-day retention and the off switch (2026-11-14).
No network -- the Supabase client is replaced with a recording stand-in."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import ballistica.supabase_store as store_module


@pytest.fixture
def store(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(store_module, "supabase_http", lambda: client)
    monkeypatch.setattr(store_module, "_SUPABASE_URL", "https://db.example")
    monkeypatch.setattr(store_module, "_log_pruned_on", {})
    monkeypatch.delenv("CONVERSATION_DEBUG_LOG", raising=False)
    s = store_module.SupabaseProfileStore("user-1", "token")
    requests.clear()
    s.requests = requests
    return s


def _log_calls(store):
    return [(r.method, dict(r.url.params)) for r in store.requests if r.url.path.endswith("conversation_debug_log")]


def _age_days(timestamp: str) -> float:
    then = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then) / timedelta(days=1)


def test_retention_is_thirty_days():
    assert store_module.CONVERSATION_LOG_RETENTION_DAYS == 30


def test_logging_a_turn_also_deletes_entries_older_than_30_days(store):
    store.log_conversation_turn("drop at 400 yards", "fast_path", "Solution...", 12)
    (insert, _), (delete, params) = _log_calls(store)
    assert (insert, delete) == ("POST", "DELETE")
    assert params["user_id"] == "eq.user-1"          # only ever this user's rows
    assert params["created_at"].startswith("lt.")
    assert _age_days(params["created_at"][3:]) == pytest.approx(30, abs=0.01)


def test_deletion_runs_once_a_day_per_user_not_every_turn(store):
    for _ in range(5):
        store.log_conversation_turn("status", "fast_path", "...", 3)
    methods = [m for m, _ in _log_calls(store)]
    assert methods.count("POST") == 5 and methods.count("DELETE") == 1
    store.prune_conversation_log(force=True)
    assert [m for m, _ in _log_calls(store)].count("DELETE") == 2


def test_reading_the_log_never_returns_entries_older_than_30_days(store):
    store.get_recent_conversation_log(500)
    (method, params), = _log_calls(store)
    assert method == "GET" and params["created_at"].startswith("gte.")
    assert _age_days(params["created_at"][4:]) == pytest.approx(30, abs=0.01)


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_off_switch_stops_new_entries_but_still_deletes_old_ones(store, monkeypatch, value):
    monkeypatch.setenv("CONVERSATION_DEBUG_LOG", value)
    store.log_conversation_turn("anything", "converse", "reply", 5)
    assert [m for m, _ in _log_calls(store)] == ["DELETE"]


@pytest.mark.parametrize("value", ["", "on", "1", "true"])
def test_logging_is_on_by_default(store, monkeypatch, value):
    monkeypatch.setenv("CONVERSATION_DEBUG_LOG", value)
    store.log_conversation_turn("anything", "converse", "reply", 5)
    assert "POST" in [m for m, _ in _log_calls(store)]
