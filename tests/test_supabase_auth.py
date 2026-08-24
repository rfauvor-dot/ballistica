"""Regression coverage for ballistica.supabase_auth.

The real positive case (a genuine Supabase-issued token verifies and
yields the correct user id) was proven live during implementation --
signed up a real throwaway user (dt-auth-smoketest@mailinator.com) against
the actual production Supabase project, confirmed the JWKS endpoint
verifies it, no mocking. Not re-run here on every test invocation since
it depends on live email delivery (slow, and a network dependency this
suite shouldn't require to pass). What IS re-run here, deterministically
and with no network dependency, is the fail-closed side: every path that
must reject a bad token still does, which matters at least as much as
the happy path -- a verifier that accepts real tokens but is loose on
invalid ones is the more dangerous failure mode, not the safer one.
"""
import jwt
import pytest

from ballistica.supabase_auth import verify_token


def test_garbage_token_rejected():
    with pytest.raises(jwt.InvalidTokenError):
        verify_token("not.a.jwt")


def test_empty_token_rejected():
    with pytest.raises(jwt.InvalidTokenError):
        verify_token("")


def test_forged_token_wrong_signer_rejected():
    """A token structurally valid and even claiming aud=authenticated,
    but signed with a key nobody but an attacker knows -- must never be
    accepted just because it's well-formed and has the right shape."""
    forged = jwt.encode(
        {"sub": "attacker-fake-user-id", "aud": "authenticated"},
        "attacker-controlled-secret-that-is-not-supabases",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        verify_token(forged)


def test_token_with_stripped_signature_rejected():
    """A real-shaped token with its signature segment removed -- must
    fail closed, not be treated as an unsigned-but-trusted token."""
    fake_header_payload = (
        "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJhbnlvbmUiLCJhdWQiOiJhdXRoZW50aWNhdGVkIn0."
    )
    with pytest.raises(jwt.InvalidTokenError):
        verify_token(fake_header_payload)
