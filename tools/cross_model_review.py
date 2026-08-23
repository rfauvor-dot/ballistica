"""Cross-model review for Chief-of-Staff-style status summaries (Addendum 25).

This is process tooling for how status reports to Rick get produced and
checked -- not part of Ballistica the product, which is why it lives
outside the ballistica/ package and is never imported by api.py/cli.py.
It reuses ballistica.openai_client for the SSL-workaround-aware OpenAI
client rather than duplicating that logic.

Usage: after Claude synthesizes a status summary, run it through
review_with_chatgpt() before delivering it to Rick, and surface whatever
gaps/disagreements come back alongside the original summary -- per
Addendum 25, not silently reconciled or dropped.

Grok/xAI review is not wired up yet: it needs either a new xAI API key
(Rick's decision, same account-creation boundary as every other key this
project has needed) or a browser-automation fallback against Rick's own
logged-in grok.com session. See review_with_grok() below.
"""
from __future__ import annotations

from ballistica.openai_client import get_openai_client

_REVIEW_MODEL = "gpt-4o"

_REVIEW_SYSTEM_PROMPT = (
    "You are reviewing a status summary written for a non-technical stakeholder "
    "about a software project. Look specifically for: claims stated as fact that "
    "aren't actually verified yet, risks or tradeoffs that are glossed over or "
    "missing, internal inconsistencies, and anything a careful second reader "
    "would flag before this reaches a decision-maker. "
    "Be specific and terse -- cite the exact claim or passage you're reacting to. "
    "If you genuinely find nothing worth flagging, say so plainly in one sentence "
    "rather than inventing a critique to seem thorough."
)


def review_with_chatgpt(summary_text: str) -> str:
    """Returns ChatGPT's critique of a status summary, or raises on a real
    API failure -- deliberately not swallowed to None here, since a
    review step that fails silently is worse than not having it: the
    caller would believe cross-checking happened when it didn't."""
    client = get_openai_client()
    response = client.chat.completions.create(
        model=_REVIEW_MODEL,
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": summary_text},
        ],
    )
    return response.choices[0].message.content


def review_with_grok(summary_text: str) -> str:
    """Not implemented -- needs Rick's choice between a new xAI API key
    (parallel to how the Anthropic key was set up) or a Claude-in-Chrome
    pass against his own logged-in grok.com session. See this module's
    docstring."""
    raise NotImplementedError(
        "Grok review needs either an xAI API key in .env (ANTHROPIC/OPENAI-style "
        "setup) or a browser-automation path against Rick's logged-in grok.com "
        "session -- neither is wired up yet, pending his choice (Addendum 25)."
    )
