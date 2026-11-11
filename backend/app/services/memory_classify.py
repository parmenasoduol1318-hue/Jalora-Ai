"""Rule-based classification of long-term memories (spec §13).

Deterministic, offline classifier used when a caller stores a memory with
kind="auto". Priority: contact > person > preference > project > fact.
"""
from __future__ import annotations

import re

MEMORY_KINDS = ("fact", "preference", "contact", "person", "project")

_CONTACT_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),                 # email
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),                        # phone-ish
    re.compile(r"\b\d+\s+\w+\s+(street|st|road|rd|avenue|ave|lane|ln|drive|dr)\b", re.I),
]
_PERSON_PATTERNS = [
    re.compile(
        r"\bmy\s+(wife|husband|partner|mum|mom|mother|father|dad|son|daughter|"
        r"brother|sister|boss|friend|colleague|manager|child|children|dog|cat)\b",
        re.I,
    ),
    re.compile(r"\b(name\s+is|full\s+name|called)\s+[A-Z][a-z]+", ),
]
_PREFERENCE_PATTERNS = [
    re.compile(
        r"\b(like|likes|loves?|hates?|prefers?|enjoys?|wants?|favourite|favorite|"
        r"usually|always|never|don't like|do not like|dislike|dislikes)\b", re.I
    ),
]
_PROJECT_PATTERNS = [
    re.compile(
        r"\b(deadlines?|meetings?|launch(es|ed)?|sprint|roadmap|project|"
        r"milestones?|due\s+(by|on|at)|quarter|release)\b", re.I
    ),
]


def classify_kind(text: str) -> str:
    """Map free text to a memory kind: contact | person | preference | project | fact."""
    if not text or not text.strip():
        return "fact"
    for pattern in _CONTACT_PATTERNS:
        if pattern.search(text):
            return "contact"
    for pattern in _PERSON_PATTERNS:
        if pattern.search(text):
            return "person"
    for pattern in _PREFERENCE_PATTERNS:
        if pattern.search(text):
            return "preference"
    for pattern in _PROJECT_PATTERNS:
        if pattern.search(text):
            return "project"
    return "fact"
