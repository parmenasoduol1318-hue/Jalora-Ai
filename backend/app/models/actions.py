"""User action tracking for duplicate prevention and audit (spec §1).

Provides models for tracking user actions to prevent duplicate executions
and enable audit trails.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import JSON, Index, String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


def compute_action_fingerprint(tool_name: str, args: dict) -> str:
    """Compute a deterministic fingerprint for an action to detect duplicates."""
    # Create a canonical representation
    canonical = {
        "tool": tool_name,
        "args": args
    }
    # Sort keys and serialize for consistent hashing
    canonical_str = json.dumps(canonical, sort_keys=True)
    return hashlib.sha256(canonical_str.encode()).hexdigest()[:16]


class UserAction(Base):
    """Record of a user action/tool execution for duplicate prevention and audit."""

    __tablename__ = "user_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    
    # User who performed the action
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    
    # Tool/function that was called
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    
    # Arguments passed to the tool (JSON for flexibility)
    args: Mapped[dict] = mapped_column(JSON)
    
    # Unique fingerprint for duplicate detection
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    
    # Execution metadata
    status: Mapped[str] = mapped_column(String(32), default="executing")  # executing|completed|failed|interrupted
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Results (if any)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    result_data: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    # Error information (if failed)
    error_message: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(64), default="")
    
    # Execution timing
    duration_ms: Mapped[int] = mapped_column("duration_ms", String(20), default="0")
    
    # Retry information
    retry_count: Mapped[int] = mapped_column("retry_count", String(10), default="0")
    
    # Source/purpose tracking
    context: Mapped[str] = mapped_column(String(128), default="user_request")  # user_request|system|workflow
    parent_action_id: Mapped[str] = mapped_column(String(32), nullable=True)  # For workflow steps
    
    __table_args__ = (
        Index("idx_user_action_fingerprint", "user_id", "fingerprint"),
        Index("idx_user_action_timestamp", "user_id", "started_at"),
    )
    
    def __repr__(self) -> str:
        return f"<UserAction {self.id} {self.tool_name} by {self.user_id[:8]}...>"
    
    @classmethod
    def from_action(cls, user_id: str, tool_name: str, args: dict, context: str = "user_request") -> "UserAction":
        """Create a new UserAction instance from execution parameters."""
        return cls(
            user_id=user_id,
            tool_name=tool_name,
            args=args,
            fingerprint=compute_action_fingerprint(tool_name, args),
            context=context
        )
    
    def to_summary(self) -> dict:
        """Return a summary dict for logging/debugging."""
        return {
            "id": self.id[:8],
            "tool": self.tool_name,
            "user_id": self.user_id[:8] if self.user_id else None,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "duration_ms": int(self.duration_ms) if self.duration_ms else 0,
            "fingerprint": self.fingerprint[:8] if self.fingerprint else None
        }


# ============================================================================
# Action Status Constants
# ============================================================================

ACTION_STATUS_EXECUTING = "executing"
ACTION_STATUS_COMPLETED = "completed"
ACTION_STATUS_FAILED = "failed"
ACTION_STATUS_INTERRUPTED = "interrupted"

ACTION_CONTEXT_USER = "user_request"
ACTION_CONTEXT_SYSTEM = "system"
ACTION_CONTEXT_WORKFLOW = "workflow"