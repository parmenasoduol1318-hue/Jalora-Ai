"""Voice call service (§31-33, §57): Telnyx/Twilio Programmable Voice + mock.

Mock-first: `auto` resolves to telnyx when configured, then twilio when its
account SID + auth token are set, otherwise to the deterministic offline mock
(identical rule to app.services.comms). The mock provider conducts a call
synchronously from its structured script, producing a transcript, collected
answers and a §32 summary — fully deterministic and offline-testable. The real
providers dial a number and drive the conversation through TeXML/TwiML
``<Gather>`` and an optional media-stream WebSocket loop; both respect
turn/duration budgets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.events import emit
from app.models.call import Call, CallTurn
from app.services import call_script as script_engine
from app.services import comms as comms_service


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _xml(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _provider() -> str:
    """Resolve the active phone provider: telnyx -> twilio -> mock (`auto`).

    Delegates to the comms resolver so messaging and voice can never disagree
    about which provider is configured.
    """
    return comms_service.resolve_provider()


def _answer_provider(call: Call) -> str:
    """The provider that owns a call's webhooks (defaults to the active one)."""
    provider = (call.provider or "").strip().lower()
    if provider in ("twilio", "telnyx"):
        return provider
    return _provider()


def _live_answer(call: Call) -> str:
    """TwiML/TeXML connecting a live call's audio to our media WebSocket."""
    if _answer_provider(call) == "telnyx":
        from app.services import voice_telnyx

        return voice_telnyx.answer_texml(call)
    from app.services import voice_twilio

    return voice_twilio.answer_twiml(call)


def _nodes(call: Call) -> list[script_engine.ScriptNode]:
    return [script_engine.ScriptNode(**n) for n in (call.script or [])]


def _script(call: Call) -> script_engine.CallScript:
    nodes = _nodes(call)
    first = nodes[0].id if nodes else "intro"
    return script_engine.CallScript(nodes=nodes, first=first, purpose=call.purpose)


def _make_script(purpose: str, questions: list[str] | None) -> script_engine.CallScript:
    script = script_engine.build_script(purpose or "a phone call", questions)
    problems = script_engine.validate_script(script)
    if problems:
        raise comms_service.CommsError("INVALID_SCRIPT", "; ".join(problems))
    return script


@dataclass
class MockReply:
    """Deterministic canned reply chosen from a script node's branch keys."""

    text: str
    label: str


def _deterministic_reply(node: script_engine.ScriptNode) -> MockReply:
    keys = node.branch or {}
    if "yes" in keys:
        return MockReply("Yes.", "yes")
    if "no" in keys:
        return MockReply("No.", "no")
    if "done" in keys or "answer" in keys:
        return MockReply("Nothing further.", "done")
    return MockReply("No.", "no")


# --------------------------------------------------------------------------- #
# persistence helpers                                                         #
# --------------------------------------------------------------------------- #

def get_call(db: Session, call_id: str, user_id: str | None = None) -> Call:
    call = db.get(Call, call_id)
    if call is None or (user_id is not None and call.user_id != user_id):
        raise comms_service.CommsError("CALL_NOT_FOUND", "Call not found.")
    return call


def append_turn(db: Session, call: Call, role: str, kind: str, text: str,
                ts: float = 0.0) -> CallTurn:
    """Persist a transcript turn on the call row and the turns table."""
    turn = CallTurn(
        call_id=call.id, role=role, kind=kind, text=text[:4000], ts=ts,
        created_at=utcnow(),
    )
    db.add(turn)
    transcript = list(call.transcript or [])
    transcript.append({"role": role, "kind": kind, "text": text[:4000], "ts": ts})
    call.transcript = transcript
    call.updated_at = utcnow()
    db.commit()
    db.refresh(turn)
    emit("call_turn", {
        "call_id": call.id, "role": role, "kind": kind, "text": text,
        "task_id": call.task_id,
    })
    return turn


