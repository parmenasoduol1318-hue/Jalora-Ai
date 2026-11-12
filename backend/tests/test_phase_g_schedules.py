"""Phase G (§49-51, §61-62, §73-L4): scheduling, reminders, notifications, briefing.

Exercises `parse_schedule` deterministically (fixed `now`), the durable fire
path `run_schedule` (reminder + full agent task, no wall-clock wait), L4-only
auto-execution through the real policy engine, cancellation/toggle, the REST
APIs and the daily briefing composition.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.agent import AgentRun, Approval
from app.models.notification import Notification
from app.models.schedule import Schedule, ScheduleOccurrence, SchedulerLease
from app.models.task import Task
from app.models.user import User
from app.services import notifications as notifications_service
from app.services import scheduler as scheduler_service
from app.services.chat_service import _finalize_task
from app.tools.schedule_tools import daily_briefing
from langchain_core.messages import AIMessage

NOW = datetime(2026, 10, 8, 10, 7, 0, tzinfo=timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes from DateTime(timezone=True); normalise."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _register(client, email: str):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Tester",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _schedules_for(user_id: str) -> list[Schedule]:
    with SessionLocal() as db:
        return list(db.scalars(
            select(Schedule).where(Schedule.user_id == user_id)
        ).all())


def _notifications_for(user_id: str) -> list[Notification]:
    with SessionLocal() as db:
        return list(db.scalars(
            select(Notification).where(Notification.user_id == user_id)
        ).all())


def _set_prefs(user_id: str, prefs: dict) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.prefs = prefs
        db.commit()


# --- spec parser ------------------------------------------------------------

def test_parse_interval_anchors_to_epoch_grid():
    plan = scheduler_service.parse_schedule("every 2 hours", now=NOW)
    assert plan.kind == "interval"
    assert plan.spec["hours"] == 2
    assert plan.spec.get("start_date") is None  # epoch-anchored, deterministic
    nxt = scheduler_service._next_fire(plan, now=NOW)
    assert nxt.minute == 0 and nxt.second == 0
    delta = (nxt - NOW).total_seconds()
    assert 0 < delta < 7200


def test_parse_interval_with_time_phase():
    plan = scheduler_service.parse_schedule("every 2 hours at 09:00", now=NOW)
    assert plan.kind == "interval"
    start = datetime.fromisoformat(plan.spec["start_date"])
    assert start.hour == 9  # tomorrow 09:00, since 09:00 today already passed


def test_parse_cron_days():
    cases = {
        "every Monday at 08:00": ("cron", {"day_of_week": "0", "hour": 8, "minute": 0}),
        "every morning at 8": ("cron", {"day_of_week": "*", "hour": 8, "minute": 0}),
        "weekdays at 18:30": ("cron", {"day_of_week": "mon-fri", "hour": 18, "minute": 30}),
        "every weekend at 10am": ("cron", {"day_of_week": "sat,sun", "hour": 10, "minute": 0}),
    }
    for text, (kind, expect) in cases.items():
        plan = scheduler_service.parse_schedule(text, now=NOW)
        assert plan.kind == kind, text
        for k, v in expect.items():
            assert plan.spec[k] == v, f"{text}: {k}"


def test_parse_next_monday():
    plan = scheduler_service.parse_schedule("every Monday at 08:00", now=NOW)
    # 2026-10-08 is a Thursday → next Monday is 2026-10-12
    assert plan.kind == "cron"
    nxt = scheduler_service._next_fire(plan, now=NOW)
    assert nxt == datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def test_parse_timezone_uses_local_wall_clock():
    plan = scheduler_service.parse_schedule(
        "daily at 9am", now=NOW, timezone_name="America/New_York",
    )
    assert plan.spec["timezone"] == "America/New_York"
    assert scheduler_service._next_fire(plan, now=NOW) == datetime(
        2026, 10, 8, 13, 0, tzinfo=timezone.utc,
    )


def test_parse_rejects_nonexistent_dst_wall_time():
    with pytest.raises(ValueError, match="does not exist"):
        scheduler_service.parse_schedule(
            "2026-03-08T02:30",
            now=NOW,
            timezone_name="America/Los_Angeles",
        )


def test_parse_iso_once():
    plan = scheduler_service.parse_schedule("run the audit 2026-10-09T08:30", now=NOW)
    assert plan.kind == "once"
    assert datetime.fromisoformat(plan.spec["run_date"]) == datetime(
        2026, 10, 9, 8, 30, tzinfo=timezone.utc)


def test_parse_crontab():
    plan = scheduler_service.parse_schedule("0 8 * * *", now=NOW)
    assert plan.kind == "cron"
    assert plan.spec["crontab"] == "0 8 * * *"


@pytest.mark.parametrize("text", [
    "", "tomorrow please", "sometime soon", "2020-01-01T09:00", "25:99",
])
def test_parse_invalid(text):
    with pytest.raises(ValueError):
        scheduler_service.parse_schedule(text, now=NOW)


@pytest.mark.parametrize("text, expect_kind", [
    ("every week", "interval"), ("weekly", "interval"), ("monthly", "cron"),
    ("daily at 9am", "cron"), ("at 18:30", "once"),
])
def test_parse_more_cases(text, expect_kind):
    plan = scheduler_service.parse_schedule(text, now=NOW)
    assert plan.kind == expect_kind


# --- durable fire: reminders -------------------------------------------------

def test_reminder_fire_creates_notification(client):
    data = _register(client, "rem@test.com")
    uid = data["user"]["id"]

    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Stand up and stretch", text="every Monday at 08:00",
        notify_only=True, now=NOW,
    )
    assert row.notify_only and row.enabled and row.kind == "cron"
    assert _utc(row.next_run) == datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)

    log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    assert log["status"] == "reminder_fired"

    notes = _notifications_for(uid)
    assert len(notes) == 1
    assert notes[0].kind == "reminder"
    assert notes[0].text == "Stand up and stretch"
    assert notes[0].schedule_id == row.id

    with SessionLocal() as db:
        after = db.get(Schedule, row.id)
        assert after.last_run is not None
        # A durable occurrence advances from the exact fire time.
        assert _utc(after.next_run) == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
        occurrence = db.get(ScheduleOccurrence, log["occurrence_id"])
        assert occurrence.status == "reminder_sent"
        assert occurrence.notification_id == notes[0].id


def test_once_fire_disables_schedule(client):
    data = _register(client, "once@test.com")
    uid = data["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Say hi", text="2026-10-09T08:30",
        notify_only=True, now=NOW,
    )
    assert row.kind == "once" and row.enabled

    log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    assert log["status"] == "reminder_fired"

    with SessionLocal() as db:
        after = db.get(Schedule, row.id)
        assert after.enabled is False
        assert after.next_run is None


def test_disabled_schedule_is_skipped(client):
    data = _register(client, "skip@test.com")
    uid = data["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Hidden", text="every day at 9am",
        notify_only=True, now=NOW,
    )
    assert scheduler_service.toggle_schedule(uid, row.id, False)
    with SessionLocal() as db:
        assert db.get(Schedule, row.id).enabled is False
    log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    assert log["skipped"] == "disabled"
    assert _notifications_for(uid) == []


# --- durable fire: full agent task -------------------------------------------

def test_scheduled_task_fire_runs_pipeline(client, fake_router, confirm_calls):
    data = _register(client, "task@test.com")
    uid = data["user"]["id"]
    fake_router.script.append(AIMessage(content="Report generated."))

    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=uid, task_template="Summarize my inbox",
            text="every morning at 8", notify_only=False, now=NOW,
        )
        log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    assert log["status"] == "completed"
    assert log["run_id"] and log["task_id"]

    with SessionLocal() as db:
        task = db.get(Task, log["task_id"])
        assert task is not None
        assert task.goal == "Summarize my inbox"
        assert task.status == "completed"
        assert task.conversation_id is not None
        assert task.trigger_key
        occurrence = db.get(ScheduleOccurrence, log["occurrence_id"])
        assert occurrence.task_id == task.id
        assert occurrence.run_id == task.run_id
        assert occurrence.occurrence_key == task.trigger_key

    notes = _notifications_for(uid)
    assert len(notes) == 1
    n = notes[0]
    assert n.kind == "scheduled_task_completed"
    assert n.link == task.conversation_id
    assert n.run_id == log["run_id"] and n.task_id == log["task_id"]
    assert "Scheduled task completed:" in n.text
    assert confirm_calls == []


def test_scheduled_task_pauses_for_approval_at_l2(client, fake_router, confirm_calls):
    """Default autonomy (2, tool_agent): a CONFIRM tool must pause — even when
    the fire comes from a schedule. No silent execution."""
    data = _register(client, "l2@test.com")
    uid = data["user"]["id"]
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "scheduled ping"},
            "id": "call_g", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Resumed."))

    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=uid, task_template="Ping the external system",
            text="every day at 9am", notify_only=False, now=NOW,
        )
        log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    assert log["status"] == "approval_required"
    assert confirm_calls == []  # nothing executed without approval

    notes = _notifications_for(uid)
    assert any(n.kind == "approval_required" for n in notes)
    with SessionLocal() as db:
        approvals = list(db.scalars(
            select(Approval).where(Approval.user_id == uid)
        ).all())
        assert len(approvals) == 1
        assert approvals[0].status == "pending"
        assert approvals[0].tool_name == "ping_external"
        occurrence = db.get(ScheduleOccurrence, log["occurrence_id"])
        assert occurrence.status == "awaiting_approval"
        assert occurrence.task_id is not None


def test_scheduled_task_at_l3_waits_for_explicit_scheduled_autonomy(
    client, fake_router, confirm_calls
):
    data = _register(client, "l3-scheduled@test.com")
    uid = data["user"]["id"]
    _set_prefs(uid, {
        "autonomy": 3,
        "tool_policies": {"ping_external": "automatic"},
    })
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "scheduled l3"},
            "id": "call_l3_scheduled", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Waiting for approval."))

    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=uid, task_template="Ping the external system",
            text="every day at 9am", notify_only=False, now=NOW,
        )
        log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    assert log["status"] == "approval_required"
    assert confirm_calls == []
    alerts = _notifications_for(uid)
    assert len(alerts) == 1
    assert alerts[0].kind == "approval_required"


def test_scheduled_occurrence_and_notice_update_after_task_resumes(
    client, fake_router, confirm_calls,
):
    user_id = _register(client, "approval-resume@test.com")["user"]["id"]
    fake_router.script.append(AIMessage(content="Initial approval required."))
    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id, task_template="Wait for approval then finish",
            text="every day at 9am", now=NOW,
        )
        fired = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    with SessionLocal() as db:
        occurrence = db.get(ScheduleOccurrence, fired["occurrence_id"])
        task = db.get(Task, fired["task_id"])
        notification = db.get(Notification, occurrence.notification_id)
        occurrence.status = "awaiting_approval"
        task.status = "waiting_for_approval"
        task.completed_at = None
        notification.kind = "approval_required"
        notification.text = "Approval is needed."
        notification.read_at = datetime.now(timezone.utc)
        _finalize_task(
            db,
            task_id=task.id,
            status="completed",
            reply="Action completed and confirmed.",
        )
        db.commit()

    with SessionLocal() as db:
        occurrence = db.get(ScheduleOccurrence, fired["occurrence_id"])
        notification = db.get(Notification, occurrence.notification_id)
        assert occurrence.status == "completed"
        assert occurrence.result_summary == "Action completed and confirmed."
        assert notification.kind == "scheduled_task_completed"
        assert notification.text.endswith("Action completed and confirmed.")
        assert notification.read_at is None
    assert confirm_calls == []


def test_duplicate_fire_does_not_repeat_task(client, fake_router, confirm_calls):
    data = _register(client, "dedupe@test.com")
    uid = data["user"]["id"]
    fake_router.script.append(AIMessage(content="Only one run."))
    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=uid, task_template="Perform this once",
            text="every day at 9am", notify_only=False, now=NOW,
        )
        first = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
        second = asyncio.run(scheduler_service.run_schedule(
            row.id,
            scheduled_for=datetime.fromisoformat(first["scheduled_for"]),
        ))
    finally:
        scheduler_service.clear_router_override()

    assert first["status"] == "completed"
    assert second["skipped"] == "stale_occurrence"
    with SessionLocal() as db:
        occurrences = list(db.scalars(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.schedule_id == row.id
            )
        ).all())
        assert len(occurrences) == 1
        assert occurrences[0].task_id == first["task_id"]
    assert len(fake_router.tape) == 1
    assert confirm_calls == []


def test_concurrent_schedule_fires_dispatch_external_action_once(
    client, fake_router, confirm_calls,
):
    user_id = _register(client, "concurrent-fire@test.com")["user"]["id"]
    _set_prefs(user_id, {
        "autonomy": 4,
        "tool_policies": {"ping_external": "automatic"},
    })
    fake_router.script.append(AIMessage(
        content="",
        tool_calls=[{
            "name": "ping_external",
            "args": {"message": "deduplicated scheduled action"},
            "id": "scheduled-call-once",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Action confirmed."))
    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id,
            task_template="Ping the test endpoint",
            text="every day at 9am",
            now=NOW,
        )
        scheduled_for = row.next_run

        async def fire_twice():
            return await asyncio.gather(
                scheduler_service.run_schedule(
                    row.id, scheduled_for=scheduled_for,
                ),
                scheduler_service.run_schedule(
                    row.id, scheduled_for=scheduled_for,
                ),
            )

        first, second = asyncio.run(fire_twice())
    finally:
        scheduler_service.clear_router_override()

    assert {
        first.get("skipped") or first["status"],
        second.get("skipped") or second["status"],
    } == {"completed", "already_claimed"}
    assert confirm_calls == [{"message": "deduplicated scheduled action"}]
    with SessionLocal() as db:
        occurrences = list(db.scalars(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.schedule_id == row.id
            )
        ).all())
        tasks = list(db.scalars(
            select(Task).where(Task.trigger_key == occurrences[0].occurrence_key)
        ).all())
    assert len(occurrences) == 1
    assert len(tasks) == 1


def test_occurrence_claim_is_atomic_for_duplicate_fire(client):
    user_id = _register(client, "claim@test.com")["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=user_id, task_template="No duplicate", text="every day at 9am",
        now=NOW,
    )
    scheduled_for = row.next_run
    barrier = Barrier(2)

    def claim():
        with SessionLocal() as db:
            schedule = db.get(Schedule, row.id)
            barrier.wait(timeout=5)
            occurrence, claimed = scheduler_service._claim_occurrence(
                db, schedule, scheduled_for,
            )
            return occurrence.id, claimed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(claimed for _, claimed in results) == 1
    assert len({occurrence_id for occurrence_id, _ in results}) == 1


def test_recovery_marks_interrupted_external_outcome_unknown(
    client, fake_router, confirm_calls,
):
    user_id = _register(client, "recovery@test.com")["user"]["id"]
    fake_router.script.append(AIMessage(content="Task began."))
    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id, task_template="Inspect provider result",
            text="every day at 9am", now=NOW,
        )
        fired = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    with SessionLocal() as db:
        occurrence = db.get(ScheduleOccurrence, fired["occurrence_id"])
        task = db.get(Task, fired["task_id"])
        run = db.get(AgentRun, fired["run_id"])
        occurrence.status = "dispatching"
        occurrence.completed_at = None
        if occurrence.notification_id:
            notification = db.get(Notification, occurrence.notification_id)
            if notification is not None:
                db.delete(notification)
            occurrence.notification_id = None
        task.status = "running"
        task.completed_at = None
        run.status = "running"
        run.completed_at = None
        db.commit()

    backend_dir = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.scheduler import recover_schedule_occurrences; "
            "recover_schedule_occurrences()",
        ],
        cwd=backend_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    with SessionLocal() as db:
        occurrence = db.get(ScheduleOccurrence, fired["occurrence_id"])
        task = db.get(Task, fired["task_id"])
        run = db.get(AgentRun, fired["run_id"])
        assert occurrence.status == "unknown"
        assert occurrence.error_code == "INTERRUPTED_OUTCOME_UNKNOWN"
        assert task.status == "partial"
        assert run.status == "partial"
    assert any(n.kind == "scheduled_task_unknown" for n in _notifications_for(user_id))
    assert len(fake_router.tape) == 1
    assert confirm_calls == []


def test_scheduler_lease_rejects_live_owner_and_allows_expired_takeover():
    now = datetime.now(timezone.utc)
    assert scheduler_service.acquire_scheduler_lease("owner-one", now=now)
    assert not scheduler_service.acquire_scheduler_lease(
        "owner-two", now=now + timedelta(seconds=1),
    )
    assert scheduler_service.acquire_scheduler_lease(
        "owner-two", now=now + timedelta(seconds=60),
    )
    with SessionLocal() as db:
        lease = db.get(SchedulerLease, "primary")
        assert lease.owner_id == "owner-two"


def test_recovery_reclaims_occurrence_without_persisted_task(
    client, fake_router, confirm_calls,
):
    user_id = _register(client, "safe-retry@test.com")["user"]["id"]
    fake_router.script.append(AIMessage(content="One safe dispatch."))
    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=user_id, task_template="No task was persisted",
            text="every day at 9am", now=NOW,
        )
        with SessionLocal() as db:
            schedule = db.get(Schedule, row.id)
            occurrence, claimed = scheduler_service._claim_occurrence(
                db, schedule, row.next_run,
            )
            assert claimed
        recovered = scheduler_service.recover_schedule_occurrences()
        assert recovered["retryable"] == 1
        fired = asyncio.run(scheduler_service.run_schedule(
            row.id,
            scheduled_for=row.next_run,
        ))
    finally:
        scheduler_service.clear_router_override()

    assert fired["status"] == "completed"
    assert fired["occurrence_id"] == occurrence.id
    assert len(fake_router.tape) == 1
    assert confirm_calls == []


def test_old_misfire_is_recorded_without_dispatch(client):
    user_id = _register(client, "misfire@test.com")["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=user_id, task_template="Do not replay old task",
        text="every Monday at 08:00", now=NOW,
    )
    with SessionLocal() as db:
        stored = db.get(Schedule, row.id)
        stored.next_run = datetime.now(timezone.utc) - timedelta(hours=2)
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduled = scheduler_service._sync_job(
            scheduler,
            stored,
            scheduler_service.SpecPlan(stored.kind, stored.spec, ""),
            db=db,
        )
        db.commit()
        assert scheduled
        assert stored.next_run > datetime.now(timezone.utc)
        missed = db.scalar(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.schedule_id == row.id
            )
        )
        assert missed.status == "missed"
        assert missed.task_id is None
    assert any(n.kind == "scheduled_task_missed" for n in _notifications_for(user_id))


def test_scheduled_task_auto_runs_at_l4(client, fake_router, confirm_calls):
    """L4 autonomy auto-runs eligible scheduled CONFIRM tools by default."""
    data = _register(client, "l4@test.com")
    uid = data["user"]["id"]
    _set_prefs(uid, {"autonomy": 4})
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "auto ping"},
            "id": "call_l4", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Done automatically."))

    scheduler_service.set_router_override(fake_router)
    try:
        row = scheduler_service.create_schedule(
            user_id=uid, task_template="Run the daily ping",
            text="every morning at 8", notify_only=False, now=NOW,
        )
        log = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))
    finally:
        scheduler_service.clear_router_override()

    assert log["status"] == "completed"
    assert confirm_calls == [{"message": "auto ping"}]
    notes = [n for n in _notifications_for(uid) if n.kind == "scheduled_task_completed"]
    assert len(notes) == 1


def test_removed_schedule_is_skipped(client):
    data = _register(client, "removed@test.com")
    uid = data["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Hi", text="every day at 9am",
        notify_only=True, now=NOW,
    )
    sched_id = row.id
    with SessionLocal() as db:
        db.delete(db.get(Schedule, sched_id))
        db.commit()

    log = asyncio.run(scheduler_service.run_schedule(sched_id, now=NOW))
    assert log["skipped"] == "removed"
    assert _notifications_for(uid) == []


# --- service CRUD: cancel / toggle / list -------------------------------------

def test_cancel_schedule_service(client):
    data = _register(client, "crud@test.com")
    uid = data["user"]["id"]
    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Briefing", text="daily at 9am",
        notify_only=True, now=NOW,
    )
    assert len(_schedules_for(uid)) == 1

    # only the owner can delete
    other = _register(client, "other@test.com")["user"]["id"]
    assert scheduler_service.delete_schedule(other, row.id) is False
    assert scheduler_service.delete_schedule(uid, row.id) is True
    assert _schedules_for(uid) == []


# --- REST APIs ----------------------------------------------------------------

def test_schedules_api_crud(client):
    data = _register(client, "api@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    resp = client.post("/api/schedules", json={
        "when": "every Monday at 08:00",
        "task_template": "Prepare the weekly report",
        "timezone": "Africa/Nairobi",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    assert resp.json()["kind"] == "cron"
    assert resp.json()["enabled"] is True
    assert resp.json()["timezone"] == "Africa/Nairobi"
    assert resp.json()["next_run"].endswith("+00:00")

    rows = client.get("/api/schedules", headers=headers).json()
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["task_template"] == "Prepare the weekly report"
    assert rows[0]["notify_only"] is False
    assert rows[0]["schedule_expression"] == "every Monday at 08:00"
    assert rows[0]["latest_occurrence"] is None

    updated = client.put(f"/api/schedules/{sid}", json={
        "when": "weekdays at 10:00",
        "task_template": "Prepare the updated report",
        "timezone": "UTC",
        "notify_only": True,
    }, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["task_template"] == "Prepare the updated report"
    assert updated.json()["notify_only"] is True
    assert updated.json()["timezone"] == "UTC"
    assert updated.json()["schedule_expression"] == "weekdays at 10:00"

    fired = asyncio.run(scheduler_service.run_schedule(sid))
    assert fired["status"] == "reminder_fired"
    latest = client.get("/api/schedules", headers=headers).json()[0]["latest_occurrence"]
    assert latest["id"] == fired["occurrence_id"]
    assert latest["status"] == "reminder_sent"
    assert latest["task_id"] is None
    assert latest["scheduled_for"].endswith("+00:00")

    toggle = client.post(f"/api/schedules/{sid}/toggle", json={"enabled": False},
                         headers=headers)
    assert toggle.status_code == 200
    assert client.get("/api/schedules", headers=headers).json()[0]["enabled"] is False

    assert client.delete(f"/api/schedules/{sid}", headers=headers).status_code == 200
    assert client.get("/api/schedules", headers=headers).json() == []


def test_schedules_api_parse_error(client):
    data = _register(client, "badapi@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    resp = client.post("/api/schedules", json={
        "when": "tomorrow please", "task_template": "x",
    }, headers=headers)
    assert resp.status_code == 422


def test_schedules_api_delete_requires_owner(client):
    owner = _register(client, "own@test.com")
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    sid = client.post("/api/schedules", json={
        "when": "daily at 9am", "task_template": "x",
    }, headers=headers).json()["id"]

    intruder = _register(client, "intruder@test.com")
    intruder_headers = {"Authorization": f"Bearer {intruder['access_token']}"}
    assert client.delete(f"/api/schedules/{sid}", headers=intruder_headers).status_code == 404


def test_notifications_api(client):
    data = _register(client, "ntf@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    uid = data["user"]["id"]

    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Take a break", text="every 2 hours",
        notify_only=True, now=NOW,
    )
    fired = asyncio.run(scheduler_service.run_schedule(row.id, now=NOW))

    assert client.get("/api/notifications/unread-count", headers=headers).json() == {"count": 1}
    listings = client.get("/api/notifications", headers=headers).json()
    assert len(listings) == 1
    assert listings[0]["kind"] == "reminder"
    assert listings[0]["read"] is False
    assert listings[0]["occurrence_id"] == fired["occurrence_id"]
    assert listings[0]["schedule_id"] == row.id

    nid = listings[0]["id"]
    mark = client.post(f"/api/notifications/{nid}/read", headers=headers)
    assert mark.status_code == 200
    assert client.get("/api/notifications/unread-count", headers=headers).json() == {"count": 0}

    # unread_only filter after marking all
    with SessionLocal() as db:
        notify_row = notifications_service.notify(
            db, user_id=uid, text="second", kind="system",
        )
        assert notify_row.id
        db.commit()
    assert client.get("/api/notifications/unread-count", headers=headers).json() == {"count": 1}
    all_resp = client.post("/api/notifications/read-all", headers=headers)
    assert all_resp.status_code == 200
    assert all_resp.json()["marked"] == 1
    assert client.get("/api/notifications", headers=headers).json()[0]["read"] is True


# --- daily briefing (§62) ------------------------------------------------------

def test_daily_briefing_composes(client):
    data = _register(client, "brief@test.com")
    uid = data["user"]["id"]
    scheduler_service.create_schedule(
        user_id=uid, task_template="Morning standup", text="every day at 8am",
        notify_only=True, now=NOW,
    )
    row = scheduler_service.create_schedule(
        user_id=uid, task_template="Weekly review", text="every week",
        notify_only=True, now=NOW,
    )
    scheduler_service.create_schedule(
        user_id=uid, task_template="Flight tomorrow", text="2026-10-09T06:30",
        notify_only=True, now=NOW,
    )
    with SessionLocal() as db:
        db.add(Approval(
            user_id=uid, run_id="run1", tool_name="gmail_send",
            arguments={"to": "a@b.c"}, kind="tool", status="pending",
        ))
        db.commit()

    set_tool_context(ToolContext(user_id=uid, run_id="r", task_id="t",
                                 conversation_id="c"))
    try:
        result = asyncio.run(daily_briefing(now=NOW))
    finally:
        set_tool_context(None)

    assert result.success
    briefing = result.data["briefing"]
    assert "# Daily briefing" in briefing
    assert "Morning standup" in briefing
    assert "Weekly review" in briefing
    assert "Flight tomorrow" in briefing
    assert "gmail_send" in briefing  # pending approval
    assert "no tasks today" in briefing