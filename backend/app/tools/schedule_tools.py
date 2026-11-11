"""Scheduling & reminders tools (spec §49, §51, §61-62).

The agent creates one-shot or recurring schedules on the user's behalf. The
`notify_only` flag separates lightweight reminders (fire a notification) from
full scheduled agent tasks (run the real pipeline). L4 ("scheduled") autonomy
is enforced downstream by the standard policy engine — these tools only persist
rows, they never bypass approvals on fire.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.agent import Approval
from app.models.schedule import Schedule
from app.models.task import Task
from app.services import scheduler as scheduler_service

__all__ = [
    "schedule_task", "create_reminder", "create_automation",
    "cancel_schedule", "list_schedules", "daily_briefing",
]


async def schedule_task(when: str, task: str) -> ToolResult:
    """Schedule an agent task to run automatically at a future time. `when` accepts natural language like 'every Monday at 08:00', 'daily at 9am', 'every 2 hours', '2026-10-08T09:00' or 'at 18:30'. `task` is the instruction to execute when the schedule fires. The scheduled run follows the user's tool policies: actions that normally require approval still pause for it."""
    user_id = get_tool_context().user_id
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id, task_template=task, text=when,
        )
    except ValueError as exc:
        return ToolResult.fail(
            "INVALID_SCHEDULE", str(exc),
            message=f"Could not parse the schedule: {exc}",
        )
    return _schedule_ok(row, "Scheduled task created.")


async def create_reminder(when: str, what: str) -> ToolResult:
    """Create a reminder that notifies the user at a future time. `when` accepts natural language like 'tomorrow at 9am', 'every Monday at 08:00', or an ISO timestamp like '2026-10-08T09:00'. `what` is the reminder text shown to the user. Reminders only raise a notification — no agent runs."""
    user_id = get_tool_context().user_id
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id, task_template=what, text=when, notify_only=True,
        )
    except ValueError as exc:
        return ToolResult.fail(
            "INVALID_SCHEDULE", str(exc),
            message=f"Could not parse the reminder time: {exc}",
        )
    return _schedule_ok(row, "Reminder created.")


async def create_automation(when: str, task: str, purpose: str = "") -> ToolResult:
    """Create a recurring automation that runs an agent task on a schedule (same as schedule_task, but intended for longer-running cadences like daily summaries). `when` accepts natural language; `task` is the instruction to run; `purpose` is an optional short label for the automation. Scheduled runs respect the user's tool policies — approval-gated actions still pause."""
    user_id = get_tool_context().user_id
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id,
            task_template=(purpose + " — " + task).strip(" —") if purpose else task,
            text=when,
        )
    except ValueError as exc:
        return ToolResult.fail(
            "INVALID_SCHEDULE", str(exc),
            message=f"Could not parse the automation schedule: {exc}",
        )
    return _schedule_ok(row, "Automation created.")


async def cancel_schedule(schedule_id: str) -> ToolResult:
    """Permanently cancel and delete a schedule/automation/reminder by its id. Use when the user asks to cancel, remove or turn off a scheduled task, reminder or automation."""
    user_id = get_tool_context().user_id
    removed = scheduler_service.delete_schedule(user_id, schedule_id)
    if not removed:
        return ToolResult.fail(
            "NOT_FOUND", f"No schedule with id {schedule_id}",
            message="That schedule does not exist.",
        )
    return ToolResult.ok("Schedule cancelled.", {"schedule_id": schedule_id})


async def list_schedules() -> ToolResult:
    """List the user's schedules (reminders, scheduled tasks and automations) with their next run time, most recent first. Use this whenever the user asks what is scheduled, when something will run, or to find a schedule id to cancel."""
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(Schedule)
                .where(Schedule.user_id == user_id)
                .order_by(Schedule.created_at.desc())
            ).all()
        )
        items = [_schedule_dict(row) for row in rows]
    return ToolResult.ok(
        f"{len(items)} schedule(s).",
        {"schedules": items},
    )


async def daily_briefing(now: datetime | None = None) -> ToolResult:
    """Compose the daily briefing: the user's pending reminders, today's tasks and pending approvals, plus memories recorded today. Called by morning/scheduled briefings — this tool only reads; the agent relays it to the user. The optional ``now`` parameter is for testing."""
    user_id = get_tool_context().user_id
    today = (now or datetime.now(timezone.utc)).date()
    with SessionLocal() as db:
        reminders = list(
            db.scalars(
                select(Schedule).where(
                    Schedule.user_id == user_id,
                    Schedule.notify_only.is_(True),
                    Schedule.enabled.is_(True),
                ).order_by(Schedule.created_at.desc())
            ).all()
        )
        tasks = list(
            db.scalars(
                select(Task).where(
                    Task.user_id == user_id,
                    Task.created_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
                    Task.status.notin_(["cancelled"]),
                ).order_by(Task.created_at.desc()).limit(20)
            ).all()
        )
        approvals = list(
            db.scalars(
                select(Approval).where(
                    Approval.user_id == user_id,
                    Approval.status == "pending",
                ).order_by(Approval.created_at.desc()).limit(20)
            ).all()
        )
        pending_reminders = [
            r for r in reminders
            if r.next_run is None or r.next_run.date() >= today
        ]

    lines: list[str] = []
    lines.append(f"# Daily briefing")
    if pending_reminders:
        lines.append("## Pending reminders")
        for r in pending_reminders:
            lines.append(f"- {r.task_template}")
    if tasks:
        lines.append("## Today's tasks")
        for tk in tasks:
            lines.append(f"- [{tk.status}] {tk.goal[:160]}")
    else:
        lines.append("## Today's tasks")
        lines.append("- no tasks today")
    if approvals:
        lines.append("## Pending approvals")
        for ap in approvals:
            lines.append(f"- {ap.tool_name}{' (waiting decision)' if ap.kind == 'tool' else ''}")
    else:
        lines.append("## Pending approvals")
        lines.append("- none")
    if not pending_reminders and not tasks and not approvals:
        lines.append("Everything is under control — no open reminders, tasks or approvals.")
    return ToolResult.ok("Daily briefing ready.", {"briefing": "\n".join(lines)})


def _schedule_ok(row: Schedule, message: str) -> ToolResult:
    return ToolResult.ok(
        message,
        {
            "schedule_id": row.id,
            "kind": row.kind,
            "next_run": row.next_run.isoformat() if row.next_run else None,
            "description": row.spec.get("description") or "",
        },
    )


def _schedule_dict(row: Schedule) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "spec": row.spec,
        "task_template": row.task_template,
        "notify_only": row.notify_only,
        "enabled": row.enabled,
        "next_run": row.next_run.isoformat() if row.next_run else None,
        "last_run": row.last_run.isoformat() if row.last_run else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --- registration -----------------------------------------------------------
from app.tools.base import PermissionLevel, registry  # noqa: E402

registry.register(schedule_task, permission=PermissionLevel.CONFIRM,
                  category="scheduling", tags=["scheduling", "schedule", "remind"])
registry.register(create_reminder, permission=PermissionLevel.AUTO,
                  category="scheduling",
                  tags=["scheduling", "remind", "reminder", "notify", "alert"])
registry.register(create_automation, permission=PermissionLevel.CONFIRM,
                  category="scheduling", tags=["scheduling"])
registry.register(cancel_schedule, permission=PermissionLevel.NOTIFY,
                  category="scheduling", tags=["scheduling", "destructive"])
registry.register(list_schedules, permission=PermissionLevel.AUTO,
                  category="scheduling", tags=["scheduling"])
registry.register(daily_briefing, permission=PermissionLevel.AUTO,
                  category="scheduling", tags=["scheduling"])