def finalize(db: Session, call: Call, status: str, summary: str | None = None) -> Call:
    if summary is None:
        summary = _summarize(call)
    call.summary = summary
    call.status = status
    call.ended_at = call.ended_at or utcnow()
    call.updated_at = utcnow()
    db.commit()
    db.refresh(call)
    emit("call", {
        "call_id": call.id, "status": status, "summary": summary,
        "to_phone": call.to_phone, "provider": call.provider,
        "task_id": call.task_id, "run_id": call.run_id,
    })
    return call


# --------------------------------------------------------------------------- #
# §32 summary                                                                 #
# --------------------------------------------------------------------------- #

def _summarize(call: Call) -> str:
    """Deterministic §32-style summary: a person's response + Jalora summary.

    Used by the mock provider and as the fallback for real calls, so the
    returned text is stable and testable offline.
    """
    purpose = call.purpose or "the call"
    person_lines = [
        t.get("text", "").strip()
        for t in (call.transcript or [])
        if t.get("role") == "person" and t.get("text", "").strip()
        and t.get("kind") != "dtmf"
    ]
    if not person_lines:
        dtmf_lines = [
            t.get("text", "").strip()
            for t in (call.transcript or [])
            if t.get("role") == "person"
        ]
        person_response = dtmf_lines[-1] if dtmf_lines else "no response yet"
    else:
        person_response = person_lines[-1]
    answers = call.answers or {}
    agreed = bool(
        str(answers.get("approved") or "").strip().lower().startswith(("yes", "y"))
    )
    facts: list[str] = []
    for key in ("approved", "answer", "q1", "q2", "q3", "q4", "q5"):
        value = answers.get(key)
        if value:
            facts.append(f"{key}={value}")
    tail = (" They agreed to proceed." if agreed else "")
    jalora_summary = (
        f"Goal: {purpose}. The call collected {len(person_lines)} spoken "
        f"response{'s' if len(person_lines) != 1 else ''}, asked to confirm "
        f"approval, and recorded the answers.{tail}"
        + (f" Notes: {', '.join(sorted(facts))}." if facts else "")
    )
    return f"Person's response: {person_response}\nJalora summary: {jalora_summary}"


# --------------------------------------------------------------------------- #
# mock conductor (§31 offline deterministic path)                              #
# --------------------------------------------------------------------------- #

def _conduct_mock(call_id: str, person_answers: list[str] | None = None) -> None:
    """Deterministic synchronous conversation used by the mock provider."""
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        script = _script(call)
        current = script.node(script.first)
        answers: dict[str, str] = {}
        used = list(person_answers or [])
        ts = 0.0
        budget = call.max_turns or 20
        while current is not None and len(call.transcript or []) < budget * 2:
            append_turn(db, call, "agent", "say", current.speak, ts=ts)
            if current.final:
                break
            ts += 2.0
            if used:
                reply = used.pop(0)
            else:
                reply = _deterministic_reply(current).text
            append_turn(db, call, "person", "speech", reply, ts=ts)
            if current.answer_key:
                answers[current.answer_key] = reply
                call.answers = {**(call.answers or {}), **answers}
                db.commit()
            ts += 2.0
            current = script_engine.advance_script(script, current, reply)
            if current is None:
                break


# --------------------------------------------------------------------------- #
# top-level: create + run                                                     #
# --------------------------------------------------------------------------- #

def call_view(call: Call) -> dict[str, Any]:
    return {
        "id": call.id,
        "user_id": call.user_id,
        "run_id": call.run_id,
        "task_id": call.task_id,
        "conversation_id": call.conversation_id,
        "to_phone": call.to_phone,
        "provider": call.provider,
        "kind": call.kind or "outbound",
        "mode": call.mode or "script",
        "mock": call.provider == "mock",
        "purpose": call.purpose,
        "status": call.status,
        "script": call.script,
        "answers": call.answers or {},
        "transcript": call.transcript or [],
        "summary": call.summary,
        "max_turns": call.max_turns,
        "max_duration_seconds": call.max_duration_seconds,
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
    }


