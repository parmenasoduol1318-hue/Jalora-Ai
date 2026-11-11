"""Inbound auto-reply engine (spec §38).

Deterministic matching: enabled rules for the user, ordered by priority, are
tested against the sender / topic (case-insensitive substring) and the rule's
time window (minutes since midnight). Matched rules honour frequency caps
(per hour / per day) and per-rule approval, and sending stays behind the same
`send_email` policy as any other outgoing mail.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import JaloraError
from app.models.agent import ActivityLog
from app.models.autoreply import AutoReplyRule
from app.models.task import TaskStep
from app.models.user import User
from app.services import email as email_service
from app.services.policy import decide_for_user
from app.tools.base import PermissionLevel

logger = logging.getLogger("jalora.autoreply")

_DEFAULT_TEMPLATES = {
    "professional": ("Thank you for your message regarding \"{subject}\". "
                     "Our team is looking into it and will get back to you shortly."),
    "friendly": ("Hi {name}, thanks for reaching out about \"{subject}\"! We "
                 "will get back to you as soon as we can."),
    "brief": "Thanks for the note about {subject}.",
}


def _name_from(addr: str) -> str:
    addr = (addr or "").strip()
    if "<" in addr and ">" in addr:
        addr = addr.split("<", 1)[0].strip().strip('"')
    return addr or "there"


def _minutes_now() -> int:
    now = datetime.now().astimezone().replace(tzinfo=None)
    return now.hour * 60 + now.minute


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _bucket_key(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def select_rule(db: Session, *, user_id: str, sender: str, subject: str,
                body: str) -> AutoReplyRule | None:
    rules = db.scalars(
        select(AutoReplyRule)
        .where(AutoReplyRule.user_id == user_id, AutoReplyRule.enabled.is_(True))
        .order_by(AutoReplyRule.priority.desc(), AutoReplyRule.created_at)
    ).all()
    hay = f"{subject} {body}".lower()
    win = _minutes_now()
    for rule in rules:
        if rule.sender_pattern and rule.sender_pattern.lower() not in (sender or "").lower():
            continue
        if rule.topic_pattern and rule.topic_pattern.lower() not in hay:
            continue
        lo, hi = rule.time_window_start, rule.time_window_end
        in_window = hi > lo and lo <= win < hi
        # Sanity fallback: a degenerate window (start==end) means "always".
        if not in_window and hi > lo:
            continue
        return rule
    return None


def build_reply(rule: AutoReplyRule, sender: str, subject: str) -> str:
    template = (rule.template or "").strip() or _DEFAULT_TEMPLATES.get(
        rule.tone or "professional", _DEFAULT_TEMPLATES["professional"]
    )
    return (template
            .replace("{name}", _name_from(sender))
            .replace("{sender}", _name_from(sender))
            .replace("{subject}", subject.strip() or "your message"))


def _within_daily(db: Session, rule: AutoReplyRule, now: datetime) -> bool:
    if rule.max_per_day <= 0:
        return True
    if rule.last_day != _day_key(now):
        rule.last_day = _day_key(now)
        rule.daily_count = 0
        db.add(rule)
        return True
    return rule.daily_count < rule.max_per_day


def _within_hourly(db: Session, rule: AutoReplyRule, now: datetime) -> bool:
    if rule.max_per_hour <= 0:
        return True
    bucket = _bucket_key(now)
    if rule.last_hour_bucket is None or rule.last_hour_bucket != bucket:
        rule.last_hour_bucket = bucket
        rule.hourly_count = 0
        db.add(rule)
        return True
    return rule.hourly_count < rule.max_per_hour


def _record_send(db: Session, rule: AutoReplyRule, now: datetime) -> None:
    if rule.last_hour_bucket != _bucket_key(now):
        rule.last_hour_bucket = _bucket_key(now)
        rule.hourly_count = 0
    if rule.last_day != _day_key(now):
        rule.last_day = _day_key(now)
        rule.daily_count = 0
    rule.hourly_count += 1
    rule.daily_count += 1
    db.add(rule)


async def run_auto_reply(*, db: Session, user_id: str, sender: str, subject: str,
                         body: str, message_id: str = "") -> dict:
    """Evaluate rules and submit any reply through the canonical executor."""
    now = email_service.utcnow_naive()
    rule = select_rule(db, user_id=user_id, sender=sender, subject=subject, body=body)
    if rule is None:
        return {"decision": "no_rule"}

    user = db.get(User, user_id)
    prefs = user.prefs if user else {}
    decision = decide_for_user(
        permission=PermissionLevel.CONFIRM,
        preferences=prefs,
        tool_name="send_email",
        tags=["email"],
    )
    if not decision.allowed:
        return {"decision": "denied", "rule_id": rule.id, "rule_name": rule.name,
                "code": decision.code, "detail": decision.detail}

    if not (_within_daily(db, rule, now) and _within_hourly(db, rule, now)):
        return {"decision": "rate_limited", "rule_id": rule.id,
                "rule_name": rule.name,
                "meta": {"max_per_hour": rule.max_per_hour,
                         "max_per_day": rule.max_per_day}}

    reply = build_reply(rule, sender, subject)
    recipient = _sender_address(sender)
    if not recipient:
        return {
            "decision": "send_error",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "code": "EMAIL_SENDER_UNAVAILABLE",
            "detail": "The inbound message has no usable sender address.",
        }

    rule_id = rule.id
    rule_name = rule.name
    db.commit()
    from app.services.direct_action import execute_direct_action
    from app.services.model_router import get_model_router

    try:
        outcome = await execute_direct_action(
            user_id=user_id,
            model_router=get_model_router(),
            tool_name="send_email",
            arguments={
                "to": recipient,
                "subject": f"Re: {subject}",
                "body": reply,
            },
            request_message=f"Send the automatic email reply to {recipient}.",
            description=f"Send an automatic reply to {recipient}.",
            success_message=f"The email provider accepted the reply to {recipient}.",
            require_approval=rule.requires_approval or decision.require_approval,
        )
    except JaloraError as exc:
        logger.warning("Canonical auto-reply submission failed: %s", exc)
        return {
            "decision": "send_error",
            "rule_id": rule_id,
            "rule_name": rule_name,
            "code": exc.code,
            "detail": exc.message,
        }

    approval = outcome.approval if isinstance(outcome.approval, dict) else None
    if approval is not None:
        approval_id = str(approval.get("id") or "")
        db.add(ActivityLog(
            user_id=user_id,
            agent="email_inbound",
            tool="auto_reply",
            task_id=outcome.task_id,
            run_id=outcome.run_id,
            result_summary=f"Auto-reply to {recipient} is awaiting approval.",
            arguments={"rule": rule_name, "to": recipient},
            meta={
                "decision": "requires_approval",
                "approval_id": approval_id,
                "task_id": outcome.task_id,
                "run_id": outcome.run_id,
                "auto_reply_rule_id": rule_id,
                "inbound_message_id": message_id,
            },
        ))
        db.commit()
        return {
            "decision": "requires_approval",
            "rule_id": rule_id,
            "rule_name": rule_name,
            "approval_id": approval_id,
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
        }

    if outcome.status != "completed":
        error = outcome.error if isinstance(outcome.error, dict) else {}
        return {
            "decision": (
                "outcome_unknown"
                if error.get("code") == "TOOL_OUTCOME_UNKNOWN"
                else "send_error"
            ),
            "rule_id": rule_id,
            "rule_name": rule_name,
            "code": error.get("code", outcome.status.upper()),
            "detail": error.get("details", outcome.reply),
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
        }

    step = db.scalar(
        select(TaskStep)
        .where(
            TaskStep.task_id == outcome.task_id,
            TaskStep.tool_id == "send_email",
        )
        .order_by(TaskStep.idx.desc())
    )
    result_data = step.result_data if step and isinstance(step.result_data, dict) else {}
    execution_result = result_data.get("execution_result", {})
    receipt = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    if not isinstance(receipt, dict) or not receipt.get("message_id"):
        return {
            "decision": "send_error",
            "rule_id": rule_id,
            "rule_name": rule_name,
            "code": "EMAIL_SEND_RECEIPT_MISSING",
            "detail": "The completed email action has no provider receipt.",
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
        }

    db.expire_all()
    rule = db.get(AutoReplyRule, rule_id)
    if rule is not None:
        _record_send(db, rule, now)
    activity = ActivityLog(
        user_id=user_id,
        run_id=outcome.run_id,
        task_id=outcome.task_id,
        agent="email_inbound",
        tool="auto_reply",
        result_summary=f"Auto-replied to {recipient} using rule '{rule_name}'.",
        arguments={"rule": rule_name, "to": recipient},
        meta={
            "decision": "sent",
            "message_id": receipt["message_id"],
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
        },
    )
    db.add(activity)
    db.commit()
    return {
        "decision": "sent",
        "rule_id": rule_id,
        "rule_name": rule_name,
        "to": recipient,
        "reply": reply,
        "message_id": receipt["message_id"],
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
        "action_outcome": "provider_accepted",
    }


def record_approved_auto_reply(
    db: Session,
    *,
    task_id: str,
    outcome: str,
    receipt: dict | None,
) -> None:
    """Reconcile the auto-reply audit and frequency cap after shared approval."""
    activity = db.scalar(
        select(ActivityLog)
        .where(
            ActivityLog.task_id == task_id,
            ActivityLog.tool == "auto_reply",
        )
        .order_by(ActivityLog.created_at.desc())
    )
    if activity is None or not isinstance(activity.meta, dict):
        return
    meta = dict(activity.meta)
    if meta.get("decision") != "requires_approval":
        return

    if outcome == "completed" and isinstance(receipt, dict) and receipt.get("message_id"):
        rule = db.get(AutoReplyRule, str(meta.get("auto_reply_rule_id") or ""))
        if rule is not None:
            _record_send(db, rule, email_service.utcnow_naive())
        meta["decision"] = "sent"
        meta["message_id"] = receipt["message_id"]
        activity.result_summary = (
            f"Approved auto-reply sent to "
            f"{(activity.arguments or {}).get('to', 'recipient')}."
        )
    else:
        meta["decision"] = (
            "outcome_unknown" if outcome == "outcome_unknown" else outcome
        )
        activity.result_summary = f"Approved auto-reply ended with outcome '{outcome}'."
    activity.meta = meta
    db.add(activity)


def _sender_address(addr: str) -> str:
    if "<" in addr and ">" in addr:
        return addr.split("<", 1)[1].split(">", 1)[0].strip()
    return addr.strip().split(" ")[-1]