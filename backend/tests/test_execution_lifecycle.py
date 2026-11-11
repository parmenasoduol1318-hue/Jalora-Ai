from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.database.session import SessionLocal
from app.context import ToolContext, bind_tool_execution, get_tool_context
from app.models.agent import AgentRun
from app.models.task import Task, TaskStep, TaskStepAttempt
from app.models.user import User
from app.services.execution_contract import (
    ActionOutcome,
    task_lifecycle,
    step_lifecycle,
)
from app.services.execution_lifecycle import (
    begin_tool_attempt,
    finish_tool_attempt,
)
from app.services.chat_service import (
    _finalize_task,
    _guard_unknown_task_outcomes,
)
from app.agents.graph import _is_retryable, _sync_plan
from app.errors import ToolResult
from app.tools.base import PermissionLevel
from app.tools.base import ToolRegistry


def _execution(tool_name: str = "read_tool", side_effect: str = "read_only"):
    with SessionLocal() as db:
        user = User(email="execution@test.com", password_hash="test")
        db.add(user)
        db.flush()
        task = Task(user_id=user.id, goal="Test execution", status="running")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user.id, task_id=task.id, status="running")
        db.add(run)
        db.flush()
        task.run_id = run.id
        step = TaskStep(
            task_id=task.id,
            idx=0,
            description="Run selected capability",
            status="active",
            tool_id=tool_name,
            arguments={"value": 1},
        )
        db.add(step)
        db.commit()
        return task.id, run.id, step.id


def test_legacy_status_adapters_translate_before_enum_parsing():
    assert task_lifecycle("waiting_for_approval").value == "awaiting_approval"
    assert task_lifecycle("running").value == "running"
    assert step_lifecycle("active").value == "running"
    assert step_lifecycle("done").value == "completed"


def test_tool_attempt_is_persisted_before_dispatch_and_reused_after_completion():
    task_id, run_id, step_id = _execution()
    handle = begin_tool_attempt(
        task_id=task_id,
        run_id=run_id,
        tool_call_id="call-1",
        tool_name="read_tool",
        arguments={"value": 1},
        plan_step_index=0,
        side_effect="read_only",
    )
    with SessionLocal() as db:
        attempt = db.get(TaskStepAttempt, handle.attempt_id)
        assert attempt is not None
        assert attempt.state == "dispatching"
        assert attempt.step_id == step_id
        assert attempt.provider_idempotency_key

    raw = json.dumps({"success": True, "message": "done", "data": {"value": 1}})
    result = finish_tool_attempt(
        handle=handle,
        task_id=task_id,
        run_id=run_id,
        raw=raw,
        tool_name="read_tool",
        success=True,
    )
    assert result is not None
    assert result.outcome == ActionOutcome.SUCCEEDED
    with SessionLocal() as db:
        step = db.get(TaskStep, step_id)
        attempt = db.get(TaskStepAttempt, handle.attempt_id)
        assert step is not None and attempt is not None
        assert step.result_data["execution_result"]["step_id"] == step_id
        assert step.result_data["execution_result"]["outcome"] == "succeeded"
        assert step.result_data["execution_result"]["started_at"]
        assert step.result_data["execution_result"]["finished_at"]
        assert attempt.result_data["raw"] == raw

    replay = begin_tool_attempt(
        task_id=task_id,
        run_id=run_id,
        tool_call_id="call-2",
        tool_name="read_tool",
        arguments={"value": 1},
        plan_step_index=0,
        side_effect="read_only",
    )
    assert replay.cached_raw == raw
    assert replay.action_id == handle.action_id
    assert replay.attempt_number == 1


def test_uncertain_side_effect_is_blocked_from_replay():
    task_id, run_id, _step_id = _execution(
        tool_name="send_tool", side_effect="external"
    )
    handle = begin_tool_attempt(
        task_id=task_id,
        run_id=run_id,
        tool_call_id="send-1",
        tool_name="send_tool",
        arguments={"value": 1},
        plan_step_index=0,
        side_effect="external",
    )
    raw = json.dumps({"success": False, "error": {"code": "TIMEOUT"}})
    finish_tool_attempt(
        handle=handle,
        task_id=task_id,
        run_id=run_id,
        raw=raw,
        tool_name="send_tool",
        success=False,
        error_code="TIMEOUT",
        outcome=ActionOutcome.OUTCOME_UNKNOWN,
    )

    replay = begin_tool_attempt(
        task_id=task_id,
        run_id=run_id,
        tool_call_id="send-2",
        tool_name="send_tool",
        arguments={"value": 1},
        plan_step_index=0,
        side_effect="external",
    )
    assert replay.blocked_code == "OUTCOME_UNKNOWN"


def test_tool_context_carries_stable_action_identity():
    context = ToolContext(
        user_id="user",
        run_id="run",
        task_id="task",
        step_id="step",
        action_id="action",
        tool_call_id="call",
        attempt_number=2,
        idempotency_key="key",
    )
    with bind_tool_execution(context):
        bound = get_tool_context()
        assert bound.step_id == "step"
        assert bound.action_id == "action"
        assert bound.attempt_number == 2
        assert bound.idempotency_key == "key"


def test_automatic_retry_is_limited_to_read_only_capabilities():
    transient_failure = ToolResult.fail("TIMEOUT", "timeout")
    assert _is_retryable(
        transient_failure,
        SimpleNamespace(
            permission=PermissionLevel.AUTO,
            side_effect="read_only",
        ),
    )
    assert not _is_retryable(
        transient_failure,
        SimpleNamespace(
            permission=PermissionLevel.AUTO,
            side_effect="external",
        ),
    )


