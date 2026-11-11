"""Outbound messaging providers: SMS, WhatsApp, voice (spec §14–18, §55–56).

Mock-first: `auto` resolves to telnyx when configured, then twilio when its
account sid + auth token are set, otherwise to the deterministic offline mock.
Real credentials must be fresh — never reuse keys that were ever exposed (§77).
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import re
import uuid
from dataclasses import dataclass

import httpx

from app.config import get_settings

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164
TELNYX_API_BASE = "https://api.telnyx.com/v2"


class CommsError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class CommsReceipt:
    provider: str  # mock | twilio | telnyx
    channel: str   # sms | whatsapp | voice
    to: str
    sid: str
    status: str    # queued | sent | failed

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "channel": self.channel, "to": self.to,
            "sid": self.sid, "status": self.status, "mock": self.provider == "mock",
        }


def normalize_phone(raw: str) -> str:
    """Accept +E.164 or local digits; return E.164 or raise CommsError."""
    cleaned = re.sub(r"[\s().-]", "", (raw or "").strip())
    if not cleaned:
        raise CommsError("INVALID_PHONE", "Phone number is empty.")
    if cleaned.isdigit() and len(cleaned) >= 8:
        cleaned = f"+{cleaned}"
    if not _PHONE_RE.match(cleaned):
        raise CommsError(
            "INVALID_PHONE",
            f"Not a valid E.164 phone number: {raw!r} (expected e.g. +254712345678).",
        )
    return cleaned


def resolve_provider() -> str:
    settings = get_settings()
    choice = (settings.comms_provider or "auto").strip().lower()
    if choice == "auto":
        if settings.telnyx_comms_configured:
            return "telnyx"
        if settings.twilio_comms_configured:
            return "twilio"
        return "mock"
    if choice not in ("mock", "twilio", "telnyx"):
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         f"Unknown comms provider '{choice}' (auto|mock|twilio|telnyx).")
    return choice


def _mock_receipt(channel: str, to: str) -> CommsReceipt:
    prefix = {"sms": "SM", "whatsapp": "SM", "voice": "CA"}[channel]
    return CommsReceipt(
        provider="mock", channel=channel, to=to,
        sid=f"{prefix}{uuid.uuid4().hex[:14].upper()}", status="queued",
    )


async def _twilio_request(path: str, data: dict) -> dict:
    settings = get_settings()
    sid, token = settings.twilio_account_sid, settings.twilio_auth_token
    if not sid or not token:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "Twilio credentials are not configured (set "
                         "JALORA_TWILIO_ACCOUNT_SID and JALORA_TWILIO_AUTH_TOKEN "
                         "with a freshly issued key, §77).")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/{path}.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url, data=data, auth=(sid, token),
            headers={"User-Agent": "JaloraAI/0.1"},
        )
    if resp.status_code >= 400:
        raise CommsError("PROVIDER_ERROR",
                         f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def _telnyx_request(path: str, *, method: str = "POST",
                          json: dict | None = None,
                          data: dict | None = None) -> dict:
    """Call the Telnyx v2 REST API with bearer auth (keys never in source, §77)."""
    settings = get_settings()
    api_key = settings.telnyx_api_key
    if not api_key:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "Telnyx credentials are not configured (set "
                         "JALORA_TELNYX_API_KEY with a freshly issued key, §77).")
    url = f"{TELNYX_API_BASE}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "JaloraAI/0.1"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, json=json, data=data,
                                    headers=headers)
    if resp.status_code >= 400:
        raise CommsError("PROVIDER_ERROR",
                         f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _telnyx_call_scope() -> tuple[str, str]:
    """Return (account_sid, api_key) for the TeXML Calls endpoint, or raise."""
    settings = get_settings()
    if not settings.telnyx_account_sid:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "JALORA_TELNYX_ACCOUNT_SID is not set.")
    return settings.telnyx_account_sid, settings.telnyx_api_key or ""


def _telnyx_sid(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(payload.get("id") or data.get("id") or payload.get("sid") or "")


def _telnyx_status(payload: dict, default: str = "queued") -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(payload.get("status") or data.get("status") or default)


async def send_sms(to: str, body: str) -> CommsReceipt:
    to = normalize_phone(to)
    settings = get_settings()
    if not body or not body.strip():
        raise CommsError("EMPTY_MESSAGE", "Message body is empty.")
    provider = resolve_provider()
    if provider == "mock":
        return _mock_receipt("sms", to)
    if provider == "telnyx":
        if not settings.telnyx_from_number:
            raise CommsError("PROVIDER_NOT_CONFIGURED",
                             "JALORA_TELNYX_FROM_NUMBER is not set.")
        payload_in: dict = {
            "from": settings.telnyx_from_number, "to": to, "text": body,
        }
        if settings.telnyx_messaging_profile_id:
            payload_in["messaging_profile_id"] = settings.telnyx_messaging_profile_id
        payload = await _telnyx_request("messages", json=payload_in)
        return CommsReceipt(provider="telnyx", channel="sms", to=to,
                            sid=_telnyx_sid(payload),
                            status=_telnyx_status(payload))
    if not settings.twilio_from_number:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "JALORA_TWILIO_FROM_NUMBER is not set.")
    payload = await _twilio_request("Messages", {
        "To": to, "From": settings.twilio_from_number, "Body": body,
    })
    return CommsReceipt(provider="twilio", channel="sms", to=to,
                        sid=payload.get("sid", ""), status=payload.get("status", "queued"))


async def send_whatsapp(to: str, body: str) -> CommsReceipt:
    to = normalize_phone(to)
    settings = get_settings()
    if not body or not body.strip():
        raise CommsError("EMPTY_MESSAGE", "Message body is empty.")
    provider = resolve_provider()
    if provider == "mock":
        return _mock_receipt("whatsapp", to)
    if provider == "telnyx":
        sender = settings.telnyx_whatsapp_from or settings.telnyx_from_number
        if not sender:
            raise CommsError("PROVIDER_NOT_CONFIGURED",
                             "JALORA_TELNYX_WHATSAPP_FROM is not set.")
        payload_in: dict = {
            "from": sender, "to": to,
            "whatsapp_message": {"type": "text", "text": {"body": body}},
        }
        if settings.telnyx_messaging_profile_id:
            payload_in["messaging_profile_id"] = settings.telnyx_messaging_profile_id
        payload = await _telnyx_request("messages/whatsapp", json=payload_in)
        return CommsReceipt(provider="telnyx", channel="whatsapp", to=to,
                            sid=_telnyx_sid(payload),
                            status=_telnyx_status(payload))
    if not settings.twilio_whatsapp_from:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "JALORA_TWILIO_WHATSAPP_FROM is not set.")
    payload = await _twilio_request("Messages", {
        "To": f"whatsapp:{to}", "From": settings.twilio_whatsapp_from, "Body": body,
    })
    return CommsReceipt(provider="twilio", channel="whatsapp", to=to,
                        sid=payload.get("sid", ""), status=payload.get("status", "queued"))


async def place_voice_call(to: str, message: str) -> CommsReceipt:
    """Outbound voice call that reads `message` aloud (TeXML/TwiML <Say>)."""
    to = normalize_phone(to)
    settings = get_settings()
    if not message or not message.strip():
        raise CommsError("EMPTY_MESSAGE", "Call message is empty.")
    provider = resolve_provider()
    if provider == "mock":
        return _mock_receipt("voice", to)
    safe = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    if provider == "telnyx":
        if not settings.telnyx_from_number:
            raise CommsError("PROVIDER_NOT_CONFIGURED",
                             "JALORA_TELNYX_FROM_NUMBER is not set.")
        account_sid, _ = _telnyx_call_scope()
        payload_in: dict = {
            "To": to, "From": settings.telnyx_from_number,
            "Texml": f"<Response><Say>{safe}</Say></Response>",
        }
        if settings.telnyx_application_id:
            payload_in["ApplicationSid"] = settings.telnyx_application_id
        payload = await _telnyx_request(
            f"texml/Accounts/{account_sid}/Calls", json=payload_in)
        return CommsReceipt(provider="telnyx", channel="voice", to=to,
                            sid=_telnyx_sid(payload),
                            status=_telnyx_status(payload))
    if not settings.twilio_from_number:
        raise CommsError("PROVIDER_NOT_CONFIGURED",
                         "JALORA_TWILIO_FROM_NUMBER is not set.")
    payload = await _twilio_request("Calls", {
        "To": to, "From": settings.twilio_from_number,
        "Twiml": f"<Response><Say>{safe}</Say></Response>",
    })
    return CommsReceipt(provider="twilio", channel="voice", to=to,
                        sid=payload.get("sid", ""), status=payload.get("status", "queued"))


# --------------------------------------------------------------------------- #
# Twilio request signature (§14 inbound webhooks)                              #
# --------------------------------------------------------------------------- #
def compute_signature(token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's HMAC-SHA1 signature: sort params, concat k+v, sign url+concat."""
    items = sorted(
        (str(k), v if isinstance(v, str) else str(v))
        for k, v in params.items() if k != "Twilio-Signature"
    )
    data = url + "".join(f"{k}{v}" for k, v in items)
    digest = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(digest.digest()).decode("ascii")


def verify_signature(token: str, url: str, params: dict[str, str],
                     signature: str | None) -> bool:
    if not signature:
        return False
    expected = compute_signature(token, url, params)
    return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------- #
# Telnyx webhook signature (§14 inbound webhooks)                              #
# --------------------------------------------------------------------------- #
def verify_telnyx_signature(public_key_b64: str, signature_b64: str,
                            timestamp: str, raw_body: bytes) -> bool:
    """Verify a Telnyx Ed25519 webhook signature.

    Telnyx signs ``f"{timestamp}|"`` + the raw request body with the account's
    Ed25519 private key; the matching public key (base64) comes from the Portal
    (Keys & Credentials -> Public Key) and the signature is sent base64-encoded
    in the ``telnyx-signature-ed25519`` header alongside ``telnyx-timestamp``.
    """
    if not public_key_b64 or not signature_b64 or not timestamp:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64))
        signed = f"{timestamp}|".encode("utf-8") + raw_body
        public_key.verify(base64.b64decode(signature_b64), signed)
        return True
    except Exception:  # noqa: BLE001 - any failure means unauthenticated
        return False
