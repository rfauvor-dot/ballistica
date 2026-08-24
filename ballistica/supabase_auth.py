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


def verify_token(token: str) -> str:
    """Returns the authenticated user's id (Supabase auth.users.id, the
    JWT's `sub` claim). Raises jwt.InvalidTokenError/PyJWKClientError on
    anything invalid, expired, malformed, or unverifiable -- callers
    must treat a verification failure as "reject the request", never as
    "no user, proceed anyway" (every path fails closed, not open)."""
    if _jwks_client is None:
        raise RuntimeError("SUPABASE_URL is not configured -- cannot verify tokens")
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token, signing_key.key, algorithms=["ES256", "RS256"],
            audience="authenticated",
        )["sub"]
    except (jwt.PyJWKClientError, jwt.InvalidTokenError):
        if not _JWT_SECRET:
            raise
        return jwt.decode(
            token, _JWT_SECRET, algorithms=["HS256"], audience="authenticated",
        )["sub"]


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