def test_plan_sync_keeps_step_ids_and_dependency_ids_across_reordering():
    with SessionLocal() as db:
        user = User(email="plan-sync@test.com", password_hash="test")
        db.add(user)
        db.flush()
        task = Task(user_id=user.id, goal="Plan sync test", status="running")
        db.add(task)
        db.commit()
        task_id = task.id

    original = [
        {
            "id": "research",
            "description": "Research",
            "tool": "read_tool",
            "arguments": {"query": "source"},
            "dependencies": [],
            "status": "pending",
        },
        {
            "id": "summarize",
            "description": "Summarize",
            "tool": "read_tool",
            "arguments": {"query": "summary"},
            "dependencies": [0],
            "status": "pending",
        },
    ]
    asyncio.run(_sync_plan(task_id, original, 0, 0))
    with SessionLocal() as db:
        initial = db.query(TaskStep).filter_by(task_id=task_id).all()
        ids_by_plan = {step.plan_step_id: step.id for step in initial}
        assert initial[1].dependency_step_ids == [initial[0].id]
        db.get(TaskStep, ids_by_plan["research"]).status = "done"
        db.commit()

    reordered = [
        {
            **original[1],
            "dependencies": [1],
        },
        {
            **original[0],
            "dependencies": [],
        },
    ]
    asyncio.run(_sync_plan(task_id, reordered, 50, 0))
    with SessionLocal() as db:
        final = db.query(TaskStep).filter_by(task_id=task_id).all()
        by_plan = {step.plan_step_id: step for step in final}
        assert by_plan["research"].id == ids_by_plan["research"]
        assert by_plan["summarize"].id == ids_by_plan["summarize"]
        assert by_plan["research"].status == "done"
        assert by_plan["summarize"].dependency_step_ids == [
            ids_by_plan["research"]
        ]


def test_in_flight_cancellation_persists_unknown_outcome(fake_router):
    task_id, run_id, _step_id = _execution(
        tool_name="cancel_action", side_effect="external"
    )
    started = asyncio.Event()
    observed_context = {}

    async def cancel_action(value: str) -> ToolResult:
        context = get_tool_context()
        observed_context.update({
            "step_id": context.step_id,
            "action_id": context.action_id,
            "tool_call_id": context.tool_call_id,
            "attempt_number": context.attempt_number,
            "idempotency_key": context.idempotency_key,
        })
        started.set()
        await asyncio.Event().wait()
        return ToolResult.ok("unreachable")

    registry = ToolRegistry()
    registry.register(
        cancel_action,
        permission=PermissionLevel.AUTO,
        category="test",
        side_effect="external",
        always_available=True,
    )
    fake_router.script.append(AIMessage(
        content="",
        tool_calls=[{
            "name": "cancel_action",
            "args": {"value": "dispatch"},
            "id": "cancel-call",
            "type": "tool_call",
        }],
    ))

    async def run_cancelled_graph():
        from app.agents.graph import build_graph

        graph = build_graph(
            model=fake_router.model,
            registry=registry,
            max_steps=4,
            tool_timeout=30,
            max_tool_calls=2,
            system_prompt="Execute the requested test capability.",
        )
        state = {
            "messages": [
                HumanMessage(content="Run cancel_action now.", id="user-message")
            ],
            "steps": 0,
            "sources": [],
            "pending_approval": None,
            "approved_call_ids": [],
            "rejected_call_ids": [],
            "status": "running",
            "run_id": run_id,
            "user_id": "execution@test.com",
            "task_id": task_id,
            "plan": None,
            "tool_calls": 0,
            "policy": {"autonomy": 5},
        }
        running = asyncio.create_task(graph.ainvoke(state))
        await asyncio.wait_for(started.wait(), timeout=10)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(run_cancelled_graph())
    assert observed_context["step_id"]
    assert observed_context["action_id"]
    assert observed_context["tool_call_id"] == "cancel-call"
    assert observed_context["attempt_number"] == 1
    assert observed_context["idempotency_key"]
    with SessionLocal() as db:
        attempt = db.query(TaskStepAttempt).filter_by(
            run_id=run_id,
            tool_call_id="cancel-call",
        ).one()
        step = db.get(TaskStep, attempt.step_id)
        assert attempt.state == "outcome_unknown"
        assert attempt.outcome_status == "outcome_unknown"
        assert step is not None
        assert step.outcome_status == "outcome_unknown"


def test_unknown_external_outcome_cannot_be_reported_as_completed():
    task_id, _run_id, step_id = _execution(
        tool_name="send_tool", side_effect="external"
    )
    with SessionLocal() as db:
        step = db.get(TaskStep, step_id)
        assert step is not None
        step.outcome_status = "outcome_unknown"
        db.commit()

        status, reply = _guard_unknown_task_outcomes(
            db,
            task_id=task_id,
            status="partial",
            reply="The message was sent.",
        )
        _finalize_task(db, task_id=task_id, status=status, reply=reply)
        db.commit()
        task = db.get(Task, task_id)
    assert status == "partial"
    assert "unknown outcome" in reply
    assert "cannot confirm" in reply
    assert task is not None and task.status == "partial"
    assert task.error and "reconcile" in task.error
