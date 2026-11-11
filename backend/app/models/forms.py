"""Form completion models (spec §6: personal/business assistance)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.user import new_id, utcnow


class FormTemplate(Base):
    """Template for a form with field definitions and validation rules."""

    __tablename__ = "form_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # Form identity
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    
    # Field definitions
    fields: Mapped[list] = mapped_column(JSON)  # [{name, type, required, options, validation}]
    
    # Default values
    default_values: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    # Validation settings
    validation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_fill_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Usage tracking
    fill_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FormData(Base):
    """Completed form data."""

    __tablename__ = "form_data"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # Form reference
    template_id: Mapped[str] = mapped_column(String(32), ForeignKey("form_templates.id"))
    template: Mapped["FormTemplate"] = relationship("FormTemplate", backref="filled_forms")
    
    # Form content
    data: Mapped[dict] = mapped_column(JSON)
    
    # Submission tracking
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    submission_method: Mapped[str] = mapped_column(String(32), nullable=True)  # form_submit, api, manual
    submission_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Validation status
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_errors: Mapped[list] = mapped_column(JSON, server_default="[]")
    confidence_scores: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)