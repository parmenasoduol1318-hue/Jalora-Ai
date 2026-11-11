"""Duplicate action prevention and detection (spec §1).

Provides a system to detect and prevent duplicate execution of tool calls
and user actions. Uses fingerprinting to identify repeated actions within
a configurable time window.

Usage:
    @prevent_duplicates("search_jobs", {"keywords": "python"})
    async def search_jobs(keywords: str):
        ...
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.actions import UserAction, compute_action_fingerprint

logger = logging.getLogger("jalora.duplicate_detection")


@dataclass
class DuplicateCheck:
    """Result of a duplicate check."""
    is_duplicate: bool
    fingerprint: str
    last_occurrence: datetime | None
    time_since_last: float | None
    action: UserAction | None


@dataclass
class DuplicatePreventionResult:
    """Result of a duplicate prevention check."""
    should_execute: bool
    message: str
    check: DuplicateCheck | None = None


class DuplicateDetector:
    """Detects and prevents duplicate action executions.
    
    Uses fingerprinting to identify repeated actions within a configurable
    time window. Tracks action history in the database for persistence.
    """
    
    def __init__(self, window_seconds: int = 300):
        """Initialize the detector.
        
        Args:
            window_seconds: Time window in seconds to consider an action as duplicate
                           (default: 5 minutes = 300 seconds)
        """
        self.window_seconds = window_seconds
        settings = get_settings()
        # Allow config override
        if hasattr(settings, 'duplicate_detection_window_seconds'):
            self.window_seconds = settings.duplicate_detection_window_seconds
    
    def action_fingerprint(self, tool_name: str, args: dict) -> str:
        """Compute a deterministic fingerprint for an action.
        
        Args:
            tool_name: Name of the tool/function
            args: Arguments passed to the tool
            
        Returns:
            SHA256 fingerprint (first 16 characters)
        """
        return compute_action_fingerprint(tool_name, args)
    
    def recently_executed(self, fingerprint: str, user_id: str, 
                          window_seconds: int | None = None) -> bool:
        """Check if an action was recently executed.
        
        Args:
            fingerprint: The action fingerprint
            user_id: The user who executed the action
            window_seconds: Time window (uses instance default if None)
            
        Returns:
            True if action was executed within the window
        """
        if window_seconds is None:
            window_seconds = self.window_seconds
        
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
            
            action = db.scalar(
                select(UserAction).where(
                    UserAction.fingerprint == fingerprint,
                    UserAction.user_id == user_id,
                    UserAction.completed_at >= cutoff,
                    UserAction.status == "completed"
                )
            )
            
            return action is not None
        finally:
            db.close()
    
    def check_before_execute(self, tool_name: str, args: dict, 
                            user_id: str,
                            window_seconds: int | None = None) -> DuplicateCheck:
        """Check if an action should be allowed to execute.
        
        Args:
            tool_name: Name of the tool to execute
            args: Arguments that would be passed
            user_id: User who would execute
            window_seconds: Time window for duplicate detection
            
        Returns:
            DuplicateCheck with result and metadata
        """
        if window_seconds is None:
            window_seconds = self.window_seconds
        
        fingerprint = self.action_fingerprint(tool_name, args)
        
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
            
            action = db.scalar(
                select(UserAction).where(
                    UserAction.fingerprint == fingerprint,
                    UserAction.user_id == user_id,
                    UserAction.completed_at >= cutoff,
                    UserAction.status == "completed"
                ).order_by(UserAction.completed_at.desc())
            )
            
            now = datetime.now(timezone.utc)
            time_since = (now - action.completed_at).total_seconds() if action else None
            
            return DuplicateCheck(
                is_duplicate=action is not None,
                fingerprint=fingerprint,
                last_occurrence=action.completed_at if action else None,
                time_since_last=time_since,
                action=action
            )
        finally:
            db.close()
    
    def record_action(self, user_id: str, tool_name: str, 
                      args: dict, result_summary: str = "",
                      status: str = "completed", duration_ms: int = 0,
                      result_data: dict | None = None) -> UserAction:
        """Record an action in the database.
        
        Args:
            user_id: User who performed the action
            tool_name: Tool that was executed
            args: Arguments passed to the tool
            result_summary: Brief summary of the result
            status: Execution status (completed, failed, interrupted)
            duration_ms: Execution duration in milliseconds
            result_data: Optional detailed result data
            
        Returns:
            The created UserAction record
        """
        fingerprint = self.action_fingerprint(tool_name, args)
        
        action = UserAction(
            user_id=user_id,
            tool_name=tool_name,
            args=args,
            fingerprint=fingerprint,
            status=status,
            completed_at=datetime.now(timezone.utc),
            duration_ms=str(duration_ms),
            result_summary=result_summary,
            result_data=result_data or {}
        )
        
        db = SessionLocal()
        try:
            db.add(action)
            db.commit()
            db.refresh(action)
            return action
        finally:
            db.close()


# Singleton instance
_detector: DuplicateDetector | None = None


def get_duplicate_detector() -> DuplicateDetector:
    """Get or create the duplicate detector singleton."""
    global _detector
    if _detector is None:
        _detector = DuplicateDetector()
    return _detector


@contextmanager
def prevent_duplicates(tool_name: str, args: dict, 
                       user_id: str | None = None,
                       window_seconds: int = 300):
    """Context manager decorator to prevent duplicate executions.
    
    Usage:
        with prevent_duplicates("search_jobs", {"keywords": "python"}, user_id):
            await search_jobs("python")
    
    Or as a decorator:
        @prevent_duplicates("get_weather", {"city": "Paris"})
        async def get_weather(city: str):
            ...
    
    Args:
        tool_name: Name of the tool
        args: Arguments that would be passed
        user_id: User ID (required for persistence)
        window_seconds: Time window for duplicate detection
        
    Yields:
        None if execution should proceed
        
    Raises:
        RuntimeError: If a duplicate is detected
    """
    detector = get_duplicate_detector()
    
    if user_id is None:
        # No user ID - just proceed (useful for testing)
        yield
        return
    
    check = detector.check_before_execute(tool_name, args, user_id, window_seconds)
    
    if check.is_duplicate:
        raise RuntimeError(
            f"Duplicate action detected: {tool_name} with args {args} "
            f"was executed {check.time_since_last:.1f}s ago. "
            f"Use a different query or wait {window_seconds}s."
        )
    
    yield


def get_recent_actions(user_id: str, limit: int = 10) -> list[UserAction]:
    """Get recent actions for a user.
    
    Args:
        user_id: User ID to look up
        limit: Maximum number of actions to return
        
    Returns:
        List of recent UserAction records
    """
    db = SessionLocal()
    try:
        actions = db.query(UserAction).filter(
            UserAction.user_id == user_id
        ).order_by(UserAction.started_at.desc()).limit(limit).all()
        return actions
    finally:
        db.close()


def clear_action_history(user_id: str | None = None) -> int:
    """Clear action history.
    
    Args:
        user_id: If provided, clear only this user's actions
                 If None, clear all actions (use with caution!)
        
    Returns:
        Number of records deleted
    """
    db = SessionLocal()
    try:
        if user_id:
            count = db.query(UserAction).filter(
                UserAction.user_id == user_id
            ).delete()
        else:
            count = db.query(UserAction).delete()
        
        db.commit()
        logger.warning(f"Cleared {count} action history records")
        return count
    finally:
        db.close()