"""P7: Twilio comms — mock providers, approval gating, inbound webhook (§14–18)."""
from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.comms import CommsIdentity
from app.models.conversation import Message
from app.models.user import User
from app.services import comms as comms_service
from app.tools import registry
from app.tools.base import PermissionLevel


def _invoke(name: str, args: dict) -> dict:
    spec = registry.get(name)
    assert spec is not None, f"tool {name} is not registered"
    raw = asyncio.run(spec.tool.ainvoke(args))
    return json.loads(raw)


@pytest.fixture
def comms_settings(monkeypatch):
    """Apply comms-related env vars for one test, always restoring settings."""
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# tools: registration, permissions, mock-first provider                       #
# --------------------------------------------------------------------------- #
def test_comms_tools_registered_and_approval_gated():
    for name in ("send_sms", "send_whatsapp", "place_voice_call"):
        spec = registry.get(name)
        assert spec is not None, f"{name} missing"
        assert spec.permission == PermissionLevel.CONFIRM == 3
        assert spec.requires_internet is True
        assert spec.category == "comms"
    manifest = {e["name"]: e for e in registry.manifest()}
    for name in ("send_sms", "send_whatsapp", "place_voice_call"):
        assert manifest[name]["permission"] == 3


def test_send_sms_mock_provider_returns_receipt():
    result = _invoke("send_sms", {"phone_number": "+254 712 345 678",
                                  "message": "Jambo"})
    assert result["success"] is True
    data = result["data"]
    assert data["provider"] == "mock"
    assert data["mock"] is True
    assert data["channel"] == "sms"
    assert data["to"] == "+254712345678"
    assert data["sid"].startswith("SM")
    assert data["status"] == "queued"
    assert "Traceback" not in json.dumps(result)


def test_whatsapp_and_voice_mock_receipts():
    wa = _invoke("send_whatsapp", {"phone_number": "+254733000111",
                                   "message": "Hello WhatsApp"})
    assert wa["success"] is True
    assert wa["data"]["channel"] == "whatsapp"
    assert wa["data"]["sid"].startswith("SM")

    call = _invoke("place_voice_call", {"phone_number": "+254733000111",
                                        "message": "Your meeting is in 10 minutes."})
    assert call["success"] is True
    assert call["data"]["channel"] == "voice"
    assert call["data"]["sid"].startswith("CA")


def test_send_sms_invalid_phone_is_structured_failure():
    result = _invoke("send_sms", {"phone_number": "not-a-number",
                                  "message": "hello"})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_PHONE"
    assert "Traceback" not in json.dumps(result)


def test_send_sms_empty_message_is_structured_failure():
    result = _invoke("send_sms", {"phone_number": "+254712345678", "message": "   "})
    assert result["success"] is False
    assert result["error"]["code"] == "EMPTY_MESSAGE"


