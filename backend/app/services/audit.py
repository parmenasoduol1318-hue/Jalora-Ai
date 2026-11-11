"""Audit/activity logging with secret redaction (spec §25)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent import ActivityLog

logger = logging.getLogger("jalora.audit")

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|authorization|pin|cvv|otp)", re.I
)
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")
# sk-… keys (OpenAI / compatible).  Newer formats include hyphens: sk-proj-…
_LONG_TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9\-]{16,}|ghp_[A-Za-z0-9]{20,})\b")
# Phase L (§74): additional secret patterns for redaction completeness.
_AWS_KEY_RE = re.compile(r"\b(AKIA[0-9A-Z]{16})\b")
_SLACK_TOKEN_RE = re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
# Three-part JWT: header.payload.signature (each base64url).
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
# Fernet encrypted tokens (credential store envelope encryption — §74/75).
# Fernet tokens start with "gAAAAAB" (base64 of version byte 0x80 + timestamp).
_FERNET_RE = re.compile(r"gAAAAA[A-Za-z0-9_-]{20,}")


def redact(value: Any) -> Any:
    """Recursively redact secrets from arguments before persisting them."""
    if isinstance(value, dict):
        return {
            k: "***redacted***" if _SENSITIVE_KEY_RE.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = _BEARER_RE.sub("Bearer ***redacted***", value)
        text = _LONG_TOKEN_RE.sub("***redacted***", text)
        text = _AWS_KEY_RE.sub("***redacted***", text)
        text = _SLACK_TOKEN_RE.sub("***redacted***", text)
        text = _PRIVATE_KEY_RE.sub("***redacted***", text)
        text = _JWT_RE.sub("***redacted***", text)
        text = _FERNET_RE.sub("***redacted***", text)
        return text
    return value


class _Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)


def log_activity(
    db: Session,
    *,
    user_id: str,
    run_id: str | None = None,
    agent: str = "chat",
    tool: str = "",
    arguments: dict | None = None,
    result_summary: str = "",
    success: bool = True,
    approval_status: str | None = None,
    duration_ms: int = 0,
    meta: dict | None = None,
    timer: _Timer | None = None,
    provider: str | None = None,
    error_code: str | None = None,
    task_id: str | None = None,
    task_step_id: str | None = None,
    action_id: str | None = None,
    attempt_id: str | None = None,
    outcome_status: str | None = None,
    attempt_number: int | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        user_id=user_id,
        run_id=run_id,
        task_id=task_id,
        task_step_id=task_step_id,
        action_id=action_id,
        attempt_id=attempt_id,
        outcome_status=outcome_status,
        attempt_number=attempt_number,
        agent=agent,
        tool=tool,
        arguments=redact(arguments) if arguments is not None else None,
        result_summary=redact(result_summary) if isinstance(result_summary, str) else str(result_summary),
        success=success,
        approval_status=approval_status,
        duration_ms=duration_ms if timer is None else timer.elapsed_ms,
        provider=provider,
        error_code=error_code,
        meta=redact(meta) if meta else None,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - auditing must never break the main flow
        db.rollback()
        logger.exception("failed to persist activity log")
    return entry


def timer() -> _Timer:
    return _Timer()
