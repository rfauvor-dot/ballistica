"""Shared Anthropic client factory, mirroring openai_client.py.

Kept as its own module for the same reason as openai_client.py: cli.py
needs it without creating a circular import through api.py.
"""
from __future__ import annotations

import anthropic

_anthropic_client: anthropic.Anthropic | None = None


def get_anthropic_client() -> anthropic.Anthropic:
    """Lazily builds (and caches) the Anthropic client.

    Unlike openai_client.py, no SSL workaround is needed here -- the
    Anthropic SDK's default httpx client construction doesn't route
    through the same truststore code path that broke httpx2/httpcore2
    against Python 3.14 (verified directly: constructing a default
    client here doesn't hit that RecursionError).

    Lazy + cached rather than built at import time so importing this
    module doesn't hard-fail in environments with no ANTHROPIC_API_KEY
    set (tests, or before the env var is configured on Render).
    """
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client