def test_twilio_provider_without_credentials_fails_structured(comms_settings):
    comms_settings(JALORA_COMMS_PROVIDER="twilio")
    result = _invoke("send_sms", {"phone_number": "+254712345678",
                                  "message": "hello"})
    assert result["success"] is False
    assert result["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert "JALORA_TWILIO" in result["error"]["details"]
    assert "Traceback" not in json.dumps(result)


def test_telnyx_provider_without_credentials_fails_structured(comms_settings):
    comms_settings(JALORA_COMMS_PROVIDER="telnyx")
    result = _invoke("send_sms", {"phone_number": "+254712345678",
                                  "message": "hello"})
    assert result["success"] is False
    assert result["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert "JALORA_TELNYX" in result["error"]["details"]
    assert "Traceback" not in json.dumps(result)


def test_unknown_provider_fails_structured(comms_settings):
    comms_settings(JALORA_COMMS_PROVIDER="carrier_pigeon")
    result = _invoke("send_sms", {"phone_number": "+254712345678", "message": "hi"})
    assert result["success"] is False
    assert result["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_auto_provider_is_mock_without_credentials(comms_settings):
    comms_settings(JALORA_COMMS_PROVIDER="auto")
    assert comms_service.resolve_provider() == "mock"
    # with a fresh key configured, auto flips to twilio
    comms_settings(JALORA_COMMS_PROVIDER="auto",
                   JALORA_TWILIO_ACCOUNT_SID="ACnew00000000000000000000000000000",
                   JALORA_TWILIO_AUTH_TOKEN="fresh-token-never-exposed-77")
    assert comms_service.resolve_provider() == "twilio"


def test_auto_provider_prefers_telnyx_over_twilio(comms_settings):
    comms_settings(JALORA_COMMS_PROVIDER="auto",
                   JALORA_TELNYX_API_KEY="key_live_fresh_77",
                   JALORA_TELNYX_ACCOUNT_SID="00000000-0000-0000-0000-000000000000",
                   JALORA_TELNYX_FROM_NUMBER="+15550001111")
    assert comms_service.resolve_provider() == "telnyx"
    # telnyx still wins when twilio is configured alongside it
    comms_settings(JALORA_COMMS_PROVIDER="auto",
                   JALORA_TELNYX_API_KEY="key_live_fresh_77",
                   JALORA_TELNYX_ACCOUNT_SID="00000000-0000-0000-0000-000000000000",
                   JALORA_TELNYX_FROM_NUMBER="+15550001111",
                   JALORA_TWILIO_ACCOUNT_SID="ACnew00000000000000000000000000000",
                   JALORA_TWILIO_AUTH_TOKEN="fresh-token-never-exposed-77")
    assert comms_service.resolve_provider() == "telnyx"


# --------------------------------------------------------------------------- #
# graph: outbound SMS pauses for approval (spec §24, §55)                      #
# --------------------------------------------------------------------------- #
def test_sms_tool_pauses_for_approval_then_sends(client, fake_router,
                                                 auth_headers, confirm_calls):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_sms",
            "args": {"phone_number": "+254700000001", "message": "hi there"},
            "id": "call_sms_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="SMS sent to your contact."))

    resp = client.post("/api/chat",
                       json={"message": "Text +254700000001 saying hi there"},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paused_approval"
    approval = data["approval"]
    assert approval is not None
    assert approval["tool_name"] == "send_sms"
    assert approval["arguments"]["phone_number"] == "+254700000001"
    assert approval["level"] == 3

    pending = client.get("/api/approvals", headers=headers).json()
    assert len(pending) == 1 and pending[0]["status"] == "pending"

    result = client.post(f"/api/approvals/{approval['id']}/approve",
                         headers=headers)
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["status"] == "completed"
    assert final["reply"] == "SMS sent to your contact."

    activity = client.get("/api/activity", headers=headers).json()
    assert "send_sms" in json.dumps(activity)


def test_sms_tool_rejection_never_sends(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_sms",
            "args": {"phone_number": "+254700000002", "message": "do not send"},
            "id": "call_sms_2", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Understood — nothing was sent."))

    resp = client.post("/api/chat", json={"message": "Send that SMS"},
                       headers=headers)
    approval = resp.json()["approval"]
    assert approval is not None

    result = client.post(f"/api/approvals/{approval['id']}/reject",
                         headers=headers)
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["status"] == "completed"

    # no send occurred: the mock receipt never reached the activity feed
    activity = client.get("/api/activity", headers=headers).json()
    assert "queued" not in json.dumps(activity)


# --------------------------------------------------------------------------- #
# inbound webhook: TwiML reply, guest identity, signature (spec §14–18)        #
# --------------------------------------------------------------------------- #
def test_inbound_sms_replies_and_reuses_guest_identity(client, fake_router):
    fake_router.script.append(AIMessage(content="Karibu! How can I help?"))
    resp = client.post("/api/comms/twilio/inbound", data={
        "From": "+254711100022", "To": "+15550001111", "Body": "hello jalora",
    })
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/xml")
    assert "<Response>" in resp.text
    assert "Karibu! How can I help?" in resp.text

    # second message from the same sender: same guest, same conversation
    fake_router.script.append(AIMessage(content="Second reply."))
    resp2 = client.post("/api/comms/twilio/inbound", data={
        "From": "+254711100022", "To": "+15550001111", "Body": "and again",
    })
    assert resp2.status_code == 200
    assert "Second reply." in resp2.text

    with SessionLocal() as db:
        guests = db.scalars(
            select(User).where(User.email.like("guest-sms-%"))).all()
        assert len(guests) == 1
        identities = db.scalars(select(CommsIdentity)).all()
        assert len(identities) == 1
        assert identities[0].channel == "sms"
        assert identities[0].user_id == guests[0].id
        assert identities[0].conversation_id
        msg_count = db.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == identities[0].conversation_id))
        assert msg_count == 4  # 2 inbound + 2 assistant replies


def test_inbound_whatsapp_channel_identity(client, fake_router):
    fake_router.script.append(AIMessage(content="WhatsApp reply."))
    resp = client.post("/api/comms/twilio/inbound", data={
        "From": "whatsapp:+254733300044", "To": "whatsapp:+15550002222",
        "Body": "hi from whatsapp",
    })
    assert resp.status_code == 200, resp.text
    assert "WhatsApp reply." in resp.text

    with SessionLocal() as db:
        identity = db.scalar(select(CommsIdentity))
        assert identity is not None
        assert identity.channel == "whatsapp"
        assert identity.address == "+254733300044"


def test_inbound_invalid_sender_gets_empty_twiml(client, fake_router):
    resp = client.post("/api/comms/twilio/inbound", data={
        "From": "not-a-phone", "To": "+15550001111", "Body": "hello",
    })
    assert resp.status_code == 200
    assert "<Message>" not in resp.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count(User.id))) == 0


def test_inbound_signature_enforced_when_token_configured(
        client, fake_router, comms_settings):
    comms_settings(JALORA_TWILIO_AUTH_TOKEN="tok_abc123_not_reused_77")
    url = "http://testserver/api/comms/twilio/inbound"
    params = {"From": "+254722200033", "To": "+15550001111",
              "Body": "signed hello"}
    get_settings.cache_clear()

    # missing signature -> rejected before any agent work
    resp = client.post(url, data=params)
    assert resp.status_code == 403

    # valid signature -> processed
    signature = comms_service.compute_signature("tok_abc123_not_reused_77",
                                                url, params)
    fake_router.script.append(AIMessage(content="Signed and sealed."))
    resp = client.post(url, data=params,
                       headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 200, resp.text
    assert "Signed and sealed." in resp.text

    # tampered body with old signature -> rejected
    tampered = {**params, "Body": "tampered content"}
    resp = client.post(url, data=tampered,
                       headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Twilio signature helper unit tests                                          #
# --------------------------------------------------------------------------- #
def test_signature_helper_roundtrip_and_tamper_detection():
    token = "STtesttoken0123456789"
    url = "https://api.jalora.example/api/comms/twilio/inbound"
    params = {"From": "+254712345678", "To": "+15550001111", "Body": "hey"}

    signature = comms_service.compute_signature(token, url, params)
    assert comms_service.verify_signature(token, url, params, signature) is True

    # param order must not matter (Twilio sorts params)
    reordered = dict(reversed(list(params.items())))
    assert comms_service.verify_signature(token, url, reordered, signature) is True

    # missing / wrong signature, wrong token, tampered param all fail
    assert comms_service.verify_signature(token, url, params, None) is False
    assert comms_service.verify_signature(token, url, params, "") is False
    assert comms_service.verify_signature(token, url, params,
                                          "b3JvbmcvZmFsc2U=") is False
    assert comms_service.verify_signature("other-token", url, params,
                                          signature) is False
    assert comms_service.verify_signature(
        token, url, {**params, "Body": "changed"}, signature) is False
    assert comms_service.verify_signature(token, url + "/x", params,
                                          signature) is False


def test_normalize_phone_accepts_local_and_e164_formats():
    assert comms_service.normalize_phone("+254 712 345 678") == "+254712345678"
    assert comms_service.normalize_phone("254712345678") == "+254712345678"
    # leading trunk zero has no country code -> honestly rejected as E.164
    with pytest.raises(comms_service.CommsError) as exc:
        comms_service.normalize_phone("0712345678")
    assert exc.value.code == "INVALID_PHONE"
    with pytest.raises(comms_service.CommsError) as exc:
        comms_service.normalize_phone("")
    assert exc.value.code == "INVALID_PHONE"


# --------------------------------------------------------------------------- #
# Telnyx inbound webhook (§14-18) + Ed25519 signature helper                   #
# --------------------------------------------------------------------------- #
def _ed25519_keypair() -> tuple[str, object]:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(public_raw).decode(), private


def test_verify_telnyx_signature_roundtrip_and_tamper_detection():
    import base64

    pub_b64, private = _ed25519_keypair()
    timestamp = "1700000000"
    body = b'{"data":{"event_type":"message.received"}}'
    signature = base64.b64encode(
        private.sign(f"{timestamp}|".encode("utf-8") + body)).decode()

    assert comms_service.verify_telnyx_signature(
        pub_b64, signature, timestamp, body) is True
    # tampered body / wrong timestamp / missing inputs all fail
    assert comms_service.verify_telnyx_signature(
        pub_b64, signature, timestamp, body + b"x") is False
    assert comms_service.verify_telnyx_signature(
        pub_b64, signature, "1700000001", body) is False
    assert comms_service.verify_telnyx_signature("", signature, timestamp, body) is False
    assert comms_service.verify_telnyx_signature(pub_b64, "", timestamp, body) is False
    assert comms_service.verify_telnyx_signature(pub_b64, signature, "", body) is False


def test_telnyx_inbound_sms_replies_and_reuses_guest_identity(client, fake_router):
    fake_router.script.append(AIMessage(content="Karibu Telnyx!"))
    payload = {"data": {
        "event_type": "message.received", "id": "evt_1",
        "payload": {
            "id": "msg_1", "from": {"phone_number": "+254711100099"},
            "to": [{"phone_number": "+15550001111"}], "text": "hello telnyx",
            "type": "SMS",
        },
    }}
    resp = client.post("/api/comms/telnyx/inbound", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    with SessionLocal() as db:
        identity = db.scalar(select(CommsIdentity))
        assert identity is not None
        assert identity.channel == "sms"
        assert identity.address == "+254711100099"
        assert identity.conversation_id


def test_telnyx_inbound_whatsapp_channel_detection(client, fake_router,
                                                   comms_settings):
    comms_settings(JALORA_TELNYX_WHATSAPP_FROM="+15550002222")
    fake_router.script.append(AIMessage(content="WhatsApp via Telnyx."))
    payload = {"data": {
        "event_type": "message.received",
        "payload": {
            "from": {"phone_number": "+254733300055"},
            "to": [{"phone_number": "+15550002222"}], "text": "hi whatsapp",
        },
    }}
    resp = client.post("/api/comms/telnyx/inbound", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    with SessionLocal() as db:
        identity = db.scalar(select(CommsIdentity))
        assert identity is not None
        assert identity.channel == "whatsapp"
        assert identity.address == "+254733300055"


def test_telnyx_inbound_ignores_non_message_events(client, fake_router):
    resp = client.post("/api/comms/telnyx/inbound", json={"data": {
        "event_type": "message.sent", "payload": {}}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_telnyx_inbound_signature_enforced_when_key_configured(
        client, fake_router, comms_settings):
    import base64

    pub_b64, private = _ed25519_keypair()
    comms_settings(JALORA_TELNYX_PUBLIC_KEY=pub_b64)

    body = json.dumps({"data": {
        "event_type": "message.received",
        "payload": {"from": {"phone_number": "+254722200077"},
                    "to": [{"phone_number": "+15550001111"}],
                    "text": "signed telnyx"}}})
    timestamp = "1700000000"
    signature = base64.b64encode(
        private.sign(f"{timestamp}|".encode("utf-8") + body.encode())).decode()
    json_headers = {"content-type": "application/json"}

    # missing signature -> rejected before any agent work
    resp = client.post("/api/comms/telnyx/inbound", content=body,
                       headers=json_headers)
    assert resp.status_code == 403

    # valid signature -> processed
    fake_router.script.append(AIMessage(content="Signed Telnyx."))
    resp = client.post("/api/comms/telnyx/inbound", content=body, headers={
        **json_headers, "telnyx-signature-ed25519": signature,
        "telnyx-timestamp": timestamp,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    # tampered body with the old signature -> rejected
    resp = client.post("/api/comms/telnyx/inbound", content=body + " ",
                       headers={**json_headers,
                                "telnyx-signature-ed25519": signature,
                                "telnyx-timestamp": timestamp})
    assert resp.status_code == 403
