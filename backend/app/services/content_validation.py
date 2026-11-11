"""Validation shared by generated user-facing content and artifacts.

Generated text is untrusted input.  A fluent model response is not evidence
that a document or message is ready to deliver, so callers must validate it
before persisting or using it in a consequential operation.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


_BRACKET_TOKEN = re.compile(r"\[([^\]\n]{1,120})\]")
_CURLY_TOKEN = re.compile(r"\{\{\s*[^{}\n]{1,120}\s*\}\}")
_ANGLE_TOKEN = re.compile(r"(?<![\w/])<([A-Za-z][^<>\n]{0,80})>(?![\w])")
_INSTRUCTIONAL_TOKEN = re.compile(
    r"\b(?:TODO|TBD|TO BE COMPLETED|INSERT\s+(?:NAME|DATE|DETAILS?)|FILL\s+IN)\b",
    re.IGNORECASE,
)
_INCOMPLETE_REFERENCE = re.compile(
    r"\(\s*(?:see|check|refer to)\s+(?:the\s+)?(?:official\s+)?"
    r"(?:portal|website|source|page)[^)]*\)"
    r"|\(\s*or\s+(?:the\s+)?(?:current\s+)?"
    r"(?:officeholder|incumbent|holder)[^)]*\)",
    re.IGNORECASE,
)


def find_unresolved_placeholders(text: str | None) -> list[str]:
    """Return unresolved template markers while ignoring Markdown links."""
    if not text:
        return []
    found: list[str] = []
    for match in _BRACKET_TOKEN.finditer(text):
        end = match.end()
        # ``[label](https://...)`` is a real Markdown link, not a placeholder.
        if end < len(text) and text[end] == "(":
            continue
        token = match.group(0)
        if token not in found:
            found.append(token)
    for pattern in (
        _CURLY_TOKEN,
        _ANGLE_TOKEN,
        _INSTRUCTIONAL_TOKEN,
        _INCOMPLETE_REFERENCE,
    ):
        for match in pattern.finditer(text):
            token = match.group(0)
            if token not in found:
                found.append(token)
    return found


def validate_generated_text(
    text: str | None, *, field: str, required: bool = True
) -> list[str]:
    """Return stable, user-safe validation errors for generated text."""
    errors: list[str] = []
    value = (text or "").strip()
    if required and not value:
        errors.append(f"{field} is required.")
    placeholders = find_unresolved_placeholders(value)
    if placeholders:
        errors.append(f"{field} contains unresolved placeholders.")
    return errors


def validate_artifact_file(path) -> list[str]:
    """Verify that a generated artifact exists, is a regular non-empty file."""
    try:
        if not path.exists():
            return ["Generated artifact was not created."]
        if not path.is_file():
            return ["Generated artifact is not a file."]
        if path.stat().st_size <= 0:
            return ["Generated artifact is empty."]
    except OSError:
        return ["Generated artifact could not be inspected."]
    return []


def validate_research_report(
    content: str | None, sources: list[dict] | None,
) -> list[str]:
    """Validate the evidence boundary for a generated research artifact.

    Search-result snippets are discovery aids, not sufficient evidence for a
    report. The persisted document must cite a page opened during this run.
    """
    errors = validate_generated_text(content, field="report content")
    valid_urls: list[str] = []
    opened_urls: list[str] = []
    for source in sources or []:
        url = str(source.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            valid_urls.append(url)
            if source.get("source_type") == "page":
                opened_urls.append(url)
    if not valid_urls:
        errors.append("At least one verified HTTP(S) source is required.")
    elif not opened_urls:
        errors.append("Open a source page before creating a research report.")
    elif not any(url in (content or "") for url in opened_urls):
        errors.append("The report must cite at least one source page opened during this run.")
    return errors
