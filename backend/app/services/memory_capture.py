"""Explicit memory capture from chat messages (spec §13).

Only clearly explicit requests are stored ("remember that ...", "don't forget ...",
"my name is ..."); nothing sensitive or inferred is ever written. The agent can
of course still use the `store_memory` tool — this path captures the USER's own
words before the model runs, so the current turn already sees them.
"""
from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(?:please\s+)?(?:remember(?:\s+that)?|don'?t forget(?:\s+that)?|"
        r"note that|keep in mind(?:\s+that)?)\s*[,:]?\s+"
        r"(?P<content>(?!(?:this|that|these|those|it|them|him|her)\b).+)$",
        re.I | re.S,
    ),
    re.compile(
        r"^\s*from now on\s*[,:]?\s+(?P<content>.+)$",
        re.I | re.S,
    ),
    re.compile(
        r"^\s*my\s+(?P<field>name|email|email address|phone|phone number|birthday|"
        r"company|job title|favourite color|favorite color|home town|hometown)"
        r"\s+is\s+(?P<value>.+)$",
        re.I | re.S,
    ),
]

_MAX_LEN = 500


def extract_explicit_memory(text: str) -> str | None:
    """Return the memory content when the message is an explicit remember
    request, else None. Empty/whitespace-only results become None."""
    if not text or not text.strip():
        return None
    for pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groupdict()
        if groups.get("field"):
            content = f"The user's {groups['field']} is {groups['value'].strip()}."
        else:
            content = (groups.get("content") or "").strip()
        content = " ".join(content.split())
        if not content:
            return None
        return content[:_MAX_LEN]
    return None
