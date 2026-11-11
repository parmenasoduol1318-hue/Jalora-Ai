"""Business planning models (spec §7: business planning extension)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class BusinessPlan(Base):
    """A business plan or project plan with structured templates."""

    __tablename__ = "business_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))  # market_analysis, financial, risk_assessment, product_launch, etc.
    
    # Template-based structure
    template: Mapped[str] = mapped_column(String(64), default="standard")
    
    # Content sections as JSON for flexibility
    sections: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    # Status tracking
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft, in_progress, review, approved, executed
    
    # Timeline
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MarketAnalysis(Base):
    """Market analysis component of a business plan."""

    __tablename__ = "market_analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(String(32), ForeignKey("business_plans.id"), index=True)
    
    # Market overview
    market_size: Mapped[float] = mapped_column(Float, nullable=True)
    growth_rate: Mapped[float] = mapped_column(Float, nullable=True)
    
    # Target segments
    segments: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Competitors
    competitors: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Key findings
    findings: Mapped[str] = mapped_column(Text, default="")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FinancialProjection(Base):
    """Financial projections for a business plan."""

    __tablename__ = "financial_projections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(String(32), ForeignKey("business_plans.id"), index=True)
    
    # Time period
    period: Mapped[str] = mapped_column(String(32))  # monthly, quarterly, yearly
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer, nullable=True)
    
    # Revenue projections
    revenue: Mapped[float] = mapped_column(Float, default=0)
    expenses: Mapped[float] = mapped_column(Float, default=0)
    profit: Mapped[float] = mapped_column(Float, default=0)
    
    # Assumptions stored as JSON
    assumptions: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RiskAssessment(Base):
    """Risk assessment for business planning."""

    __tablename__ = "risk_assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(String(32), ForeignKey("business_plans.id"), index=True)
    
    # Risk identification
    risk_name: Mapped[str] = mapped_column(String(255))
    risk_category: Mapped[str] = mapped_column(String(64))  # market, operational, financial, legal, technical
    
    # Assessment
    probability: Mapped[float] = mapped_column(Float)  # 0.0-1.0
    impact: Mapped[float] = mapped_column(Float)  # 0.0-1.0
    risk_score: Mapped[float] = mapped_column(Float, default=0)  # probability * impact
    
    # Mitigation
    mitigation_strategy: Mapped[str] = mapped_column(Text, default="")
    responsible_party: Mapped[str] = mapped_column(String(128), default="")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Milestone(Base):
    """Milestone in a business plan."""

    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(String(32), ForeignKey("business_plans.id"), index=True)
    
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    
    # Timeline
    planned_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Status
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, in_progress, completed, delayed
    
    # Dependencies
    dependencies: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)