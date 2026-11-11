"""Scheduling, reminders and durable job execution (spec §49-51, §61-62, §73).

`parse_schedule` turns natural-language trigger strings into a structured plan
(kind + apscheduler-compatible spec). The AsyncIOScheduler (SQLAlchemy job
store, UTC) mirrors enabled Schedule rows into jobs; when a job fires it either
raises a notification (`notify_only` reminder) or runs the real agent pipeline
via `execute_chat`, which creates a Conversation / AgentRun / Task and handles
approvals like any interactive turn. L4 ("scheduled") autonomy is real because
scheduled runs honour the exact same per-tool policy engine as live chats — no
blanket auto-approval except where the user's tool policies already allow it.

Tests drive `run_schedule` directly (no wall-clock wait) and may inject a
scripted router via `set_router_override` so scheduled runs never hit a real
provider.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database.session import SessionLocal
from app.events import ListEmitter
from app.models.agent import AgentRun
from app.models.notification import Notification
from app.models.schedule import Schedule, ScheduleOccurrence, SchedulerLease
from app.models.task import Task
from app.models.user import User, utcnow
from app.services import chat_service
from app.services.audit import log_activity
from app.services.notifications import notify, update_notification

logger = logging.getLogger("jalora.scheduler")

MORNING = 9
AFTERNOON = 13
EVENING = 18
NIGHT = 21

WEEKDAY_NAMES = {
    "mon": 0, "monday": 0, "tues": 1, "tue": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
WEEKDAY_DISPLAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "thirty": 30, "sixty": 60,
}
_UNIT_TO_KWARG = {
    "min": "minutes", "minute": "minutes", "minutes": "minutes", "mins": "minutes",
    "hr": "hours", "hour": "hours", "hours": "hours", "hrs": "hours",
    "day": "days", "days": "days", "week": "weeks", "weeks": "weeks",
}
_CRONTAB_TOKEN_RE = re.compile(r"^[\d*/\-,L#?]+$")

_ISO_ONCE_RE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"\s*(z|[+-]\d{2}:?\d{2})?\b",
    re.IGNORECASE,
)
_TIME_SUFFIX_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\b")
_INTERVAL_RE = re.compile(
    r"\bevery\s+(?:(\d+|[a-z]+))\s+(minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks)\b"
)


@dataclass(frozen=True)
class SpecPlan:
    """Normalised trigger: kind + spec dict compatible with apscheduler."""
    kind: str            # once | cron | interval
    spec: dict[str, Any]  # e.g. {"hour": 8, "minute": 0}, {"minutes": 15}, {"run_date": ...}
    description: str

    @property
    def raw(self) -> dict[str, Any]:
        return {"kind": self.kind, "spec": self.spec, "description": self.description}


def _parse_clock(hour: str, minute: str | None, meridian: str | None) -> tuple[int, int]:
    h = int(hour)
    m = int(minute) if minute else 0
    if m not in range(60):
        raise ValueError(f"invalid minute value: {m}")
    if meridian:
        mark = meridian.lstrip(".").lower()
        if mark == "pm":
            if h == 12:
                h = 12
            elif h <= 11:
                h += 12
            else:
                raise ValueError(f"invalid PM hour: {h}")
        else:  # am
            if h == 12:
                h = 0
            elif h > 12:
                raise ValueError(f"invalid AM hour: {h}")
    elif h > 23:
        raise ValueError(f"invalid hour: {h}")
    return h, m


def _split_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def _find_time(text: str) -> tuple[int, int] | None:
    m = _TIME_SUFFIX_RE.search(text.lower())
    if not m:
        return None
    return _parse_clock(m.group(1), m.group(2), m.group(3))


def _day_names_in(text: str) -> list[int]:
    found: list[int] = []
    for word, num in WEEKDAY_NAMES.items():
        if word in text.lower():
            if num not in found:
                found.append(num)
    return sorted(found)


def _describe_cron(
    days: str, hour: int, minute: int, *, original: str
) -> str:
    if days == "*":
        return f"every day at {hour:02d}:{minute:02d}"
    if "-" in days and len(days) == 7:  # "mon-fri"
        return f"weekdays at {hour:02d}:{minute:02d}"
    names = []
    for d in days.split(","):
        if d.isdigit():
            names.append(WEEKDAY_DISPLAY[int(d)])
        else:
            names.append(WEEKDAY_DISPLAY[WEEKDAY_NAMES[d.lower()]])
    return f"every {', '.join(names)} at {hour:02d}:{minute:02d}"


def _next_fire(plan: SpecPlan, now: datetime | None = None) -> datetime | None:
    trigger = _build_trigger(plan)
    if trigger is None:
        return None
    zone = _timezone(str(plan.spec.get("timezone") or "UTC"))
    now = now or utcnow()
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    now = now.astimezone(zone)
    fired = trigger.get_next_fire_time(None, now)
    if fired is None:
        return None
    if fired.tzinfo is None:
        fired = fired.replace(tzinfo=timezone.utc)
    return fired.astimezone(timezone.utc)


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc


def _local_wall_time(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    zone: ZoneInfo,
) -> datetime:
    local = datetime(year, month, day, hour, minute, second, tzinfo=zone)
    roundtrip = local.astimezone(timezone.utc).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError(
            f"The requested local time does not exist in {zone.key} because of a daylight-saving transition."
        )
    return local


def _coerce_dt(value: str | datetime, zone: ZoneInfo | None = None) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone or timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_trigger(plan: SpecPlan) -> Any | None:
    zone = _timezone(str(plan.spec.get("timezone") or "UTC"))
    if plan.kind == "once":
        run_date = plan.spec.get("run_date")
        return DateTrigger(run_date=run_date, timezone=zone) if run_date else None
    if plan.kind == "interval":
        kwargs = dict(plan.spec)
        kwargs.pop("timezone", None)
        if not any(k in kwargs and kwargs[k] for k in ("minutes", "hours", "days", "weeks")):
            return None
        # Apscheduler defaults start_date to trigger-construction time (the real
        # clock); anchor it instead so next-fire is deterministic in `now`. A
        # user-supplied time hint anchors the repeating grid to that wall-clock.
        if kwargs.get("start_date"):
            start = _coerce_dt(kwargs.pop("start_date"), zone)
        else:
            start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return IntervalTrigger(**kwargs, start_date=start, timezone=zone)
    if plan.kind == "cron":
        if "crontab" in plan.spec:
            return CronTrigger.from_crontab(plan.spec["crontab"], timezone=zone)
        kwargs = dict(plan.spec)
        kwargs.pop("timezone", None)
        kwargs.setdefault("hour", MORNING)
        kwargs.setdefault("minute", 0)
        return CronTrigger(**kwargs, timezone=zone)
    return None


def parse_schedule(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> SpecPlan:
    """Parse natural-language trigger text into a SpecPlan.

    Raises ValueError with a clear message when the text cannot be parsed.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No schedule text provided.")
    lowered = " ".join(raw.lower().split())
    zone = _timezone(timezone_name)
    now = now or utcnow()
    utc_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local_now = utc_now.astimezone(zone)

    # 1. ISO one-shot: "2026-10-08 09:00"
    iso = _ISO_ONCE_RE.search(lowered)
    if iso:
        y, mo, d = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        h = int(iso.group(4))
        mi = int(iso.group(5))
        se = int(iso.group(6) or 0)
        offset_text = iso.group(7)
        if offset_text:
            offset_text = offset_text.upper()
            normalized_offset = (
                "+00:00" if offset_text == "Z"
                else (
                    offset_text if ":" in offset_text
                    else f"{offset_text[:3]}:{offset_text[3:]}"
                )
            )
            offset = datetime.fromisoformat(
                f"2000-01-01T00:00:00{normalized_offset}"
            ).tzinfo or timezone.utc
            run_date = datetime(y, mo, d, h, mi, se, tzinfo=offset)
        else:
            run_date = _local_wall_time(y, mo, d, h, mi, se, zone)
        if run_date <= utc_now:
            raise ValueError("The scheduled time is in the past.")
        return SpecPlan(
            kind="once",
            spec={"run_date": run_date.isoformat(), "timezone": timezone_name},
            description=f"once at {run_date.strftime('%Y-%m-%d %H:%M')}",
        )

    # 2. Interval: "every 15 minutes", "every 2 hours", "every day at 8am"
    interval = _INTERVAL_RE.search(lowered)
    if interval:
        count_str = interval.group(1)
        count = _NUM_WORDS.get(count_str, int(count_str) if count_str.isdigit() else 1)
        if count <= 0:
            raise ValueError("Interval must be positive.")
        unit = _UNIT_TO_KWARG[interval.group(2).rstrip("s")]
        # A time hint anchors the phase (first fire at that wall-clock time).
        time_of_day = _find_time(lowered)
        if unit == "days" and count == 1 and time_of_day:
            hour, minute = time_of_day
            spec = {
                "day_of_week": "*", "hour": hour, "minute": minute,
                "timezone": timezone_name,
            }
            return SpecPlan(
                kind="cron", spec=spec,
                description=f"every day at {hour:02d}:{minute:02d}",
            )
        spec: dict[str, Any] = {unit: count, "timezone": timezone_name}
        if time_of_day:
            hour, minute = time_of_day
            phase = local_now.replace(
                hour=int(hour), minute=int(minute), second=0, microsecond=0
            )
            if phase <= local_now:
                phase += timedelta(days=1)
            spec["start_date"] = phase.isoformat()
        unit_name = unit.rstrip("s")
        desc_unit = unit_name if count == 1 else unit
        return SpecPlan(
            kind="interval",
            spec=spec,
            description=f"every {count} {desc_unit}",
        )

    # 3. Cron-style: weekdays / daily / weekdays / weekends / periods of day
    time_of_day = _find_time(lowered)
    # weekly / monthly (no count): give them honest cadences
    if re.search(r"\b(every|each)\s+week\b|\bweekly\b", lowered):
        return SpecPlan(
            kind="interval",
            spec={"weeks": 1, "timezone": timezone_name},
            description="every week",
        )
    if re.search(r"\b(every|each)\s+month\b|\bmonthly\b", lowered):
        hour, minute = time_of_day or (MORNING, 0)
        return SpecPlan(
            kind="cron",
            spec={
                "day": "1", "hour": int(hour), "minute": int(minute),
                "timezone": timezone_name,
            },
            description=f"every month on the 1st at {int(hour):02d}:{int(minute):02d}",
        )
    days_names = _day_names_in(lowered)
    is_daily = bool(re.search(r"\b(every|each)\s+day\b|\bdaily\b", lowered))
    is_weekdays = bool(re.search(r"\bweekdays\b|\bworkdays\b", lowered))
    is_weekends = bool(re.search(r"\bweekends?\b", lowered))
    period = None
    for phrase, default in (
        ("every morning", MORNING), ("morning", MORNING),
        ("noon", 12), ("afternoon", AFTERNOON),
        ("evening", EVENING), ("night", NIGHT),
    ):
        if phrase in lowered:
            period = default
            break
    if days_names or is_daily or is_weekdays or is_weekends or period is not None:
        if is_weekdays:
            days = "mon-fri"
        elif is_weekends:
            days = "sat,sun"
        elif is_daily or period is not None:
            days = "*"
        else:
            days = ",".join(str(d) for d in days_names)
        hour, minute = time_of_day or (period if period is not None else MORNING, 0)
        spec = {
            "day_of_week": days, "hour": hour, "minute": minute,
            "timezone": timezone_name,
        }
        return SpecPlan(
            kind="cron", spec=spec,
            description=_describe_cron(days, int(hour), int(minute), original=raw),
        )

    # 4. Raw crontab: "0 8 * * *"
    tokens = lowered.split()
    if len(tokens) == 5 and all(_CRONTAB_TOKEN_RE.match(t) for t in tokens):
        return SpecPlan(
            kind="cron",
            spec={"crontab": " ".join(tokens), "timezone": timezone_name},
            description=f"cron '{' '.join(tokens)}'",
        )

    # 5. One-shot time: "at 08:30" -> next occurrence
    if time_of_day is not None:
        hour, minute = time_of_day
        candidate = local_now.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
        if candidate <= local_now:
            candidate += timedelta(days=1)
        run_date = candidate
        return SpecPlan(
            kind="once",
            spec={"run_date": run_date.isoformat(), "timezone": timezone_name},
            description=f"once at {run_date.strftime('%H:%M')}",
        )

    raise ValueError(
        "Could not understand the schedule. Try formats such as "
        "'every Monday at 08:00', 'daily at 9am', 'every 2 hours', "
        "'weekdays at 18:30' or an ISO timestamp like '2026-10-08T09:00'."
    )


