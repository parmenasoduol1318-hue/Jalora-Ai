"""Objective checks for explicit response-format constraints.

These checks are intentionally small and general. They do not judge whether a
free-form answer is "good"; they only detect measurable requirements that the
user explicitly requested and can be safely checked without another model.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseConstraints:
    exact_sentences: int | None = None
    exact_bullets: int | None = None
    max_words_per_bullet: int | None = None
    json_only: bool = False


def infer_constraints(prompt: str) -> ResponseConstraints:
    text = " ".join((prompt or "").split())
    exact_sentences: int | None = None
    exact_bullets: int | None = None
    max_words: int | None = None

    sentence_match = re.search(
        r"\b(?:in|with|exactly)\s+(?:exactly\s+)?(\d+)\s+sentences?\b|"
        r"\b(?:one|1)\s+sentence\b", text, re.I,
    )
    if sentence_match:
        exact_sentences = int(sentence_match.group(1) or 1)

    bullet_match = re.search(
        r"\b(?:exactly\s+)?(\d+)\s+(?:bullet\s+points?|bullets?)\b",
        text, re.I,
    )
    if bullet_match:
        exact_bullets = int(bullet_match.group(1))

    words_match = re.search(
        r"\b(?:each|every)\s+(?:(?:bullet|point|item)\w*\s+)?"
        r"(?:under|below|less\s+than|no\s+more\s+than)\s+(\d+)\s+words?\b",
        text, re.I,
    ) or re.search(r"\b(?:each|every)\s+under\s+(\d+)\s+words?\b", text, re.I)
    if words_match:
        max_words = int(words_match.group(1))

    return ResponseConstraints(
        exact_sentences=exact_sentences,
        exact_bullets=exact_bullets,
        max_words_per_bullet=max_words,
        json_only=bool(re.search(r"\b(?:valid\s+)?json\s+only\b", text, re.I)),
    )


def _sentence_count(text: str) -> int:
    # Require terminal punctuation or line boundaries; this avoids splitting
    # decimals and abbreviations in ordinary short responses.
    chunks = re.findall(r"[^.!?\n]+[.!?](?=\s|$)|[^.!?\n]+$", text.strip())
    return sum(bool(chunk.strip()) for chunk in chunks)


def _bullet_lines(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for line in text.splitlines()
        if (match := re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$", line))
    ]


def validate_response(prompt: str, response: str) -> list[str]:
    """Return measurable constraint violations, or an empty list."""
    constraints = infer_constraints(prompt)
    text = (response or "").strip()
    issues: list[str] = []
    if constraints.exact_sentences is not None:
        actual = _sentence_count(text)
        if actual != constraints.exact_sentences:
            issues.append(
                f"expected exactly {constraints.exact_sentences} sentence(s), found {actual}"
            )
    bullets = _bullet_lines(text)
    if constraints.exact_bullets is not None and len(bullets) != constraints.exact_bullets:
        issues.append(
            f"expected exactly {constraints.exact_bullets} bullet(s), found {len(bullets)}"
        )
    if constraints.max_words_per_bullet is not None:
        too_long = [len(re.findall(r"\b[\w'’-]+\b", item)) for item in bullets
                    if len(re.findall(r"\b[\w'’-]+\b", item)) > constraints.max_words_per_bullet]
        if too_long:
            issues.append(
                f"each bullet must contain at most {constraints.max_words_per_bullet} words"
            )
    if constraints.json_only:
        candidate = text
        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.I)
        try:
            json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            issues.append("response must be valid JSON only")
    return issues


def correction_instruction(prompt: str, response: str, issues: list[str]) -> str:
    del prompt  # included in the message history; avoid duplicating user text
    return (
        "Your previous answer violated explicit output constraints: "
        + "; ".join(issues)
        + ". Rewrite the answer now, preserving its factual content, and return "
        "only the corrected response. Do not mention this validation instruction."
    )
