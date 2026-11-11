"""Contact tools (§65): find/create/update address-book entries.

Contacts resolve names to phone numbers/emails for SMS, calls and email.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import contacts as contacts_service
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


async def find_contact(name: str) -> ToolResult:
    """Look up people in the user's contacts by name, email, company or number. Use to resolve "John" to a phone number or address."""
    results = contacts_service.list_contacts(user_id=_user_id(), query=name, limit=10)
    if not results:
        return ToolResult.ok(f"No contact matched '{name}'.", {"contacts": []})
    return ToolResult.ok(f"Found {len(results)} contact(s) for '{name}'.",
                         {"contacts": results})


async def create_contact(name: str, phone: str = "", email: str = "",
                         company: str = "") -> ToolResult:
    """Add a new contact (name plus optional phone, email, company)."""
    try:
        data = contacts_service.add_contact(
            user_id=_user_id(), name=name, phone=phone, email=email, company=company)
    except contacts_service.ContactError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Saved contact '{data['name']}'.", data)


async def update_contact(contact_id: str, name: str = "", phone: str = "",
                         email: str = "", company: str = "") -> ToolResult:
    """Update an existing contact by id (only the fields provided change)."""
    fields = {k: v for k, v in {
        "name": name, "phone": phone, "email": email, "company": company,
    }.items() if v}
    try:
        data = contacts_service.edit_contact(
            user_id=_user_id(), contact_id=contact_id, **fields)
    except contacts_service.ContactError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Updated contact '{data['name']}'.", data)


registry.register(find_contact, permission=PermissionLevel.AUTO, category="contacts",
                  tags=["voice"])
registry.register(create_contact, permission=PermissionLevel.NOTIFY, category="contacts")
registry.register(update_contact, permission=PermissionLevel.NOTIFY, category="contacts")
