from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.agent import ActivityLog
from app.models.user import User
from app.schemas import ActivityOut

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", response_model=list[ActivityOut])
def list_activity(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                  limit: int = Query(default=100, le=500)):
    rows = db.scalars(
        select(ActivityLog).where(ActivityLog.user_id == user.id)
        .order_by(ActivityLog.created_at.desc()).limit(limit)
    ).all()
    return [ActivityOut.model_validate(r) for r in rows]
