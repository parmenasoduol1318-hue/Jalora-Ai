from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.errors import BadRequestError, NotFoundError
from app.models.conversation import Conversation
from app.models.idea_project import IdeaProject
from app.models.task import Task
from app.models.user import User, utcnow

router = APIRouter(prefix="/api/idea-projects", tags=["idea-projects"])

ProjectMilestone = Annotated[str, Field(max_length=1_000)]
RelatedRecordId = Annotated[str, Field(min_length=1, max_length=32)]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    brief: str = Field(default="", max_length=20_000)
    goals: str = Field(default="", max_length=10_000)
    next_steps: str = Field(default="", max_length=10_000)
    notes: str = Field(default="", max_length=20_000)
    milestones: list[ProjectMilestone] = Field(default_factory=list, max_length=100)
    task_ids: list[RelatedRecordId] = Field(default_factory=list, max_length=100)
    conversation_ids: list[RelatedRecordId] = Field(default_factory=list, max_length=100)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    brief: str | None = Field(default=None, max_length=20_000)
    goals: str | None = Field(default=None, max_length=10_000)
    next_steps: str | None = Field(default=None, max_length=10_000)
    notes: str | None = Field(default=None, max_length=20_000)
    milestones: list[ProjectMilestone] | None = Field(default=None, max_length=100)
    task_ids: list[RelatedRecordId] | None = Field(default=None, max_length=100)
    conversation_ids: list[RelatedRecordId] | None = Field(default=None, max_length=100)


def _view(project: IdeaProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "brief": project.brief,
        "goals": project.goals,
        "next_steps": project.next_steps,
        "notes": project.notes,
        "milestones": project.milestones or [],
        "task_ids": project.task_ids or [],
        "conversation_ids": project.conversation_ids or [],
        "archived": project.archived_at is not None,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _owned_project(db: Session, owner_id: str, project_id: str) -> IdeaProject:
    project = db.scalar(
        select(IdeaProject).where(
            IdeaProject.id == project_id,
            IdeaProject.owner_id == owner_id,
        )
    )
    if project is None:
        raise NotFoundError("Idea project not found.")
    return project


def _validate_links(
    db: Session,
    owner_id: str,
    task_ids: list[str] | None,
    conversation_ids: list[str] | None,
) -> None:
    if task_ids is not None:
        if any(len(item) > 32 for item in task_ids):
            raise BadRequestError("Related task IDs are invalid.")
        owned_tasks = set(
            db.scalars(
                select(Task.id).where(
                    Task.user_id == owner_id,
                    Task.id.in_(task_ids),
                )
            ).all()
        ) if task_ids else set()
        if owned_tasks != set(task_ids):
            raise BadRequestError("Related tasks must belong to this account.")
    if conversation_ids is not None:
        if any(len(item) > 32 for item in conversation_ids):
            raise BadRequestError("Related conversation IDs are invalid.")
        owned_conversations = set(
            db.scalars(
                select(Conversation.id).where(
                    Conversation.user_id == owner_id,
                    Conversation.id.in_(conversation_ids),
                )
            ).all()
        ) if conversation_ids else set()
        if owned_conversations != set(conversation_ids):
            raise BadRequestError("Related conversations must belong to this account.")


@router.get("")
def list_projects(
    include_archived: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(IdeaProject).where(IdeaProject.owner_id == user.id)
    if not include_archived:
        stmt = stmt.where(IdeaProject.archived_at.is_(None))
    projects = db.scalars(stmt.order_by(IdeaProject.updated_at.desc())).all()
    return [_view(project) for project in projects]


@router.post("", status_code=201)
def create_project(
    payload: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    values = payload.model_dump()
    values["title"] = values["title"].strip()
    if not values["title"]:
        raise BadRequestError("A project title is required.")
    _validate_links(db, user.id, values["task_ids"], values["conversation_ids"])
    project = IdeaProject(owner_id=user.id, **values)
    db.add(project)
    db.commit()
    db.refresh(project)
    return _view(project)


@router.get("/{project_id}")
def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _view(_owned_project(db, user.id, project_id))


@router.patch("/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _owned_project(db, user.id, project_id)
    values = payload.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()):
        raise BadRequestError("Project fields cannot be null; use an empty value to clear them.")
    if "title" in values:
        values["title"] = values["title"].strip()
        if not values["title"]:
            raise BadRequestError("A project title is required.")
    _validate_links(
        db,
        user.id,
        values.get("task_ids"),
        values.get("conversation_ids"),
    )
    for key, value in values.items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return _view(project)


@router.post("/{project_id}/archive")
def archive_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _owned_project(db, user.id, project_id)
    if project.archived_at is None:
        project.archived_at = utcnow()
        db.commit()
        db.refresh(project)
    return _view(project)


@router.post("/{project_id}/reopen")
def reopen_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _owned_project(db, user.id, project_id)
    if project.archived_at is not None:
        project.archived_at = None
        db.commit()
        db.refresh(project)
    return _view(project)
