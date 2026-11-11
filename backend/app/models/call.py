"""Phone call persistence (spec §31–33, §57).

A `Call` records one outbound voice conversation driven by a structured
script; `CallTurn` stores each agent/person utterance so a full transcript
and a §32 summary are available on the Task / chat result card.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agent_runs.id"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversations.id"), nullable=True, index=True)

    # inapp live calls have no phone; outbound/inbound phone calls set E.164.
    to_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(16), default="mock")  # mock | twilio
    # inapp = in-app live voice session; outbound/inbound = phone (Twilio).
    kind: Mapped[str] = mapped_column(String(12), default="outbound")
    # script = deterministic TwiML <Gather> script; live = bidirectional
    # Media Streams conversation driven by VoiceSession (Phase 2/3).
    mode: Mapped[str] = mapped_column(String(8), default="script")
    purpose: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="dialing", index=True)
    # dialing | in_progress | completed | failed | cancelled | no_answer

    sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    script: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    transcript: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    max_turns: Mapped[int] = mapped_column(Integer, default=20)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, default=600)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CallTurn(Base):
    __tablename__ = "call_turns"
    __table_args__ = ()

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    call_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("calls.id"), index=True)
    role: Mapped[str] = mapped_column(String(8))      # agent | person
    kind: Mapped[str] = mapped_column(String(12), default="say")
    # say | speech | dtmf | system
    text: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[float] = mapped_column(Float, default=0)  # seconds since start
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)