# ---------------------------------------------------------------------------
# scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler: AsyncIOScheduler | None = None
_router_override: Any | None = None
_scheduler_owner_id: str | None = None
_scheduler_standby_task: asyncio.Task | None = None
_SCHEDULER_LEASE_NAME = "primary"
_SCHEDULER_LEASE_TTL = timedelta(seconds=45)
_SCHEDULER_HEARTBEAT_SECONDS = 10
_MISFIRE_GRACE_SECONDS = 3600


def set_router_override(router: Any | None) -> None:
    """Tests inject a scripted router so scheduled runs stay offline."""
    global _router_override
    _router_override = router


def clear_router_override() -> None:
    global _router_override
    _router_override = None


def _default_router() -> Any:
    if _router_override is not None:
        return _router_override
    from app.services.model_router import ModelRouter
    return ModelRouter()


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def acquire_scheduler_lease(owner_id: str, *, now: datetime | None = None) -> bool:
    """Atomically elect one scheduler process across app workers."""
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    expires = now + _SCHEDULER_LEASE_TTL
    with SessionLocal() as db:
        if db.get(SchedulerLease, _SCHEDULER_LEASE_NAME) is None:
            db.add(SchedulerLease(
                name=_SCHEDULER_LEASE_NAME,
                owner_id=owner_id,
                lease_expires_at=expires,
            ))
            try:
                db.commit()
                return True
            except IntegrityError:
                db.rollback()
        result = db.execute(
            update(SchedulerLease)
            .where(
                SchedulerLease.name == _SCHEDULER_LEASE_NAME,
                or_(
                    SchedulerLease.owner_id == owner_id,
                    SchedulerLease.lease_expires_at <= now,
                ),
            )
            .values(owner_id=owner_id, lease_expires_at=expires, updated_at=now)
        )
        db.commit()
        return result.rowcount == 1


