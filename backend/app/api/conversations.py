from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.errors import NotFoundError
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.schemas import ConversationCreate, ConversationOut, ConversationPatch, MessageOut

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
def list_conversations(project: str | None = Query(default=None),
                       exclude_project: str | None = Query(default=None),
                       q: str | None = Query(default=None, max_length=255),
                       include_archived: bool = Query(default=False),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """List the user's conversations.

    ``project`` filters to an exact project (e.g. ``workspace`` for the coding
    workspace); ``exclude_project`` drops one project so the main chat sidebar
    never shows workspace sessions (and vice-versa)."""
    stmt = select(Conversation).where(Conversation.user_id == user.id)
    if project is not None:
        stmt = stmt.where(Conversation.project == project)
    if exclude_project is not None:
        stmt = stmt.where(
            (Conversation.project.is_(None)) | (Conversation.project != exclude_project)
        )
    if not include_archived:
        stmt = stmt.where(Conversation.archived.is_(False))
    if q and q.strip():
        stmt = stmt.where(Conversation.title.ilike(f"%{q.strip()}%"))
    rows = db.scalars(stmt.order_by(Conversation.updated_at.desc())).all()
    return [ConversationOut.model_validate(r) for r in rows]


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(payload: ConversationCreate, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = Conversation(user_id=user.id, title=payload.title, project=payload.project)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationOut.model_validate(conv)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found.")
    return ConversationOut.model_validate(conv)


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: str, payload: ConversationPatch,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found.")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        conv.title = changes["title"].strip()
    if "archived" in changes:
        conv.archived = bool(changes["archived"])
    db.commit()
    db.refresh(conv)
    return ConversationOut.model_validate(conv)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found.")
    rows = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    return [MessageOut.model_validate(r) for r in rows]


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found.")
    db.delete(conv)
    db.commit()
    return None
