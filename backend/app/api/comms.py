"""Comms webhooks: inbound SMS/WhatsApp (Twilio + Telnyx) -> agent turn (§14–18)."""
from __future__ import annotations

import json
import re
import secrets
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_router
from app.auth.security import hash_password
from app.config import get_settings
from app.database.session import SessionLocal
from app.events import ListEmitter
from app.models.comms import CommsIdentity
from app.models.user import User
from app.services import chat_service
from app.services import comms as comms_service
from app.services.model_router import ModelRouter

router = APIRouter(prefix="/api/comms", tags=["comms"])

_XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'


def _twiml(message: str) -> Response:
    body = f"{_XML_HEAD}<Response><Message>{escape(message)}</Message></Response>"
    return Response(content=body, media_type="text/xml")


def _empty_twiml() -> Response:
    return Response(content=f"{_XML_HEAD}<Response></Response>", media_type="text/xml")


async def _read_params(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        raw = (await request.body()).decode("utf-8", errors="replace")
        return dict(parse_qsl(raw, keep_blank_values=True))
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


def _resolve_identity(db: Session, channel: str, address: str) -> CommsIdentity:
    identity = db.scalar(select(CommsIdentity).where(
        CommsIdentity.channel == channel, CommsIdentity.address == address))
    if identity is not None:
        return identity
    slug = re.sub(r"\D", "", address)[-10:] or "0000000000"
    email = f"guest-{channel}-{slug}@comms.jalora.local"
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            name=f"{channel.title()} {address}",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            prefs={"memory_enabled": True, "locale": "en"},
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    identity = CommsIdentity(user_id=user.id, channel=channel, address=address)
    db.add(identity)
    db.commit()
    db.refresh(identity)
    return identity


async def _agent_reply(channel: str, address: str, body: str,
                       model_router: ModelRouter) -> str:
    """Resolve the sender's identity, run one agent turn, return the reply text."""
    with SessionLocal() as db:
        identity = _resolve_identity(db, channel, address)
        identity_id = identity.id
        user_id, conversation_id = identity.user_id, identity.conversation_id

    outcome = await chat_service.execute_chat(
        user_id=user_id,
        request_message=body,
        conversation_id=conversation_id,
        router=model_router,
        emitter=ListEmitter(),
    )

    reply = (outcome.reply or "").strip()
    if outcome.status == "paused_approval":
        reply = ("This request needs your approval in the Jalora web app "
                 "before I can continue.")
    elif outcome.status == "error":
        reply = "Sorry — I could not process that message right now."
    elif outcome.status == "stopped":
        reply = reply or "Stopped."
    elif not reply:
        reply = "Okay."

    if outcome.conversation_id:
        with SessionLocal() as db:
            row = db.get(CommsIdentity, identity_id)
            if row is not None:
                row.conversation_id = outcome.conversation_id
                db.commit()

    return reply[:1500]


@router.post("/twilio/inbound")
async def twilio_inbound(request: Request,
                         model_router: ModelRouter = Depends(get_router)):
    """Twilio inbound SMS/WhatsApp webhook: run one agent turn, reply via TwiML."""
    settings = get_settings()
    params = await _read_params(request)

    if settings.twilio_auth_token:
        signature = request.headers.get("X-Twilio-Signature")
        if not comms_service.verify_signature(
            settings.twilio_auth_token, str(request.url), params, signature
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")

    raw_from = params.get("From", "").strip()
    body = params.get("Body", "").strip()
    if not raw_from or not body:
        return _empty_twiml()

    channel = "whatsapp" if raw_from.lower().startswith("whatsapp:") else "sms"
    address_raw = raw_from.split(":", 1)[-1]
    try:
        address = comms_service.normalize_phone(address_raw)
    except comms_service.CommsError:
        return _empty_twiml()

    reply = await _agent_reply(channel, address, body, model_router)
    return _twiml(reply)


def _parse_telnyx_event(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _telnyx_channel(to_number: str, settings) -> str:
    """Infer sms vs whatsapp from the receiving number (WhatsApp sender match)."""
    wa = settings.telnyx_whatsapp_from or ""
    if to_number and wa:
        try:
            if comms_service.normalize_phone(to_number) == \
                    comms_service.normalize_phone(wa):
                return "whatsapp"
        except comms_service.CommsError:
            pass
    return "sms"


@router.post("/telnyx/inbound")
async def telnyx_inbound(request: Request,
                         model_router: ModelRouter = Depends(get_router)) -> dict:
    """Telnyx inbound SMS/WhatsApp webhook: run one agent turn, reply via REST.

    Telnyx messaging webhooks are JSON (`message.received`) and replies are sent
    back through the Messages API (no TwiML), so this returns 200 with no body.
    """
    settings = get_settings()
    raw = await request.body()

    if settings.telnyx_public_key:
        sig = request.headers.get("telnyx-signature-ed25519", "")
        ts = request.headers.get("telnyx-timestamp", "")
        if not comms_service.verify_telnyx_signature(
            settings.telnyx_public_key, sig, ts, raw
        ):
            raise HTTPException(status_code=403, detail="Invalid Telnyx signature.")

    data = _parse_telnyx_event(raw)
    event = data.get("data") if isinstance(data, dict) else None
    if not isinstance(event, dict) or event.get("event_type") != "message.received":
        return {"status": "ignored"}

    payload = event.get("payload") or {}
    raw_from = str((payload.get("from") or {}).get("phone_number") or "").strip()
    tos = payload.get("to") or []
    if isinstance(tos, list) and tos:
        to_number = str((tos[0] or {}).get("phone_number") or "")
    elif isinstance(tos, dict):
        to_number = str(tos.get("phone_number") or "")
    else:
        to_number = ""
    text = str(payload.get("text") or "").strip()
    if not raw_from or not text:
        return {"status": "ignored"}

    try:
        address = comms_service.normalize_phone(raw_from)
    except comms_service.CommsError:
        return {"status": "ignored"}

    channel = _telnyx_channel(to_number, settings)
    reply = await _agent_reply(channel, address, text, model_router)

    try:
        if channel == "whatsapp":
            await comms_service.send_whatsapp(address, reply)
        else:
            await comms_service.send_sms(address, reply)
    except comms_service.CommsError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Failed to send reply: {exc.detail}") from exc

    return {"status": "ok"}
