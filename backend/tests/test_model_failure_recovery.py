"""Deterministic recovery checks for model failure after tool execution."""
from __future__ import annotations

import asyncio

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agents.graph import build_graph
from app.errors import ToolResult
from app.tools.base import PermissionLevel, ToolRegistry


def _persisted_task_run(goal: str) -> tuple[str, str, str]:
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun
    from app.models.task import Task
    from app.models.user import User

    with SessionLocal() as db:
        user = User(email="model-recovery@test.com", password_hash="test-hash")
        db.add(user)
        db.flush()
        task = Task(user_id=user.id, goal=goal, status="running")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user.id, task_id=task.id, status="running")
        db.add(run)
        db.flush()
        task.run_id = run.id
        db.commit()
        return user.id, task.id, run.id


def test_model_failure_after_successful_tool_preserves_partial_state(monkeypatch):
    from conftest import Box, FakeModel
    user_id, task_id, run_id = _persisted_task_run(
        "perform the workflow"
    )

    class FailingAfterFirstModel(FakeModel):
        calls: int = 0

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("local model unavailable")
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    async def successful_tool(value: str = "") -> ToolResult:
        return ToolResult.ok("completed", {"value": value})

    registry = ToolRegistry()
    registry.register(successful_tool, permission=PermissionLevel.AUTO)
    model = FailingAfterFirstModel(script=Box([AIMessage(
        content="", tool_calls=[{
            "name": "successful_tool", "args": {"value": "research preserved"},
            "id": "call-1",
        }]
    )]))

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)

    state = {
        "messages": [HumanMessage(content="perform the workflow")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id, "task_id": task_id,
        "plan": None, "tool_calls": 0, "policy": {"autonomy": 2, "tool_policies": {}},
        "intent": "chat",
    }

    async def run():
        graph = build_graph(
            model=model, registry=registry, max_steps=5, tool_timeout=2,
            max_tool_calls=5, max_retries=0, system_prompt="test",
        )
        return await graph.ainvoke(state)

    result = asyncio.run(run())
    assert result["status"] == "partial"
    assert result["recoverable"] is True
    assert "Completed results were preserved" in result["messages"][-1].content
    assert any("research preserved" in str(message.content) for message in result["messages"])

    from app.services.chat_service import _state_for_resume

    resumable = _state_for_resume(result)
    assert resumable["status"] == "running"
    assert resumable["force_final"] is False
    assert resumable["recoverable"] is False
    assert len(resumable["messages"]) == len(result["messages"]) - 1
    assert any("research preserved" in str(message.content) for message in resumable["messages"])


def test_model_outage_after_cbk_research_finishes_from_official_evidence():
    from conftest import FakeModel
    from app.agents.graph import _official_cbr_fallback

    request = (
        "Search for the current Central Bank of Kenya Central Bank Rate CBR "
        "announced by the Monetary Policy Committee and provide the exact "
        "current percentage rate."
    )
    official_url = (
        "https://www.centralbank.go.ke/2026/10/07/mpc-retains-the-cbr-at-8-75/"
    )
    messages = [
        HumanMessage(content=request),
        AIMessage(content="", tool_calls=[{
            "name": "search_web",
            "args": {"query": request},
            "id": "cbk-search",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Found official result.",
                {"results": [{
                    "title": "MPC retains the CBR at 8.75 percent",
                    "url": official_url,
                    "snippet": (
                        "The Monetary Policy Committee retained the Central "
                        "Bank Rate (CBR) at 8.75 percent."
                    ),
                }]},
            ).model_dump_json(),
            tool_call_id="cbk-search",
        ),
        AIMessage(content="", tool_calls=[{
            "name": "open_url",
            "args": {"url": official_url},
            "id": "cbk-open",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Opened official release.",
                {
                    "url": official_url,
                    "title": "MPC retains the CBR at 8.75 percent",
                    "text": "The MPC retained the Central Bank Rate at 8.75 percent.",
                },
            ).model_dump_json(),
            tool_call_id="cbk-open",
        ),
        AIMessage(content="The model became unavailable after research."),
    ]
    plan = [
        {"id": "search", "tool": "search_web", "status": "done"},
        {"id": "open", "tool": "open_url", "status": "done"},
        {
            "id": "answer",
            "kind": "response",
            "status": "pending",
            "dependencies": [],
        },
    ]
    state = {
        "messages": messages,
        "steps": 2,
        "sources": [],
        "artifacts": [],
        "pending_approval": None,
        "approved_call_ids": [],
        "rejected_call_ids": [],
        "status": "partial",
        "recoverable": True,
        "force_final": True,
        "run_id": "cbk-recovery-run",
        "user_id": "cbk-recovery-user",
        "task_id": "",
        "plan": plan,
        "tool_calls": 2,
        "policy": {"autonomy": 2, "tool_policies": {}},
        "intent": "research",
        "request": request,
    }

    result = asyncio.run(build_graph(
        model=FakeModel(),
        registry=ToolRegistry(),
        max_steps=5,
        tool_timeout=2,
        max_tool_calls=5,
        max_retries=0,
        system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert "8.75%" in result["messages"][-1].content
    assert official_url in result["messages"][-1].content
    assert result["plan"][-1]["status"] == "done"
    assert _official_cbr_fallback(request, messages) is not None


def test_buupass_search_result_is_finalized_without_a_second_model_call(monkeypatch):
    from conftest import Box, FakeModel

    user_id, task_id, run_id = _persisted_task_run(
        "Show BuuPass bus options."
    )

    class FirstCallOnlyModel(FakeModel):
        calls: int = 0

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("BuuPass result should not need another model call")
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    async def search_buses(origin: str, destination: str, date: str) -> ToolResult:
        return ToolResult.ok(
            "Verified BuuPass options. Reply with the option number you want.",
            {"provider": "buupass", "status": "live_inventory", "offers": [{"id": 1}]},
        )

    registry = ToolRegistry()
    registry.register(search_buses, permission=PermissionLevel.AUTO)
    model = FirstCallOnlyModel(script=Box([AIMessage(
        content="", tool_calls=[{
            "name": "search_buses",
            "args": {
                "origin": "Mombasa",
                "destination": "Nairobi",
                "date": "2026-10-31",
            },
            "id": "bus-search-1",
        }]
    )]))

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    state = {
        "messages": [HumanMessage(content="Show BuuPass bus options.")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "task",
    }

    async def run():
        graph = build_graph(
            model=model, registry=registry, max_steps=5, tool_timeout=2,
            max_tool_calls=5, max_retries=0, system_prompt="test",
        )
        return await graph.ainvoke(state)

    result = asyncio.run(run())

    assert result["status"] == "completed"
    assert result["messages"][-1].content == (
        "Verified BuuPass options. Reply with the option number you want."
    )
    assert model.calls == 1


def test_model_outage_explains_exhausted_provider_credits():
    from app.agents.graph import _model_outage_message

    message = _model_outage_message(
        [HumanMessage(content="Complete the investment workflow.")],
        RuntimeError("openai API error 429: You have no credits remaining"),
    )

    assert "usage credits are exhausted" in message
    assert "remaining steps were not executed" in message
    assert "resume this task" in message.casefold()
    assert "openai API error" not in message


def test_model_outage_explains_temporary_provider_rate_limit():
    from app.agents.graph import _model_outage_message

    message = _model_outage_message(
        [HumanMessage(content="Complete the investment workflow.")],
        RuntimeError("openrouter in_flight_budget_exhausted; Retry-After: 120"),
    )

    assert "temporarily rate-limited" in message
    assert "Wait for the provider limit to reset" in message
    assert "resume this task" in message.casefold()


def test_model_outage_explains_provider_timeout_after_completed_actions():
    from app.agents.graph import _model_outage_message

    message = _model_outage_message(
        [HumanMessage(content="Complete the outreach task.")],
        RuntimeError("ReadTimeout from model provider"),
    )

    assert "AI provider timed out" in message
    assert "Completed results were preserved" in message
    assert "resume this task" in message.casefold()


def test_model_outage_explains_prompt_context_limit():
    from app.agents.graph import _model_outage_message

    message = _model_outage_message(
        [HumanMessage(content="Complete the outreach task.")],
        RuntimeError("Prompt tokens limit exceeded: 11527 > 8760"),
    )

    assert "exceeded its prompt/context limit" in message
    assert "Completed results were preserved" in message
    assert "larger context window" in message


def test_multi_step_task_recovers_when_model_cannot_format_a_plan(monkeypatch):
    from app.tools.plan import update_plan
    from conftest import Box, FakeModel

    user_id, task_id, run_id = _persisted_task_run(
        "Find roles and prepare a summary."
    )
    calls = []

    async def find_roles(query: str) -> ToolResult:
        calls.append(("find_roles", query))
        return ToolResult.ok("Found current roles.")

    async def create_summary(content: str) -> ToolResult:
        calls.append(("create_summary", content))
        return ToolResult.ok("Summary created.")

    registry = ToolRegistry()
    registry.register(
        find_roles,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    registry.register(create_summary, permission=PermissionLevel.AUTO)
    registry.register(update_plan, permission=PermissionLevel.AUTO)
    model = FakeModel(script=Box([
        AIMessage(content="I cannot create a validated plan."),
        AIMessage(content="", tool_calls=[{
            "name": "find_roles",
            "args": {"query": "software engineering roles in Kenya"},
            "id": "recovery-search",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "create_summary",
            "args": {"content": "Found current roles."},
            "id": "recovery-summary",
        }]),
        AIMessage(content="I found the roles and prepared the summary."),
    ]))

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    state = {
        "messages": [HumanMessage(
            content="Find current software engineering roles in Kenya and prepare a summary."
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "task",
    }

    async def run():
        graph = build_graph(
            model=model,
            registry=registry,
            tool_names=["find_roles", "create_summary", "update_plan"],
            max_steps=5,
            tool_timeout=2,
            max_tool_calls=5,
            max_retries=0,
            system_prompt="test",
        )
        return await graph.ainvoke(state)

    result = asyncio.run(run())

    assert result["status"] == "completed"
    assert calls == [
        ("find_roles", "software engineering roles in Kenya"),
        ("create_summary", "Found current roles."),
    ]
    assert result["plan_recovery"] is True
    assert [step["tool"] for step in result["plan"]] == [
        "find_roles",
        "create_summary",
    ]
    assert all(step["status"] == "done" for step in result["plan"])
    assert "could not create a validated plan" not in str(
        result["messages"][-1].content
    ).lower()
    assert any(
        isinstance(message, SystemMessage)
        and "Planning pass" in str(message.content)
        for turn in model.tape.items
        for message in turn
    )
    assert any(
        isinstance(message, SystemMessage)
        and "plan formatter did not produce a usable plan" in str(message.content).lower()
        for turn in model.tape.items
        for message in turn
    )


def test_forced_final_without_a_plan_dispatches_recovery_tool_calls(monkeypatch):
    from app.tools.plan import update_plan
    from conftest import Box, FakeModel

    user_id, task_id, run_id = _persisted_task_run(
        "Find roles and prepare a summary."
    )
    calls = []

    async def find_roles(query: str) -> ToolResult:
        calls.append(query)
        return ToolResult.ok("Found current software engineering roles.")

    registry = ToolRegistry()
    registry.register(
        find_roles,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    registry.register(update_plan, permission=PermissionLevel.AUTO)
    model = FakeModel(script=Box([
        AIMessage(content="", tool_calls=[{
            "name": "find_roles",
            "args": {"query": "software engineering roles in Kenya"},
            "id": "forced-final-recovery-search",
        }]),
        AIMessage(content="I found the current software engineering roles."),
    ]))

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    state = {
        "messages": [HumanMessage(
            content=(
                "Find current software engineering roles in Kenya and prepare "
                "a summary."
            )
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id,
        "user_id": user_id,
        "task_id": task_id,
        "force_final": True,
        "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "task",
    }

    result = asyncio.run(build_graph(
        model=model,
        registry=registry,
        tool_names=["find_roles", "update_plan"],
        max_steps=5,
        tool_timeout=2,
        max_tool_calls=5,
        max_retries=0,
        system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert calls == ["software engineering roles in Kenya"]
    assert result["plan_recovery"] is True
    assert result["plan"][0]["tool"] == "find_roles"
    assert result["plan"][0]["status"] == "done"
    assert "couldn't safely start the requested multi-step task" not in str(
        result["messages"][-1].content
    ).lower()


def test_old_turn_capability_rejection_does_not_block_current_continuation():
    from conftest import Box, FakeModel

    historical_tool_call = AIMessage(content="", tool_calls=[{
        "name": "search_web",
        "args": {"query": "previous task"},
        "id": "old-search",
    }])
    historical_rejection = ToolMessage(
        content=ToolResult.fail(
            "CAPABILITY_NOT_SELECTED",
            "The previous request did not bind search_web.",
        ).model_dump_json(),
        tool_call_id="old-search",
    )
    model = FakeModel(script=Box([
        AIMessage(content="I am continuing the task with the available tools.")
    ]))
    state = {
        "messages": [
            HumanMessage(content="Original multi-step research task."),
            historical_tool_call,
            historical_rejection,
            AIMessage(content="The previous run was blocked."),
            HumanMessage(content="Continue from where you left."),
        ],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "current-turn-guard", "user_id": "recovery-user",
        "task_id": "", "plan": None, "tool_calls": 0,
        "request": "Continue from where you left.",
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "chat",
    }

    result = asyncio.run(build_graph(
        model=model, registry=ToolRegistry(), max_steps=5,
        tool_timeout=2, max_tool_calls=5, max_retries=0,
        system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert result["messages"][-1].content == (
        "I am continuing the task with the available tools."
    )


def test_graph_reuses_prior_outreach_style_for_name_correction(monkeypatch):
    from conftest import Box, FakeModel

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    model = FakeModel(script=Box([AIMessage(
        content="I will prepare separate custom drafts with the corrected name."
    )]))
    state = {
        "messages": [
            HumanMessage(content=(
                "Find public contacts for the organizations and draft custom "
                "outreach emails for each."
            )),
            AIMessage(content="I found the contact addresses."),
            HumanMessage(content=(
                "The app is Vox Kenta, not the old name. Replace it in all the "
                "drafts and write emails to all of them."
            )),
        ],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "outreach-name-correction", "user_id": "recovery-user",
        "task_id": "", "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "task",
    }

    result = asyncio.run(build_graph(
        model=model, registry=ToolRegistry(), max_steps=5,
        tool_timeout=2, max_tool_calls=5, max_retries=0, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert result["messages"][-1].content.startswith("I will prepare separate custom")
    assert len(model.tape) == 1


def test_successful_planned_draft_is_done_when_next_model_call_times_out(
    monkeypatch,
):
    from conftest import Box, FakeModel

    class FailingAfterDraft(FakeModel):
        calls: int = 0

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("provider timed out after the draft")
            async for chunk in super()._astream(
                messages, stop, run_manager, **kwargs
            ):
                yield chunk

    async def save_draft(
        to: str, subject: str, body: str
    ) -> ToolResult:
        return ToolResult.ok(f"Draft saved for {to}.")

    registry = ToolRegistry()
    registry.register(save_draft, permission=PermissionLevel.AUTO)
    model = FailingAfterDraft(script=Box([AIMessage(
        content="", tool_calls=[{
            "name": "save_draft",
            "args": {
                "to": "eacc@example.org",
                "subject": "Vox Kenta partnership",
                "body": "Hello EACC. Introducing Vox Kenta.",
            },
            "id": "save-eacc",
        }],
    )]))
    state = {
        "messages": [HumanMessage(
            content="Draft emails to EACC and Global Innovation Fund."
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "outreach-draft-provider-timeout", "user_id": "recovery-user",
        "task_id": "", "plan": [
            {
                "id": "eacc",
                "description": "Compose EACC email",
                "tool": "save_draft",
                "status": "pending",
                "dependencies": [],
            },
            {
                "id": "gif",
                "description": "Compose Global Innovation Fund email",
                "tool": "save_draft",
                "status": "pending",
                "dependencies": [],
            },
        ],
        "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}},
        "intent": "task",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=5, tool_timeout=2,
        max_tool_calls=5, max_retries=0, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "partial"
    assert [(step["id"], step["status"]) for step in result["plan"]] == [
        ("eacc", "done"),
        ("gif", "pending"),
    ]
    message = result["messages"][-1].content
    assert "Still pending: Compose Global Innovation Fund email." in message
    assert "Compose EACC email" not in message
    assert "timed out while continuing" in message


def test_historical_tool_result_does_not_hide_current_turn_model_failure(monkeypatch):
    import pytest

    from conftest import FakeModel

    class UnavailableModel(FakeModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("current turn provider unavailable")
            yield

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    state = {
        "messages": [
            HumanMessage(content="Earlier task."),
            AIMessage(content="", tool_calls=[{
                "name": "old_tool", "args": {}, "id": "old-call",
            }]),
            ToolMessage(
                content=ToolResult.ok("Earlier result.").model_dump_json(),
                tool_call_id="old-call",
            ),
            AIMessage(content="Earlier task completed."),
            HumanMessage(content="Who is the current Kenyan president?"),
        ],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "historical-tool-run", "user_id": "recovery-user",
        "task_id": "historical-tool-task", "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "web_search",
    }

    async def run():
        graph = build_graph(
            model=UnavailableModel(), registry=ToolRegistry(),
            max_steps=5, tool_timeout=2, max_tool_calls=5, max_retries=0,
            system_prompt="test",
        )
        return await graph.ainvoke(state)

    with pytest.raises(RuntimeError, match="current turn provider unavailable"):
        asyncio.run(run())


def test_model_turn_fallback_discards_failed_candidate_output(monkeypatch):
    from conftest import Box, FakeModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    from app.events import ListEmitter, set_emitter

    class FailingAfterPartialModel(FakeModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="uncommitted primary text")
            )
            raise RuntimeError("primary provider unavailable")

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    primary = FailingAfterPartialModel()
    fallback = FakeModel(script=Box([AIMessage(content="fallback completed")]))
    emitter = ListEmitter()
    set_emitter(emitter)
    state = {
        "messages": [HumanMessage(content="answer the request")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "fallback-run", "user_id": "fallback-user", "task_id": "fallback-task",
        "plan": None, "tool_calls": 0, "policy": {"autonomy": 2, "tool_policies": {}},
        "intent": "chat",
    }

    async def run():
        graph = build_graph(
            model=primary, fallback_models=[fallback], registry=ToolRegistry(),
            max_steps=5, tool_timeout=2, max_tool_calls=5, max_retries=0,
            system_prompt="test",
        )
        return await graph.ainvoke(state)

    try:
        result = asyncio.run(run())
    finally:
        set_emitter(None)

    assert result["status"] == "completed"
    assert result["messages"][-1].content == "fallback completed"
    token_text = "".join(event["text"] for event in emitter.of_type("token"))
    assert token_text == "fallback completed"


def test_planned_workflow_falls_back_when_primary_cannot_plan_or_act(monkeypatch):
    from conftest import Box, FakeModel

    from app.tools import registry

    user_id, task_id, run_id = _persisted_task_run(
        "Get the current date and create a summary."
    )
    primary = FakeModel(script=Box([
        AIMessage(content="I cannot make a plan."),
        AIMessage(content="I cannot execute the plan."),
    ]))
    fallback = FakeModel(script=Box([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {"description": "Get the current date", "tool": "current_datetime"},
                {
                    "description": "Summarize the date",
                    "kind": "response",
                    "dependencies": ["step_1"],
                },
            ]},
            "id": "plan-fallback",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "current_datetime", "args": {},
            "id": "date-fallback", "type": "tool_call",
        }]),
    ]))
    state = {
        "messages": [HumanMessage(content=(
            "Get the current date and create a summary."
        ))],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "chat",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)

    import asyncio

    from app.agents.graph import build_graph

    graph = build_graph(
        model=primary, fallback_models=[fallback], registry=registry,
        max_steps=5, tool_timeout=2, max_tool_calls=5, max_retries=0,
        system_prompt="test",
    )
    result = asyncio.run(graph.ainvoke(state))

    assert result["status"] == "completed"
    assert result["plan"][0]["status"] == "done"
    assert any(
        isinstance(message, AIMessage)
        and any(call.get("name") == "current_datetime" for call in message.tool_calls)
        for message in result["messages"]
    )


def test_empty_model_stream_retries_before_abandoning_planning():
    from conftest import Box, FakeModel
    from app.tools import registry

    user_id, task_id, run_id = _persisted_task_run(
        "Get the current date and create a summary."
    )
    class EmptyOnceModel(FakeModel):
        empty_once: bool = True

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            if self.empty_once:
                self.empty_once = False
                return
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    model = EmptyOnceModel(script=Box([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {"description": "Get the current date", "tool": "current_datetime"},
                {
                    "description": "Summarize the date",
                    "kind": "response",
                    "dependencies": ["step_1"],
                },
            ]},
            "id": "plan-after-empty",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "current_datetime", "args": {},
            "id": "date-after-empty", "type": "tool_call",
        }]),
        AIMessage(content="The current date is verified."),
    ]))
    state = {
        "messages": [HumanMessage(content="Get the current date and create a summary.")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "chat",
    }

    import asyncio

    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=5, tool_timeout=2,
        max_tool_calls=5, max_retries=0, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert result["plan"][0]["status"] == "done"


def test_partial_model_outage_persists_recoverable_task_state(
    client, fake_router, monkeypatch,
):
    from app.services import chat_service
    from conftest import Box, FakeModel
    from test_agent_phase_b import _tc, _token

    monkeypatch.setattr(
        chat_service, "_fallback_models_for_turn", lambda *args, **kwargs: [],
    )

    class FailingAfterToolModel(FakeModel):
        calls: int = 0
        fail_after_first: bool = True

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if self.fail_after_first and self.calls > 1:
                raise RuntimeError("provider unavailable")
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    model = FailingAfterToolModel(script=Box([AIMessage(
        content="",
        tool_calls=[_tc("update_plan", {"steps": [
            {"description": "Research local LLM production practices"},
            {"description": "Prepare a summary"},
        ]}, "recover-plan")],
    )]))
    fake_router.model = model
    headers = _token(client, "recoverable-workflow@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Plan research on local LLM production practices."},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    partial = response.json()
    assert partial["status"] == "partial"

    task = client.get("/api/tasks", headers=headers).json()[0]
    assert task["status"] == "partial"
    assert task["resumable"] is True

    model.fail_after_first = False
    model.script.append(AIMessage(content="Research continued and completed."))
    resumed = client.post(
        f"/api/runs/{partial['run_id']}/resume", headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    task = client.get("/api/tasks", headers=headers).json()[0]
    assert task["resumable"] is False


def test_fallback_candidates_preserve_provider_and_privacy_boundaries(monkeypatch):
    from app.services import chat_service
    from app.services.model_router import ProviderSpec, TaskRoute

    primary = TaskRoute(
        ProviderSpec("openai", "openai", "https://api.openai.com/v1", "k", "m", "m"),
        "m", "auto (simple, text)",
    )
    fallback = TaskRoute(
        ProviderSpec("groq", "openai", "https://api.groq.com/openai/v1", "k", "g", "g"),
        "g", "chain:groq",
    )
    local_fallback = TaskRoute(
        ProviderSpec("lmstudio", "openai", "http://localhost:1234/v1", None, "l", "l", False),
        "l", "chain:lmstudio",
    )

    class Router:
        def __init__(self):
            self.called_with = None

        def route_chain(self, **kwargs):
            self.called_with = kwargs
            disabled = set(kwargs.get("disabled", []))
            return [route for route in (primary, fallback, local_fallback)
                    if route.provider.name not in disabled]

        def get_model(self, route):
            return route.provider.name

    monkeypatch.setattr(
        chat_service, "_apply_decoding_profile", lambda model, intent: model
    )
    router = Router()
    models = chat_service._fallback_models_for_turn(
        router, primary, message="research", modality="text", intent="research",
        provider_locked=False, selected_model=None, disabled=[],
    )
    assert router.called_with["disabled"] == []
    assert router.called_with["provider_override"] == "openai"
    assert models == ["groq", "lmstudio"]
    disabled_models = chat_service._fallback_models_for_turn(
        router, primary, message="research", modality="text", intent="research",
        provider_locked=False, selected_model=None, disabled=["groq"],
    )
    assert router.called_with["disabled"] == ["groq"]
    assert disabled_models == ["lmstudio"]

    assert chat_service._fallback_models_for_turn(
        router, primary, message="research", modality="text", intent="research",
        provider_locked=True, selected_model=None, disabled=[],
    ) == []
    assert chat_service._fallback_models_for_turn(
        router, primary, message="research", modality="text", intent="research",
        provider_locked=False, selected_model="pinned-model", disabled=[],
    ) == []
    local = TaskRoute(
        ProviderSpec("ollama", "openai", "http://localhost:11434/v1", None, "q", "q", False),
        "q", "auto (simple, text)",
    )
    local_fallbacks = chat_service._fallback_models_for_turn(
        router, local, message="research", modality="text", intent="research",
        provider_locked=False, selected_model=None, disabled=[],
    )
    assert local_fallbacks == ["lmstudio"]


def test_model_fallback_after_tool_does_not_replay_completed_tool(monkeypatch):
    from conftest import Box, FakeModel

    user_id, task_id, run_id = _persisted_task_run(
        "Run the successful tool and recover."
    )
    calls = 0

    async def successful_tool() -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.ok("completed once")

    class FailingAfterToolModel(FakeModel):
        calls: int = 0

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("preferred provider unavailable")
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    registry = ToolRegistry()
    registry.register(successful_tool, permission=PermissionLevel.AUTO)
    primary = FailingAfterToolModel(script=Box([AIMessage(
        content="", tool_calls=[{
            "name": "successful_tool", "args": {}, "id": "once",
        }]
    )]))
    fallback = FakeModel(script=Box([AIMessage(content="workflow completed")]))
    state = {
        "messages": [HumanMessage(content="perform the workflow")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "chat",
    }

    async def run():
        graph = build_graph(
            model=primary, fallback_models=[fallback], registry=registry,
            max_steps=5, tool_timeout=2, max_tool_calls=5, max_retries=0,
            system_prompt="test",
        )
        return await graph.ainvoke(state)

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["messages"][-1].content == "workflow completed"
    assert calls == 1


def test_failed_provider_is_not_retried_during_the_same_workflow(monkeypatch):
    from conftest import Box, FakeModel
    from app.services.model_router import ProviderSpec

    user_id, task_id, run_id = _persisted_task_run("Do a quick check.")
    tool_calls = {"first": 0, "second": 0}

    async def first_tool() -> ToolResult:
        tool_calls["first"] += 1
        return ToolResult.ok("first step complete")

    async def second_tool() -> ToolResult:
        tool_calls["second"] += 1
        return ToolResult.ok("second step complete")

    class UnavailablePreferredModel(FakeModel):
        calls: int = 0

        @property
        def spec(self):
            return ProviderSpec(
                "preferred", "openai", "https://preferred.example/v1",
                "key", "model", "model",
            )

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            if False:
                yield None
            raise RuntimeError("preferred provider unavailable")

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    registry = ToolRegistry()
    registry.register(first_tool, permission=PermissionLevel.AUTO)
    registry.register(second_tool, permission=PermissionLevel.AUTO)
    primary = UnavailablePreferredModel()
    fallback = FakeModel(script=Box([
        AIMessage(content="", tool_calls=[{
            "name": "first_tool", "args": {}, "id": "first",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "second_tool", "args": {}, "id": "second",
        }]),
        AIMessage(content="workflow completed"),
    ]))
    state = {
        "messages": [HumanMessage(content="Do a quick check.")],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": run_id, "user_id": user_id,
        "task_id": task_id, "plan": None, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "chat",
    }

    async def run():
        graph = build_graph(
            model=primary, fallback_models=[fallback], registry=registry,
            max_steps=5, tool_timeout=2, max_tool_calls=5, max_retries=0,
            system_prompt="test",
        )
        return await graph.ainvoke(state)

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["messages"][-1].content == "workflow completed"
    assert primary.calls == 1
    assert tool_calls == {"first": 1, "second": 1}


def test_planned_workflow_can_continue_after_model_responds_without_tool_calls(
    monkeypatch,
):
    from conftest import Box, FakeModel

    calls = []

    async def lookup_contact(query: str) -> ToolResult:
        calls.append(("lookup", query))
        return ToolResult.ok("contact lookup completed")

    registry = ToolRegistry()
    registry.register(
        lookup_contact,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    model = FakeModel(script=Box([
        AIMessage(content="I will continue with the planned lookups."),
        AIMessage(content="The next action is ready."),
        AIMessage(content="I am checking the remaining plan."),
        AIMessage(content="Continuing with the verified pending action."),
        AIMessage(content="", tool_calls=[{
            "name": "lookup_contact",
            "args": {"query": "organization contact"},
            "id": "lookup-after-non-tool-response",
        }]),
        AIMessage(content="The planned lookup is complete."),
    ]))
    plan = [{
        "id": "lookup",
        "description": "Look up the organization's public contact",
        "tool": "lookup_contact",
        "arguments": {"query": "organization contact"},
        "dependencies": [],
        "status": "pending",
    }]
    state = {
        "messages": [HumanMessage(
            content="Research the public contact and prepare a response."
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "pending-plan-continuation", "user_id": "recovery-user",
        "task_id": "", "plan": plan, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "research",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=24, tool_timeout=2,
        max_tool_calls=24, max_retries=5, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert calls == [("lookup", "organization contact")]


def test_public_contact_refusal_retries_until_search_tool_is_called(monkeypatch):
    from conftest import Box, FakeModel

    calls = []

    async def search_public_contacts(organizations: list[str]) -> ToolResult:
        calls.extend(organizations)
        return ToolResult.ok("Public contact search completed.")

    registry = ToolRegistry()
    registry.register(
        search_public_contacts,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    model = FakeModel(script=Box([
        AIMessage(content=(
            "I cannot look up external email addresses. Please provide them."
        )),
        AIMessage(content="", tool_calls=[{
            "name": "search_public_contacts",
            "args": {"organizations": ["Transparency International Kenya"]},
            "id": "public-contact-search",
        }]),
        AIMessage(content="The public contact lookup was completed."),
    ]))
    state = {
        "messages": [HumanMessage(
            content=(
                "Find the public contact email for Transparency International "
                "Kenya."
            )
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "public-contact-required-search", "user_id": "recovery-user",
        "task_id": "", "plan": [{
            "id": "contacts",
            "description": "Find the organization's public contact email",
            "tool": "search_public_contacts",
            "arguments": {
                "organizations": ["Transparency International Kenya"],
            },
            "dependencies": [],
            "status": "pending",
        }],
        "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}},
        "intent": "research",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=8, tool_timeout=2,
        max_tool_calls=8, max_retries=1, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert calls == ["Transparency International Kenya"]
    assert "cannot look up external email addresses" not in (
        result["messages"][-1].content.lower()
    )
    assert result["plan"][0]["status"] == "done"


def test_nine_planned_actions_fit_within_the_default_workflow_budget(monkeypatch):
    from conftest import Box, FakeModel
    from app.config import Settings

    assert Settings.model_fields["agent_max_steps"].default == 40
    assert Settings.model_fields["agent_max_tool_calls"].default == 40
    assert Settings.model_fields["agent_max_retries"].default == 5

    completed_queries = []
    draft_calls = []

    async def search_contact(query: str) -> ToolResult:
        completed_queries.append(query)
        return ToolResult.ok("official contact search completed")

    async def compose_drafts() -> ToolResult:
        draft_calls.append("composed")
        return ToolResult.ok("drafts composed")

    registry = ToolRegistry()
    registry.register(
        search_contact,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    registry.register(compose_drafts, permission=PermissionLevel.AUTO)

    search_names = [
        "Transparency International Kenya",
        "Code for Africa",
        "Katiba Institute",
        "Open Contracting Partnership",
        "Kituo Cha Sheria",
        "ARTICLE 19 Eastern Africa",
        "Ethics and Anti-Corruption Commission",
        "Global Innovation Fund",
    ]
    plan = [
        {
            "id": f"search_{index}",
            "description": f"Search public contact for {name}",
            "tool": "search_contact",
            "arguments": {"query": name},
            "dependencies": [],
            "status": "pending",
        }
        for index, name in enumerate(search_names)
    ]
    plan.append({
        "id": "drafts",
        "description": "Compose a separate draft for each verified contact",
        "tool": "compose_drafts",
        "dependencies": list(range(len(search_names))),
        "status": "pending",
    })
    model = FakeModel(script=Box([
        AIMessage(content="", tool_calls=[
            {
                "name": "search_contact",
                "args": {"query": name},
                "id": f"search-{index}",
            }
            for index, name in enumerate(search_names)
        ]),
        AIMessage(content="", tool_calls=[{
            "name": "compose_drafts", "args": {}, "id": "compose-all-drafts",
        }]),
        AIMessage(content="All eight contact searches and the draft step completed."),
    ]))
    state = {
        "messages": [HumanMessage(
            content="Find public contact emails for eight organizations and draft "
                    "individual outreach emails."
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "multi-step-budget", "user_id": "recovery-user",
        "task_id": "", "plan": plan, "tool_calls": 0,
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "research",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=24, tool_timeout=2,
        max_tool_calls=24, max_retries=0, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "completed"
    assert completed_queries == search_names
    assert draft_calls == ["composed"]
    assert all(step["status"] == "done" for step in result["plan"])


def test_unfinished_plan_is_persisted_as_resumable_when_step_budget_is_reached(
    monkeypatch,
):
    from conftest import Box, FakeModel
    from app.agents.graph import deserialize_state, serialize_state

    calls = []

    async def lookup_contact(query: str) -> ToolResult:
        calls.append(query)
        return ToolResult.ok("lookup completed")

    registry = ToolRegistry()
    registry.register(
        lookup_contact,
        permission=PermissionLevel.AUTO,
        side_effect="read_only",
    )
    model = FakeModel(script=Box([AIMessage(
        content="", tool_calls=[{
            "name": "lookup_contact",
            "args": {"query": "organization contact"},
            "id": "lookup-budget-limited",
        }],
    )]))
    state = {
        "messages": [HumanMessage(
            content="Research public contacts and draft an outreach message."
        )],
        "steps": 0, "sources": [], "artifacts": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [], "status": "running",
        "run_id": "resumable-plan", "user_id": "recovery-user",
        "task_id": "", "tool_calls": 0,
        "plan": [{
            "id": "lookup",
            "description": "Look up the organization's public contact",
            "tool": "lookup_contact",
            "arguments": {"query": "organization contact"},
            "dependencies": [],
            "status": "pending",
        }],
        "policy": {"autonomy": 2, "tool_policies": {}}, "intent": "research",
    }

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)
    result = asyncio.run(build_graph(
        model=model, registry=registry, max_steps=0, tool_timeout=2,
        max_tool_calls=24, max_retries=0, system_prompt="test",
    ).ainvoke(state))

    assert result["status"] == "partial"
    assert result["recoverable"] is True
    assert calls == []
    assert "resume this task" in result["messages"][-1].content.lower()
    restored = deserialize_state(serialize_state(result))
    assert restored["recoverable"] is True
    assert restored["plan"][0]["status"] == "pending"
