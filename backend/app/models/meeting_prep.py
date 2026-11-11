"""Meeting preparation models (spec §6: personal/business assistance)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class MeetingPrep(Base):
    """Meeting preparation record."""

    __tablename__ = "meeting_preps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # Meeting details
    title: Mapped[str] = mapped_column(String(255))
    meeting_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    
    # Participants
    participants: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{name, email, role}]
    
    # Agenda items
    agenda: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{time, topic, owner, notes}]
    
    # Related documents
    related_documents: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{name, url, summary}]
    
    # Pre-meeting tasks
    pre_tasks: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{description, assigned_to, due_before}]
    
    # Materials generated
    agenda_document: Mapped[str] = mapped_column(Text, default="")
    prep_notes: Mapped[str] = mapped_column(Text, default="")
    
    # Status tracking
    prepared: Mapped[bool] = mapped_column(Boolean, default=False)
    materials_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Timeline
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MeetingAgendaItem(Base):
    """Individual agenda item for a meeting."""

    __tablename__ = "meeting_agenda_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    prep_id: Mapped[str] = mapped_column(String(32), ForeignKey("meeting_preps.id"), index=True)
    
    # Item details
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    allocated_time: Mapped[int] = mapped_column(Integer, default=5)  # minutes
    owner: Mapped[str] = mapped_column(String(128), default="")
    
    # Status
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    
    # Position in agenda
    order: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingMinutes(Base):
    """Meeting minutes and outcomes record."""

    __tablename__ = "meeting_minutes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    prep_id: Mapped[str] = mapped_column(String(32), ForeignKey("meeting_preps.id"), index=True)
    
    # Meeting outcome
    meeting_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    # Key decisions made
    decisions: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{topic, decision, owner, date}]
    
    # Action items
    action_items: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{description, assignee, due_date, status}]
    
    # Discussion highlights
    discussion_points: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{topic, participants, key_points}]
    
    # Attendee tracking
    attendees: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{name, present, role}]
    
    # Next steps
    next_meeting_topic: Mapped[str] = mapped_column(String(255), default="")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)