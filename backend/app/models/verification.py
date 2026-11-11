"""Fact verification models (spec §§3, 14: research and reasoning)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text, DateTime, Float, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class FactVerification(Base):
    """Record of a fact verification request and result."""

    __tablename__ = "fact_verifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # The claim being verified
    claim: Mapped[str] = mapped_column(Text)
    
    # Verification result
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0-1.0
    
    # Evidence gathered
    sources: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{url, title, credibility}]
    evidence_summary: Mapped[str] = mapped_column(Text, default="")
    
    # Contradictory evidence
    contradictions: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Search metadata
    search_query: Mapped[str] = mapped_column(String(500), default="")
    sources_searched: Mapped[int] = mapped_column(Integer, default=0)
    
    # Status
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, in_progress, completed, failed
    
    # Timeline
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # User feedback
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    user_feedback: Mapped[str] = mapped_column(Text, default="")


class EvidenceSource(Base):
    """Individual evidence source found during verification."""

    __tablename__ = "evidence_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    verification_id: Mapped[str] = mapped_column(String(32), ForeignKey("fact_verifications.id"))
    
    # Source information
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500), default="")
    
    # Credibility metrics
    domain: Mapped[str] = mapped_column(String(255), default="")
    credibility_score: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0-1.0
    
    # Content
    snippet: Mapped[str] = mapped_column(Text, default="")  # Relevant excerpt
    publish_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Match quality
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Timestamp
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComparisonRanking(Base):
    """Comparison and ranking result between items."""

    __tablename__ = "comparison_rankings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # What was compared
    comparison_type: Mapped[str] = mapped_column(String(64))  # tool_comparison, product_comparison, etc.
    
    # Items being compared
    items: Mapped[list] = mapped_column(JSON)  # List of item identifiers/names
    
    # Ranking criteria and scores
    criteria: Mapped[dict] = mapped_column(JSON)  # {criterion_name: weight}
    scores: Mapped[dict] = mapped_column(JSON)   # {item_id: {criterion: score}}
    
    # Final ranking
    ranking: Mapped[list] = mapped_column(JSON)  # Ordered list of item IDs
    
    # Results
    recommended: Mapped[str] = mapped_column(String(32), nullable=True)  # Top recommendation
    rationale: Mapped[str] = mapped_column(Text, default="")
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)