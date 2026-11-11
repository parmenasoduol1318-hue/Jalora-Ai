"""Preference extractor and feedback→memory promotion (Phase K §27, §70, §72).

A post-task job inspects recent user feedback on a finished task.  Repeated
negative feedback on the same dimension (e.g. "too verbose", "wrong tone")
is promoted into a ``kind=preference`` long-term memory via
``memory_service.update_memory`` which reconciles contradictions (§72).

The default extractor is **deterministic and rule-based** — no LLM call is
required, so tests are fully hermetic.  When an LLM is available it can act
as a second pass, but the rule-based path is the source of truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feedback import Feedback
from app.models.task import Task
from app.services import memory_service

# ---------------------------------------------------------------------------
# rule patterns — each maps a feedback signal to (scope, preference_content,
# minimum repetition count)

# Maps keyword → (scope, preference_text, min_count)
_LENGTH_PATTERNS: list[tuple[re.Pattern[str], str, str, int]] = [
    (re.compile(r"\b(very\s+)?(long|verbose|wordy|too\s+many\s+details?)\b", re.I),
     "response_length", "User prefers concise, brief answers with bullet points.",
     2),
    (re.compile(r"\b(concise|short|brief|too\s+long|more\s+detail|too\s+short)\b", re.I),
     "response_length", "User prefers detailed, thorough answers with full explanations.",
     2),
]

_TONE_PATTERNS: list[tuple[re.Pattern[str], str, str, int]] = [
    (re.compile(r"\b(too\s+formal|too\s+casual|too\s+stiff|professional)\b", re.I),
     "tone", "User prefers a friendly, conversational tone.",
     2),
    (re.compile(r"\b(too\s+friendly|too\s+casual|professional|serious)\b", re.I),
     "tone", "User prefers a professional, formal tone.",
     2),
]

_LANGUAGE_PATTERNS: list[tuple[re.Pattern[str], str, str, int]] = [
    (re.compile(r"\b(swahili|kiswahili|swahili)\b", re.I),
     "language", "User prefers responses in Swahili.",
     2),
    (re.compile(r"\b(english|english)\b", re.I),
     "language", "User prefers responses in English.",
     2),
]

_PROVIDER_PATTERNS: list[tuple[re.Pattern[str], str, str, int]] = [
    (re.compile(r"\b(too\s+slow|prefer\s+(?P<provider>openai|anthropic|groq|google|deepseek|mistral|ollama))\b", re.I),
     "preferred_provider", "User prefers the {provider} AI provider for faster responses.",
     2),
]


def _extract_length(content: str) -> tuple[str, str] | None:
    for pattern, scope, pref_text, _min in _LENGTH_PATTERNS:
        if pattern.search(content):
            return (scope, pref_text)
    return None


def _extract_tone(content: str) -> tuple[str, str] | None:
    for pattern, scope, pref_text, _min in _TONE_PATTERNS:
        if pattern.search(content):
            return (scope, pref_text)
    return None


def _extract_language(content: str) -> tuple[str, str] | None:
    for pattern, scope, pref_text, _min in _LANGUAGE_PATTERNS:
        if pattern.search(content):
            return (scope, pref_text)
    return None


def _extract_provider(content: str) -> tuple[str, str] | None:
    for pattern, scope, pref_text_tmpl, _min in _PROVIDER_PATTERNS:
        m = pattern.search(content)
        if m and m.group("provider"):
            return (scope, pref_text_tmpl.format(provider=m.group("provider")))
    return None


def _extract_preference(content: str) -> tuple[str, str] | None:
    """Rule-based single-pass extractor. Returns (scope, preference_text) or None."""
    for fn in (_extract_length, _extract_tone, _extract_language, _extract_provider):
        result = fn(content)
        if result:
            return result
    return None


@dataclass
class ExtractionResult:
    """Result of preference extraction for a single task."""
    promoted: list[dict[str, str]] = field(default_factory=list)
    """List of {scope, content, source_fb_id} dicts for preferences promoted."""


async def extract_preferences(
    db: Session,
    *,
    user_id: str,
    task_id: str,
    min_repetitions: int = 2,
) -> ExtractionResult:
    """Post-task preference extraction (spec §27, §70, §72).

    Scans feedback on ``task_id``. When the same preference scope appears in
    ``min_repetitions`` or more feedback items, promote it to a
    ``kind=preference`` memory via ``memory_service.update_memory`` (which
    reconciles contradictions — §72).
    """
    if not memory_service.is_enabled({"memory_enabled": True}, True):
        return ExtractionResult()

    # Gather all feedback on this task (positive and negative).
    rows = list(db.scalars(
        select(Feedback).where(
            Feedback.user_id == user_id,
            Feedback.task_id == task_id,
        ).order_by(Feedback.created_at.desc())
    ))
    if not rows:
        return ExtractionResult()

    # Group extracted preferences by scope.
    scope_groups: dict[str, list[tuple[str, str]]] = {}  # scope -> [(content, fb_id)]
    for fb in rows:
        if not fb.content.strip():
            continue
        result = _extract_preference(fb.content)
        if result:
            scope, pref_text = result
            scope_groups.setdefault(scope, []).append((pref_text, fb.id))

    result = ExtractionResult()
    for scope, items in scope_groups.items():
        # Count unique preference texts within this scope.
        texts = {t for t, _ in items}
        if len(items) >= min_repetitions:
            pref_text = next(iter(texts))  # pick the first matching text
            source_fb = items[0][1]
            memory = await memory_service.update_memory(
                db,
                user_id=user_id,
                content=pref_text,
                kind="preference",
                source="preference_extractor",
                scope=scope,
            )
            if memory is None:
                continue
            result.promoted.append({
                "scope": scope,
                "content": memory.content,
                "source_fb_id": source_fb,
            })
    return result


def build_preference_context(memories: list) -> str:
    """Build a preference context string from preference memories.

    Called during system prompt construction to inject the user's learned
    preferences (response length, tone, language, etc.) into the prompt.
    """
    prefs = [m for m in memories if getattr(m, "kind", "") == "preference"]
    if not prefs:
        return ""
    lines = [f"- {m.content}" for m in prefs]
    return (
        "Learned user preferences (follow these in your responses):\n"
        + "\n".join(lines)
    )
