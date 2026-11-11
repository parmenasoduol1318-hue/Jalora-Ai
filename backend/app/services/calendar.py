"""Calendar service (§63).

Mock-first: `auto` resolves to Google Calendar only when a connected `calendar`
integration carries tokens, otherwise to the deterministic local calendar
(rows in `calendar_events`). Creating an event detects conflicts and proposes a
free alternative so the agent can present it before the CONFIRM-gated write.
"""
from __future__ import annotations

import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.calendar import CalendarEvent
from app.models.integration import Integration
from app.models.user import new_id
from app.services import oauth as oauth_service

logger = logging.getLogger("jalora.calendar")

DEFAULT_DURATION_MIN = 30


def _run_async(coro):
    """Run an async provider call from both sync endpoints and async tools."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[object] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # propagate provider errors to caller
            errors.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0] if result else None


class CalendarError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise CalendarError("INVALID_DATETIME",
                                f"Could not parse datetime '{value}' (use ISO 8601).")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def validate_timezone(value: str | None) -> str:
    name = (value or "Africa/Nairobi").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarError("INVALID_TIMEZONE", "Use a valid IANA timezone.")
    return name


def _normalize_attendees(attendees) -> list[dict]:
    out: list[dict] = []
    for item in attendees or []:
        if isinstance(item, dict):
            out.append({
                "name": str(item.get("name", "")),
                "email": str(item.get("email", "")),
                "phone": str(item.get("phone", "")),
            })
        else:
            out.append({"name": str(item), "email": "", "phone": ""})
    return out


def event_as_dict(e: CalendarEvent) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "start": e.start_at.isoformat() if e.start_at else None,
        "end": e.end_at.isoformat() if e.end_at else None,
        "timezone": e.timezone or "Africa/Nairobi",
        "location": e.location,
        "description": e.description,
        "attendees": list(e.attendees or []),
        "provider": e.provider,
        "status": e.status,
        "external_id": e.external_id,
    }


def resolve_provider(db: Session, *, user_id: str) -> str:
    choice = (get_settings().calendar_provider or "auto").strip().lower()
    if choice in ("mock", "google"):
        return choice
    if choice != "auto":
        raise CalendarError("PROVIDER_NOT_CONFIGURED",
                            f"Unknown calendar provider '{choice}' (auto|mock|google).")
    row = db.scalar(select(Integration).where(
        Integration.user_id == user_id, Integration.provider == "calendar",
        Integration.status == "connected"))
    if row is not None and (row.config or {}).get("access_token"):
        return "google"
    return "mock"


def _overlaps(db: Session, user_id: str, start: datetime, end: datetime,
              exclude_id: str | None = None) -> list[CalendarEvent]:
    rows = db.scalars(select(CalendarEvent).where(
        CalendarEvent.user_id == user_id,
        CalendarEvent.status != "cancelled",
    )).all()
    hits = []
    for e in rows:
        if exclude_id and e.id == exclude_id:
            continue
        if e.start_at is None or e.end_at is None:
            continue
        if e.start_at < end and e.end_at > start:
            hits.append(e)
    return hits


def _suggest_alternative(db: Session, user_id: str, start: datetime,
                         duration: timedelta) -> datetime | None:
    """First free slot at +1h steps within the next 8 hours."""
    for step in range(1, 9):
        cand = start + timedelta(hours=step)
        if not _overlaps(db, user_id, cand, cand + duration):
            return cand
    return None


def _row(db: Session, user_id: str, event_id: str) -> CalendarEvent:
    row = db.get(CalendarEvent, event_id)
    if row is None or row.user_id != user_id:
        raise CalendarError("EVENT_NOT_FOUND", "Calendar event not found.")
    return row


def _create_mock(db: Session, *, user_id: str, title: str, start: datetime,
                 end: datetime, location: str, description: str,
                 attendees: list[dict], timezone_name: str,
                 external_id: str = "") -> CalendarEvent:
    row = CalendarEvent(
        user_id=user_id, title=title, start_at=start, end_at=end,
        timezone=timezone_name,
        location=location, description=description, attendees=attendees,
        provider="mock", external_id=external_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_event(db: Session, *, user_id: str, title: str, start,
                 end=None, location: str = "", description: str = "",
                 attendees=None, timezone_name: str = "Africa/Nairobi",
                 force: bool = False) -> dict:
    title = (title or "").strip()
    if not title:
        raise CalendarError("INVALID_EVENT", "An event title is required.")
    start_dt = parse_dt(start)
    if start_dt is None:
        raise CalendarError("INVALID_EVENT", "An event start time is required.")
    end_dt = parse_dt(end) or (start_dt + timedelta(minutes=DEFAULT_DURATION_MIN))
    if end_dt <= start_dt:
        raise CalendarError("INVALID_EVENT", "Event end must be after its start.")
    timezone_name = validate_timezone(timezone_name)
    people = _normalize_attendees(attendees)
    provider = resolve_provider(db, user_id=user_id)

    conflicts = _overlaps(db, user_id, start_dt, end_dt)
    if conflicts and not force:
        alt = _suggest_alternative(db, user_id, start_dt, end_dt - start_dt)
        return {
            "status": "conflict",
            "message": "The requested time overlaps an existing event.",
            "conflicts": [event_as_dict(c) for c in conflicts],
            "suggested": {
                "start": alt.isoformat() if alt else None,
                "end": (alt + (end_dt - start_dt)).isoformat() if alt else None,
            },
            "requested": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
            "provider": provider,
        }

    if provider == "google":
        external_id = _google_create_event(user_id, title, start_dt, end_dt,
                                           location, description, people,
                                           timezone_name)
    else:
        external_id = ""
    row = _create_mock(db, user_id=user_id, title=title, start=start_dt,
                       end=end_dt, location=location, description=description,
                       attendees=people, timezone_name=timezone_name,
                       external_id=external_id)
    row.provider = provider
    db.commit()
    db.refresh(row)
    _schedule_event_reminders(user_id, row)
    return {"status": "created", "event": event_as_dict(row), "provider": provider}


def list_events(db: Session, *, user_id: str, start=None, end=None,
                limit: int = 50) -> list[CalendarEvent]:
    stmt = select(CalendarEvent).where(CalendarEvent.user_id == user_id,
                                       CalendarEvent.status != "cancelled")
    start_dt, end_dt = parse_dt(start), parse_dt(end)
    if start_dt is not None:
        stmt = stmt.where(CalendarEvent.end_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(CalendarEvent.start_at <= end_dt)
    stmt = stmt.order_by(CalendarEvent.start_at).limit(max(1, min(int(limit or 50), 200)))
    return list(db.scalars(stmt).all())


def update_event(db: Session, *, user_id: str, event_id: str, **fields) -> dict:
    row = _row(db, user_id, event_id)
    if "title" in fields and fields["title"]:
        row.title = str(fields["title"]).strip()
    if "location" in fields and fields["location"] is not None:
        row.location = str(fields["location"])
    if "description" in fields and fields["description"] is not None:
        row.description = str(fields["description"])
    if "attendees" in fields and fields["attendees"] is not None:
        row.attendees = _normalize_attendees(fields["attendees"])
    if fields.get("timezone"):
        row.timezone = validate_timezone(fields["timezone"])
    new_start = parse_dt(fields.get("start")) if fields.get("start") else row.start_at
    new_end = parse_dt(fields.get("end")) if fields.get("end") else row.end_at
    if new_start and new_end and new_end <= new_start:
        raise CalendarError("INVALID_EVENT", "Event end must be after its start.")
    if new_start and new_end:
        conflicts = _overlaps(db, user_id, new_start, new_end, exclude_id=row.id)
        if conflicts and not fields.get("force"):
            return {"status": "conflict",
                    "conflicts": [event_as_dict(c) for c in conflicts]}
    if new_start:
        row.start_at = new_start
    if new_end:
        row.end_at = new_end
    db.commit()
    db.refresh(row)
    _reschedule_event_reminders(user_id, row)
    return {"status": "updated", "event": event_as_dict(row)}


def delete_event(db: Session, *, user_id: str, event_id: str) -> dict:
    row = _row(db, user_id, event_id)
    db.delete(row)
    db.commit()
    _clear_event_reminders(user_id, event_id)
    return {"status": "deleted", "event_id": event_id}


# --- Event reminders (§63): notify the user before an event starts ---
def _schedule_event_reminders(user_id: str, event: CalendarEvent) -> list[str]:
    if event is None or event.start_at is None:
        return []
    from app.services import scheduler as scheduler_service

    title = event.title or "event"
    created: list[str] = []
    for delta, label in ((timedelta(minutes=-10), "starts in 10 minutes"),
                         (timedelta(0), "is starting now")):
        row = scheduler_service.create_once_reminder(
            user_id=user_id, run_at=event.start_at + delta,
            text=f"Reminder: {title} {label}.",
            meta={"event_id": event.id})
        if row is not None:
            created.append(row.id)
    return created


def _clear_event_reminders(user_id: str, event_id: str) -> int:
    from app.services import scheduler as scheduler_service

    try:
        return scheduler_service.delete_reminders_for_event(user_id, event_id)
    except Exception:  # noqa: BLE001 - reminder bookkeeping must not fail writes
        logger.exception("failed to clear reminders for event %s", event_id)
        return 0


def _reschedule_event_reminders(user_id: str, event: CalendarEvent) -> None:
    _clear_event_reminders(user_id, event.id)
    _schedule_event_reminders(user_id, event)


# --- Google Calendar adapter (optional; requires a connected integration) ---
def _calendar_integration(db: Session, user_id: str) -> Integration | None:
    return db.scalar(select(Integration).where(
        Integration.user_id == user_id, Integration.provider == "calendar",
        Integration.status == "connected"))


def _google_access_token(db: Session, user_id: str) -> str:
    import time

    from app.services.credential_store import fernet_decrypt, fernet_encrypt

    row = _calendar_integration(db, user_id)
    if row is None or not (row.config or {}).get("access_token"):
        raise CalendarError("PROVIDER_NOT_CONFIGURED",
                            "Google Calendar is not connected for this user.")
    cfg = row.config
    if (cfg.get("token_expires_at") or 0) > time.time() + 30:
        return fernet_decrypt(cfg["access_token"])
    refresh = fernet_decrypt(cfg.get("refresh_token", ""))
    if not refresh or refresh == "missing":
        return fernet_decrypt(cfg["access_token"])
    payload = _run_async(oauth_service.refresh_access("calendar", refresh))
    access = payload.get("access_token")
    if not access:
        return fernet_decrypt(cfg["access_token"])
    new_cfg = dict(cfg)
    new_cfg["access_token"] = fernet_encrypt(access)
    new_cfg["token_expires_at"] = time.time() + int(payload.get("expires_in", 3600))
    row.config = new_cfg
    db.commit()
    return access


def _google_event_to_dict(item: dict) -> dict:
    start = item.get("start", {}) or {}
    end = item.get("end", {}) or {}
    attendees = item.get("attendees") or []

    def _iso(value) -> str | None:
        dt = parse_dt(value) if value else None
        return dt.isoformat() if dt else None

    return {
        "id": f"g:{item.get('id', '')}",
        "title": item.get("summary") or "(untitled)",
        "start": _iso(start.get("dateTime") or start.get("date")),
        "end": _iso(end.get("dateTime") or end.get("date")),
        "location": item.get("location") or "",
        "description": item.get("description") or "",
        "attendees": [
            {"name": a.get("displayName", ""), "email": a.get("email", ""),
             "phone": ""}
            for a in attendees
        ],
        "provider": "google",
        "status": "cancelled" if item.get("status") == "cancelled" else "confirmed",
        "external_id": item.get("id", ""),
    }


def _google_list_events(user_id: str, time_min: datetime, time_max: datetime,
                        limit: int) -> list[dict]:
    import httpx

    db = SessionLocal()
    try:
        token = _google_access_token(db, user_id)
    finally:
        db.close()
    params = {
        "timeMin": time_min.isoformat() + "Z",
        "timeMax": time_max.isoformat() + "Z",
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max(1, min(int(limit or 50), 250))),
    }

    async def _call() -> list[dict]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                params=params,
                headers={"Authorization": f"Bearer {token}",
                         "User-Agent": "JaloraAI/0.1"},
            )
        if resp.status_code >= 400:
            raise CalendarError(
                "PROVIDER_ERROR",
                f"Google Calendar HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("items", [])

    items = _run_async(_call())
    return [_google_event_to_dict(it) for it in items]


def _google_create_event(user_id: str, title: str, start: datetime, end: datetime,
                         location: str, description: str, attendees: list[dict],
                         timezone_name: str) -> str:
    import httpx

    db = SessionLocal()
    try:
        token = _google_access_token(db, user_id)
    finally:
        db.close()
    event_zone = ZoneInfo(timezone_name)
    local_start = start.replace(tzinfo=timezone.utc).astimezone(event_zone)
    local_end = end.replace(tzinfo=timezone.utc).astimezone(event_zone)
    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": local_start.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": local_end.isoformat(), "timeZone": timezone_name},
        "attendees": [{"email": a["email"]} for a in attendees if a.get("email")],
    }

    async def _call() -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                json=body,
                headers={"Authorization": f"Bearer {token}",
                         "User-Agent": "JaloraAI/0.1"},
            )
        if resp.status_code >= 400:
            raise CalendarError("PROVIDER_ERROR",
                                f"Google Calendar HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("id", "")

    return _run_async(_call())


# --- public API (own session) ----------------------------------------------
def list_calendar(*, user_id: str, start=None, end=None, limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        provider = resolve_provider(db, user_id=user_id)
        local = [event_as_dict(e) for e in list_events(
            db, user_id=user_id, start=start, end=end, limit=limit)]
    finally:
        db.close()
    if provider != "google":
        return local
    start_dt = parse_dt(start) or datetime.now(timezone.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    end_dt = parse_dt(end) or (start_dt + timedelta(days=30))
    try:
        remote = _google_list_events(user_id, start_dt, end_dt, limit)
    except CalendarError:
        logger.exception("Google Calendar list failed; returning local events only")
        return local
    merged: dict[str, dict] = {}
    for e in remote:
        merged[e["id"]] = e
    for e in local:
        ext = e.get("external_id")
        key = f"g:{ext}" if e.get("provider") == "google" and ext else e["id"]
        merged.setdefault(key, e)
    events = sorted(merged.values(), key=lambda x: x.get("start") or "")
    return events[: max(1, min(int(limit or 50), 200))]


def add_calendar_event(*, user_id: str, **kwargs) -> dict:
    db = SessionLocal()
    try:
        return create_event(db, user_id=user_id, **kwargs)
    finally:
        db.close()


def edit_calendar_event(*, user_id: str, event_id: str, **fields) -> dict:
    db = SessionLocal()
    try:
        return update_event(db, user_id=user_id, event_id=event_id, **fields)
    finally:
        db.close()


def remove_calendar_event(*, user_id: str, event_id: str) -> dict:
    db = SessionLocal()
    try:
        return delete_event(db, user_id=user_id, event_id=event_id)
    finally:
        db.close()