def _renew_scheduler_lease() -> bool:
    owner_id = _scheduler_owner_id
    if not owner_id:
        return False
    now = utcnow()
    with SessionLocal() as db:
        result = db.execute(
            update(SchedulerLease)
            .where(
                SchedulerLease.name == _SCHEDULER_LEASE_NAME,
                SchedulerLease.owner_id == owner_id,
            )
            .values(
                lease_expires_at=now + _SCHEDULER_LEASE_TTL,
                updated_at=now,
            )
        )
        db.commit()
        return result.rowcount == 1


def _release_scheduler_lease(owner_id: str) -> None:
    now = utcnow()
    with SessionLocal() as db:
        db.execute(
            update(SchedulerLease)
            .where(
                SchedulerLease.name == _SCHEDULER_LEASE_NAME,
                SchedulerLease.owner_id == owner_id,
            )
            .values(lease_expires_at=now, updated_at=now)
        )
        db.commit()


def _scheduler_lease_heartbeat() -> None:
    global _scheduler, _scheduler_owner_id
    if _renew_scheduler_lease():
        return
    logger.error("scheduler lease lost; pausing scheduled dispatch")
    if _scheduler is not None and _scheduler.running:
        _scheduler.pause()
        _scheduler.shutdown(wait=False)
    _scheduler = None
    _scheduler_owner_id = None
    _ensure_scheduler_standby()


async def _scheduler_standby_loop() -> None:
    global _scheduler_standby_task
    try:
        while _scheduler is None:
            await asyncio.sleep(_SCHEDULER_HEARTBEAT_SECONDS)
            if _scheduler is not None:
                break
            try:
                start_scheduler()
            except Exception:
                logger.exception("standby scheduler could not acquire and start")
    finally:
        if _scheduler_standby_task is asyncio.current_task():
            _scheduler_standby_task = None


def _ensure_scheduler_standby() -> None:
    global _scheduler_standby_task
    if _scheduler_standby_task is not None and not _scheduler_standby_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("scheduler standby retry unavailable outside an event loop")
        return
    _scheduler_standby_task = loop.create_task(_scheduler_standby_loop())


