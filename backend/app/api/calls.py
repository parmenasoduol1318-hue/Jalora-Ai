"""Phone call API (§31-33, §57): REST for users, provider webhooks for TwiML.

The REST routes are bearer-authenticated. Provider status callbacks are
signature-verified before they can update delivery state. The answer/gather
routes remain provider webhooks; the media-stream route drives a live call from
transcript/DTMF events.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.database.session import SessionLocal
from app.errors import BadRequestError
from app.events import emit
from app.models.call import Call
from app.models.user import User
from app.services import comms as comms_service
from app.services import voice, voice_telnyx, voice_twilio

router = APIRouter(prefix="/api/calls", tags=["calls"])


class CallCreate(BaseModel):
    phone_number: str = Field(..., description="E.164 target, e.g. +254712345678")
    purpose: str = Field(..., min_length=1, max_length=2000)
    confirm: bool = False
    questions: Optional[list[str]] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    conversation_id: Optional[str] = None
    provider: Optional[str] = None  # auto | mock | twilio | telnyx
    mode: Optional[str] = "script"  # script (TwiML <Gather>) | live (Media Streams)


@router.post("", status_code=201)
async def create_call(body: CallCreate, user: User = Depends(get_current_user)) -> dict:
    """Create and run a call. Uses the mock provider offline; dials via Telnyx
    or Twilio when configured. Direct REST calls require explicit confirmation;
    status must be checked through the returned call ID and status callbacks."""
    if not body.confirm:
        raise BadRequestError(
            "Confirm before placing this phone call.",
            "CALL_CONFIRMATION_REQUIRED",
        )
    try:
        return await voice.place_call(
            user_id=user.id,
            to_phone=body.phone_number,
            purpose=body.purpose,
            questions=body.questions,
            run_id=body.run_id or None,
            task_id=body.task_id or None,
            conversation_id=body.conversation_id or None,
            provider_override=body.provider or None,
            mode=body.mode or "script",
        )
    except comms_service.CommsError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from None


@router.get("")
async def list_calls_endpoint(
    task_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
) -> list[dict]:
    return voice.list_calls(user.id, task_id=task_id, status=status, limit=min(limit, 200))


@router.get("/{call_id}")
async def call_detail(call_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> dict:
    try:
        call = voice.get_call(db, call_id, user.id)
    except comms_service.CommsError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from None
    return voice.call_view(call)


@router.post("/{call_id}/hangup")
async def hangup_endpoint(call_id: str, user: User = Depends(get_current_user)) -> dict:
    try:
        return await voice.hangup(user.id, call_id)
    except comms_service.CommsError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from None


# --------------------------------------------------------------------------- #
# Twilio webhooks (form-encoded, unauthenticated)                              #
# --------------------------------------------------------------------------- #

@router.post("/{call_id}/answer")
async def answer_webhook(call_id: str) -> Response:
    """Twilio asks for TwiML when the callee picks up."""
    with SessionLocal() as db:
        try:
            call = voice.get_call(db, call_id)
        except comms_service.CommsError:
            return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
                            media_type="text/xml")
        twiml = await voice.handle_answer(db, call, {})
    return Response(content=twiml, media_type="text/xml")


@router.post("/{call_id}/gather")
async def gather_webhook(call_id: str, request: Request) -> Response:
    """Person reply captured by <Gather>; speak the next script leg or hang up."""
    from app.api.comms import _read_params

    params = await _read_params(request)
    with SessionLocal() as db:
        try:
            call = voice.get_call(db, call_id)
        except comms_service.CommsError:
            return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
                            media_type="text/xml")
        out = await voice.handle_gather(db, call, params)
    return Response(content=out, media_type="text/xml")


@router.post("/{call_id}/status")
async def status_webhook(call_id: str, request: Request) -> Response:
    """Twilio status callback (ringing/completed/failed/no-answer/...)."""
    from app.api.comms import _read_params

    params = await _read_params(request)
    with SessionLocal() as db:
        try:
            call = voice.get_call(db, call_id)
        except comms_service.CommsError:
            return Response(status_code=200)
        await _verify_status_callback(request, call, params)
        await voice.handle_status(db, call, params)
    return Response(status_code=200)


async def _verify_status_callback(
    request: Request, call: Call, params: dict[str, str],
) -> None:
    settings = get_settings()
    if settings.env.lower() == "test" or call.provider == "mock":
        return
    if call.provider == "twilio":
        if not settings.twilio_auth_token:
            raise HTTPException(
                status_code=503,
                detail="Twilio status callbacks cannot be verified without an auth token.",
            )
        signature = request.headers.get("X-Twilio-Signature")
        if not comms_service.verify_signature(
            settings.twilio_auth_token, str(request.url), params, signature,
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")
        return
    if call.provider == "telnyx":
        if not settings.telnyx_public_key:
            raise HTTPException(
                status_code=503,
                detail="Telnyx status callbacks cannot be verified without a public key.",
            )
        if not comms_service.verify_telnyx_signature(
            settings.telnyx_public_key,
            request.headers.get("telnyx-signature-ed25519", ""),
            request.headers.get("telnyx-timestamp", ""),
            await request.body(),
        ):
            raise HTTPException(status_code=403, detail="Invalid Telnyx signature.")
        return
    raise HTTPException(status_code=400, detail="Unsupported call provider.")


@router.websocket("/{call_id}/media")
async def media_websocket(websocket: WebSocket, call_id: str) -> None:
    """Media-stream WebSocket.

    Real Twilio Media Streams / Telnyx Media Streams frames
    (`connected`/`start`/`media`/`stop`) are bridged to a live `VoiceSession`
    (auth: signed `?token=`; `?provider=telnyx` selects the Telnyx dialect). A
    non-provider JSON protocol (`transcript`/`dtmf`/`close`) still drives the
    deterministic script loop.
    """
    await websocket.accept()

    async def receive() -> dict | None:
        try:
            raw = await websocket.receive_text()
        except Exception:  # noqa: BLE001 - client disconnected
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    async def send(payload: dict) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:  # noqa: BLE001
            pass

    try:
        first = await receive()
        if first is None:
            return
        provider = (websocket.query_params.get("provider") or "").lower()
        token = websocket.query_params.get("token", "")
        if provider == "telnyx":
            if not voice_telnyx.verify_stream_token(call_id, token):
                await websocket.close(code=1008)  # policy violation
                return
            await voice_telnyx.run_bridge(call_id, first, receive, send)
            return
        if voice_twilio.is_twilio_message(first):
            if not voice_twilio.verify_stream_token(call_id, token):
                await websocket.close(code=1008)  # policy violation
                return
            await voice_twilio.run_bridge(call_id, first, receive, send)
            return

        await voice.media_stream(call_id, _prepend(first, receive), send)
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


def _prepend(first: dict, receive):
    """Return a receive() that yields ``first`` once, then delegates."""
    state = {"done": False}

    async def _receive() -> dict | None:
        if not state["done"]:
            state["done"] = True
            return first
        return await receive()

    return _receive


# --------------------------------------------------------------------------- #
# inbound calls (Twilio "A CALL COMES IN" webhook)                            #
# --------------------------------------------------------------------------- #

@router.post("/inbound")
async def inbound_webhook(request: Request) -> Response:
    """Answer an inbound call with a live VoiceSession on the owner's behalf.

    Works for both Telnyx (TeXML) and Twilio; the provider is detected from the
    webhook signature headers (``telnyx-signature-ed25519`` vs
    ``X-Twilio-Signature``), falling back to whichever is configured. Signatures
    are verified whenever the matching key/token is present.

    Enabled only when `JALORA_VOICE_INBOUND_ENABLED` is set and an owner email is
    configured (`JALORA_VOICE_INBOUND_EMAIL`).
    """
    from app.api.comms import _read_params

    settings = get_settings()
    raw = await request.body()
    params = await _read_params(request)

    provider = _inbound_provider(request, settings)

    if provider == "telnyx":
        if settings.telnyx_public_key:
            sig = request.headers.get("telnyx-signature-ed25519", "")
            ts = request.headers.get("telnyx-timestamp", "")
            if not comms_service.verify_telnyx_signature(
                settings.telnyx_public_key, sig, ts, raw,
            ):
                return Response(status_code=403)
    elif settings.twilio_auth_token:
        signature = request.headers.get("X-Twilio-Signature")
        if not comms_service.verify_signature(
            settings.twilio_auth_token, str(request.url), params, signature,
        ):
            return Response(status_code=403)

    if not getattr(settings, "voice_inbound_enabled", False):
        return _twiml(_reject_for(provider, "This number is not taking calls right now."))

    email = getattr(settings, "voice_inbound_email", None)
    with SessionLocal() as db:
        owner = None
        if email:
            owner = db.scalar(select(User).where(User.email == email))
        if owner is None or not owner.is_active:
            return _twiml(_reject_for(provider, "This number is not configured yet."))
        call = Call(
            user_id=owner.id, kind="inbound", mode="live", provider=provider,
            from_phone=params.get("From") or None,
            to_phone=params.get("To") or None,
            purpose="Inbound phone call: converse freely on the owner's behalf, "
                    "answer questions, and take a message or note any request.",
            status="in_progress", answers={}, transcript=[],
            max_turns=settings.call_max_turns,
            max_duration_seconds=settings.call_max_duration_seconds,
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        emit("call", {
            "call_id": call.id, "status": "in_progress",
            "to_phone": call.to_phone, "from_phone": call.from_phone,
            "provider": provider, "kind": "inbound",
        })
        twiml = _answer_for(provider, call)
    return _twiml(twiml)


def _inbound_provider(request: Request, settings) -> str:
    """Detect the calling telephony provider from headers, then config."""
    if request.headers.get("telnyx-signature-ed25519"):
        return "telnyx"
    if request.headers.get("X-Twilio-Signature"):
        return "twilio"
    if settings.telnyx_comms_configured:
        return "telnyx"
    return "twilio"


def _answer_for(provider: str, call) -> str:
    if provider == "telnyx":
        return voice_telnyx.answer_texml(call)
    return voice_twilio.answer_twiml(call)


def _reject_for(provider: str, message: str) -> str:
    if provider == "telnyx":
        return voice_telnyx.reject_texml(message)
    return voice_twilio.reject_twiml(message)


def _twiml(body: str) -> Response:
    return Response(content=body, media_type="text/xml")