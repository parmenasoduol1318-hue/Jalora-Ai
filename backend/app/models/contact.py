from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Contact(Base):
    """A person in the user's address book (§65).

    Local-first: contacts live here and (optionally) sync from Google People.
    A contact is the resolution source for `send_sms`/`make_call`/email so the
    assistant can turn "John" into a phone number or address.
    """

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # local | google
    source: Mapped[str] = mapped_column(String(20), default="local")
    external_id: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)