def start_scheduler() -> bool:
    """Start the APScheduler process and sync enabled schedules. No-op when
    disabled by settings (e.g. the test suite) — rows stay durable in the DB
    and `run_schedule` remains testable directly."""
    global _scheduler, _scheduler_owner_id
    if _scheduler is not None and _scheduler.running:
        return True
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled (JALORA_SCHEDULER_ENABLED=false)")
        return False
    owner_id = uuid.uuid4().hex
    if not acquire_scheduler_lease(owner_id):
        logger.info("scheduler running in another worker; this worker remains standby")
        _ensure_scheduler_standby()
        return False
    _scheduler_owner_id = owner_id
    recover_schedule_occurrences()
    jobstore = SQLAlchemyJobStore(
        url=str(settings.database_url), tablename="apscheduler_jobs"
    )
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.scheduler_timezone or "UTC",
    )
    scheduler.add_job(
        _scheduler_lease_heartbeat,
        trigger=IntervalTrigger(seconds=_SCHEDULER_HEARTBEAT_SECONDS),
        id="jalora_scheduler_lease_heartbeat",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=utcnow() + timedelta(seconds=_SCHEDULER_HEARTBEAT_SECONDS),
    )
    _scheduler = scheduler
    try:
        scheduler.start()
    except Exception:
        _scheduler = None
        _release_scheduler_lease(owner_id)
        _scheduler_owner_id = None
        raise
    logs = sync_schedules()
    logger.info(
        "scheduler started | jobs=%s | timezone=%s",
        logs.get("scheduled", 0), settings.scheduler_timezone or "UTC",
    )
    return True


def stop_scheduler() -> None:
    global _scheduler, _scheduler_owner_id, _scheduler_standby_task
    if _scheduler_standby_task is not None:
        _scheduler_standby_task.cancel()
        _scheduler_standby_task = None
    if _scheduler is None:
        return
    owner_id = _scheduler_owner_id
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001 - shutdown must not crash the app
        logger.exception("scheduler shutdown failed")
    _scheduler = None
    _scheduler_owner_id = None
    if owner_id:
        _release_scheduler_lease(owner_id)


def sync_schedules() -> dict[str, int]:
    """Mirror enabled Schedule rows into the running scheduler."""
    result = {"scheduled": 0, "skipped": 0}
    if _scheduler is None or not _scheduler.running:
        return result
    with SessionLocal() as db:
        rows = list(
            db.scalars(select(Schedule).where(Schedule.enabled.is_(True))).all()
        )
        for row in rows:
            try:
                plan = SpecPlan(kind=row.kind, spec=row.spec, description="")
                if _sync_job(_scheduler, row, plan, db=db):
                    result["scheduled"] += 1
                else:
                    result["skipped"] += 1
            except Exception:  # noqa: BLE001 - one bad schedule must not stop the rest
                logger.exception("failed to schedule %s", row.id)
                result["skipped"] += 1
        db.commit()  # persist next_run refreshes
    return result


def _sync_job(
    sched: AsyncIOScheduler,
    row: Schedule,
    plan: SpecPlan,
    *,
    db,
) -> bool:
    scheduled_for = _coerce_dt(row.next_run) if row.next_run else _next_fire(plan)
    if scheduled_for is None:
        return False
    now = utcnow()
    if scheduled_for < now - timedelta(seconds=_MISFIRE_GRACE_SECONDS):
        _record_missed_occurrence(db, row, scheduled_for)
        if row.kind == "once":
            row.enabled = False
            row.next_run = None
            return False
        scheduled_for = _next_fire(plan, now=now)
        if scheduled_for is None:
            row.next_run = None
            return False
        row.next_run = scheduled_for
    elif row.next_run is None:
        row.next_run = scheduled_for
    sched.add_job(
        fire_schedule,
        trigger=DateTrigger(run_date=scheduled_for, timezone=timezone.utc),
        args=[row.id, scheduled_for.isoformat()],
        id=str(row.id),
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
    )
    return True


def refresh_schedule(schedule_id: str) -> None:
    """Re-register (or drop) one schedule in the running scheduler. Call after
    tool/API create, update, toggle or delete."""
    if _scheduler is None or not _scheduler.running:
        return
    with SessionLocal() as db:
        row = db.get(Schedule, schedule_id)
        if row is not None and row.enabled:
            try:
                plan = SpecPlan(kind=row.kind, spec=row.spec, description="")
                _sync_job(_scheduler, row, plan, db=db)
                db.commit()
                return
            except Exception:  # noqa: BLE001
                logger.exception("refresh_schedule %s failed", schedule_id)
        try:
            if _scheduler.get_job(str(schedule_id)):
                _scheduler.remove_job(str(schedule_id))
        except Exception:  # noqa: BLE001
            logger.exception("remove_job %s failed", schedule_id)


