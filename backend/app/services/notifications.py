"""Notification fan-out (spec §50, §61-62).

The durable Notification row is the source of truth for the frontend bell.
`emit(...)` is additionally called so live SSE streams can surface new items,
but the scheduler-driven paths run without an active request emitter and rely
on the DB row alone.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.events import emit
from app.models.notification import Notification

# Simple in-memory cache for unread counts
_unread_cache: dict[str, tuple[int, float]] = {}  # {user_id: (count, timestamp)}
_cache_lock = Lock()
_cache_ttl_seconds = 2.0  # Cache for 2 seconds to reduce database load


def _get_cached_unread_count(user_id: str) -> int | None:
    """Get cached unread count if still valid."""
    import time
    with _cache_lock:
        entry = _unread_cache.get(user_id)
        if entry is not None:
            count, timestamp = entry
            if time.time() - timestamp < _cache_ttl_seconds:
                return count
            # Cache expired
            del _unread_cache[user_id]
    return None


def _set_cached_unread_count(user_id: str, count: int) -> None:
    """Cache the unread count result."""
    import time
    with _cache_lock:
        _unread_cache[user_id] = (count, time.time())


def notify(
    db: Session,
    *,
    user_id: str,
    text: str,
    kind: str = "system",
    link: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    schedule_id: str | None = None,
    occurrence_id: str | None = None,
) -> Notification:
    row = Notification(
        user_id=user_id,
        text=text[:2000],
        kind=kind,
        link=link,
        run_id=run_id,
        task_id=task_id,
        schedule_id=schedule_id,
        occurrence_id=occurrence_id,
    )
    db.add(row)
    db.flush()
    # Invalidate cache when a new notification is created
    with _cache_lock:
        _unread_cache.pop(user_id, None)
    emit("notification", {
        "id": row.id,
        "text": row.text,
        "kind": row.kind,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })
    return row


def update_notification(
    db: Session,
    row: Notification,
    *,
    text: str,
    kind: str,
    link: str | None,
    run_id: str | None,
    task_id: str | None,
    schedule_id: str | None,
) -> Notification:
    row.text = text[:2000]
    row.kind = kind
    row.link = link
    row.run_id = run_id
    row.task_id = task_id
    row.schedule_id = schedule_id
    row.read_at = None
    db.flush()
    with _cache_lock:
        _unread_cache.pop(row.user_id, None)
    emit("notification", {
        "id": row.id,
        "text": row.text,
        "kind": row.kind,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })
    return row


def unread_count(db: Session, user_id: str) -> int:
    """Get unread notification count with in-memory caching."""
    # Check cache first
    cached = _get_cached_unread_count(user_id)
    if cached is not None:
        return cached
    
    count = int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )
    
    # Cache the result
    _set_cached_unread_count(user_id, count)
    return count


def list_notifications(
    db: Session, user_id: str, *, limit: int = 50, unread_only: bool = False
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list(db.scalars(stmt).all())


def mark_read(db: Session, user_id: str, notification_id: str) -> bool:
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        return False
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        # Invalidate cache when notification is marked read
        with _cache_lock:
            _unread_cache.pop(user_id, None)
    return True


def mark_all_read(db: Session, user_id: str) -> int:
    now = datetime.now(timezone.utc)
    rows = list_notifications(db, user_id, limit=10000, unread_only=True)
    for row in rows:
        row.read_at = now
    # Invalidate cache after marking all as read
    with _cache_lock:
        _unread_cache.pop(user_id, None)
    return len(rows)