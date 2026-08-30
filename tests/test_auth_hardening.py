"""Adversarial tests for the JWT verification layer itself
(ballistica/supabase_auth.py), distinct from test_tenant_isolation.py's
per-row RLS/ownership tests. Those prove a *valid* token can't reach
another user's data; these prove an *invalid* token in each of the
specific ways a real one can be broken is rejected cleanly (401), not
silently accepted or allowed to crash the request (500).

Requires SUPABASE_URL/SUPABASE_JWT_SECRET configured (same as
supabase_auth.py) -- skipped entirely otherwise. Signs test tokens with
the real legacy HS256 secret (SUPABASE_JWT_SECRET) rather than a
made-up one: a token signed with the wrong secret only proves signature
checking works (already covered by test_tenant_isolation.py's "forged"
case) -- these tests need a *validly signed* token that's broken in
some other specific way (expired, missing a claim, tampered after
signing) to actually exercise the claim-validation logic, not just the
signature check. verify_token() tries JWKS first and falls through to
this legacy secret on any JWKS failure (see supabase_auth.py's own
docstring) -- a token signed with the real project secret but with no
JWKS-recognized `kid` header takes that fallback path, exercising the
real, live verify_token() function end to end either way.
"""
from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

from conftest import requires_supabase

pytestmark = requires_supabase

_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

requires_jwt_secret = pytest.mark.skipif(
    not _SECRET, reason="SUPABASE_JWT_SECRET not configured -- can't sign real-secret test tokens.",
)


def _sign(claims: dict, secret: str = _SECRET, algorithm: str = "HS256") -> str:
    return pyjwt.encode(claims, secret, algorithm=algorithm)


def _valid_claims(**overrides) -> dict:
    claims = {"sub": "00000000-0000-0000-0000-000000000000", "aud": "authenticated",
              "exp": int(time.time()) + 3600}
    claims.update(overrides)
    return claims


def _assert_rejected(api_client, token: str):
    resp = api_client.get("/v2/rifles", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text}"


@requires_jwt_secret
def test_expired_token_rejected(api_client):
    token = _sign(_valid_claims(exp=int(time.time()) - 3600))
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_token_expiring_this_instant_rejected(api_client):
    # Exactly at the boundary, past the clock-skew leeway -- not just
    # "very expired," the edge case the leeway window itself could mask
    # if it were implemented wrong (e.g. applied to the wrong side).
    token = _sign(_valid_claims(exp=int(time.time()) - 60))
    _assert_rejected(api_client, token)


def test_completely_malformed_token_rejected(api_client):
    _assert_rejected(api_client, "this-is-not-a-jwt-at-all")


def test_empty_token_rejected(api_client):
    _assert_rejected(api_client, "")


def test_truncated_real_token_rejected(api_client):
    """A syntactically-plausible but incomplete JWT (has the right
    number of dot-separated segments cut short) -- a different failure
    mode than a bare non-JWT string."""
    if not _SECRET:
        pytest.skip("SUPABASE_JWT_SECRET not configured")
    token = _sign(_valid_claims())
    _assert_rejected(api_client, token[: len(token) // 2])


@requires_jwt_secret
def test_tampered_signature_rejected(api_client):
    """A validly-shaped token whose signature has been corrupted after
    signing -- distinct from test_tenant_isolation.py's "signed with
    the wrong secret entirely" forgery case: this proves the signature
    bytes themselves are actually checked, not just present."""
    token = _sign(_valid_claims())
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    _assert_rejected(api_client, f"{header}.{payload}.{tampered_signature}")


@requires_jwt_secret
def test_tampered_payload_rejected(api_client):
    """Payload altered after signing (e.g. swapping in a different
    sub/user id) without re-signing -- the signature no longer matches
    the (now-different) payload it was computed over."""
    token = _sign(_valid_claims())
    header, payload, signature = token.split(".")
    tampered_payload = _sign(_valid_claims(sub="11111111-1111-1111-1111-111111111111")).split(".")[1]
    _assert_rejected(api_client, f"{header}.{tampered_payload}.{signature}")


@requires_jwt_secret
def test_missing_sub_claim_rejected(api_client):
    """Regression test for a real bug found 2026-08-29: a validly-signed
    token with no 'sub' claim at all used to raise a bare KeyError from
    payload["sub"], which get_current_user_id's except clause didn't
    catch -- an unhandled 500 instead of a clean 401. Fixed in
    supabase_auth.py's _extract_sub()."""
    claims = _valid_claims()
    del claims["sub"]
    token = _sign(claims)
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_null_sub_claim_rejected(api_client):
    token = _sign(_valid_claims(sub=None))
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_empty_string_sub_claim_rejected(api_client):
    token = _sign(_valid_claims(sub=""))
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_missing_aud_claim_rejected(api_client):
    claims = _valid_claims()
    del claims["aud"]
    token = _sign(claims)
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_wrong_aud_claim_rejected(api_client):
    token = _sign(_valid_claims(aud="not-authenticated"))
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_missing_exp_claim_rejected(api_client):
    """No expiration at all -- a token that (absent this check) would
    never expire. PyJWT rejects a missing 'exp' by default when the
    verify_exp option is on, but this is exactly the kind of default
    worth pinning down with a real test rather than trusting it never
    regresses via some future decode-options change."""
    claims = _valid_claims()
    del claims["exp"]
    token = _sign(claims)
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_none_algorithm_rejected(api_client):
    """The classic 'alg: none' JWT attack -- a token that declares it
    has no signature at all, hoping a lenient verifier skips signature
    checking entirely. PyJWT's decode() requires the algorithm to be in
    the explicit allow-list (["ES256", "RS256"] / ["HS256"] in
    supabase_auth.py) and 'none' is never in either, but this is
    security-critical enough to prove directly rather than infer from
    reading the algorithms= list."""
    header = pyjwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    payload = pyjwt.utils.base64url_encode(
        __import__("json").dumps(_valid_claims()).encode()
    ).decode()
    token = f"{header}.{payload}."
    _assert_rejected(api_client, token)


@requires_jwt_secret
def test_wrong_algorithm_family_rejected(api_client):
    """A token whose header claims RS256 (asymmetric) but is actually
    HMAC-signed with the public verification material as the secret --
    the other classic JWT algorithm-confusion attack. Not directly
    reachable here without a real RSA public key to substitute, so this
    instead confirms the narrower, directly-testable guarantee: a token
    signed with a algorithm outside supabase_auth.py's explicit
    allow-list is rejected rather than silently accepted."""
    token = _sign(_valid_claims(), algorithm="HS512")
    _assert_rejected(api_client, token)