def create_schedule(
    *,
    user_id: str,
    task_template: str,
    text: str,
    notify_only: bool = False,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> Schedule:
    """Parse text and persist a new Schedule row (+ mirror into scheduler)."""
    plan = parse_schedule(text, now=now, timezone_name=timezone_name)
    with SessionLocal() as db:
        row = Schedule(
            user_id=user_id,
            kind=plan.kind,
            spec=plan.spec,
            schedule_expression=text.strip(),
            task_template=task_template,
            notify_only=notify_only,
            enabled=True,
            next_run=_next_fire(plan, now=now),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    refresh_schedule(row.id)
    _record_activity(user_id, "schedule_create", row=row, ok=True)
    return row


def update_schedule(
    user_id: str,
    schedule_id: str,
    *,
    task_template: str | None = None,
    text: str | None = None,
    notify_only: bool | None = None,
    timezone_name: str | None = None,
) -> Schedule | None:
    with SessionLocal() as db:
        row = db.get(Schedule, schedule_id)
        if row is None or row.user_id != user_id:
            return None
        updated_text = text.strip() if text is not None else row.schedule_expression
        updated_timezone = timezone_name or str((row.spec or {}).get("timezone") or "UTC")
        if text is not None or timezone_name is not None:
            if not updated_text:
                raise ValueError("This schedule needs a trigger description before its timezone can be changed.")
            plan = parse_schedule(updated_text, timezone_name=updated_timezone)
            row.kind = plan.kind
            row.spec = plan.spec
            row.schedule_expression = updated_text
            row.next_run = _next_fire(plan)
        if task_template is not None:
            row.task_template = task_template.strip()
        if notify_only is not None:
            row.notify_only = notify_only
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
    refresh_schedule(schedule_id)
    _record_activity(user_id, "schedule_update", row=row, ok=True)
    return row


def delete_schedule(user_id: str, schedule_id: str) -> bool:
    with SessionLocal() as db:
        row = db.get(Schedule, schedule_id)
        if row is None or row.user_id != user_id:
            return False
        db.delete(row)
        db.commit()
    refresh_schedule(schedule_id)
    _record_activity(user_id, "schedule_delete", schedule_id=schedule_id, ok=True)
    return True


def toggle_schedule(user_id: str, schedule_id: str, enabled: bool) -> bool:
    with SessionLocal() as db:
        row = db.get(Schedule, schedule_id)
        if row is None or row.user_id != user_id:
            return False
        row.enabled = enabled
        db.commit()
    if enabled:
        refresh_schedule(schedule_id)
    else:
        if _scheduler is not None and _scheduler.running:
            try:
                _scheduler.remove_job(schedule_id) if _scheduler.get_job(schedule_id) else None
            except Exception:  # noqa: BLE001
                logger.exception("failed to pause %s", schedule_id)
    _record_activity(user_id, "schedule_toggle", schedule_id=schedule_id, ok=True)
    return True


def _record_activity(
    user_id: str, tool: str, *, row: Schedule | None = None,
    schedule_id: str | None = None, ok: bool = True,
) -> None:
    try:
        with SessionLocal() as db:
            log_activity(
                db, user_id=user_id, agent="scheduler", tool=tool,
                arguments={"schedule_id": row.id if row else schedule_id,
                           "kind": row.kind if row else None,
                           "task_template": (row.task_template[:300] if row else None)},
                result_summary=f"{tool} {'ok' if ok else 'failed'}",
                success=ok,
            )
    except Exception:  # noqa: BLE001
        logger.exception("activity log failed")


# ---------------------------------------------------------------------------
# job execution
# ---------------------------------------------------------------------------

async def fire_schedule(
    schedule_id: str,
    scheduled_for: str | None = None,
) -> dict[str, Any]:
    """APScheduler entry point — delegates to the durable runner."""
    parsed = datetime.fromisoformat(scheduled_for) if scheduled_for else None
    return await run_schedule(schedule_id, scheduled_for=parsed)


def _occurrence_key(schedule_id: str, scheduled_for: datetime) -> str:
    instant = _coerce_dt(scheduled_for).isoformat(timespec="microseconds")
    return sha256(f"{schedule_id}:{instant}".encode("utf-8")).hexdigest()


def _claim_occurrence(
    db,
    row: Schedule,
    scheduled_for: datetime,
) -> tuple[ScheduleOccurrence, bool]:
    key = _occurrence_key(row.id, scheduled_for)
    occurrence = db.scalar(
        select(ScheduleOccurrence).where(
            ScheduleOccurrence.schedule_id == row.id,
            ScheduleOccurrence.occurrence_key == key,
        )
    )
    if occurrence is not None:
        if occurrence.status == "not_dispatched":
            result = db.execute(
                update(ScheduleOccurrence)
                .where(
                    ScheduleOccurrence.id == occurrence.id,
                    ScheduleOccurrence.status == "not_dispatched",
                )
                .values(status="dispatching", claimed_at=utcnow(), updated_at=utcnow())
            )
            db.commit()
            db.refresh(occurrence)
            return occurrence, result.rowcount == 1
        return occurrence, False

    occurrence = ScheduleOccurrence(
        schedule_id=row.id,
        user_id=row.user_id,
        occurrence_key=key,
        scheduled_for=_coerce_dt(scheduled_for),
        status="dispatching",
        task_template=row.task_template,
        notify_only=row.notify_only,
    )
    db.add(occurrence)
    try:
        db.commit()
        db.refresh(occurrence)
        return occurrence, True
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.schedule_id == row.id,
                ScheduleOccurrence.occurrence_key == key,
            )
        )
        if existing is None:
            raise
        return _claim_occurrence(db, row, scheduled_for)


def _next_after(plan: SpecPlan, previous_fire: datetime) -> datetime | None:
    if plan.kind == "once":
        return None
    trigger = _build_trigger(plan)
    next_fire = trigger.get_next_fire_time(previous_fire, previous_fire)
    if next_fire is None:
        return None
    if next_fire.tzinfo is None:
        next_fire = next_fire.replace(tzinfo=timezone.utc)
    return next_fire.astimezone(timezone.utc)


def _notify_occurrence(
    db,
    occurrence: ScheduleOccurrence,
    *,
    text: str,
    kind: str,
    link: str | None = None,
    update_existing: bool = False,
) -> None:
    existing = db.scalar(
        select(Notification).where(Notification.occurrence_id == occurrence.id)
    )
    if existing is not None:
        occurrence.notification_id = existing.id
        if update_existing:
            update_notification(
                db,
                existing,
                text=text,
                kind=kind,
                link=link,
                run_id=occurrence.run_id,
                task_id=occurrence.task_id,
                schedule_id=occurrence.schedule_id,
            )
        return
    note = notify(
        db,
        user_id=occurrence.user_id,
        text=text,
        kind=kind,
        link=link,
        run_id=occurrence.run_id,
        task_id=occurrence.task_id,
        schedule_id=occurrence.schedule_id,
        occurrence_id=occurrence.id,
    )
    occurrence.notification_id = note.id


