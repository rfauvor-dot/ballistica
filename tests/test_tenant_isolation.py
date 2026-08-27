"""Adversarial tenant-isolation tests, per MULTI_TENANCY_DESIGN.md #7.4.

Binding requirement, not optional: "Assume User A is malicious and knows
or guesses User B's IDs (user, rifle, load, event). Can User A cause the
system to return, modify, delete, or infer any of User B's data?" These
tests answer that against the REAL Supabase project (RLS enforced by
Postgres itself, not a mock), using two real throwaway accounts.

Requires SUPABASE_URL/SUPABASE_ANON_KEY configured (same as
supabase_auth.py) and network access to the live project -- skipped
entirely if those aren't present, e.g. in an environment with no
Supabase credentials at all.

The two test accounts are fixed, pre-provisioned throwaway users
(dt-auth-smoketest@mailinator.com / dt-tenant-test-b@mailinator.com,
both password Sm0keTest!Passw0rd / Sm0keTest!Passw0rd2) rather than
signed up fresh on every run -- Supabase's own signup flow requires a
real email-confirmation round-trip (not something to repeat on every
test invocation), so these tests sign IN to already-confirmed accounts
instead. Each test cleans up whatever data it creates so runs don't
interfere with each other.
"""
from __future__ import annotations

import os

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

pytestmark = pytest.mark.skipif(
    not _SUPABASE_URL or not _SUPABASE_ANON_KEY,
    reason="SUPABASE_URL/SUPABASE_ANON_KEY not configured -- tenant isolation "
           "tests require the real Supabase project.",
)

_USER_A = {"email": "dt-auth-smoketest@mailinator.com", "password": "Sm0keTest!Passw0rd"}
_USER_B = {"email": "dt-tenant-test-b@mailinator.com", "password": "Sm0keTest!Passw0rd2"}


def _sign_in(creds: dict) -> tuple[str, str]:
    """Returns (user_id, access_token)."""
    resp = httpx.post(
        f"{_SUPABASE_URL}/auth/v1/token", params={"grant_type": "password"},
        headers={"apikey": _SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json=creds, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["user"]["id"], data["access_token"]


def _headers(token: str) -> dict:
    return {
        "apikey": _SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def user_a():
    return _sign_in(_USER_A)


@pytest.fixture
def user_b():
    return _sign_in(_USER_B)


@pytest.fixture
def rifle_owned_by_a(user_a):
    """Creates a real rifle owned by User A, cleans it up after -- the
    target every test in this file tries to have User B reach."""
    user_id, token = user_a
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/rifles",
        headers={**_headers(token), "Prefer": "return=representation"},
        json={"name": "Isolation Test Rifle", "scope_height_in": 2.5, "user_id": user_id},
    )
    resp.raise_for_status()
    rifle = resp.json()[0]
    yield rifle
    httpx.delete(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token),
        params={"id": f"eq.{rifle['id']}"},
    )


# ---------------------------------------------------------- read isolation

def test_user_b_cannot_list_user_a_rifles(user_a, user_b, rifle_owned_by_a):
    """List/search endpoints must be tenant-scoped, not just single-object
    lookups (explicit #7.4 requirement -- a broad SELECT is a different
    code path than a by-ID GET and must be checked separately)."""
    _, token_b = user_b
    resp = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_b),
        params={"select": "*"},
    )
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert "Isolation Test Rifle" not in names


def test_user_b_cannot_fetch_user_a_rifle_by_guessed_id(user_a, user_b, rifle_owned_by_a):
    """Direct by-ID lookup, knowing the real ID (the core adversarial
    scenario: User A's ID is knowable/guessable, not secret)."""
    _, token_b = user_b
    resp = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_b),
        params={"id": f"eq.{rifle_owned_by_a['id']}", "select": "*"},
    )
    assert resp.status_code == 200
    assert resp.json() == []  # RLS makes the row invisible, not a 403 -- both are fine, empty is what matters


# -------------------------------------------------------- write isolation

def test_user_b_cannot_update_user_a_rifle_by_guessed_id(user_a, user_b, rifle_owned_by_a):
    _, token_b = user_b
    resp = httpx.patch(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_b),
        params={"id": f"eq.{rifle_owned_by_a['id']}"},
        json={"name": "Hijacked By B"},
    )
    # Confirmed live: PostgREST returns 204 (not 200/403/404) for a
    # write that correctly matched zero rows -- exactly what RLS hiding
    # another user's row produces. Whichever of these it is, the real
    # assertion is the one that follows: A's data must be unchanged.
    assert resp.status_code in (200, 204, 403, 404)

    _, token_a = user_a
    check = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_a),
        params={"id": f"eq.{rifle_owned_by_a['id']}", "select": "name"},
    )
    assert check.json()[0]["name"] == "Isolation Test Rifle"


def test_user_b_cannot_delete_user_a_rifle_by_guessed_id(user_a, user_b, rifle_owned_by_a):
    _, token_b = user_b
    resp = httpx.delete(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_b),
        params={"id": f"eq.{rifle_owned_by_a['id']}"},
    )
    # Same 204-for-zero-rows-matched behavior as the update test above.
    assert resp.status_code in (200, 204, 403, 404)

    _, token_a = user_a
    check = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_a),
        params={"id": f"eq.{rifle_owned_by_a['id']}", "select": "id"},
    )
    assert len(check.json()) == 1  # still there


