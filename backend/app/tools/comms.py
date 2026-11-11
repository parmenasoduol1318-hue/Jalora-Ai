"""Messaging tools: SMS, WhatsApp and outbound voice calls (§14–18).

Phone numbers may be given literally (E.164) or as a contact name — the name is
resolved through the user's address book (§65), so "text John the invite" works.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import comms
from app.services import contacts as contacts_service
from app.tools.base import PermissionLevel, registry


def _resolve_phone(number_or_name: str) -> str | ToolResult:
    """Return E.164, or a failed ToolResult when the contact can't be resolved."""
    value = (number_or_name or "").strip()
    if contacts_service.looks_like_phone(value):
        return value
    try:
        user_id = get_tool_context().user_id
    except RuntimeError:
        # No agent run bound (e.g. direct invocation): hand the raw value to the
        # comms layer so it can reject it with a structured INVALID_PHONE.
        return value
    try:
        return contacts_service.resolve_phone_number(user_id, value)
    except contacts_service.ContactError as exc:
        # The input was neither a valid phone nor a saved contact. Surface it as
        # INVALID_PHONE (an invalid recipient) rather than a misleading
        # "contact not found" for something that wasn't a recognised name. The
        # detail still explains exactly what we looked for, so the user is never
        # left guessing.
        return ToolResult.fail("INVALID_PHONE", exc.detail, message=exc.detail)


async def send_sms(phone_number: str, message: str) -> ToolResult:
    """Send an SMS to a phone number (E.164, e.g. +254712345678) or a contact name. May pause for confirmation per the user's approval settings before sending."""
    phone = _resolve_phone(phone_number)
    if isinstance(phone, ToolResult):
        return phone
    try:
        receipt = await comms.send_sms(phone, message)
    except comms.CommsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"SMS queued to {receipt.to} ({receipt.provider} provider).",
        receipt.as_dict(),
    )


async def send_whatsapp(phone_number: str, message: str) -> ToolResult:
    """Send a WhatsApp message to a phone number (E.164) or a contact name. May pause for confirmation per the user's approval settings before sending."""
    phone = _resolve_phone(phone_number)
    if isinstance(phone, ToolResult):
        return phone
    try:
        receipt = await comms.send_whatsapp(phone, message)
    except comms.CommsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"WhatsApp message queued to {receipt.to} ({receipt.provider} provider).",
        receipt.as_dict(),
    )


async def place_voice_call(phone_number: str, message: str) -> ToolResult:
    """Place an outbound voice call that reads a short message aloud to a phone number (E.164) or a contact name. May pause for confirmation per the user's approval settings."""
    phone = _resolve_phone(phone_number)
    if isinstance(phone, ToolResult):
        return phone
    try:
        receipt = await comms.place_voice_call(phone, message)
    except comms.CommsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Voice call queued to {receipt.to} ({receipt.provider} provider).",
        receipt.as_dict(),
    )


registry.register(send_sms, permission=PermissionLevel.CONFIRM, category="comms",
                  requires_internet=True,
                  tags=["comms", "text", "sms", "message", "voice", "external"])
registry.register(send_whatsapp, permission=PermissionLevel.CONFIRM, category="comms",
                  requires_internet=True,
                  tags=["comms", "whatsapp", "message", "voice", "external"])
registry.register(place_voice_call, permission=PermissionLevel.CONFIRM, category="comms",
                  requires_internet=True, tags=["comms", "external"])