def sync_task_occurrence(db, task: Task) -> None:
    """Keep schedule progress and its single linked notification aligned."""
    trigger = task.trigger_data or {}
    if trigger.get("kind") != "schedule" or not task.trigger_key:
        return
    occurrence = db.scalar(
        select(ScheduleOccurrence).where(
            ScheduleOccurrence.occurrence_key == task.trigger_key
        )
    )
    if occurrence is None:
        return
    status_map = {
        "queued": "dispatching",
        "running": "dispatching",
        "waiting_for_approval": "awaiting_approval",
        "paused": "paused",
        "completed": "completed",
        "partial": "partial",
        "failed": "failed",
        "stopped": "cancelled",
        "cancelled": "cancelled",
    }
    new_status = status_map.get(task.status)
    if task.status == "paused" and task.error and "outcome" in task.error.lower():
        new_status = "unknown"
    if new_status is None or occurrence.status in {"missed", "cancelled"}:
        return
    old_status = occurrence.status
    if new_status == old_status:
        return
    occurrence.status = new_status
    occurrence.task_id = task.id
    occurrence.run_id = task.run_id
    occurrence.result_summary = task.result or task.error or task.goal
    terminal = new_status in {
        "completed", "partial", "failed", "cancelled", "unknown",
    }
    occurrence.completed_at = utcnow() if terminal else None
    text_prefix = {
        "dispatching": "Scheduled task is running",
        "awaiting_approval": "A scheduled task needs your approval",
        "paused": "Scheduled task is paused",
        "completed": "Scheduled task completed",
        "partial": "Scheduled task partially completed",
        "failed": "Scheduled task failed",
        "cancelled": "Scheduled task was cancelled",
        "unknown": "Scheduled task outcome is unknown",
    }[new_status]
    detail = task.result or task.error or task.goal
    _notify_occurrence(
        db,
        occurrence,
        text=f"{text_prefix}: {detail[:400]}",
        kind=(
            "approval_required" if new_status == "awaiting_approval"
            else f"scheduled_task_{new_status}"
        ),
        link=task.conversation_id,
        update_existing=old_status != "dispatching",
    )


def _record_missed_occurrence(
    db,
    row: Schedule,
    scheduled_for: datetime,
) -> None:
    key = _occurrence_key(row.id, scheduled_for)
    occurrence = db.scalar(
        select(ScheduleOccurrence).where(
            ScheduleOccurrence.schedule_id == row.id,
            ScheduleOccurrence.occurrence_key == key,
        )
    )
    if occurrence is None:
        occurrence = ScheduleOccurrence(
            schedule_id=row.id,
            user_id=row.user_id,
            occurrence_key=key,
            scheduled_for=_coerce_dt(scheduled_for),
            status="missed",
            task_template=row.task_template,
            notify_only=row.notify_only,
            result_summary="Skipped after the one-hour misfire window; no action was dispatched.",
            completed_at=utcnow(),
        )
        db.add(occurrence)
        db.flush()
    if occurrence.status == "not_dispatched":
        occurrence.status = "missed"
        occurrence.result_summary = "Skipped after the one-hour misfire window; no action was dispatched."
        occurrence.completed_at = utcnow()
    _notify_occurrence(
        db,
        occurrence,
        text=(
            "A scheduled reminder was missed and was not delivered."
            if row.notify_only
            else "A scheduled task was missed and was not dispatched."
        ),
        kind="scheduled_task_missed",
    )


def recover_schedule_occurrences() -> dict[str, int]:
    """Reconcile interrupted fires without replaying a task with uncertain effects."""
    counts = {"recovered": 0, "unknown": 0, "retryable": 0}
    with SessionLocal() as db:
        occurrences = list(db.scalars(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.status.in_(
                    ("dispatching", "awaiting_approval", "paused")
                )
            )
        ).all())
        for occurrence in occurrences:
            previous_status = occurrence.status
            schedule = db.get(Schedule, occurrence.schedule_id)
            task = db.scalar(
                select(Task).where(Task.trigger_key == occurrence.occurrence_key)
            )
            if task is None and occurrence.notify_only:
                _notify_occurrence(
                    db,
                    occurrence,
                    text=occurrence.task_template.strip() or "Reminder",
                    kind="reminder",
                )
                occurrence.status = "reminder_sent"
                occurrence.completed_at = utcnow()
                counts["recovered"] += 1
            elif task is None and (schedule is None or not schedule.enabled):
                occurrence.status = "cancelled"
                occurrence.result_summary = "Schedule was removed or paused before dispatch."
                occurrence.completed_at = utcnow()
                counts["recovered"] += 1
            elif task is None and previous_status in {"awaiting_approval", "paused"}:
                occurrence.status = "unknown"
                occurrence.error_code = "CANONICAL_TASK_MISSING"
                occurrence.result_summary = (
                    "The canonical task for an interrupted approval or paused run "
                    "could not be found; it was not automatically replayed."
                )
                occurrence.completed_at = utcnow()
                _notify_occurrence(
                    db,
                    occurrence,
                    text=occurrence.result_summary,
                    kind="scheduled_task_unknown",
                    update_existing=True,
                )
                counts["unknown"] += 1
            elif task is None:
                occurrence.status = "not_dispatched"
                occurrence.result_summary = "No canonical task was persisted; this occurrence is safe to dispatch once."
                counts["retryable"] += 1
            else:
                run = db.get(AgentRun, task.run_id) if task.run_id else None
                occurrence.task_id = task.id
                occurrence.run_id = task.run_id
                task_state = task.status
                run_state = run.status if run else ""
                if task_state in {"waiting_for_approval", "paused"} or run_state == "paused_approval":
                    occurrence.status = "awaiting_approval"
                    _notify_occurrence(
                        db,
                        occurrence,
                        text=f"A scheduled task needs your approval: {task.goal[:300]}",
                        kind="approval_required",
                        link=task.conversation_id,
                        update_existing=previous_status != occurrence.status,
                    )
                elif task_state in {"running", "queued"} or run_state == "running":
                    occurrence.status = "unknown"
                    occurrence.error_code = "INTERRUPTED_OUTCOME_UNKNOWN"
                    occurrence.result_summary = (
                        "The app restarted while this task was running. Its external outcome "
                        "is unknown; it was not automatically retried."
                    )
                    task.status = "partial"
                    task.error = occurrence.result_summary
                    task.completed_at = utcnow()
                    if run is not None:
                        run.status = "partial"
                        run.error = occurrence.result_summary
                        run.completed_at = utcnow()
                    _notify_occurrence(
                        db,
                        occurrence,
                        text=occurrence.result_summary,
                        kind="scheduled_task_unknown",
                        link=task.conversation_id,
                        update_existing=previous_status != occurrence.status,
                    )
                    counts["unknown"] += 1
                else:
                    occurrence.status = task_state
                    occurrence.result_summary = task.result or task.error
                    occurrence.completed_at = task.completed_at or utcnow()
                    _notify_occurrence(
                        db,
                        occurrence,
                        text=f"Scheduled task recovered as {task_state}: {(task.result or task.error or task.goal)[:400]}",
                        kind=(
                            "scheduled_task_completed"
                            if task_state == "completed"
                            else "scheduled_task_failed"
                        ),
                        link=task.conversation_id,
                        update_existing=previous_status != occurrence.status,
                    )
                    counts["recovered"] += 1
            if schedule is not None:
                schedule.last_run = occurrence.scheduled_for
                if schedule.kind == "once":
                    schedule.enabled = False
                    schedule.next_run = None
                elif occurrence.status != "not_dispatched":
                    plan = SpecPlan(schedule.kind, schedule.spec, "")
                    schedule.next_run = _next_after(plan, occurrence.scheduled_for)
            occurrence.updated_at = utcnow()
        db.commit()
    return counts


