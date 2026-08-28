"""Shared fixtures for tests that need a real, live-signed-in Supabase
user and/or Ballistica's own FastAPI app -- used by test_tenant_isolation.py
and by the handful of test_engine.py tests that specifically regression-test
the /v2 HTTP/Pydantic layer itself (not just BallisticaCLI's engine logic,
which the majority of test_engine.py exercises directly with no network
dependency at all).

Requires SUPABASE_URL/SUPABASE_ANON_KEY configured (same as
supabase_auth.py) and network access to the live project -- any test using
these fixtures is skipped entirely if those aren't present.

The two test accounts are fixed, pre-provisioned throwaway users
(dt-auth-smoketest@mailinator.com / dt-tenant-test-b@mailinator.com) rather
than signed up fresh on every run -- Supabase's own signup flow requires a
real email-confirmation round-trip, not something to repeat on every test
invocation.
"""
from __future__ import annotations

import os

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

requires_supabase = pytest.mark.skipif(
    not SUPABASE_URL or not SUPABASE_ANON_KEY,
    reason="SUPABASE_URL/SUPABASE_ANON_KEY not configured -- this test requires "
           "the real Supabase project.",
)

USER_A = {"email": "dt-auth-smoketest@mailinator.com", "password": "Sm0keTest!Passw0rd"}
USER_B = {"email": "dt-tenant-test-b@mailinator.com", "password": "Sm0keTest!Passw0rd2"}


def sign_in(creds: dict) -> tuple[str, str]:
    """Returns (user_id, access_token)."""
    resp = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token", params={"grant_type": "password"},
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json=creds, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["user"]["id"], data["access_token"]


def auth_headers(token: str) -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def user_a():
    return sign_in(USER_A)


@pytest.fixture
def user_b():
    return sign_in(USER_B)


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    import ballistica.api as api_module
    return TestClient(api_module.app)


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Rate limiting (api.py, security hardening 2026-08-28) is keyed by
    IP -- Starlette's TestClient reports every request as the same
    synthetic "testclient" address, and `ballistica.api.app` is a single
    module-level object shared by every test in this process, so the
    limiter's counters accumulate across the WHOLE test run, not just
    within one test. Without this, running the full suite (dozens of
    HTTP-level tests, several firing many requests each) would start
    tripping 429s partway through for reasons that have nothing to do
    with whatever each individual test is actually checking -- a
    real, reproducible flake, not a hypothetical one. Rate limiting
    itself already has its own direct smoke test (manual, against a
    live local server, not part of this automated suite -- confirmed
    live: 20 allowed then 429 on a 20/minute-limited route); disabling
    it here only affects test runs, never production."""
    import ballistica.api as api_module
    api_module.limiter.enabled = False
    yield
    api_module.limiter.enabled = True
