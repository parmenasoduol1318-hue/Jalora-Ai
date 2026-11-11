"""Customer support models (spec §6: personal/business assistance)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class SupportTicket(Base):
    """Customer support ticket for handling inquiries."""

    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # Ticket identification
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(255))
    
    # Category and priority
    category: Mapped[str] = mapped_column(String(64))  # billing, technical, feature, general
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # p1, p2, p3, normal, low
    status: Mapped[str] = mapped_column(String(32), default="open")  # open, in_progress, resolved, closed, escalated
    
    # Content
    description: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    
    # Source tracking
    source: Mapped[str] = mapped_column(String(64))  # email, chat, phone, web, api
    channel_id: Mapped[str] = mapped_column(String(128), nullable=True)
    
    # Timeline
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Customer info
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    customer_email: Mapped[str] = mapped_column(String(255), default="")
    customer_id: Mapped[str] = mapped_column(String(32), nullable=True)
    
    # SLA tracking
    sla_time: Mapped[float] = mapped_column(Float, default=24.0)  # Hours
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Assignment
    assigned_to: Mapped[str] = mapped_column(String(32), nullable=True)  # Agent user_id
    tags: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Conversation history
    conversation: Mapped[list] = mapped_column(JSON, server_default="[]")


class FAQ(Base):
    """Frequently asked questions repository."""

    __tablename__ = "faqs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    
    # Categorization
    category: Mapped[str] = mapped_column(String(64), default="general")
    tags: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Content
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    
    # Search optimization
    keywords: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KnowledgeBaseArticle(Base):
    """Knowledge base article for self-service support."""

    __tablename__ = "kb_articles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    
    # Content
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(String(1000), default="")
    
    # Categorization
    category: Mapped[str] = mapped_column(String(64))
    subcategories: Mapped[list] = mapped_column(JSON, server_default="[]")
    tags: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # SEO/Search
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    keywords: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Metadata
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Versioning
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    
    # Timeline
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportTemplate(Base):
    """Pre-defined response templates for common scenarios."""

    __tablename__ = "support_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    
    # Categorization
    category: Mapped[str] = mapped_column(String(64))  # apology, resolution, followup, escalation
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(255), default="")
    
    # Template content
    subject_template: Mapped[str] = mapped_column(String(255), default="")
    body_template: Mapped[str] = mapped_column(Text)
    
    # Variables that can be used
    variables: Mapped[list] = mapped_column(JSON, server_default="[]")  # [ticket_id, customer_name, etc.]
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)