async def run_schedule(
    schedule_id: str,
    *,
    now: datetime | None = None,
    scheduled_for: datetime | None = None,
) -> dict[str, Any]:
    """Execute one durable occurrence without replaying duplicate fires."""
    started = utcnow()
    request = ""
    occurrence_id = ""
    occurrence_key = ""
    user_id = ""
    schedule_timezone = "UTC"
    with SessionLocal() as db:
        row = db.get(Schedule, schedule_id)
        if row is None:
            return {"schedule_id": schedule_id, "skipped": "removed"}
        if not row.enabled:
            return {"schedule_id": schedule_id, "skipped": "disabled"}
        if db.get(User, row.user_id) is None:
            row.enabled = False
            db.commit()
            return {"schedule_id": schedule_id, "skipped": "user_removed"}

        planned = _coerce_dt(scheduled_for or row.next_run or now or utcnow())
        if scheduled_for is not None and row.next_run is not None:
            if _coerce_dt(row.next_run) != planned:
                return {
                    "schedule_id": schedule_id,
                    "skipped": "stale_occurrence",
                    "scheduled_for": planned.isoformat(),
                }
        occurrence, claimed = _claim_occurrence(db, row, planned)
        if not claimed:
            return {
                "schedule_id": schedule_id,
                "skipped": "already_claimed",
                "occurrence_id": occurrence.id,
                "status": occurrence.status,
                "task_id": occurrence.task_id,
                "run_id": occurrence.run_id,
            }
        occurrence_id = occurrence.id
        occurrence_key = occurrence.occurrence_key
        user_id = row.user_id
        schedule_timezone = str((row.spec or {}).get("timezone") or "UTC")
        request = (row.task_template or "").strip() or "Follow up on your scheduled task."
        if row.notify_only:
            _notify_occurrence(
                db,
                occurrence,
                text=(row.task_template or "").strip() or "Reminder",
                kind="reminder",
            )
            occurrence.status = "reminder_sent"
            occurrence.result_summary = "Reminder notification created."
            occurrence.completed_at = utcnow()
            row.last_run = started
            row.next_run = (
                None if row.kind == "once"
                else _next_after(SpecPlan(row.kind, row.spec, ""), planned)
            )
            if row.kind == "once":
                row.enabled = False
            is_enabled = row.enabled
            db.commit()
            if is_enabled:
                refresh_schedule(schedule_id)
            return {
                "schedule_id": schedule_id,
                "occurrence_id": occurrence_id,
                "status": "reminder_fired",
                "scheduled_for": planned.isoformat(),
                "notification_id": occurrence.notification_id,
            }

    outcome = None
    dispatch_error: Exception | None = None
    try:
        outcome = await chat_service.execute_chat(
            user_id=user_id,
            request_message=request,
            router=_default_router(),
            emitter=ListEmitter(),
            mode="agent",
            trigger_kind="schedule",
            trigger_key=occurrence_key,
            trigger_data={
                "schedule_id": schedule_id,
                "occurrence_id": occurrence_id,
                "scheduled_for": planned.isoformat(),
                "timezone": schedule_timezone,
            },
        )
    except Exception as exc:
        logger.exception("schedule %s occurrence %s crashed", schedule_id, occurrence_id)
        dispatch_error = exc

    task_id = getattr(outcome, "task_id", "") or None
    run_id = getattr(outcome, "run_id", "") or None
    conversation_id = getattr(outcome, "conversation_id", "") or None
    reply = (getattr(outcome, "reply", "") or "").strip().replace("\n", " ")
    err_value = getattr(outcome, "error", None)
    if isinstance(err_value, dict):
        error_text = str(
            err_value.get("details") or err_value.get("message") or "unknown error"
        )
    else:
        error_text = str(err_value or dispatch_error or "unknown error")

    with SessionLocal() as db:
        occurrence = db.get(ScheduleOccurrence, occurrence_id)
        row = db.get(Schedule, schedule_id)
        task = db.get(Task, task_id) if task_id else db.scalar(
            select(Task).where(Task.trigger_key == occurrence_key)
        )
        if task is not None:
            task_id = task.id
            run_id = task.run_id
            conversation_id = task.conversation_id
        if outcome is None:
            if task is None:
                status = "failed"
                kind = "scheduled_task_failed"
                message = f"Scheduled task was not dispatched: {error_text[:400]}"
            elif task.status in {"running", "queued"}:
                status = "unknown"
                kind = "scheduled_task_unknown"
                message = (
                    "Scheduled task execution was interrupted; its outcome is unknown "
                    "and it was not automatically retried."
                )
                task.status = "partial"
                task.error = message
                task.completed_at = utcnow()
                run = db.get(AgentRun, task.run_id) if task.run_id else None
                if run is not None:
                    run.status = "partial"
                    run.error = message
                    run.completed_at = utcnow()
            else:
                status = task.status
                kind = "scheduled_task_failed"
                message = f"Scheduled task ended as {status}: {(task.error or error_text)[:400]}"
        elif outcome.status == "paused_approval":
            status = "awaiting_approval"
            kind = "approval_required"
            message = f"A scheduled task needs your approval: {request[:300]}"
        elif outcome.status == "completed":
            status = "completed"
            kind = "scheduled_task_completed"
            message = f"Scheduled task completed: {reply[:400]}"
        elif outcome.status == "partial":
            status = "partial"
            kind = "scheduled_task_partial"
            message = f"Scheduled task partially completed: {reply[:400] or error_text[:400]}"
        elif outcome.status in {"stopped", "cancelled"}:
            status = "cancelled"
            kind = "scheduled_task_cancelled"
            message = f"Scheduled task was cancelled: {reply[:400]}"
        elif outcome.status == "failed":
            status = "failed"
            kind = "scheduled_task_failed"
            message = f"Scheduled task failed: {error_text[:400]}"
        else:
            status = "unknown"
            kind = "scheduled_task_unknown"
            message = (
                f"Scheduled task returned an unrecognized state ({outcome.status}); "
                "its outcome must be reviewed."
            )

        occurrence.task_id = task_id
        occurrence.run_id = run_id
        occurrence.status = status
        occurrence.result_summary = message[:2000]
        occurrence.error_code = (
            "INTERRUPTED_OUTCOME_UNKNOWN" if status == "unknown"
            else "SCHEDULE_DISPATCH_FAILED"
            if status == "failed" and outcome is None
            else None
        )
        occurrence.completed_at = utcnow()
        _notify_occurrence(
            db,
            occurrence,
            text=message,
            kind=kind,
            link=conversation_id,
        )
        if row is not None:
            row.last_run = started
            if row.kind == "once":
                row.enabled = False
                row.next_run = None
            else:
                row.next_run = _next_after(SpecPlan(row.kind, row.spec, ""), planned)
        db.commit()
        try:
            log_activity(
                db,
                user_id=user_id,
                agent="scheduler",
                tool="schedule_fire",
                arguments={
                    "schedule_id": schedule_id,
                    "occurrence_id": occurrence_id,
                    "scheduled_for": planned.isoformat(),
                },
                result_summary=f"schedule occurrence -> {status}",
                success=status not in {"failed", "unknown"},
                meta={
                    "task_id": task_id,
                    "run_id": run_id,
                    "occurrence_id": occurrence_id,
                },
            )
        except Exception:
            logger.exception("schedule occurrence activity logging failed")
        is_enabled = row.enabled if row is not None else False
    if is_enabled:
        refresh_schedule(schedule_id)
    return {
        "schedule_id": schedule_id,
        "occurrence_id": occurrence_id,
        "status": "approval_required" if status == "awaiting_approval" else status,
        "task_id": task_id,
        "run_id": run_id,
        "scheduled_for": planned.isoformat(),
    }


