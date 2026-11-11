"""Live in-app voice call (Phase 1): session pipeline, VAD, confirmation gate,
config surface and persistence — all offline via the mock STT/TTS providers.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import FAKE_SPEC, Box, FakeModel, FakeRouter
from fastapi.testclient import TestClient  # noqa: F401  (typing aid)
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk  # noqa: F401
from pydantic import Field  # noqa: E401
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.call import Call
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services.model_router import TaskRoute
from app.services.voice_providers import MockSTT, MockTTS, STTChain, TTSChain
from app.services.voice_session import ENERGY_RMS, SILENCE_MS, VoiceSession


def _loud(ms: int = 1000, rate: int = 16000) -> bytes:
    """A frame of tone loud enough to clear the energy VAD."""
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    wave = (np.sin(2 * np.pi * 220 * t) * (ENERGY_RMS * 4)).astype("<i2")
    return wave.tobytes()


def _first_user_id() -> str:
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).limit(1))
    assert uid is not None
    return uid


def _session(user_id: str, *, phrase: str = "hello there"):
    events: list[dict] = []

    async def send(ev: dict) -> None:
        events.append(ev)

    session = VoiceSession.create(
        user_id=user_id, send=send,
        stt=STTChain([MockSTT(phrase)]),
        tts=TTSChain([MockTTS()]),
        purpose="unit test call",
    )
    return session, events


def test_voice_config_reports_server_live(client, auth_headers):
    resp = client.get("/api/voice/config", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tts"] == "server"
    assert data["live"] is True
    assert data["sample_rate"] == 16000
    assert data["tts_sample_rate"] > 0
    assert data["stt_provider"] in {"auto", "faster_whisper", "openai"}


def test_mock_roundtrip_transcript_and_audio(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()

    async def fake_stream(self, messages):
        return AIMessage(content="Hi! How can I help you?"), "stub"

    monkeypatch.setattr(VoiceSession, "_stream_ai", fake_stream)

    session, events = _session(user_id, phrase="what is the weather")

    async def run() -> None:
        await session.start()
        await session.handle_audio(_loud(1000))
        await session.handle_control({"type": "end"})
        await session.close("hangup")

    asyncio.run(run())

    types = [e["type"] for e in events]
    assert "ready" in events[0]["type"] or events[0]["type"] == "ready"
    users = [e for e in events if e["type"] == "user"]
    assert [e["text"] for e in users] == ["what is the weather"]
    agents = [e["text"] for e in events if e["type"] == "agent"]
    assert any("help" in t.lower() for t in agents)
    assert any(e["type"] == "audio" for e in events)
    assert any(e["type"] == "audio_begin" for e in events)
    assert events[-1]["type"] == "ended"

    with SessionLocal() as db:
        call = db.scalar(select(Call).where(Call.id == events[0]["call_id"]))
        assert call is not None
        assert call.kind == "inapp"
        assert call.provider == "inapp"
        assert call.to_phone is None
        assert call.status == "completed"
        assert "in-app" in (call.summary or "")
        assert any(t["role"] == "user" for t in (call.transcript or []))
        assert any(t["role"] == "agent" for t in (call.transcript or []))


def test_voice_turns_are_persisted_in_the_bound_conversation(client, auth_headers, monkeypatch):
    headers = auth_headers("voice-conversation@test.com")
    user_id = _first_user_id()
    with SessionLocal() as db:
        conversation = Conversation(user_id=user_id, title="Voice test")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        conversation_id = conversation.id

    async def fake_stream(self, messages):
        return AIMessage(content="Voice reply"), "stub"

    monkeypatch.setattr(VoiceSession, "_stream_ai", fake_stream)
    events: list[dict] = []

    async def send(ev: dict) -> None:
        events.append(ev)

    session = VoiceSession.create(
        user_id=user_id, conversation_id=conversation_id, send=send,
        stt=STTChain([MockSTT("voice question")]), tts=TTSChain([MockTTS()]),
    )

    async def run() -> None:
        await session.start()
        await session.handle_audio(_loud(1000))
        await session.handle_control({"type": "end"})
        await session.close("hangup")

    asyncio.run(run())
    with SessionLocal() as db:
        rows = db.scalars(select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at, Message.id)).all()
        assert [(row.role, row.content) for row in rows] == [
            ("assistant", rows[0].content),
            ("user", "voice question"),
            ("assistant", "Voice reply"),
        ]
        assert rows[0].meta["source"] == "voice"


@pytest.mark.parametrize(
    "decision", ["approve", "reject", "cancel", "ui_approve"],
)
def test_voice_action_uses_shared_task_and_approval_lifecycle(
    client, auth_headers, monkeypatch, decision,
):
    from app.models.agent import Approval, AgentRun
    from app.models.task import Task
    from app.tools import PermissionLevel, registry

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    dispatched: list[str] = []

    async def voice_shared_action(target: str) -> ToolResult:
        dispatched.append(target)
        return ToolResult.ok("Action accepted.", {"target": target})

    registry.register(
        voice_shared_action,
        permission=PermissionLevel.CONFIRM,
        category="test",
        side_effect="external",
        idempotent=True,
        retryable=True,
    )
    headers = auth_headers("voice-shared-action@test.com")
    user_id = _first_user_id()
    from conftest import FakeRouter

    router = FakeRouter([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "voice_action",
                "description": "Perform the requested action",
                "tool": "voice_shared_action",
                "arguments": {"target": "shared target"},
                "dependencies": [],
            }]},
            "id": "voice_action_plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "voice_shared_action",
            "args": {"target": "shared target"},
            "id": "voice_action_call",
            "type": "tool_call",
        }]),
        AIMessage(content="The action completed."),
        AIMessage(content="The request was not approved."),
    ])
    router.route_chain = lambda **kwargs: [
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="voice-test"),
    ]
    events: list[dict] = []

    async def send(event: dict) -> None:
        events.append(event)

    session = VoiceSession.create(
        user_id=user_id,
        send=send,
        router=router,
        stt=STTChain([MockSTT()]),
        tts=TTSChain([MockTTS()]),
    )

    async def run() -> None:
        await session.start()
        await session.handle_control({
            "type": "text",
            "text": "Perform the action for shared target.",
        })

    asyncio.run(run())
    pending_event = next(
        event for event in events if event["type"] == "needs_confirmation"
    )
    task_event = next(event for event in events if event["type"] == "task_state")
    assert pending_event["approval_id"]
    assert pending_event["task_id"] == task_event["task_id"]
    assert pending_event["run_id"] == task_event["run_id"]
    assert dispatched == []

    approvals = client.get("/api/approvals", headers=headers)
    assert approvals.status_code == 200, approvals.text
    shared_approval = next(
        row for row in approvals.json()
        if row["id"] == pending_event["approval_id"]
    )
    assert shared_approval["status"] == "pending"
    assert shared_approval["task_id"] == task_event["task_id"]
    assert shared_approval["run_id"] == task_event["run_id"]

    if decision == "ui_approve":
        from app.api.deps import get_router

        monkeypatch.setitem(
            client.app.dependency_overrides,
            get_router,
            lambda: router,
        )
        ui_approval = client.post(
            f"/api/approvals/{pending_event['approval_id']}/approve",
            headers=headers,
        )
        assert ui_approval.status_code == 200, ui_approval.text
        assert dispatched == ["shared target"]

    async def decide() -> None:
        await session.handle_control({
            "type": "confirm",
            "yes": True,
            "approval_id": "unrelated-approval",
        })
        assert dispatched == (["shared target"] if decision == "ui_approve" else [])
        if decision != "cancel":
            await session.handle_control({
                "type": "confirm",
                "yes": decision in {"approve", "ui_approve"},
                "approval_id": pending_event["approval_id"],
            })
        await session.close("hangup")

    asyncio.run(decide())
    if decision == "cancel":
        cancelled = client.post(
            f"/api/tasks/{task_event['task_id']}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

    with SessionLocal() as db:
        task = db.get(Task, task_event["task_id"])
        run = db.get(AgentRun, task_event["run_id"])
        approval = db.get(Approval, pending_event["approval_id"])
        call = db.scalar(select(Call).where(Call.id == session.call_id))
        assert task is not None
        assert run is not None
        assert approval is not None
        assert call is not None
        assert call.task_id == task.id
        assert call.run_id == run.id
        assert call.conversation_id == task.conversation_id
        expected_approval_status = {
            "approve": "approved",
            "ui_approve": "approved",
            "reject": "rejected",
            "cancel": "cancelled",
        }[decision]
        assert approval.status == expected_approval_status
        assert task.status == (
            "completed" if decision in {"approve", "ui_approve"}
            else "cancelled" if decision == "cancel"
            else "partial"
        )
        assert run.status == (
            "completed" if decision in {"approve", "ui_approve"}
            else "stopped" if decision == "cancel"
            else "partial"
        )
        voice_messages = db.scalars(
            select(Message).where(
                Message.conversation_id == task.conversation_id,
                Message.meta["run_id"].as_string() == run.id,
            )
        ).all()
        assert voice_messages
        assert all(message.meta.get("source") == "voice" for message in voice_messages)
    assert dispatched == (
        ["shared target"] if decision in {"approve", "ui_approve"} else []
    )


def test_voice_pending_approval_recovers_from_persisted_call_after_restart(
    client, auth_headers, monkeypatch,
):
    from app.models.agent import Approval
    from app.models.task import Task
    from app.tools import PermissionLevel, registry
    from conftest import FakeRouter

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    dispatched: list[str] = []

    async def restart_voice_action(target: str) -> ToolResult:
        dispatched.append(target)
        return ToolResult.ok("Action accepted.", {"target": target})

    registry.register(
        restart_voice_action,
        permission=PermissionLevel.CONFIRM,
        category="test",
        side_effect="external",
        idempotent=True,
        retryable=True,
    )
    headers = auth_headers("voice-restart@test.com")
    user_id = _first_user_id()
    router = FakeRouter([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "restart_voice_action",
                "description": "Perform the requested action",
                "tool": "restart_voice_action",
                "arguments": {"target": "restart target"},
                "dependencies": [],
            }]},
            "id": "restart_voice_plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "restart_voice_action",
            "args": {"target": "restart target"},
            "id": "restart_voice_call",
            "type": "tool_call",
        }]),
        AIMessage(content="The resumed voice task completed."),
    ])
    router.route_chain = lambda **kwargs: [
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="restart-test"),
    ]
    events: list[dict] = []

    async def send(event: dict) -> None:
        events.append(event)

    first_session = VoiceSession.create(
        user_id=user_id, send=send, router=router,
        stt=STTChain([MockSTT()]), tts=TTSChain([MockTTS()]),
    )

    async def pause() -> None:
        await first_session.start()
        await first_session.handle_control({
            "type": "text",
            "text": "Perform the action for restart target.",
        })

    asyncio.run(pause())
    paused = next(event for event in events if event["type"] == "needs_confirmation")
    assert dispatched == []

    asyncio.run(first_session.close("disconnect"))
    resumed_events: list[dict] = []

    async def send_resumed(event: dict) -> None:
        resumed_events.append(event)

    resumed_session = VoiceSession.create(
        user_id=user_id,
        send=send_resumed,
        call_id=first_session.call_id,
        router=router,
        stt=STTChain([MockSTT()]),
        tts=TTSChain([MockTTS()]),
    )

    async def resume() -> None:
        await resumed_session.start()
        restored = next(
            event for event in resumed_events
            if event["type"] == "needs_confirmation"
        )
        assert restored["approval_id"] == paused["approval_id"]
        assert restored["task_id"] == paused["task_id"]
        assert restored["run_id"] == paused["run_id"]
        assert dispatched == []
        await resumed_session.handle_control({
            "type": "confirm",
            "yes": True,
            "approval_id": restored["approval_id"],
        })
        await resumed_session.close("hangup")

    asyncio.run(resume())
    with SessionLocal() as db:
        approval = db.get(Approval, paused["approval_id"])
        task = db.get(Task, paused["task_id"])
        call = db.get(Call, first_session.call_id)
        assert approval is not None and approval.status == "approved"
        assert task is not None and task.status == "completed"
        assert call is not None and call.task_id == task.id
        assert call.run_id == paused["run_id"]
    assert dispatched == ["restart target"]


def test_silence_does_not_trigger_stt(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()
    called = {"n": 0}

    async def fake_stream(self, messages):
        called["n"] += 1
        return AIMessage(content="should not happen"), "stub"

    monkeypatch.setattr(VoiceSession, "_stream_ai", fake_stream)
    session, events = _session(user_id)

    async def run():
        await session.start()
        await session.handle_audio(bytes(16000 * 2))  # 1s of silence
        await session.handle_control({"type": "end"})
        await session.close()

    asyncio.run(run())
    assert called["n"] == 0
    assert not any(e["type"] == "user" for e in events)


def test_confirm_tool_gates_on_spoken_yes(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()
    calls = {"n": 0}

    async def fake_stream(self, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "send_sms",
                    "args": {"phone_number": "+254700111222", "message": "hi"},
                    "id": "t1", "type": "tool_call",
                }],
            ), "stub"
        return AIMessage(content="Alright, it's sent."), "stub"

    monkeypatch.setattr(VoiceSession, "_stream_ai", fake_stream)
    session, events = _session(user_id, phrase="text John that I'm late")

    async def run():
        await session.start()
        await session.handle_control({"type": "text", "text": "text John that I'm late"})
        assert session._pending_tool is not None
        assert any(e["type"] == "needs_confirmation" for e in events), events
        assert not any(e["type"] == "tool_result" for e in events)
        await session.handle_control({"type": "confirm", "yes": True})
        await session.close()

    asyncio.run(run())
    assert any(e["type"] == "tool_result" for e in events)
    assert calls["n"] == 2
    assert any(e["type"] == "agent" and "sent" in e["text"].lower() for e in events)


def test_confirm_tool_declined_never_executes(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()

    async def fake_stream(self, messages):
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "send_sms",
                "args": {"phone_number": "+254700333444", "message": "hi"},
                "id": "t2", "type": "tool_call",
            }],
        ), "stub"

    monkeypatch.setattr(VoiceSession, "_stream_ai", fake_stream)
    session, events = _session(user_id)

    async def run():
        await session.start()
        await session.handle_control({"type": "text", "text": "send an sms"})
        await session.handle_control({"type": "confirm", "yes": False})
        await session.close()

    asyncio.run(run())
    assert any(e["type"] == "needs_confirmation" for e in events)
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and all(e.get("approved") is False for e in results)
    assert any(e["type"] == "agent" for e in events)  # a spoken "no" acknowledgement


def _register_policy_probe(monkeypatch, executed):
    from langchain_core.tools import StructuredTool

    from app.tools import PermissionLevel, registry
    from app.tools.base import ToolSpec

    async def probe(query: str) -> str:
        executed.append(query)
        return "executed"

    tool = StructuredTool.from_function(
        coroutine=probe,
        name="policy_probe",
        description="A local tool used to test voice policy.",
    )
    spec = ToolSpec(
        name="policy_probe",
        tool=tool,
        permission=PermissionLevel.AUTO,
        category="test",
        tags=["voice"],
    )
    original_get = registry.get
    monkeypatch.setattr(
        registry,
        "get",
        lambda name: spec if name == spec.name else original_get(name),
    )


@pytest.mark.parametrize(
    ("preferences", "expected_code"),
    [
        ({"autonomy": 0}, "AUTONOMY_DISABLED"),
        (
            {"autonomy": 2, "tool_policies": {"policy_probe": "denied"}},
            "TOOL_DENIED",
        ),
    ],
)
def test_voice_policy_denies_tools_before_execution(
    auth_headers, monkeypatch, preferences, expected_code,
):
    auth_headers()
    user_id = _first_user_id()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.prefs = preferences
        db.commit()

    executed: list[str] = []
    _register_policy_probe(monkeypatch, executed)
    session, events = _session(user_id)

    async def run():
        await session.start()
        assert await session._execute_tools([{
            "name": "policy_probe",
            "args": {"query": "must not run"},
            "id": "blocked-call",
            "type": "tool_call",
        }])
        await session.close()

    asyncio.run(run())
    assert executed == []
    assert expected_code in session._messages[-1].content
    denied = [event for event in events if event["type"] == "tool_result"]
    assert denied and denied[-1]["success"] is False
    assert denied[-1]["error_code"] == expected_code
    assert not any(event["type"] == "needs_confirmation" for event in events)


def test_voice_approval_policy_prompts_before_auto_tool(
    auth_headers, monkeypatch,
):
    auth_headers()
    user_id = _first_user_id()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.prefs = {
            "autonomy": 2,
            "tool_policies": {"policy_probe": "approval"},
        }
        db.commit()

    executed: list[str] = []
    _register_policy_probe(monkeypatch, executed)
    session, events = _session(user_id)

    async def run():
        await session.start()
        assert not await session._execute_tools([{
            "name": "policy_probe",
            "args": {"query": "wait for consent"},
            "id": "approval-call",
            "type": "tool_call",
        }])
        assert session._pending_tool is not None
        await session.handle_control({"type": "confirm", "yes": False})
        await session.close()

    asyncio.run(run())
    assert executed == []
    assert any(event["type"] == "needs_confirmation" for event in events)
    results = [event for event in events if event["type"] == "tool_result"]
    assert results and results[-1]["approved"] is False
    assert results[-1]["permission"] == "AUTO"


def test_voice_rejects_unstructured_tool_output(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.prefs = {"autonomy": 2}
        db.commit()

    executed: list[str] = []
    _register_policy_probe(monkeypatch, executed)
    session, events = _session(user_id)

    async def run():
        await session.start()
        await session._execute_tools([{
            "name": "policy_probe",
            "args": {"query": "malformed result"},
            "id": "malformed-call",
            "type": "tool_call",
        }])
        await session.close()

    asyncio.run(run())
    assert executed == ["malformed result"]
    assert "MALFORMED_TOOL_RESULT" in session._messages[-1].content
    results = [event for event in events if event["type"] == "tool_result"]
    assert results and results[-1]["success"] is False
    assert results[-1]["error_code"] == "MALFORMED_TOOL_RESULT"


def test_voice_tool_names_are_small_and_include_core():
    from app.tools.routing import voice_tool_names

    names = set(voice_tool_names())
    assert {"search_web", "get_weather", "send_sms"} <= names
    assert "browser_open" not in names
    assert len(names) < 20


def _drain_until(ws, wanted: str, limit: int = 4000) -> list[dict]:
    """Read WS frames until (and including) the first ``wanted`` event."""
    seen: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg.get("type") == wanted:
            return seen
    raise AssertionError(
        f"never saw {wanted!r}; last types={[m.get('type') for m in seen[-10:]]}"
    )


def test_live_ws_rejects_missing_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice/live") as ws:
            ws.receive_json()


def test_live_ws_disabled_closes(client, monkeypatch):
    from app.api import voice as voice_api

    class _Settings:
        voice_enabled = False

    monkeypatch.setattr(voice_api, "get_settings", lambda: _Settings())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice/live?token=whatever") as ws:
            ws.receive_json()


def test_live_ws_text_turn_and_hangup(
    client, auth_headers, fake_router, monkeypatch,
):
    from app.services import voice_session as vs_mod

    token = auth_headers()["Authorization"].split(" ", 1)[1]
    fake_router.route_chain = lambda **kw: [
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="test"),
    ]
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: fake_router)
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "voice_reply",
            "description": "Reply to the spoken user",
            "kind": "response",
            "dependencies": [],
        }]},
        "id": "voice_reply_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="Hi, I can help with that."))

    with client.websocket_connect(f"/api/voice/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_json({"type": "text", "text": "hello"})
        seen = _drain_until(ws, "agent")
        assert any(m["type"] == "user" and m.get("text") == "hello" for m in seen)
        agent = next(m for m in seen if m["type"] == "agent")
        assert "help" in agent["text"].lower()

        ws.send_json({"type": "hangup"})
        tail = _drain_until(ws, "ended")
        assert tail[-1]["type"] == "ended"




# --------------------------------------------------------------------------- #
# Phase-1 voice latency / speed surface (offline)                              #
# --------------------------------------------------------------------------- #
def _stub_ai(text: str, provider: str = "stub", counter: dict | None = None):
    """Stand-in for ``VoiceSession._stream_ai`` that records invocations."""
    async def _fake_stream(self, messages):
        if counter is not None:
            counter["n"] += 1
        return AIMessage(content=text), provider
    return _fake_stream


class _SlowStreamingModel(FakeModel):
    """FakeModel that yields each scripted piece as its own stream chunk,
    optionally delayed, so the token->TTS interleaving is observable offline."""

    pieces: Box = Field(default_factory=Box)
    delay: float = 0.0

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.tape.append(list(messages))
        for piece in list(self.pieces):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece, id="streaming-fake"))


def _streaming_router(pieces, delay: float = 0.0):
    """A fake router + slow-model pair; ``route_chain`` accepts the override
    kwargs the real ``ModelRouter.route_chain`` exposes."""
    router = FakeRouter()
    model = _SlowStreamingModel(pieces=Box(list(pieces)), delay=delay)
    router.model = model

    def _route_chain(**kw):
        return [TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="test")]
    router.route_chain = _route_chain
    return router, model


def test_voice_math_fast_path_is_instant(client, auth_headers, monkeypatch):
    auth_headers()
    user_id = _first_user_id()
    calls = {"n": 0}
    monkeypatch.setattr(VoiceSession, "_stream_ai",
                        _stub_ai("slow llm", provider="stub", counter=calls))
    # "2+2*5" is a direct math expression: classify_math("2+2*5").direct is True
    # (see tests/test_math.py::test_bare_expression_is_direct), so the voice
    # channel answers it locally with SymPy and never calls the LLM.
    session, events = _session(user_id)

    async def run():
        await session.handle_control({"type": "text", "text": "2+2*5"})

    asyncio.run(run())
    assert calls["n"] == 0  # the LLM was never consulted
    instant = [e for e in events
               if e.get("type") == "agent" and e.get("instant") is True]
    assert instant, events
    assert instant[0]["model"] == "sympy"
    assert instant[0]["route_reason"] == "math-direct"
    assert "12" in instant[0]["text"]
    # the computed answer is persisted to the call transcript
    with SessionLocal() as db:
        call = db.scalar(select(Call).where(Call.id == session.call_id))
        assert call is not None
        roles = [t["role"] for t in (call.transcript or [])]
        assert "user" in roles and "agent" in roles


def test_voice_vad_floor_300ms(client, auth_headers, monkeypatch):
    from app.config import get_settings

    assert SILENCE_MS == 300
    assert get_settings().voice_vad_silence_ms == 300
    auth_headers()
    user_id = _first_user_id()
    monkeypatch.setattr(VoiceSession, "_stream_ai", _stub_ai("pong"))
    session, events = _session(user_id, phrase="ping")
    speech = _loud(300)                        # 300 ms speech (> MIN_SPEECH_MS 250)
    below = bytes(int(16000 * 2 * 0.25))       # 250 ms trailing silence (< 300 floor)
    above = bytes(int(16000 * 2 * 0.35))       # +350 ms -> 600 ms total, crosses 300
    async def run():
        await session.start()
        await session.handle_audio(speech)
        await session.handle_audio(below)      # below the 300 ms floor -> no turn yet
        assert not any(e["type"] == "user" for e in events)
        await session.handle_audio(above)      # crosses the floor -> utterance finalised
    asyncio.run(run())
    users = [e for e in events if e["type"] == "user"]
    assert len(users) == 1 and users[0]["text"] == "ping"
    assert any(e["type"] == "agent" for e in events)


def test_voice_latency_budget_switches_provider(client, auth_headers, monkeypatch):
    import app.services.voice_session as vs_mod

    auth_headers()
    user_id = _first_user_id()
    session, events = _session(user_id)
    session._settings = SimpleNamespace(
        voice_latency_budget_ms=100, voice_fallback_provider="openai",
        voice_fast_model=None, voice_vad_silence_ms=300,
        voice_stream_tokens=False, voice_tts_sample_rate=22050,
    )
    # 2000 ms time-to-first-token vs a 100 ms budget with an openai fallback.
    router, model = _streaming_router(["ok"], delay=0.0)
    captured: dict = {}
    _chain = router.route_chain

    def _recording_route_chain(**kw):
        captured.update({k: v for k, v in kw.items()
                         if k in ("provider_override", "model_override")})
        return _chain(**kw)

    router.route_chain = _recording_route_chain
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)

    async def run():
        ok = await session._maybe_downgrade(2000.0, "ollama")
        assert ok is True
        assert session._fast_mode is True
        assert session._downgraded_from == "ollama"
        # the downgrade must steer the next routing call to the fast provider.
        ai, prov = await session._stream_ai_incremental([HumanMessage(content="hi")])
        assert prov == "fake"
        assert captured["provider_override"] == "openai"
        assert captured["model_override"] is None
        assert ai.content == "ok"

    asyncio.run(run())
    switched = [e for e in events if e["type"] == "provider_switched_fast"]
    assert switched, events
    sw = switched[0]
    assert sw["from"] == "ollama"
    assert sw["to"] == "openai"
    assert sw["reason"] == "ttfb_exceeds_latency_budget_ms"
    assert sw["budget_ms"] == 100
    assert sw["ttfb_ms"] == 2000.0


def test_voice_streams_tokens_into_audio(client, auth_headers, monkeypatch):
    import app.services.voice_session as vs_mod

    auth_headers()
    user_id = _first_user_id()
    router, _model = _streaming_router(["hello", " ", "world", "!"], delay=0.0)
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)
    session, events = _session(user_id)
    session._settings = SimpleNamespace(
        voice_stream_tokens=True, voice_tts_sample_rate=22050,
        voice_latency_budget_ms=1200, voice_fallback_provider=None,
        voice_fast_model=None, voice_vad_silence_ms=300,
    )

    async def run():
        await session.handle_control({"type": "text", "text": "hi there"})

    asyncio.run(run())
    types = [e["type"] for e in events]
    assert "audio_begin" in types
    bi = types.index("audio_begin")
    tokens = [i for i, t in enumerate(types) if t == "token"]
    assert tokens, types
    assert bi < tokens[-1]                  # speech begins before the last token lands
    assert events[-1]["type"] == "agent"
    assert events[-1].get("streamed") is True
    assert events[-1]["text"] == "hello world!"




def test_voice_stream_ai_downgrade_via_real_ttt(client, auth_headers, monkeypatch):
    """The buffered path must measure time-to-first-token in the *real*
    ``_stream_ai`` (not only the incremental path) and trigger the latency
    budget downgrade from inside it, end-to-end through ``handle_control``."""
    import app.services.voice_session as vs_mod

    auth_headers()
    user_id = _first_user_id()
    session, events = _session(user_id)
    session._settings = SimpleNamespace(
        voice_latency_budget_ms=50, voice_fallback_provider="openai",
        voice_fast_model=None, voice_vad_silence_ms=300,
        voice_stream_tokens=False, voice_tts_sample_rate=22050,
    )
    # One chunk, delayed 0.12 s -> first_token_ms ~120 ms > 50 ms budget.
    router, _model = _streaming_router(["hello"], delay=0.12)
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)

    async def run():
        await session.handle_control({"type": "text", "text": "hi"})

    asyncio.run(run())

    switched = [e for e in events if e["type"] == "provider_switched_fast"]
    assert switched, events
    sw = switched[0]
    assert sw["from"] == "fake"
    assert sw["to"] == "openai"
    assert sw["reason"] == "ttfb_exceeds_latency_budget_ms"
    assert sw["ttfb_ms"] > 50.0
    assert session._fast_mode is True
    assert any(e["type"] == "agent" for e in events)

def test_live_ws_uses_central_task_lifecycle(
    client, auth_headers, monkeypatch,
):
    """A live voice turn goes through the shared task executor."""
    from app.services import voice_session as vs_mod

    token = auth_headers()["Authorization"].split(" ", 1)[1]
    router = FakeRouter([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "voice_reply",
                "description": "Reply to the spoken user",
                "kind": "response",
                "dependencies": [],
            }]},
            "id": "voice_reply_plan",
            "type": "tool_call",
        }]),
        AIMessage(content="A central agent response."),
    ])
    router.route_chain = lambda **kwargs: [
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="voice-test"),
    ]
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)

    with client.websocket_connect(f"/api/voice/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "text": "hello"})
        seen = _drain_until(ws, "agent")

    task_state = next(m for m in seen if m["type"] == "task_state")
    assert task_state["task_id"]
    assert task_state["run_id"]
    assert task_state["conversation_id"]
    assert task_state["status"] == "completed"
    assert any(m["type"] == "user" for m in seen)
    assert any(
        m["type"] == "agent" and "central agent" in m["text"].lower()
        for m in seen
    )

def test_live_ws_streams_central_reply_audio_over_wire(
    client, auth_headers, monkeypatch,
):
    """The central executor's reply is synthesized and streamed as audio."""
    from app.config import get_settings as _real_get_settings
    from app.services import voice_session as vs_mod

    token = auth_headers()["Authorization"].split(" ", 1)[1]
    base = _real_get_settings()
    s2 = base.model_copy(update={"voice_stream_tokens": True})
    monkeypatch.setattr(vs_mod, "get_settings", lambda: s2)
    router = FakeRouter([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "voice_reply",
                "description": "Reply to the spoken user",
                "kind": "response",
                "dependencies": [],
            }]},
            "id": "voice_reply_plan",
            "type": "tool_call",
        }]),
        AIMessage(content="Central reply is spoken."),
    ])
    router.route_chain = lambda **kwargs: [
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="voice-test"),
    ]
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)

    with client.websocket_connect(f"/api/voice/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "text": "hello"})
        seen = _drain_until(ws, "agent")
        ws.send_json({"type": "hangup"})
        seen += _drain_until(ws, "ended")

    frames = [m.get("type") for m in seen]
    user_idx = frames.index("user")
    abi = frames.index("audio_begin", user_idx)
    audio_idx = [i for i, t in enumerate(frames) if t == "audio" and i > abi]
    end_idx = frames.index("audio_end", abi)

    assert seen[abi]["sample_rate"] == 22050
    assert audio_idx
    assert abi < audio_idx[0] < end_idx
    assert any(m["type"] == "task_state" for m in seen)
    assert any(
        m["type"] == "agent" and "central reply" in m["text"].lower()
        for m in seen
    )
def test_voice_memory_budget_skips_oversize_route(client, auth_headers):
    """A route whose explicit memory_mb exceeds voice_model_memory_budget_mb is
    dropped and reported (§1.14/§1.17); lighter and unknown-footprint routes
    survive so the turn is never starved."""
    auth_headers()
    user_id = _first_user_id()
    session, events = _session(user_id)
    session._settings = SimpleNamespace(
        voice_model_memory_budget_mb=4096,
        voice_stream_tokens=False, voice_tts_sample_rate=22050,
        voice_vad_silence_ms=300, voice_fast_model=None,
        voice_latency_budget_ms=1200, voice_fallback_provider=None,
    )
    big = TaskRoute(provider=FAKE_SPEC, model="big-model", reason="test", memory_mb=8192)
    small = TaskRoute(provider=FAKE_SPEC, model="small-model", reason="fallback", memory_mb=2048)
    unknown = TaskRoute(provider=FAKE_SPEC, model="cloud-gpt", reason="fallback")  # unknown footprint
    kept = asyncio.run(session._within_memory_budget([big, small, unknown]))

    assert [r.model for r in kept] == ["small-model", "cloud-gpt"]
    lim = [e for e in events if e["type"] == "model_memory_limited"]
    assert len(lim) == 1
    assert lim[0]["from_model"] == "big-model"
    assert lim[0]["provider"] == "fake"
    assert lim[0]["memory_mb"] == 8192
    assert lim[0]["budget_mb"] == 4096
    assert lim[0]["reason"] == "exceeds_voice_memory_budget_mb"


def test_voice_memory_budget_downgrades_live(client, auth_headers, monkeypatch):
    """End-to-end: an oversize route is skipped at routing time (before any model
    call) and the lighter route completes the turn (§1.14/§1.17)."""
    import app.services.voice_session as vs_mod

    auth_headers()
    user_id = _first_user_id()
    session, events = _session(user_id)
    session._settings = SimpleNamespace(
        voice_model_memory_budget_mb=4096,
        voice_stream_tokens=False, voice_tts_sample_rate=22050,
        voice_vad_silence_ms=300, voice_fast_model=None,
        voice_latency_budget_ms=1200, voice_fallback_provider=None,
    )
    router, _model = _streaming_router(["ok"], delay=0.0)
    big = TaskRoute(provider=FAKE_SPEC, model="big-model", reason="test", memory_mb=8192)
    # route_chain leads with the oversize route, then the light route the fake
    # router actually serves, so the turn completes on the lighter model.
    router.route_chain = lambda **kw: [
        big,
        TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="fallback", memory_mb=512),
    ]
    monkeypatch.setattr(vs_mod, "get_model_router", lambda: router)

    asyncio.run(session.handle_control({"type": "text", "text": "hi"}))

    types = [e["type"] for e in events]
    assert "model_memory_limited" in types
    lim = next(e for e in events if e["type"] == "model_memory_limited")
    assert lim["from_model"] == "big-model"
    assert lim["memory_mb"] == 8192
    assert "agent" in types
    agent = next(e for e in events if e["type"] == "agent")
    assert agent["text"] == "ok"
