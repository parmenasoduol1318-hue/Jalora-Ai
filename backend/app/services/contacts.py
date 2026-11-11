"""Contacts service (§65).

Local-first address book. Contacts are the resolution source for comms: the
assistant can turn a name ("John") into a phone number for SMS/calls, or an
email address. A Google People adapter is optional; the local store is always
authoritative and works offline.
"""
from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.contact import Contact

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_DIGITS_RE = re.compile(r"[\s().-]")


class ContactError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def contact_as_dict(c: Contact) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone,
        "company": c.company,
        "notes": c.notes,
        "source": c.source,
    }


def looks_like_phone(value: str) -> bool:
    cleaned = _DIGITS_RE.sub("", (value or "").strip())
    if cleaned.isdigit() and len(cleaned) >= 8:
        cleaned = f"+{cleaned}"
    return bool(_PHONE_RE.match(cleaned))


def normalize_phone(value: str) -> str:
    cleaned = _DIGITS_RE.sub("", (value or "").strip())
    if cleaned.isdigit() and len(cleaned) >= 8:
        cleaned = f"+{cleaned}"
    return cleaned


def create_contact(db: Session, *, user_id: str, name: str, email: str = "",
                   phone: str = "", company: str = "", notes: str = "",
                   source: str = "local") -> Contact:
    name = (name or "").strip()
    if not name:
        raise ContactError("INVALID_CONTACT", "A contact name is required.")
    row = Contact(
        user_id=user_id, name=name, email=(email or "").strip(),
        phone=(phone or "").strip(), company=(company or "").strip(),
        notes=(notes or "").strip(), source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def find_contacts(db: Session, *, user_id: str, query: str = "", limit: int = 20) -> list[Contact]:
    stmt = select(Contact).where(Contact.user_id == user_id)
    q = (query or "").strip()
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            Contact.name.ilike(like),
            Contact.email.ilike(like),
            Contact.phone.ilike(like),
            Contact.company.ilike(like),
        ))
    stmt = stmt.order_by(Contact.name).limit(max(1, min(int(limit or 20), 100)))
    return list(db.scalars(stmt).all())


def resolve_contact(db: Session, *, user_id: str, query: str) -> Contact | None:
    """Best-effort contact lookup: exact name, then prefix, then substring."""
    q = (query or "").strip().lower()
    if not q:
        return None
    rows = list(db.scalars(
        select(Contact).where(Contact.user_id == user_id).order_by(Contact.name)
    ).all())
    exact = [c for c in rows if c.name.lower() == q]
    if exact:
        return exact[0]
    prefix = [c for c in rows if c.name.lower().startswith(q)]
    if prefix:
        return prefix[0]
    contains = [c for c in rows if q in c.name.lower()
                or q in (c.email or "").lower()
                or q in (c.company or "").lower()]
    return contains[0] if contains else None


def update_contact(db: Session, *, user_id: str, contact_id: str,
                   **fields) -> Contact:
    row = db.get(Contact, contact_id)
    if row is None or row.user_id != user_id:
        raise ContactError("CONTACT_NOT_FOUND", "Contact not found.")
    for key in ("name", "email", "phone", "company", "notes"):
        if key in fields and fields[key] is not None:
            setattr(row, key, str(fields[key]).strip())
    db.commit()
    db.refresh(row)
    return row


# --- public API (opens its own session, mirroring the email service) --------
def list_contacts(*, user_id: str, query: str = "", limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        return [contact_as_dict(c) for c in find_contacts(
            db, user_id=user_id, query=query, limit=limit)]
    finally:
        db.close()


def add_contact(*, user_id: str, name: str, email: str = "", phone: str = "",
                company: str = "", notes: str = "") -> dict:
    db = SessionLocal()
    try:
        return contact_as_dict(create_contact(
            db, user_id=user_id, name=name, email=email, phone=phone,
            company=company, notes=notes))
    finally:
        db.close()


def edit_contact(*, user_id: str, contact_id: str, **fields) -> dict:
    db = SessionLocal()
    try:
        return contact_as_dict(update_contact(
            db, user_id=user_id, contact_id=contact_id, **fields))
    finally:
        db.close()


def resolve_phone_number(user_id: str, raw: str) -> str:
    """Return an E.164 number from a literal phone or a contact name.

    Raises ContactError when neither a phone nor a resolvable contact exists.
    Accepts "+2547..." or a name like "John" / "John Doe".
    """
    value = (raw or "").strip()
    if looks_like_phone(value):
        return normalize_phone(value)
    if not value:
        raise ContactError("INVALID_PHONE", "No phone number or contact name given.")
    db = SessionLocal()
    try:
        contact = resolve_contact(db, user_id=user_id, query=value)
    finally:
        db.close()
    if contact is None or not contact.phone:
        raise ContactError(
            "CONTACT_NOT_FOUND",
            f"No contact named '{value}' with a phone number on file.",
        )
    if not looks_like_phone(contact.phone):
        raise ContactError(
            "INVALID_PHONE",
            f"Contact '{contact.name}' has an invalid phone number on file.",
        )
    return normalize_phone(contact.phone)