def test_user_b_cannot_insert_a_row_claiming_user_a_as_owner(user_a, user_b):
    """A malicious client could try setting user_id in the request body
    directly rather than relying on the server to infer it -- the RLS
    WITH CHECK clause on insert must reject that, not just SELECT-side
    policies."""
    user_id_a, _ = user_a
    _, token_b = user_b
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/rifles",
        headers={**_headers(token_b), "Prefer": "return=representation"},
        json={"name": "Forged Ownership Rifle", "scope_height_in": 2.0, "user_id": user_id_a},
    )
    assert resp.status_code in (401, 403)


# ----------------------------------------------------- loads / cross-table

def test_user_b_cannot_read_loads_belonging_to_user_a_rifle(user_a, user_b, rifle_owned_by_a):
    """loads.user_id is denormalized specifically so RLS can check it
    directly without a join (design doc #2.2) -- confirm that actually
    holds, not just the parent rifles table."""
    user_id_a, token_a = user_a
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/loads",
        headers={**_headers(token_a), "Prefer": "return=representation"},
        json={
            "rifle_id": rifle_owned_by_a["id"], "user_id": user_id_a,
            "name": "Isolation Test Load", "bullet_weight_gr": 175, "bc": 0.5,
            "drag_model": "G1", "muzzle_velocity_fps": 2700, "zero_distance_yd": 100,
        },
    )
    resp.raise_for_status()
    load = resp.json()[0]
    try:
        _, token_b = user_b
        leak = httpx.get(
            f"{_SUPABASE_URL}/rest/v1/loads", headers=_headers(token_b),
            params={"id": f"eq.{load['id']}", "select": "*"},
        )
        assert leak.json() == []
    finally:
        httpx.delete(
            f"{_SUPABASE_URL}/rest/v1/loads", headers=_headers(token_a),
            params={"id": f"eq.{load['id']}"},
        )


# --------------------------------------------------- unauthenticated access

def test_no_token_at_all_cannot_read_any_rifles():
    """The baseline case: no Authorization header, just the public anon
    key. Must not be treated as "no user, show nothing filtered" --
    must fail closed."""
    resp = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/rifles",
        headers={"apikey": _SUPABASE_ANON_KEY}, params={"select": "*"},
    )
    # Either an empty result set (RLS has no matching auth.uid() at all
    # for an unauthenticated request) or an outright rejection are both
    # fail-closed; returning ANY row would be the actual failure.
    if resp.status_code == 200:
        assert resp.json() == []
    else:
        assert resp.status_code in (401, 403)


# ------------------------------------------------- through Ballistica's API
# Everything above talks to raw PostgREST directly -- proves RLS itself is
# correct, but not that Ballistica's own /v2 endpoints (api.py) actually
# use it correctly end to end. §7.4 explicitly requires both: "through the
# API and voice-mediated access" are separate surfaces, not covered just
# because the other is. These use FastAPI's TestClient against the real
# app object, with real Supabase tokens -- the app logic is in-process,
# but every data call it makes still goes out over the network to the
# real project, so this is a genuine end-to-end check, not a mock.

@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    import ballistica.api as api_module
    return TestClient(api_module.app)


@pytest.fixture
def rifle_owned_by_a_via_api(user_a, api_client):
    _, token_a = user_a
    resp = api_client.post(
        "/v2/rifles", headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "API Isolation Test Rifle", "scope_height_in": 2.0, "loads": []},
    )
    assert resp.status_code == 200
    yield resp.json()
    api_client.delete(
        "/v2/rifles/API Isolation Test Rifle", headers={"Authorization": f"Bearer {token_a}"},
    )


def test_user_b_cannot_list_user_a_rifles_through_the_api(user_b, rifle_owned_by_a_via_api, api_client):
    _, token_b = user_b
    resp = api_client.get("/v2/rifles", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    assert "API Isolation Test Rifle" not in [r["name"] for r in resp.json()]


def test_user_b_cannot_fetch_user_a_rifle_by_name_through_the_api(user_b, rifle_owned_by_a_via_api, api_client):
    """User B knows the exact rifle name (not even a guessed ID) and
    still can't reach it -- Ballistica's own find_rifle() only ever
    searches the per-request store, which only ever contains the
    authenticated user's own rows."""
    _, token_b = user_b
    resp = api_client.get(
        "/v2/rifles/API Isolation Test Rifle", headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404


def test_user_b_cannot_delete_user_a_rifle_through_the_api(user_a, user_b, rifle_owned_by_a_via_api, api_client):
    _, token_b = user_b
    resp = api_client.delete(
        "/v2/rifles/API Isolation Test Rifle", headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404

    _, token_a = user_a
    check = api_client.get("/v2/rifles", headers={"Authorization": f"Bearer {token_a}"})
    assert "API Isolation Test Rifle" in [r["name"] for r in check.json()]


def test_v2_endpoints_reject_missing_or_forged_auth(api_client):
    """Fail-closed baseline for the app layer itself, not just RLS: no
    header, and a well-formed-but-fake token, must both be rejected."""
    no_auth = api_client.get("/v2/rifles")
    assert no_auth.status_code in (401, 422)  # 422: FastAPI's own required-header validation

    import jwt as pyjwt
    forged = pyjwt.encode({"sub": "attacker", "aud": "authenticated"}, "not-the-real-secret", algorithm="HS256")
    rejected = api_client.get("/v2/rifles", headers={"Authorization": f"Bearer {forged}"})
    assert rejected.status_code == 401
