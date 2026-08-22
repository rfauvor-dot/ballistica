"""Shared OpenAI client factory.

Split out from api.py so cli.py can use it too (for LLM-based intent
extraction) without api.py and cli.py importing each other -- api.py
already imports BallisticaCLI from cli.py, so the reverse import would
be circular.
"""
from __future__ import annotations

import ssl

import certifi
import httpx2
import openai

_openai_client: openai.OpenAI | None = None


def get_openai_client() -> openai.OpenAI:
    """Lazily builds (and caches) the OpenAI client.

    httpx2/httpcore2's default SSL context construction routes through
    `truststore` for native OS certificate-store integration, which has
    a confirmed infinite-recursion bug (RecursionError) against Python
    3.14's ssl module as of truststore 0.10.4 -- reproduced directly,
    not assumed. Supplying our own pre-built context via certifi's CA
    bundle instead of letting httpcore2 construct its default one skips
    that code path entirely. This is a workaround for an upstream
    library bug, not a security downgrade: it still verifies against a
    real, current CA bundle.

    Lazy + cached rather than built at import time so importing this
    module doesn't hard-fail in environments with no OPENAI_API_KEY set
    (tests, or Render before the env var is configured).
    """
    global _openai_client
    if _openai_client is None:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        http_client = httpx2.Client(verify=ssl_context)
        _openai_client = openai.OpenAI(http_client=http_client)
    return _openai_client