async def place_call(
    *,
    user_id: str,
    to_phone: str,
    purpose: str,
    questions: list[str] | None = None,
    script: dict | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    conversation_id: str | None = None,
    max_turns: int | None = None,
    max_duration_seconds: int | None = None,
    provider_override: str | None = None,
    mode: str = "script",
) -> dict[str, Any]:
    """Create a Call row and run it: synchronously (mock) or dial real Twilio.

    Approval policy is enforced by the graph/tool layer BEFORE this runs; this
    function never dials on its own behalf.

    ``mode`` selects the phone transport: ``script`` uses the deterministic
    TwiML ``<Gather>`` script; ``live`` bridges Twilio Media Streams to a
    ``VoiceSession`` so the caller talks to the assistant in real time.
    """
    to = comms_service.normalize_phone(to_phone)
    mode = (mode or "script").strip().lower()
    if mode not in ("script", "live"):
        raise comms_service.CommsError(
            "INVALID_MODE", f"Unknown call mode '{mode}' (script|live).")
    settings = get_settings()
    max_turns = max_turns or settings.call_max_turns
    max_duration_seconds = max_duration_seconds or settings.call_max_duration_seconds

    if isinstance(script, dict) and script.get("nodes"):
        nodes = [script_engine.ScriptNode(**{
            k: v for k, v in n.items() if k in (
                "id", "speak", "expect", "branch", "answer_key", "final")
        }) for n in script["nodes"]]
        current = script_engine.CallScript(
            nodes=nodes, first=script.get("first") or nodes[0].id, purpose=purpose)
        problems = script_engine.validate_script(current)
        if problems:
            raise comms_service.CommsError("INVALID_SCRIPT", "; ".join(problems))
    else:
        current = _make_script(purpose, questions)

    provider = provider_override or _provider()

    stored_purpose = purpose or "a phone call"
    if mode == "live" and questions:
        stored_purpose = f"{stored_purpose} Questions to cover: " + "; ".join(questions)

    with SessionLocal() as db:
        call = Call(
            user_id=user_id, run_id=run_id, task_id=task_id,
            conversation_id=conversation_id,
            to_phone=to, provider=provider, kind="outbound", mode=mode,
            purpose=stored_purpose,
            status="dialing", script=current.as_dict(),
            answers={}, transcript=[],
            max_turns=max_turns, max_duration_seconds=max_duration_seconds,
            created_at=utcnow(), updated_at=utcnow(),
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        call_id = call.id

        if provider == "mock":
            call.status = "in_progress"
            call.started_at = call.started_at or utcnow()
            db.commit()
            emit("call", {
                "call_id": call_id, "status": "in_progress", "to_phone": to,
                "provider": "mock", "task_id": task_id, "run_id": run_id,
            })
            _conduct_mock(call_id)
            db.refresh(call)
            finalize(db, call, "completed")
            return call_view(call)

        # ---- real telephony ---------------------------------------------
        base = str(getattr(settings, "calls_base_url", None) or "http://localhost:5173").rstrip("/")
        answer_url = f"{base}/api/calls/{call_id}/answer"
        status_url = f"{base}/api/calls/{call_id}/status"
        if provider == "telnyx":
            if not settings.telnyx_from_number:
                raise comms_service.CommsError(
                    "PROVIDER_NOT_CONFIGURED", "JALORA_TELNYX_FROM_NUMBER is not set.")
            account_sid, _ = comms_service._telnyx_call_scope()
            payload_in: dict = {
                "To": to,
                "From": settings.telnyx_from_number,
                "Url": answer_url,
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
                "Timeout": "30",
                "TimeLimit": str(max_duration_seconds),
            }
            if settings.telnyx_application_id:
                payload_in["ApplicationSid"] = settings.telnyx_application_id
            payload = await comms_service._telnyx_request(
                f"texml/Accounts/{account_sid}/Calls", json=payload_in)
            call.sid = comms_service._telnyx_sid(payload)
        else:
            if not settings.twilio_from_number:
                raise comms_service.CommsError(
                    "PROVIDER_NOT_CONFIGURED", "JALORA_TWILIO_FROM_NUMBER is not set.")
            payload = await comms_service._twilio_request("Calls", {
                "To": to,
                "From": settings.twilio_from_number,
                "Url": answer_url,
                "StatusCallback": status_url,
                "StatusCallbackEvent": "initiated ringing answered completed failed busy no-answer canceled",
                "TimeLimit": str(max_duration_seconds),
                "MachineDetection": "DetectMessageEnd",
            })
            call.sid = payload.get("sid", "")
        call.status = "in_progress"
        call.started_at = call.started_at or utcnow()
        db.commit()
        emit("call", {
            "call_id": call_id, "status": "in_progress", "to_phone": to,
            "provider": provider, "task_id": task_id, "run_id": run_id,
        })
        return call_view(call)


def list_calls(user_id: str, *, task_id: str | None = None,
               status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        q = select(Call).where(Call.user_id == user_id)
        if task_id:
            q = q.where(Call.task_id == task_id)
        if status:
            q = q.where(Call.status == status)
        rows = db.scalars(q.order_by(Call.created_at.desc()).limit(limit)).all()
        return [call_view(r) for r in rows]


async def hangup(user_id: str, call_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        call = get_call(db, call_id, user_id)
        if call.status in ("completed", "failed", "cancelled", "no_answer"):
            return call_view(call)
        if call.provider == "telnyx" and call.sid:
            settings = get_settings()
            try:
                if settings.telnyx_account_sid:
                    await comms_service._telnyx_request(
                        f"texml/Accounts/{settings.telnyx_account_sid}/Calls/{call.sid}",
                        json={"Status": "canceled"},
                    )
            except comms_service.CommsError:
                pass  # already ended upstream — proceed to mark locally
        elif call.provider == "twilio" and call.sid:
            try:
                await comms_service._twilio_request(f"Calls/{call.sid}", {
                    "Status": "canceled",
                })
            except comms_service.CommsError:
                pass  # already ended upstream — proceed to mark locally
        summary = _summarize(call)
        finalize(db, call, "cancelled", summary=summary)
        return call_view(call)


# --------------------------------------------------------------------------- #
# Twilio TwiML webhook handlers                                               #
# --------------------------------------------------------------------------- #

FINAL_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}
_LOCAL_FINAL = {"completed", "failed", "cancelled", "no_answer"}


def _next_twiml(call: Call) -> str:
    """TwiML for the turn AFTER `intro` (replay-based, branch aware)."""
    if call.mode == "live":
        return _live_answer(call)
    script = _script(call)
    current = script.node(script.first)
    for turn in (call.transcript or []):
        if turn.get("role") != "person":
            continue
        if current is None:
            break
        current = script_engine.advance_script(script, current, turn.get("text", ""))
    if current is None or current.final:
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            f"<Say>{_xml(_summarize(call).splitlines()[0])}</Say><Hangup/></Response>"
        )
    say = _xml(current.speak)
    action = f"/api/calls/{call.id}/gather"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"<Say>{say}</Say>"
        f'<Gather input="speech dtmf" timeout="5" speechTimeout="auto" action="{action}" method="POST">'
        "<Say>Please speak your answer, or press 1 for yes and 2 for no.</Say>"
        "</Gather>"
        "<Say>I did not hear a response. Goodbye.</Say><Hangup/>"
        "</Response>"
    )


