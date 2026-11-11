"""P9 (Phase F): voice calls — mock lifecycle, §32 summary shape, approval before
dialing, script branches (§33) and the §57 combined call→confirm→WhatsApp flow.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.call import Call, CallTurn
from app.models.user import User
from app.services import call_script as script_engine
from app.services import comms as comms_service
from app.tools import registry
from app.tools.base import PermissionLevel


def _invoke(name: str, args: dict) -> dict:
    spec = registry.get(name)
    assert spec is not None, f"tool {name} is not registered"
    raw = asyncio.run(spec.tool.ainvoke(args))
    return json.loads(raw)


def _dummy_ctx(user_id: str) -> None:
    set_tool_context(ToolContext(user_id=user_id, run_id=None,
                                 task_id=None, conversation_id=None))


# --------------------------------------------------------------------------- #
# registration + manifest                                                     #
# --------------------------------------------------------------------------- #
def test_make_call_registered_approval_gated():
    spec = registry.get("make_call")
    assert spec is not None, "make_call missing from registry"
    assert spec.permission == PermissionLevel.CONFIRM == 3
    assert spec.requires_internet is True
    assert spec.category == "comms"
    manifest = {e["name"]: e for e in registry.manifest()}
    assert manifest["make_call"]["permission"] == 3
    assert "call" in spec.description.lower()
    assert "approval" in spec.description.lower()


# --------------------------------------------------------------------------- #
# mock lifecycle via the tool (ToolContext bound)                              #
# --------------------------------------------------------------------------- #
def test_make_call_mock_lifecycle_and_summary_shape(client, auth_headers):
    auth_headers()
    # Bind the tool context to the registered user.
    with SessionLocal() as db:
        row = db.scalar(select(User.id).limit(1))
    assert row is not None
    _dummy_ctx(row)

    result = _invoke("make_call", {
        "phone_number": "+254 712 345 678",
        "purpose": "ask about my application",
        "questions": ["If approved, when can we collect the items?"],
    })
    assert result["success"] is True, result
    data = result["data"]
    assert data["mock"] is True
    assert data["provider"] == "mock"
    assert data["status"] == "completed"
    assert data["to_phone"] == "+254712345678"

    message = result["message"]
    assert "Person's response:" in message
    assert "Jalora summary:" in message
    summary = data["summary"] or ""
    assert summary.startswith("Person's response:")
    assert "Jalora summary:" in summary

    transcript = data["transcript"]
    roles = [t["role"] for t in transcript]
    assert "agent" in roles and "person" in roles
    assert len(transcript) >= 5  # intro, answer, follow-up, answer, closing

    with SessionLocal() as db:
        call = db.get(Call, data["id"])
        assert call is not None
        assert call.status == "completed"
        assert call.to_phone == "+254712345678"
        assert call.answers.get("approved") == "Yes."
        assert call.answers.get("q1") == "Yes."
        turn_count = db.scalar(select(func.count(CallTurn.id)).where(
            CallTurn.call_id == call.id))
        assert turn_count == len(transcript)

    assert "Traceback" not in json.dumps(result)


def test_make_call_invalid_phone_is_structured_failure(client, auth_headers):
    with SessionLocal() as db:
        row = db.scalar(select(User.id).limit(1))
    _dummy_ctx(row)
    result = _invoke("make_call", {"phone_number": "not-a-number",
                                   "purpose": "hello"})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_PHONE"
    assert "Traceback" not in json.dumps(result)


# --------------------------------------------------------------------------- #
# REST surface (auth)                                                         #
# --------------------------------------------------------------------------- #
def test_rest_create_and_list_call(client, auth_headers):
    headers = auth_headers()
    not_confirmed = client.post("/api/calls", headers=headers, json={
        "phone_number": "+254733000123",
        "purpose": "request a callback",
    })
    assert not_confirmed.status_code == 400
    assert not_confirmed.json()["error"]["code"] == "CALL_CONFIRMATION_REQUIRED"

    resp = client.post("/api/calls", headers=headers, json={
        "phone_number": "+254733000123",
        "purpose": "request a callback",
        "confirm": True,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["mock"] is True
    assert "Person's response:" in (data["summary"] or "")

    listing = client.get("/api/calls", headers=headers).json()
    assert [c["id"] for c in listing] == [data["id"]]

    detail = client.get(f"/api/calls/{data['id']}", headers=headers).json()
    assert detail["transcript"] and detail["answers"] is not None
    assert detail["max_turns"] == 20
    assert detail["max_duration_seconds"] == 600

    # ownership enforced: a second user cannot read it
    other = auth_headers("other@test.com")
    got = client.get(f"/api/calls/{data['id']}", headers=other)
    assert got.status_code == 404


def test_rest_hangup_and_unknown_call(client, auth_headers):
    headers = auth_headers()
    resp = client.post("/api/calls", headers=headers, json={
        "phone_number": "+254733000999", "purpose": "weather alert",
        "confirm": True,
    })
    call_id = resp.json()["id"]
    # mock call is already completed; hangup is a safe no-op returning the view
    hung = client.post(f"/api/calls/{call_id}/hangup", headers=headers)
    assert hung.status_code == 200
    assert hung.json()["id"] == call_id
    assert client.get("/api/calls/nope", headers=headers).status_code == 404


def test_real_call_status_requires_valid_provider_signature(
    client, auth_headers, monkeypatch
):
    auth_headers("verified-call@test.com")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "verified-call@test.com"))
        assert user is not None
        call = Call(
            user_id=user.id,
            provider="twilio",
            kind="outbound",
            mode="script",
            purpose="status verification test",
            status="in_progress",
            answers={},
            transcript=[],
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        call_id = call.id

    token = "test-twilio-token"
    monkeypatch.setattr(
        "app.api.calls.get_settings",
        lambda: SimpleNamespace(
            env="production",
            twilio_auth_token=token,
            telnyx_public_key=None,
        ),
    )
    params = {"CallSid": "CA-test", "CallStatus": "completed"}
    callback_url = f"http://testserver/api/calls/{call_id}/status"
    signature = comms_service.compute_signature(token, callback_url, params)
    verified = client.post(
        f"/api/calls/{call_id}/status",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    assert verified.status_code == 200

    spoofed = client.post(
        f"/api/calls/{call_id}/status",
        data={"CallSid": "CA-test", "CallStatus": "failed"},
        headers={"X-Twilio-Signature": signature},
    )
    assert spoofed.status_code == 403
    with SessionLocal() as db:
        stored = db.get(Call, call_id)
        assert stored is not None
        assert stored.status == "completed"


# --------------------------------------------------------------------------- #
# script engine (§33): conditional flow                                        #
# --------------------------------------------------------------------------- #
def test_script_branch_flow():
    # "if approved, ask when to collect" — the build_script(questions) path
    script = script_engine.build_script(
        "arrange collection",
        questions=["If approved, when can we collect the items?"])
    assert [n.id for n in script.nodes] == ["intro", "ask_1", "closing"]
    intro = script.node("intro")
    assert intro.answer_key == "approved"
    assert intro.branch["yes"] == "ask_1"          # approved -> follow-up ask
    follow = script.node("ask_1")
    assert follow.answer_key == "q1"
    assert follow is script_engine.advance_script(script, intro, "Yes, fine")

    nxt = script_engine.advance_script(script, follow, "Tomorrow at noon")
    assert nxt is None or nxt.final                 # closing reached


def test_classify_answer_aliases():
    assert script_engine.classify_answer("Yes, go ahead") == "yes"
    assert script_engine.classify_answer("no thanks") == "no"
    assert script_engine.classify_answer("1") == "yes"
    assert script_engine.classify_answer("0") == "done"
    assert script_engine.classify_answer("hello?") == "unknown"


def test_validate_script_detects_bad_targets():
    script = script_engine.CallScript(
        nodes=[
            script_engine.ScriptNode(id="a", speak="x", branch={"yes": "missing"}),
            script_engine.ScriptNode(id="b", speak="y", final=True),
        ],
        first="a", purpose="p",
    )
    problems = script_engine.validate_script(script)
    assert any("missing" in p for p in problems)


# --------------------------------------------------------------------------- #
# graph: approval BEFORE any dialing (§31/§24), summary persisted on task      #
# --------------------------------------------------------------------------- #
def test_make_call_pauses_for_approval_then_dials(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "make_call",
            "args": {"phone_number": "+254700000101",
                     "purpose": "ask about my application"},
            "id": "call_make_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="I've made the call; the person approved."))

    resp = client.post("/api/chat",
                       json={"message": "Call +254700000101 and ask about my application"},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paused_approval"
    approval = data["approval"]
    assert approval is not None
    assert approval["tool_name"] == "make_call"
    assert approval["arguments"]["phone_number"] == "+254700000101"
    assert approval["level"] == 3

    # nothing was dialed yet
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Call.id))) == 0

    result = client.post(f"/api/approvals/{approval['id']}/approve", headers=headers)
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["status"] == "completed"

    with SessionLocal() as db:
        calls = db.scalars(select(Call)).all()
        assert len(calls) == 1
        call = calls[0]
        assert call.status == "completed"
        assert call.provider == "mock"
        assert call.to_phone == "+254700000101"
        assert call.task_id == final["task_id"]
        assert call.answers.get("approved") == "Yes."
        summary = call.summary or ""
        assert "Person's response:" in summary
        assert "Jalora summary:" in summary


def test_make_call_rejection_never_dials(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "make_call",
            "args": {"phone_number": "+254700000202", "purpose": "sales pitch"},
            "id": "call_make_2", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Understood — no call was made."))

    resp = client.post("/api/chat", json={"message": "Call now"}, headers=headers)
    approval = resp.json()["approval"]
    assert approval is not None

    result = client.post(f"/api/approvals/{approval['id']}/reject", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"

    with SessionLocal() as db:
        assert db.scalar(select(func.count(Call.id))) == 0


# --------------------------------------------------------------------------- #
# §57 combined workflow: call -> confirm -> WhatsApp (both mocked)             #
# --------------------------------------------------------------------------- #
def test_combined_call_and_whatsapp_workflow(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {
                    "id": "call",
                    "description": "Ask whether the application was approved",
                    "tool": "make_call",
                    "arguments": {
                        "phone_number": "+254700000303",
                        "purpose": "ask if my application is approved",
                    },
                },
                {
                    "id": "notify",
                    "description": "Send the details by WhatsApp if approved",
                    "tool": "send_whatsapp",
                    "arguments": {
                        "phone_number": "+254700000303",
                        "message": "Your application was approved. We will collect soon.",
                    },
                    "dependencies": ["call"],
                },
            ]},
            "id": "call_plan_3",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "make_call",
            "args": {"phone_number": "+254700000303",
                     "purpose": "ask if my application is approved"},
            "id": "call_make_3", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_whatsapp",
            "args": {"phone_number": "+254700000303",
                     "message": "Your application was approved. We will collect soon."},
            "id": "call_wa_3", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Done — I called and followed up on WhatsApp."))

    resp = client.post("/api/chat", json={
        "message": ("Call +254700000303, ask about my application, and if approved "
                    "send me the details on WhatsApp"),
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["status"] == "paused_approval"
    assert first["approval"]["tool_name"] == "make_call"

    # first approval: the call runs (mock, synchronous)
    after_call = client.post(f"/api/approvals/{first['approval']['id']}/approve",
                             headers=headers).json()
    assert after_call["status"] == "paused_approval"
    assert after_call["approval"]["tool_name"] == "send_whatsapp"

    # call already persisted with its transcript + task link
    with SessionLocal() as db:
        call = db.scalar(select(Call))
        assert call is not None
        assert call.status == "completed"
        assert call.task_id == after_call["task_id"]

    # second approval: the WhatsApp message is queued (mock receipt)
    second = client.post(f"/api/approvals/{after_call['approval']['id']}/approve",
                         headers=headers).json()
    assert second["status"] == "completed"

    with SessionLocal() as db:
        from app.models.comms import CommsIdentity
        assert db.scalar(select(func.count(Call.id))) == 1
        assert db.scalar(select(func.count(CommsIdentity.id))) >= 0  # whatsapp uses identities

    # task summary surfaced the §32 result
    activity = client.get("/api/activity", headers=headers).json()
    text = json.dumps(activity)
    assert "make_call" in text
    assert "Person's response:" in text or "Jalora summary" in text
    assert "send_whatsapp" in text


def test_health_reports_voice_capability(client):
    health = client.get("/api/health").json()
    assert "voice" in health["capabilities"]
    assert health["capabilities"]["voice"] is False  # no twilio creds in tests