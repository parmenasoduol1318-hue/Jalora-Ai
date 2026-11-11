"""Workflow pattern memory (spec §71, Phase K).

On task *success*: store a fingerprint of the tool sequence so the planner
  can reuse a known-good recipe.
On task *failure*: store (context, failing_tool, cause, replacement) so the
  next plan avoids the same trap.

Patterns are retrieved by matching the goal/context and injected into the
planner context as guidance.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import utcnow
from app.models.workflow import WorkflowPattern


def _fingerprint_from_steps(steps: list[dict]) -> list[str]:
    """Extract an ordered list of tool-call names from task plan steps."""
    names: list[str] = []
    for step in steps or []:
        name = step.get("tool") or step.get("name") or ""
        if name:
            names.append(name)
    return names


def _label_from_fingerprint(fingerprint: list[str]) -> str:
    """Generate a short human-readable label from a tool fingerprint."""
    if not fingerprint:
        return "empty-workflow"
    # Use the first verb + last action as a label hint.
    first = fingerprint[0].replace("_", "-") if fingerprint else "workflow"
    last = fingerprint[-1].replace("_", "-") if fingerprint else "action"
    return f"{first}->{last}" if first != last else first


def _make_success_label(goal: str, fingerprint: list[str]) -> str:
    """Build a label from the goal keywords + fingerprint summary."""
    fp_label = _label_from_fingerprint(fingerprint)
    # Pull the most distinctive word(s) from the goal.
    tokens = [t for t in re.split(r"[^\w]+", goal.lower()) if t and len(t) > 3]
    if tokens:
        return f"{fp_label} [{tokens[0]}]"
    return fp_label


def record_success(
    db: Session,
    *,
    user_id: str,
    goal: str,
    fingerprint: list[str],
) -> WorkflowPattern:
    """Store a successful tool-sequence fingerprint.

    If an identical fingerprint already exists for this user, increment its
    hit_count instead of duplicating.
    """
    existing = db.scalars(
        select(WorkflowPattern).where(
            WorkflowPattern.user_id == user_id,
            WorkflowPattern.pattern_type == "success",
            WorkflowPattern.fingerprint == json.dumps(fingerprint),
        )
    ).first()
    if existing:
        existing.hit_count += 1
        existing.updated_at = utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    pattern = WorkflowPattern(
        user_id=user_id,
        pattern_type="success",
        label=_make_success_label(goal, fingerprint),
        fingerprint=json.dumps(fingerprint),
        context=goal[:1000],
        hit_count=1,
    )
    db.add(pattern)
    db.commit()
    db.refresh(pattern)
    return pattern


def record_failure(
    db: Session,
    *,
    user_id: str,
    goal: str,
    failing_tool: str,
    cause: str,
    replacement: str = "",
) -> WorkflowPattern:
    """Store a failure pattern: what went wrong and what to try instead."""
    pattern = WorkflowPattern(
        user_id=user_id,
        pattern_type="failure",
        label=failing_tool or "unknown-failure",
        fingerprint="[]",
        context=goal[:1000],
        failing_tool=failing_tool,
        cause=cause,
        replacement=replacement,
    )
    db.add(pattern)
    db.commit()
    db.refresh(pattern)
    return pattern


def find_matching_failure(
    db: Session,
    *,
    user_id: str,
    context: str,
) -> WorkflowPattern | None:
    """Find a failure pattern whose context or failing_tool matches the
    current task context. Returns the most recent match."""
    ctx_lower = context.lower()
    rows = db.scalars(
        select(WorkflowPattern).where(
            WorkflowPattern.user_id == user_id,
            WorkflowPattern.pattern_type == "failure",
        ).order_by(WorkflowPattern.created_at.desc()).limit(50)
    ).all()
    for row in rows:
        if row.failing_tool and row.failing_tool.lower() in ctx_lower:
            return row
        if row.context and _context_overlap(row.context, ctx_lower) > 0.3:
            return row
    return None


def find_successful_fingerprint(
    db: Session,
    *,
    user_id: str,
    context: str,
) -> list[str] | None:
    """Find a successful tool sequence fingerprint for a similar context."""
    ctx_lower = context.lower()
    rows = db.scalars(
        select(WorkflowPattern).where(
            WorkflowPattern.user_id == user_id,
            WorkflowPattern.pattern_type == "success",
        ).order_by(WorkflowPattern.hit_count.desc(),
                   WorkflowPattern.created_at.desc()).limit(50)
    ).all()
    for row in rows:
        if not row.fingerprint:
            continue
        if _context_overlap(row.context, ctx_lower) > 0.3:
            try:
                return json.loads(row.fingerprint)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _context_overlap(a: str, b: str) -> float:
    """Jaccard similarity of token sets between two context strings."""
    if not a or not b:
        return 0.0
    tokens_a = set(_tokenize(a))
    tokens_b = set(_tokenize(b))
    if not tokens_a and not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if t]


def build_workflow_context(
    db: Session,
    *,
    user_id: str,
    context: str,
) -> str:
    """Build a guidance string from workflow patterns for the current task."""
    parts: list[str] = []

    failure = find_matching_failure(db, user_id=user_id, context=context)
    if failure:
        parts.append(
            "Workflow guidance — last time something similar was attempted:\n"
            f"- Tool '{failure.failing_tool}' failed: {failure.cause}\n"
            f"- Suggested alternative: {failure.replacement or '(try a different approach)'}"
        )

    fingerprint = find_successful_fingerprint(db, user_id=user_id, context=context)
    if fingerprint:
        parts.append(
            "Workflow guidance — a similar task was completed successfully before "
            f"using: {', '.join(fingerprint)}. "
            "You may reuse this recipe."
        )

    if not parts:
        return ""
    return "\n\n".join(parts)
