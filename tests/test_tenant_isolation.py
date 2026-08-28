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

import httpx
import pytest

from conftest import SUPABASE_ANON_KEY as _SUPABASE_ANON_KEY
from conftest import SUPABASE_URL as _SUPABASE_URL
from conftest import auth_headers as _headers
from conftest import requires_supabase

# user_a, user_b, api_client fixtures come from conftest.py (auto-discovered
# by pytest, no import needed) -- this file used to define its own copies;
# moved there 2026-08-28 so test_engine.py's few HTTP/API-layer-specific
# tests (not just this file's adversarial ones) can share the same
# real-account sign-in machinery instead of duplicating it.
pytestmark = requires_supabase


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


def test_user_b_cannot_insert_load_referencing_user_a_rifle_id(user_a, user_b, rifle_owned_by_a):
    """The sneakier cross-reference variant (post-cutover hardening,
    2026-08-28): User B is legitimately authenticated as themselves and
    only ever claims THEIR OWN user_id on the row -- the old policy's
    `with check (auth.uid() = user_id)` happily allowed that -- but
    rifle_id points at a rifle that's actually User A's. Confirmed live
    before the db/003 migration: this insert succeeded (201). Ballistica's
    own app code never constructs a request shaped like this (no /v2
    endpoint accepts a raw rifle_id from the client at all -- only rifle
    NAMES, resolved against the caller's own already-scoped rifles), so
    this is specifically a raw-PostgREST-level check: a client that skips
    Ballistica's API entirely and talks to Supabase directly, with
    nothing more than their own valid token, must still be blocked by
    the schema itself, not just by app code that happens not to do this."""
    user_id_a = rifle_owned_by_a["user_id"]
    user_id_b, token_b = user_b
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/loads",
        headers={**_headers(token_b), "Prefer": "return=representation"},
        json={
            "rifle_id": rifle_owned_by_a["id"], "user_id": user_id_b,
            "name": "Cross-Ref Attack Load", "bullet_weight_gr": 168, "bc": 0.5,
            "drag_model": "G1", "muzzle_velocity_fps": 2700, "zero_distance_yd": 100,
        },
    )
    assert resp.status_code not in (200, 201), (
        f"User B inserted a load referencing User A's rifle_id -- status {resp.status_code}, "
        f"body {resp.text}"
    )
    # Nothing should have landed under either user's own rifle either way,
    # but confirm A's rifle really still has zero loads attached.
    _, token_a = user_a
    check = httpx.get(
        f"{_SUPABASE_URL}/rest/v1/loads", headers=_headers(token_a),
        params={"rifle_id": f"eq.{rifle_owned_by_a['id']}", "select": "*"},
    )
    assert check.json() == []


def test_user_b_cannot_point_own_rifle_active_load_at_user_a_load(user_a, user_b, rifle_owned_by_a):
    """The other direction of the same class of gap: User B updates a
    rifle THEY genuinely own (passes the top-level ownership check
    cleanly) but sets active_load_id to a load id that belongs to User
    A. Confirmed live before db/003: this PATCH succeeded (200) and the
    rifle's active_load_id really did end up set to A's load's real id."""
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b

    load_resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/loads",
        headers={**_headers(token_a), "Prefer": "return=representation"},
        json={
            "rifle_id": rifle_owned_by_a["id"], "user_id": user_id_a,
            "name": "A Real Load For Cross-Ref Test", "bullet_weight_gr": 175, "bc": 0.5,
            "drag_model": "G1", "muzzle_velocity_fps": 2600, "zero_distance_yd": 100,
        },
    )
    load_resp.raise_for_status()
    load_a = load_resp.json()[0]

    rifle_b_resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/rifles",
        headers={**_headers(token_b), "Prefer": "return=representation"},
        json={"name": "B's Own Rifle For Cross-Ref Test", "scope_height_in": 2.0, "user_id": user_id_b},
    )
    rifle_b_resp.raise_for_status()
    rifle_b = rifle_b_resp.json()[0]

    try:
        resp = httpx.patch(
            f"{_SUPABASE_URL}/rest/v1/rifles",
            headers={**_headers(token_b), "Prefer": "return=representation"},
            params={"id": f"eq.{rifle_b['id']}"},
            json={"active_load_id": load_a["id"]},
        )
        # A 200 with the row's active_load_id genuinely set to A's load id
        # is the actual vulnerability; anything else (204/403/404, or a
        # 200 where the update was rejected/no-opped) is fine.
        if resp.status_code == 200 and resp.text:
            body = resp.json()
            if body:
                assert body[0].get("active_load_id") != load_a["id"], (
                    f"User B's rifle active_load_id was set to User A's real load id: {body[0]}"
                )
    finally:
        httpx.delete(f"{_SUPABASE_URL}/rest/v1/rifles", headers=_headers(token_b), params={"id": f"eq.{rifle_b['id']}"})
        httpx.delete(f"{_SUPABASE_URL}/rest/v1/loads", headers=_headers(token_a), params={"id": f"eq.{load_a['id']}"})


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


