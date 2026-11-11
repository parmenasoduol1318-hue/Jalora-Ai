from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_router
from app.errors import BadRequestError, NotFoundError
from app.models.agent import AgentRun, Approval
from app.models.task import Task, TaskStepAttempt
from app.models.user import User, utcnow
from app.schemas import ApprovalEditRequest, ApprovalOut, ApproveRequest, ChatResponse
from app.services import run_registry
from app.services.chat_service import (
    ChatOutcome,
    resume_approval,
    resume_run,
    update_pending_approval,
)
from app.services.model_router import ModelRouter
from app.services.approval_lifecycle import (
    cancel_run_pending_approvals,
    expire_stale_approvals,
)

router = APIRouter(prefix="/api", tags=["runs", "approvals"])


def _chat_response(outcome: ChatOutcome) -> ChatResponse:
    if outcome.status == "error" and outcome.error:
        from app.errors import JaloraError

        raise JaloraError(502, outcome.error["code"], outcome.error["details"])
    return ChatResponse.from_outcome(outcome)


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expire_stale_approvals(db, user_id=user.id)
    rows = db.scalars(
        select(Approval).where(Approval.user_id == user.id)
        .order_by(Approval.created_at.desc()).limit(100)
    ).all()
    return [ApprovalOut.model_validate(r) for r in rows]


@router.post("/approvals/{approval_id}/approve", response_model=ChatResponse)
async def approve(approval_id: str, user: User = Depends(get_current_user),
                  model_router: ModelRouter = Depends(get_router),
                  body: ApproveRequest | None = None):
    """Approve a paused tool call. For §18 login-credential approvals the body
    carries the credentials; they are stored encrypted and never echoed back."""
    outcome = await resume_approval(
        user_id=user.id, approval_id=approval_id, approve=True,
        router=model_router,
        credentials=body.credentials.model_dump() if body and body.credentials else None,
    )
    return _chat_response(outcome)


@router.patch("/approvals/{approval_id}", response_model=ApprovalOut)
def edit_approval(approval_id: str, body: ApprovalEditRequest,
                  user: User = Depends(get_current_user)):
    """Persist edits to the pending email draft or structured input prompt."""
    updated = update_pending_approval(
        user_id=user.id,
        approval_id=approval_id,
        arguments=body.arguments,
        payload_version=body.payload_version,
    )
    return updated


@router.post("/approvals/{approval_id}/reject", response_model=ChatResponse)
async def reject(approval_id: str, user: User = Depends(get_current_user),
                 model_router: ModelRouter = Depends(get_router)):
    outcome = await resume_approval(
        user_id=user.id, approval_id=approval_id, approve=False, router=model_router
    )
    return _chat_response(outcome)


@router.post("/approvals/{approval_id}/skip", response_model=ChatResponse)
async def skip_email_draft(
    approval_id: str,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """Skip a pending organization email without treating it as a rejection."""
    outcome = await resume_approval(
        user_id=user.id,
        approval_id=approval_id,
        approve=False,
        router=model_router,
        skip=True,
    )
    return _chat_response(outcome)


@router.post("/approvals/{approval_id}/revoke", response_model=ChatResponse)
async def revoke_approval(
    approval_id: str,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """Revoke an unclaimed approval and resume the task without dispatching it."""
    outcome = await resume_approval(
        user_id=user.id,
        approval_id=approval_id,
        approve=False,
        revoke=True,
        router=model_router,
    )
    return _chat_response(outcome)


@router.get("/runs")
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AgentRun).where(AgentRun.user_id == user.id)
        .order_by(AgentRun.created_at.desc()).limit(50)
    ).all()
    return [
        {
            "id": r.id, "conversation_id": r.conversation_id, "status": r.status,
            "model": r.model, "created_at": r.created_at, "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop a single agent run: no further tool calls execute; the graph
    wraps up with an honest 'stopped' reply (spec §12)."""
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise NotFoundError("Run not found.")
    run_registry.stop_run(user.id, run_id)
    task = db.scalar(
        select(Task).where(Task.run_id == run_id, Task.user_id == user.id)
    )
    active_attempt = db.scalar(
        select(TaskStepAttempt.id)
        .where(
            TaskStepAttempt.run_id == run_id,
            TaskStepAttempt.state.in_(("dispatching", "reconciling")),
        )
        .limit(1)
    )
    sending_approval = db.scalar(
        select(Approval.id)
        .where(
            Approval.run_id == run_id,
            Approval.user_id == user.id,
            Approval.status == "sending",
        )
        .limit(1)
    )
    if active_attempt is None and sending_approval is None:
        cancel_run_pending_approvals(
            db, run_id=run_id, actor_id=user.id
        )
        run.status = "stopped"
        run.state_json = None
        run.completed_at = utcnow()
        if task is not None:
            task.status = "stopped"
            task.completed_at = utcnow()
    else:
        run.status = "running"
        if task is not None:
            task.status = "paused"
            task.error = (
                "Stop requested while an action was in flight; its outcome "
                "must be reconciled before the task can finish."
            )
    db.commit()
    return {
        "success": True,
        "run_id": run_id,
        "stopped": True,
        "outcome_reconciliation_required": (
            active_attempt is not None or sending_approval is not None
        ),
    }


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ask a running agent run to pause at its next checkpoint (spec §24).
    The run persists its state and can be resumed later with
    POST /api/runs/{run_id}/resume."""
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise NotFoundError("Run not found.")
    if run.status not in ("running",):
        raise BadRequestError(
            f"Run is '{run.status}' and cannot be paused.", "RUN_NOT_PAUSABLE"
        )
    run_registry.request_pause(user.id, run_id)
    return {"success": True, "run_id": run_id, "pause_requested": True}


@router.post("/runs/{run_id}/resume", response_model=ChatResponse)
async def resume_paused_run(run_id: str, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db),
                            model_router: ModelRouter = Depends(get_router)):
    """Resume a run that was paused with the generic pause (spec §24)."""
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise NotFoundError("Run not found.")
    outcome = await resume_run(user_id=user.id, run_id=run_id, router=model_router)
    return _chat_response(outcome)


@router.post("/agents/stop")
def stop_all_agents(user: User = Depends(get_current_user)):
    """Emergency stop: halt every agent run currently executing for this user."""
    count = run_registry.stop_all(user.id)
    return {"success": True, "stopped": count,
            "message": f"Stopped {count} running agent(s)." if count
            else "No agents were running."}
