"""Cross-app workflow models (spec §5: browser/apps/communications)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class CrossAppWorkflow(Base):
    """Definition of a cross-app workflow."""

    __tablename__ = "cross_app_workflows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    
    steps: Mapped[list] = mapped_column(JSON)
    branches: Mapped[list] = mapped_column(JSON, server_default="[]")
    triggers: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    on_error: Mapped[str] = mapped_column(String(64), default="stop")
    fallback_workflow: Mapped[str] = mapped_column(String(32), nullable=True)
    
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=60)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_executed: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowExecutionRecord(Base):
    """A specific execution of a workflow."""

    __tablename__ = "workflow_execution_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    pattern_id: Mapped[str] = mapped_column(String(32), ForeignKey("cross_app_workflows.id"))
    pattern_name: Mapped[str] = mapped_column(String(255))
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    
    status: Mapped[str] = mapped_column(String(32), default="running")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    
    input_data: Mapped[dict] = mapped_column(JSON, server_default="{}")
    intermediate_results: Mapped[dict] = mapped_column(JSON, server_default="{}")
    output_data: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    tools_used: Mapped[list] = mapped_column(JSON, server_default="[]")
    permissions: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    error_message: Mapped[str] = mapped_column(Text, default="")
    error_step: Mapped[int] = mapped_column(Integer, nullable=True)
    
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    triggered_by: Mapped[str] = mapped_column(String(64), default="manual")


class WorkflowStepResult(Base):
    """Result of a single step in a workflow execution."""

    __tablename__ = "workflow_step_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(String(32), ForeignKey("workflow_execution_records.id"))
    
    step_order: Mapped[int] = mapped_column(Integer)
    step_name: Mapped[str] = mapped_column(String(255))
    task_step_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_args: Mapped[dict] = mapped_column(JSON)
    
    success: Mapped[bool] = mapped_column(Boolean)
    result_data: Mapped[dict] = mapped_column(JSON)
    error_message: Mapped[str] = mapped_column(Text, default="")
    
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)