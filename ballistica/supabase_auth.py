"""Supabase JWT verification.

Verifies the bearer token FastAPI receives on each request against
Supabase's own signing keys, returning the authenticated user's id (the
`sub` claim) -- the seam every per-user-scoped endpoint depends on.

Supabase supports two signing schemes: the newer asymmetric JWT signing
keys (verified locally via a JWKS public-key endpoint, no shared secret
needed -- Supabase's own recommended approach) and the older single
symmetric HS256 secret. This project's JWKS endpoint should expose
whichever key(s) are actually issuing tokens, so JWKS verification is
tried first; the legacy secret (SUPABASE_JWT_SECRET) is only a fallback
for a token JWKS can't find a matching key for, covering a project still
on the old symmetric-only scheme (confirmed live: this project's tokens
verify via JWKS, so the fallback path is currently unused in practice --
kept because SUPABASE_JWT_SECRET was provided and legacy tokens are a
real possibility Supabase itself documents, not a hypothetical).
"""
from __future__ import annotations

import os
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException

# Self-contained rather than relying on another module (api.py/cli.py)
# having already loaded .env first -- import order shouldn't matter for
# whether this module can find its own configuration.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
_JWKS_URL = f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json" if _SUPABASE_URL else ""

_jwks_client = jwt.PyJWKClient(_JWKS_URL) if _JWKS_URL else None


# Root-caused live: the intermittent 401s chasing this whole file
# weren't a JWKS/algorithm problem at all (that retry-with-a-fresh-
# client fix above was solving a real but different, much rarer issue).
# Direct reproduction caught the actual exception: ImmatureSignatureError
# ("the token is not yet valid (iat)") -- ordinary clock skew between
# this machine and Supabase's server, PyJWT rejecting a token whose
# issued-at claim looks a few seconds in the future by local system
# time, with zero tolerance by default. leeway is the standard,
# widely-used mitigation for exactly this -- real-world clocks are
# never perfectly synchronized -- not a security weakening at this
# scale (a few seconds of tolerance doesn't meaningfully help forge a
# token; the signature check itself is untouched).
_CLOCK_SKEW_LEEWAY_SECONDS = 10

# PyJWT only *validates* exp/aud if they're present -- it doesn't require
# them to be there at all unless told to. A token with no 'exp' claim at
# all would otherwise verify successfully and never expire, and a
# missing 'aud' would skip the audience check entirely (found via real
# testing, tests/test_auth_hardening.py -- not a hypothetical).
_REQUIRED_CLAIMS = ["exp", "aud", "sub"]


def _extract_sub(payload: dict) -> str:
    # jwt.decode() only validates the claims it's told to check (signature,
    # exp, aud) -- a well-signed token missing 'sub' entirely decodes
    # without error, and payload["sub"] would then raise a bare KeyError
    # that get_current_user_id's except clause doesn't catch, surfacing as
    # an unhandled 500 instead of a clean 401. Found via real testing
    # (tests/test_auth_hardening.py), not a hypothetical -- a raw ["sub"]
    # was the pre-2026-08-29 code here.
    sub = payload.get("sub")
    if not sub:
        raise jwt.InvalidTokenError("Token missing required 'sub' claim")
    return sub


def _verify_via_jwks(token: str, client: jwt.PyJWKClient) -> str:
    signing_key = client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token, signing_key.key, algorithms=["ES256", "RS256"], audience="authenticated",
        leeway=_CLOCK_SKEW_LEEWAY_SECONDS, options={"require": _REQUIRED_CLAIMS},
    )
    return _extract_sub(payload)


def verify_token(token: str) -> str:
    """Returns the authenticated user's id (Supabase auth.users.id, the
    JWT's `sub` claim). Raises jwt.InvalidTokenError/PyJWKClientError on
    anything invalid, expired, malformed, or unverifiable -- callers
    must treat a verification failure as "reject the request", never as
    "no user, proceed anyway" (every path fails closed, not open).

    Root-caused an intermittent live failure: PyJWKClient's own
    get_signing_key() already refetches once on a kid it doesn't
    recognize (Supabase can have multiple simultaneously-valid signing
    keys in rotation -- Active/Standby/Previously-used per their own
    docs), but a completely fresh client -- no reliance on ANY internal
    cache state -- is a stronger fallback than trusting that one
    internal retry always covers it. Previously this fell through to
    the legacy HS256 secret on ANY JWKS failure, which produced a
    misleading "alg not allowed" error for what was actually a
    transient JWKS lookup issue on a real ES256 token (this project
    doesn't use the legacy scheme at all -- confirmed live) -- masking
    the real cause instead of surfacing or resolving it."""
    if _jwks_client is None:
        raise RuntimeError("SUPABASE_URL is not configured -- cannot verify tokens")
    try:
        return _verify_via_jwks(token, _jwks_client)
    except jwt.PyJWKClientError:
        # A fully independent client, not the cached module-level one --
        # rules out any stale in-memory state before concluding this
        # genuinely isn't a JWKS-verifiable token.
        try:
            return _verify_via_jwks(token, jwt.PyJWKClient(_JWKS_URL))
        except jwt.PyJWKClientError:
            pass
    except jwt.InvalidTokenError:
        raise
    if not _JWT_SECRET:
        raise jwt.InvalidTokenError("No matching JWKS signing key found, and no legacy secret configured")
    payload = jwt.decode(
        token, _JWT_SECRET, algorithms=["HS256"], audience="authenticated",
        leeway=_CLOCK_SKEW_LEEWAY_SECONDS, options={"require": _REQUIRED_CLAIMS},
    )
    return _extract_sub(payload)


def get_current_user_id(authorization: str = Header(...)) -> str:
    """FastAPI dependency. A missing/malformed header or a token that
    fails verification is rejected outright (401) -- never silently
    treated as an unauthenticated-but-permitted request."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization[len("Bearer "):]
    try:
        return verify_token(token)
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