async def handle_answer(db: Session, call: Call, params: dict[str, str]) -> str:
    """The Twilio answer webhook returns TwiML speaking the first script node."""
    call.status = "in_progress"
    call.started_at = call.started_at or utcnow()
    db.commit()
    if call.mode == "live":
        return _live_answer(call)
    script = _script(call)
    first = script.node(script.first)
    say = _xml(first.speak if first else f"Hello, I'm calling about {call.purpose}.")
    action = f"/api/calls/{call.id}/gather"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"<Say>{say}</Say>"
        f'<Gather input="speech dtmf" timeout="5" speechTimeout="auto" action="{action}" method="POST">'
        "<Say>Please speak your answer, or press 1 for yes and 2 for no.</Say>"
        "</Gather>"
        "<Say>I did not hear a response. Goodbye.</Say><Hangup/>"
        "</Response>"
    )


async def handle_gather(db: Session, call: Call, params: dict[str, str]) -> str:
    """A gathered person reply: store it, advance the script, hang up at end."""
    spoken = params.get("SpeechResult") or params.get("Digits") or ""
    if not spoken.strip():
        spoken = "No response"
    kind = "dtmf" if params.get("Digits") else "speech"
    append_turn(db, call, "person", kind, spoken.strip(), ts=len(call.transcript or []) / 2.0)
    script = _script(call)
    current = script.node(script.first)
    for turn in (call.transcript or []):
        if turn.get("role") != "person":
            continue
        if current is None:
            break
        if current.answer_key:
            call.answers = {**(call.answers or {}), current.answer_key: turn.get("text", "")}
        current = script_engine.advance_script(script, current, turn.get("text", ""))
    db.commit()
    if len(call.transcript or []) >= call.max_turns * 2:
        finalize(db, call, "completed")
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            f"<Say>{_xml(call.summary.splitlines()[0] if call.summary else 'Thank you.')}</Say>"
            "<Hangup/></Response>"
        )
    return _next_twiml(call)


