"""Workflow pattern memory (spec §71, Phase K).

On task *success* we store a tool-sequence fingerprint so the planner can
replay a known-good recipe. On task *failure* we store a
(context, failing_tool, cause, replacement) record so the next plan can
avoid the same trap.  These rows are injected into the planner context as
guidance before a new task runs.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class WorkflowPattern(Base):
    """A stored workflow pattern — success fingerprint or failure guidance."""

    __tablename__ = "workflow_patterns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    # "success" | "failure"
    pattern_type: Mapped[str] = mapped_column(String(16), default="success", index=True)
    # Short human label, e.g. "research-and-synthesize" or "site-needs-js"
    label: Mapped[str] = mapped_column(String(255), default="")
    # Fingerprint of the tool sequence that was used (success) or that failed
    # (failure). Stored as a JSON list of tool-call names.
    fingerprint: Mapped[str] = mapped_column(Text, default="[]")
    # Context / goal snippet that triggered this pattern (failure only)
    context: Mapped[str] = mapped_column(Text, default="")
    # The tool that failed (failure only)
    failing_tool: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    # Free-text cause and suggested replacement (failure only)
    cause: Mapped[str] = mapped_column(Text, default="")
    replacement: Mapped[str] = mapped_column(Text, default="")
    # How many times this pattern has been matched/used
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
