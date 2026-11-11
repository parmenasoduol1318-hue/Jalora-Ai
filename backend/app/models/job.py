"""Job application tracking models (spec §J: job search extension)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class JobPosting(Base):
    """A job posting found during search; stored for reference and application."""

    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Unknown Position")
    company: Mapped[str] = mapped_column(String(255), default="Unknown Company")
    location: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64))  # e.g., "linkedin", "indeed", "company"
    description: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[list] = mapped_column(JSON, server_default="[]")
    qualifications: Mapped[list] = mapped_column(JSON, server_default="[]")
    salary_range: Mapped[str] = mapped_column(String(128), default="")
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    application_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobApplication(Base):
    """A submitted job application tracking record."""

    __tablename__ = "job_applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    job_posting_id: Mapped[str] = mapped_column(String(32), ForeignKey("job_postings.id"), index=True)
    company: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="applied")  # applied, interview, offer, rejected, withdrawn
    resume_version: Mapped[str] = mapped_column(String(128), default="")
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    email_sent_to: Mapped[str] = mapped_column(String(255), default="")
    email_message_id: Mapped[str] = mapped_column(String(255), default="")
    application_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    interview_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_received: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CVTemplate(Base):
    """User's CV/resume template and versions."""

    __tablename__ = "cv_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32), default="1")
    content: Mapped[str] = mapped_column(Text)  # Markdown or plaintext CV content
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobQualification(Base):
    """User's qualifications/skills that can be matched against job postings."""

    __tablename__ = "job_qualifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(64))  # experience, education, skills, certifications
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0=high priority, 1=medium, 2=low
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApplicationDraft(Base):
    """Draft of an application email/body that can be reviewed before sending."""

    __tablename__ = "application_drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    job_posting_id: Mapped[str] = mapped_column(String(32), ForeignKey("job_postings.id"), index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    cv_content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft, ready, sent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)