async def handle_status(db: Session, call: Call, params: dict[str, str]) -> str:
    """Twilio status callback; maps the remote CallStatus onto the Call row."""
    remote = params.get("CallStatus") or params.get("call_status") or ""
    mapping = {"no-answer": "no_answer", "busy": "no_answer", "canceled": "cancelled"}
    local = mapping.get(remote, remote if remote in _LOCAL_FINAL else "")
    if not local or call.status == local:
        return ""
    if local in _LOCAL_FINAL:
        finalize(db, call, local)
    else:
        call.status = local
        call.updated_at = utcnow()
        db.commit()
    return ""


# --------------------------------------------------------------------------- #
# WebSocket media-stream loop (Twilio <Stream>)                                #
# --------------------------------------------------------------------------- #

async def media_stream(call_id: str, receive, send) -> None:
    """Drive an in-progress call with JSON events from a live media stream.

    Accepts `connected`, `transcript` (person speech), `dtmf` and `close`
    messages; replies with `say` legs (deterministic script lines) and a final
    `summary`. Offline tests use the mock REST path; this is the real transport.
    """
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            await send({"event": "error", "error": "CALL_NOT_FOUND"})
            return
        await send({"event": "connected", "call_id": call.id})
        while True:
            try:
                message = await receive()
            except Exception:  # noqa: BLE001 - socket closed / no pending message
                break
            if not message:
                break
            event = message.get("event") or message.get("type") or ""
            if event == "close":
                break
            if event in ("transcript", "speech", "dtmf"):
                text = str(message.get("text") or message.get("SpeechResult") or "").strip()
                if not text:
                    continue
                append_turn(db, call, "person", event, text,
                            ts=len(call.transcript or []) / 2.0)
                current = _script(call).node(call.script[0]["id"] if call.script else "intro")
                for turn in (call.transcript or []):
                    if turn.get("role") != "person" or current is None:
                        continue
                    if current.answer_key:
                        call.answers = {**(call.answers or {}),
                                        current.answer_key: turn.get("text", "")}
                    current = script_engine.advance_script(_script(call), current,
                                                           turn.get("text", ""))
                db.commit()
                if current is None or (call.transcript or []) and (
                    len(call.transcript or []) >= call.max_turns * 2):
                    finalize(db, call, "completed")
                    await send({"event": "summary", "summary": call.summary})
                    await send({"event": "hangup", "reason": "script complete"})
                    break
                if current is not None and not current.final:
                    await send({"event": "say", "text": current.speak})
                else:
                    finalize(db, call, "completed")
                    await send({"event": "summary", "summary": call.summary})
                    await send({"event": "hangup", "reason": "script complete"})
                    break
        if call.status in ("dialing", "in_progress"):
            finalize(db, call, "cancelled")