# ---------------------------------------- /v2 API-layer regression tests
# Not adversarial -- these moved here from test_engine.py 2026-08-28 when
# the old, unauthenticated single-tenant endpoints they used to test
# against were removed (security hardening pass). What they guard is
# specifically the HTTP/Pydantic-schema layer (RifleUpdate actually
# carrying has_suppressor through a PUT, GET/POST not routing through a
# resolver that wrongly requires an active load, /v2/status returning
# null rather than 404 for a loadless rifle) -- not BallisticaCLI's
# conversation engine, which the bulk of test_engine.py already covers
# with no network dependency at all. These need a real account + the
# live app because that's genuinely what they're testing.

def test_api_persists_suppressor_fields_on_the_rifle(user_a, api_client):
    """Addendum 36: suppressor tracking round-trips through the REST API
    (create, then PUT to edit) same as any other rifle field, as a
    plain open-text field rather than a constrained brand enum."""
    _, token_a = user_a
    headers = {"Authorization": f"Bearer {token_a}"}
    try:
        r = api_client.post("/v2/rifles", headers=headers, json={
            "name": "Suppressed 45 API Test", "scope_height_in": 2.0, "click_value_mrad": 0.1,
            "has_suppressor": True, "suppressor_type": "unclear -- inherited, no markings", "loads": [],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["has_suppressor"] is True
        assert body["suppressor_type"] == "unclear -- inherited, no markings"

        r = api_client.get("/v2/rifles/Suppressed 45 API Test", headers=headers)
        assert r.json()["has_suppressor"] is True

        r = api_client.put("/v2/rifles/Suppressed 45 API Test", headers=headers, json={
            "scope_height_in": 2.0, "click_value_mrad": 0.1, "has_suppressor": False, "suppressor_type": "",
        })
        assert r.status_code == 200
        assert r.json()["has_suppressor"] is False
    finally:
        api_client.delete("/v2/rifles/Suppressed 45 API Test", headers=headers)


def test_get_and_add_load_work_on_a_rifle_with_no_loads_yet(user_a, api_client):
    """Regression: GET /v2/rifles/{name} and POST /v2/rifles/{name}/loads
    both used to be able to route through a resolver that defaults a
    missing load_query to get_active_load() -- raising for any rifle
    with zero loads. That 404'd GET on a freshly created rifle and,
    worse, made it impossible to POST a rifle's first load via the API
    at all. Neither endpoint touches a load; only the rifle needs to
    resolve, so v2_get_rifle/v2_add_load call find_rifle() directly."""
    _, token_a = user_a
    headers = {"Authorization": f"Bearer {token_a}"}
    try:
        r = api_client.post("/v2/rifles", headers=headers, json={
            "name": "Loadless Rifle API Test", "scope_height_in": 2.5, "click_value_mrad": 0.1, "loads": [],
        })
        assert r.status_code == 200

        r = api_client.get("/v2/rifles/Loadless Rifle API Test", headers=headers)
        assert r.status_code == 200
        assert r.json()["loads"] == []

        r = api_client.post("/v2/rifles/Loadless Rifle API Test/loads", headers=headers, json={
            "name": "First Load", "bullet_weight_gr": 175, "bc": 0.5, "drag_model": "G1",
            "muzzle_velocity_fps": 2700, "zero_distance_yd": 100,
        })
        assert r.status_code == 200
        assert r.json()["name"] == "First Load"

        r = api_client.get("/v2/rifles/Loadless Rifle API Test", headers=headers)
        assert r.status_code == 200
        assert [load["name"] for load in r.json()["loads"]] == ["First Load"]

        r = api_client.get("/v2/rifles/No Such Rifle API Test", headers=headers)
        assert r.status_code == 404

        r = api_client.post("/v2/rifles/No Such Rifle API Test/loads", headers=headers, json={
            "name": "X", "bullet_weight_gr": 175, "bc": 0.5, "drag_model": "G1",
            "muzzle_velocity_fps": 2700, "zero_distance_yd": 100,
        })
        assert r.status_code == 404
    finally:
        api_client.delete("/v2/rifles/Loadless Rifle API Test", headers=headers)


def test_status_reports_null_active_load_for_rifle_with_no_loads(user_a, api_client):
    """Regression: GET /v2/status used to require both an active rifle
    AND an active load, 404ing whenever the active rifle had zero
    loads. That's a normal state (e.g. a rifle profile set up by voice
    before its first load exists) -- the web UI's post-setup refresh
    hit this 404 and silently failed to show the newly created rifle at
    all."""
    _, token_a = user_a
    headers = {"Authorization": f"Bearer {token_a}"}
    try:
        r = api_client.post("/v2/rifles", headers=headers, json={
            "name": "Statusless Rifle API Test", "scope_height_in": 2.5, "click_value_mrad": 0.1, "loads": [],
        })
        assert r.status_code == 200

        r = api_client.get("/v2/status", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["rifle"]["name"] == "Statusless Rifle API Test"
        assert body["active_load"] is None

        r = api_client.post("/v2/rifles/Statusless Rifle API Test/loads", headers=headers, json={
            "name": "L1", "bullet_weight_gr": 175, "bc": 0.5, "drag_model": "G1",
            "muzzle_velocity_fps": 2700, "zero_distance_yd": 100,
        })
        assert r.status_code == 200

        r = api_client.get("/v2/status", headers=headers)
        assert r.status_code == 200
        assert r.json()["active_load"]["name"] == "L1"
    finally:
        api_client.delete("/v2/rifles/Statusless Rifle API Test", headers=headers)


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


# --------------------------------------- voice conversation state persistence
# Not an isolation test -- a correctness test for the DB-persisted
# conversation state design decision (MULTI_TENANCY_DESIGN.md #6.1/#7.3).
# /v2/voice/query is stateless per-request (a fresh BallisticaCLI every
# call, no cached object between requests), so a multi-turn voice
# conversation only works at all if setup/calibration state genuinely
# round-trips through conversation_state.state_json. Lives here because it
# needs the same live-account/api_client fixtures already built for the
# isolation suite, not because it's adversarial.

def test_setup_conversation_survives_across_separate_requests(user_a, api_client):
    """Two independent HTTP requests, no shared Python object between
    them (api_client.post() constructs a fresh BallisticaCLI + store
    each call) -- if the second request still recognizes the session
    the first one started, that's only possible because the state
    genuinely round-tripped through Supabase, not in-process memory."""
    _, token_a = user_a
    headers = {"Authorization": f"Bearer {token_a}"}

    start = api_client.post("/v2/voice/query", headers=headers, json={"text": "new rifle"})
    assert start.status_code == 200
    assert "call this rifle" in start.json()["reply"].lower()
    assert start.json()["awaiting_response"] is True

    # A real LLM extraction call, not just "is a session open" -- proves
    # the draft dict's actual contents survive the round trip, not just
    # the session's bare existence.
    answer = api_client.post(
        "/v2/voice/query", headers=headers,
        json={"text": "call it the Persistence Test Rifle"},
    )
    assert answer.status_code == 200
    assert "scope height" in answer.json()["reply"].lower()
    assert answer.json()["awaiting_response"] is True

    cancel = api_client.post("/v2/voice/query", headers=headers, json={"text": "cancel"})
    assert "scrapped" in cancel.json()["reply"].lower()
    assert cancel.json()["awaiting_response"] is False

    # Confirms the cancelled draft was never actually saved.
    rifles = api_client.get("/v2/rifles", headers=headers)
    assert "Persistence Test Rifle" not in [r["name"] for r in rifles.json()]


def test_user_b_voice_command_not_swallowed_by_user_a_open_session(user_a, user_b, api_client):
    """The adversarial half of the persistence test above: User A has a
    genuinely in-progress setup session; User B's completely unrelated
    command must be processed as a fresh command for User B, never
    routed into User A's session -- proving conversation state is
    isolated per-user, not just persisted per-request."""
    _, token_a = user_a
    _, token_b = user_b

    start = api_client.post(
        "/v2/voice/query", headers={"Authorization": f"Bearer {token_a}"},
        json={"text": "new rifle"},
    )
    assert start.json()["awaiting_response"] is True
    try:
        b_reply = api_client.post(
            "/v2/voice/query", headers={"Authorization": f"Bearer {token_b}"},
            json={"text": "status"},
        )
        # A genuine top-level response for B (whatever B's own state is),
        # never anything resembling A's rifle-setup interview.
        assert "call this rifle" not in b_reply.json()["reply"].lower()
        assert "what do you want" not in b_reply.json()["reply"].lower()
    finally:
        api_client.post(
            "/v2/voice/query", headers={"Authorization": f"Bearer {token_a}"},
            json={"text": "cancel"},
        )
