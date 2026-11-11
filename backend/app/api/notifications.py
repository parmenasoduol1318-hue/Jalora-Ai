"""Notifications REST API (spec §50, §61-62).

The frontend bell polls unread-count and lists recent notifications; marking
read/unread is per-row. Notifications are user-scoped and bearer-authenticated.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.notification import Notification
from app.models.user import User
from app.services import notifications as notifications_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _view(row: Notification) -> dict:
    return {
        "id": row.id,
        "text": row.text,
        "kind": row.kind,
        "link": row.link,
        "run_id": row.run_id or None,
        "task_id": row.task_id or None,
        "schedule_id": row.schedule_id or None,
        "occurrence_id": row.occurrence_id or None,
        "read": row.read_at is not None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _make_etag(data: Any) -> str:
    """Generate ETag for response caching."""
    import json
    json_bytes = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.md5(json_bytes).hexdigest()


@router.get("")
def list_notifications_endpoint(
    limit: int = 50,
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = notifications_service.list_notifications(
        db, user.id, limit=min(limit, 200), unread_only=unread_only,
    )
    result = [_view(r) for r in rows]
    return result


@router.get("/unread-count")
def unread_count_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    count = notifications_service.unread_count(db, user.id)
    result = {"count": count}
    return result


@router.post("/{notification_id}/read")
def mark_read_endpoint(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not notifications_service.mark_read(db, user.id, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found.")
    db.commit()
    return {"id": notification_id, "read": True}


@router.post("/read-all")
def mark_all_read_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    count = notifications_service.mark_all_read(db, user.id)
    db.commit()
    return {"marked": count}