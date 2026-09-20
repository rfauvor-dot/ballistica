"""Per-user daily spend budget for the paid third-party APIs (2026-09-19).

Why this exists: /voice/speak, /voice/transcribe and /v2/voice/query each
proxy a paid API, and the per-minute rate limit alone can't bound cost --
a signed-in (free-to-create) account maxing all three could spend tens of
dollars an hour (COST_MODEL.md). This meters what each user's calls
ACTUALLY cost, in dollars, and blocks paid calls for the rest of the UTC
day once a per-user budget is spent.

Costs are measured, not guessed, wherever the provider reports usage:
Claude calls from the response's token counts (including prompt-cache
reads/writes), speech-to-text from its token usage, text-to-speech from the
exact character count sent. Prices are per COST_MODEL.md.

Deliberate limits, so nobody mistakes this for billing:
  - It's an in-memory safety cap, not a durable ledger. It resets when the
    server restarts/redeploys (worst case: a user gets a fresh budget after
    a deploy) and is per server instance -- the same single-instance
    assumption the rate limiter already makes. Turning it into a durable
    per-user usage record (for metering or billing) would mean a table.
  - The check is BEFORE a paid call and the cost is added AFTER, so a user
    can overshoot the budget by at most one call.
  - The day is the UTC calendar day (resets 6 PM Mountain in daylight time).
"""
from __future__ import annotations

import contextvars
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DEFAULT_DAILY_BUDGET_USD = 3.00

# USD per token / character. Claude Haiku 4.5 and tts-1 verified against the
# providers' own pricing pages; the speech-to-text audio-input rate ($6.00/M)
# is corroborated by secondary sources only (the primary page lists just the
# $2.50 text-input row), so it errs high on purpose -- this is a safety cap.
_HAIKU_IN = 1.00 / 1e6
_HAIKU_OUT = 5.00 / 1e6
_HAIKU_CACHE_WRITE = 1.25 / 1e6
_HAIKU_CACHE_READ = 0.10 / 1e6
_STT_AUDIO_IN = 6.00 / 1e6
_STT_TEXT_IN = 2.50 / 1e6
_STT_OUT = 10.00 / 1e6
_TTS_PER_CHAR = 15.00 / 1e6
# Only used when the transcription response carries no usage block: charge
# by upload size at a conservative ~32 kbps so a missing field can never
# make a call look free.
_STT_FALLBACK_PER_BYTE = (1 / 4000) * 0.006 / 60


def daily_budget_usd() -> float:
    """DAILY_BUDGET_USD env var, read on every call so it can be changed
    without a code change. Missing, unparseable, or non-positive falls back
    to the default rather than silently disabling the cap (set a large
    number to effectively lift it)."""
    try:
        value = float(os.environ.get("DAILY_BUDGET_USD", ""))
    except ValueError:
        return DEFAULT_DAILY_BUDGET_USD
    return value if value > 0 else DEFAULT_DAILY_BUDGET_USD


def _n(obj, name) -> int:
    return int(getattr(obj, name, 0) or 0)


def llm_cost(usage) -> float:
    """Cost of one Claude Messages API call from its usage block."""
    return (_n(usage, "input_tokens") * _HAIKU_IN
            + _n(usage, "output_tokens") * _HAIKU_OUT
            + _n(usage, "cache_creation_input_tokens") * _HAIKU_CACHE_WRITE
            + _n(usage, "cache_read_input_tokens") * _HAIKU_CACHE_READ)


def stt_cost(usage, upload_bytes: int = 0) -> float:
    """Cost of one gpt-4o-transcribe call from its token usage (audio and
    text input are priced differently; the text part is our own bias
    prompt). Falls back to a size-based estimate if usage is missing."""
    if usage is None:
        return upload_bytes * _STT_FALLBACK_PER_BYTE
    details = getattr(usage, "input_token_details", None)
    audio = _n(details, "audio_tokens")
    text = _n(details, "text_tokens")
    if not audio and not text:  # no breakdown: price all input as audio (the dearer rate)
        audio = _n(usage, "input_tokens")
    return audio * _STT_AUDIO_IN + text * _STT_TEXT_IN + _n(usage, "output_tokens") * _STT_OUT


def tts_cost(chars: int) -> float:
    return chars * _TTS_PER_CHAR


class BudgetExceeded(Exception):
    def __init__(self, spent: float, budget: float, retry_after: int) -> None:
        super().__init__(f"daily budget {budget:.2f} spent ({spent:.2f})")
        self.spent, self.budget, self.retry_after = spent, budget, retry_after


class SpendLedger:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._spent: dict[str, tuple[str, float]] = {}  # user_id -> (utc day, usd)

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)

    def _day(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def seconds_until_reset(self) -> int:
        now = self._now()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((midnight - now).total_seconds()))

    def spent_today(self, user_id: str) -> float:
        with self._lock:
            day, usd = self._spent.get(user_id, ("", 0.0))
            return usd if day == self._day() else 0.0

    def add(self, user_id: str, usd: float) -> None:
        if usd <= 0:
            return
        with self._lock:
            today = self._day()
            day, current = self._spent.get(user_id, (today, 0.0))
            self._spent[user_id] = (today, (current if day == today else 0.0) + usd)
            if len(self._spent) > 5000:  # drop yesterday's entries so this can't grow without bound
                self._spent = {u: v for u, v in self._spent.items() if v[0] == today}

    def check(self, user_id: str) -> None:
        spent, budget = self.spent_today(user_id), daily_budget_usd()
        if spent >= budget:
            raise BudgetExceeded(spent, budget, self.seconds_until_reset())

    def reset(self) -> None:
        with self._lock:
            self._spent.clear()


ledger = SpendLedger()

# Claude calls happen deep inside BallisticaCLI.handle() (up to three per
# turn across intent.py's call sites), far from the endpoint that knows the
# user. A per-request accumulator lets intent.py report each call's cost
# without threading a user id through the whole engine.
_request_cost: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar(
    "ballistica_request_cost", default=None,
)


@contextmanager
def track():
    """Collects the cost of every Claude call made inside the block:
        with spend.track() as box: ...handle()...
        ledger.add(user_id, box[0])"""
    box = [0.0]
    token = _request_cost.set(box)
    try:
        yield box
    finally:
        _request_cost.reset(token)


def record_llm(usage) -> None:
    """Called by intent.py after each Claude call. Never raises: metering
    must not be able to break a voice turn."""
    try:
        box = _request_cost.get()
        if box is not None and usage is not None:
            box[0] += llm_cost(usage)
    except Exception:
        pass
