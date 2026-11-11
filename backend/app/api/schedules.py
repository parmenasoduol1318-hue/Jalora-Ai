"""Schedules REST API (spec §49-51, §61).

The frontend Schedules view lists rows and manages enable/disable and
deletion; agent-created schedules go through the schedule tools. Creating via
the API is also supported for direct form entry (text is parsed server-side).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.schedule import Schedule, ScheduleOccurrence
from app.models.user import User
from app.services import scheduler as scheduler_service

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


class ScheduleCreate(BaseModel):
    task_template: str = Field(..., min_length=1, max_length=2000)
    when: str = Field(..., min_length=1, max_length=200, description="Natural-language trigger: 'every Monday at 08:00', 'daily at 9am', ISO once, ...")
    notify_only: bool = False
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class ScheduleToggle(BaseModel):
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    task_template: str | None = Field(default=None, min_length=1, max_length=2000)
    when: str | None = Field(default=None, min_length=1, max_length=200)
    notify_only: bool | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=100)


def _schedule_expression(row: Schedule) -> str:
    if row.schedule_expression:
        return row.schedule_expression
    spec = row.spec or {}
    if row.kind == "once":
        return str(spec.get("run_date") or "")
    if row.kind == "interval":
        unit = next(
            (key for key in ("weeks", "days", "hours", "minutes") if spec.get(key)),
            None,
        )
        return f"every {spec[unit]} {unit}" if unit else ""
    if "crontab" in spec:
        return str(spec["crontab"])
    return " ".join(
        str(spec.get(key, default))
        for key, default in (
            ("minute", "0"),
            ("hour", "0"),
            ("day", "*"),
            ("month", "*"),
            ("day_of_week", "*"),
        )
    )


def _view(
    row: Schedule,
    latest_occurrence: ScheduleOccurrence | None = None,
) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "spec": row.spec,
        "schedule_expression": _schedule_expression(row),
        "timezone": str((row.spec or {}).get("timezone") or "UTC"),
        "task_template": row.task_template,
        "notify_only": row.notify_only,
        "enabled": row.enabled,
        "next_run": _utc_iso(row.next_run),
        "last_run": _utc_iso(row.last_run),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "latest_occurrence": (
            {
                "id": latest_occurrence.id,
                "status": latest_occurrence.status,
                "scheduled_for": _utc_iso(latest_occurrence.scheduled_for),
                "result_summary": latest_occurrence.result_summary,
                "task_id": latest_occurrence.task_id,
                "run_id": latest_occurrence.run_id,
            }
            if latest_occurrence
            else None
        ),
    }


@router.get("")
def list_schedules_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list(
        db.scalars(
            select(Schedule)
            .where(Schedule.user_id == user.id)
            .order_by(Schedule.created_at.desc())
        ).all()
    )
    schedule_ids = [row.id for row in rows]
    latest_times = (
        select(
            ScheduleOccurrence.schedule_id.label("schedule_id"),
            func.max(ScheduleOccurrence.scheduled_for).label("scheduled_for"),
        )
        .where(ScheduleOccurrence.schedule_id.in_(schedule_ids))
        .group_by(ScheduleOccurrence.schedule_id)
        .subquery()
        if schedule_ids
        else None
    )
    occurrences = (
        list(
            db.scalars(
                select(ScheduleOccurrence)
                .join(
                    latest_times,
                    (ScheduleOccurrence.schedule_id == latest_times.c.schedule_id)
                    & (ScheduleOccurrence.scheduled_for == latest_times.c.scheduled_for),
                )
            ).all()
        )
        if latest_times is not None
        else []
    )
    latest: dict[str, ScheduleOccurrence] = {}
    for occurrence in occurrences:
        latest.setdefault(occurrence.schedule_id, occurrence)
    return [_view(row, latest.get(row.id)) for row in rows]


@router.post("", status_code=201)
def create_schedule_endpoint(
    body: ScheduleCreate,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        row = scheduler_service.create_schedule(
            user_id=user.id,
            task_template=body.task_template,
            text=body.when,
            notify_only=body.notify_only,
            timezone_name=body.timezone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _view(row)


@router.put("/{schedule_id}")
def update_schedule_endpoint(
    schedule_id: str,
    body: ScheduleUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        row = scheduler_service.update_schedule(
            user.id,
            schedule_id,
            task_template=body.task_template,
            text=body.when,
            notify_only=body.notify_only,
            timezone_name=body.timezone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return _view(row)


@router.post("/{schedule_id}/toggle")
def toggle_schedule_endpoint(
    schedule_id: str,
    body: ScheduleToggle,
    user: User = Depends(get_current_user),
) -> dict:
    if not scheduler_service.toggle_schedule(user.id, schedule_id, body.enabled):
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"id": schedule_id, "enabled": body.enabled}


@router.delete("/{schedule_id}")
def delete_schedule_endpoint(
    schedule_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    if not scheduler_service.delete_schedule(user.id, schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"deleted": schedule_id}