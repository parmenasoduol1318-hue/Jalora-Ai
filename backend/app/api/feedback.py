"""Feedback API (spec §70, Phase K).

Endpoints for submitting and listing user feedback on tasks, messages and
tool calls.  Feedback is later inspected by the preference extractor to
promote repeated signals into long-term preference memories.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.feedback import Feedback
from app.models.user import User
from app.schemas import FeedbackCreate, FeedbackOut

router = APIRouter(prefix="/api", tags=["feedback"])


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    fb = Feedback(
        user_id=user.id,
        task_id=payload.task_id,
        message_id=payload.message_id,
        run_id=payload.run_id,
        tool_name=payload.tool_name,
        sentiment=payload.sentiment,
        content=payload.content,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return FeedbackOut.model_validate(fb)


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    task_id: str | None = None,
    limit: int = 200,
):
    stmt = select(Feedback).where(Feedback.user_id == user.id)
    if task_id:
        stmt = stmt.where(Feedback.task_id == task_id)
    rows = db.scalars(
        stmt.order_by(Feedback.created_at.desc()).limit(limit)
    ).all()
    return [FeedbackOut.model_validate(r) for r in rows]