# --- Event reminders (§63): one-shot notify-only schedules tied to an event ---
def create_once_reminder(*, user_id: str, run_at: datetime, text: str,
                         meta: dict[str, Any] | None = None) -> Schedule | None:
    """Create a one-shot notify-only reminder at `run_at` (skips past times).

    `meta` is merged into the schedule spec (e.g. ``{"event_id": ...}``) so the
    reminder can be found and cancelled when its event changes or is deleted.
    Returns the row, or ``None`` when the time is already in the past.
    """
    when = _coerce_dt(run_at)
    if when <= utcnow():
        return None
    with SessionLocal() as db:
        row = Schedule(
            user_id=user_id,
            kind="once",
            spec={"run_date": when.isoformat(), **(meta or {})},
            task_template=text,
            notify_only=True,
            enabled=True,
            next_run=when,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    refresh_schedule(row.id)
    return row


def delete_reminders_for_event(user_id: str, event_id: str) -> int:
    """Remove every one-shot reminder linked to `event_id`; returns the count."""
    with SessionLocal() as db:
        rows = list(db.scalars(select(Schedule).where(
            Schedule.user_id == user_id,
            Schedule.notify_only.is_(True),
            Schedule.kind == "once",
        )).all())
        targets = [r for r in rows if (r.spec or {}).get("event_id") == event_id]
        ids = [r.id for r in targets]
        for row in targets:
            db.delete(row)
        db.commit()
    for sid in ids:
        refresh_schedule(sid)
    return len(ids)