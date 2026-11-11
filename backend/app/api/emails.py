"""Email UX API: search/read/threads/compose/send + auto-reply rules (§34-38).

`send` always creates a standalone Approval row (run_id="") and requires an
explicit review through `/api/emails/send/{id}/approve|reject`, regardless of
autonomy settings.

Additional endpoint `/api/emails/task` creates a reviewable email draft from
natural-language requests without relying on the general chat model.
"""
from __future__ import annotations

import mimetypes
import uuid
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_router
from app.config import get_settings
from app.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.agent import Approval
from app.models.autoreply import AutoReplyRule
from app.models.task import TaskStep
from app.models.user import User, utcnow
from app.schemas import (
    EmailCompose,
    EmailDraftOut,
    EmailReplyDraftRequest,
    EmailReplyDraftResult,
    EmailSend,
    EmailSendResult,
    EmailSentOut,
    EmailTaskRequest,
    EmailTaskResult,
    RuleCreate,
    RuleOut,
    RulePatch,
)
from app.services import email as email_service
from app.services.model_router import ModelRouter
from app.services.policy import decide_for_user
from app.services.approval_lifecycle import (
    claim_approval,
    edit_approval_payload,
    finalize_approval,
    initialize_approval,
    reconcile_unknown_approval,
)
from app.tools.base import PermissionLevel

router = APIRouter(prefix="/api/emails", tags=["emails"])
_EMAIL_ATTACHMENT_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".rtf": "application/rtf",
}


def _ensure_send_allowed(user: User) -> None:
    decision = decide_for_user(
        permission=PermissionLevel.CONFIRM,
        preferences=user.prefs or {},
        tool_name="send_email",
        tags=["email"],
    )
    if not decision.allowed:
        raise ForbiddenError(decision.detail or "Email sending is denied for this user.",
                             decision.code or "TOOL_DENIED")


async def _execute_canonical_email_tool(
    *,
    user: User,
    model_router: ModelRouter,
    arguments: dict,
    request_message: str,
    tool_name: str = "send_email",
    description: str | None = None,
    success_message: str | None = None,
    require_approval: bool = True,
) -> tuple[Any, dict | None]:
    from app.errors import JaloraError
    from app.services.direct_action import execute_direct_action

    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name=tool_name,
        arguments=arguments,
        request_message=request_message,
        description=description or f"Execute {tool_name}.",
        success_message=success_message or f"Completed {tool_name}.",
        require_approval=require_approval,
    )
    if outcome.status == "error" and outcome.error:
        raise JaloraError(
            502,
            outcome.error.get("code", "EMAIL_TASK_FAILED"),
            outcome.error.get("details", "The email task could not be prepared."),
        )
    approval = dict(outcome.approval) if outcome.approval else None
    if approval is not None:
        approval.update({
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
            "conversation_id": outcome.conversation_id,
            "standalone": False,
        })
    return outcome, approval


@router.get("/search")
async def search_emails(q: str = "", limit: int = 10,
                        user: User = Depends(get_current_user)):
    return await email_service.search_emails(user_id=user.id, query=q,
                                             max_results=limit)


@router.get("/threads")
async def list_threads(limit: int = 20, user: User = Depends(get_current_user)):
    return await email_service.list_threads(user_id=user.id, max_results=limit)


@router.post("/attachments")
async def upload_email_attachment(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Store an email attachment directly without document extraction or indexing."""
    content = await file.read(email_service.MAX_EMAIL_ATTACHMENT_BYTES + 1)
    if not content:
        raise BadRequestError("The attachment is empty.", "EMPTY_ATTACHMENT")
    if len(content) > email_service.MAX_EMAIL_ATTACHMENT_BYTES:
        raise BadRequestError(
            "Email attachments cannot exceed 20 MB.", "ATTACHMENT_TOO_LARGE"
        )
    filename = Path((file.filename or "").replace("\\", "/")).name
    mime_type = (
        _EMAIL_ATTACHMENT_MIME_OVERRIDES.get(Path(filename).suffix.lower())
        or mimetypes.guess_type(filename)[0]
        or ""
    ).lower()
    if mime_type not in email_service._ALLOWED_ATTACHMENT_MIMES:
        raise BadRequestError(
            f"File type '{Path(filename).suffix or 'unknown'}' cannot be attached.",
            "INVALID_ATTACHMENT_MIME",
        )

    relative_dir = Path("email-attachments")
    stored_name = f"{uuid.uuid4().hex}-{filename}"
    attachment_dir = get_settings().upload_dir / user.id / relative_dir
    attachment_dir.mkdir(parents=True, exist_ok=True)
    (attachment_dir / stored_name).write_bytes(content)
    return {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(content),
        "url": f"/api/artifacts/{quote(user.id)}/"
        f"{quote(str(relative_dir / stored_name).replace(chr(92), '/'))}",
    }


# Keep the dynamic /{email_id} route AFTER the static ones so "/rules" and
# "/threads" never get swallowed by the read handler.
@router.get("/rules", response_model=list[RuleOut])
def list_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AutoReplyRule).where(AutoReplyRule.user_id == user.id)
        .order_by(AutoReplyRule.priority.desc(), AutoReplyRule.created_at)
    ).all()
    return [RuleOut.model_validate(r) for r in rows]


@router.post("/rules", status_code=202)
async def create_rule(
    body: RuleCreate,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    from app.services.direct_action import execute_direct_action

    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="create_email_auto_reply_rule",
        arguments=body.model_dump(),
        request_message=f"Create email auto-reply rule '{body.name}'.",
        description=f"Create email auto-reply rule '{body.name}'.",
        success_message=f"Created email auto-reply rule '{body.name}'.",
    )
    if outcome.status == "error" and outcome.error:
        from app.errors import JaloraError

        raise JaloraError(
            502,
            outcome.error.get("code", "EMAIL_RULE_CREATE_FAILED"),
            outcome.error.get("details", "The email rule could not be created."),
        )
    return outcome.as_response()


@router.patch("/rules/{rule_id}", status_code=202)
async def patch_rule(
    rule_id: str,
    body: RulePatch,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
    db: Session = Depends(get_db),
):
    rule = db.get(AutoReplyRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise NotFoundError("Rule not found.")
    updates = body.model_dump(exclude_unset=True)
    if not updates or any(value is None for value in updates.values()):
        raise BadRequestError(
            "Provide at least one non-null rule field to update.",
            "INVALID_EMAIL_RULE",
        )
    from app.services.direct_action import execute_direct_action

    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="update_email_auto_reply_rule",
        arguments={"rule_id": rule_id, "updates": updates},
        request_message=f"Update email auto-reply rule '{rule.name}'.",
        description=f"Update email auto-reply rule '{rule.name}'.",
        success_message=f"Updated email auto-reply rule '{rule.name}'.",
    )
    if outcome.status == "error" and outcome.error:
        from app.errors import JaloraError

        raise JaloraError(
            502,
            outcome.error.get("code", "EMAIL_RULE_UPDATE_FAILED"),
            outcome.error.get("details", "The email rule could not be updated."),
        )
    return outcome.as_response()


@router.delete("/rules/{rule_id}", status_code=202)
async def delete_rule(
    rule_id: str,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
    db: Session = Depends(get_db),
):
    rule = db.get(AutoReplyRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise NotFoundError("Rule not found.")
    from app.services.direct_action import execute_direct_action

    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="delete_email_auto_reply_rule",
        arguments={"rule_id": rule_id},
        request_message=f"Delete email auto-reply rule '{rule.name}'.",
        description=f"Delete email auto-reply rule '{rule.name}'.",
        success_message=f"Deleted email auto-reply rule '{rule.name}'.",
    )
    if outcome.status == "error" and outcome.error:
        from app.errors import JaloraError

        raise JaloraError(
            502,
            outcome.error.get("code", "EMAIL_RULE_DELETE_FAILED"),
            outcome.error.get("details", "The email rule could not be deleted."),
        )
    return outcome.as_response()


@router.get("/{email_id}")
async def read_email(email_id: str, user: User = Depends(get_current_user)):
    return await email_service.read_email(user_id=user.id, email_id=email_id)


@router.post("/{email_id}/reply-draft", response_model=EmailReplyDraftResult)
async def draft_email_reply(
    email_id: str,
    body: EmailReplyDraftRequest,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
    db: Session = Depends(get_db),
):
    """Prepare a reply draft for editing; this endpoint never saves or sends it."""
    result = await email_service.read_email(user_id=user.id, email_id=email_id)
    message = result.get("email") or {}
    instruction = body.instruction.strip()
    if not instruction:
        raise BadRequestError(
            "The reply instruction cannot be blank.", "EMPTY_REPLY_INSTRUCTION"
        )
    recipient = parseaddr(str(message.get("from_addr") or ""))[1]
    if not recipient:
        raise BadRequestError(
            "The selected message does not have a usable sender address.",
            "EMAIL_SENDER_UNAVAILABLE",
        )
    from app.services.direct_action import execute_direct_action

    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="draft_email_reply",
        arguments={"email_id": email_id, "instruction": instruction},
        request_message=f"Draft a reply to email {email_id}.",
        description="Generate an editable email reply for review.",
        success_message="Generated an editable reply. It was not saved or sent.",
        require_approval=False,
    )
    if outcome.status != "completed":
        error = outcome.error or {}
        raise ServiceUnavailableError(
            error.get("details", "The model did not return a usable reply draft."),
            error.get("code", "REPLY_GENERATION_FAILED"),
        )
    db.expire_all()
    step = db.scalar(
        select(TaskStep)
        .where(
            TaskStep.task_id == outcome.task_id,
            TaskStep.tool_id == "draft_email_reply",
        )
        .order_by(TaskStep.idx.desc())
    )
    execution_result = (
        step.result_data.get("execution_result", {})
        if step is not None and isinstance(step.result_data, dict)
        else {}
    )
    draft_data = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    if not all(str(draft_data.get(field) or "").strip() for field in ("to", "subject", "body")):
        raise ServiceUnavailableError(
            "The generated reply did not pass completion checks.",
            "EMAIL_DRAFT_CHECK_FAILED",
        )
    return EmailReplyDraftResult(
        status="editable_draft",
        to=draft_data["to"],
        subject=draft_data["subject"],
        body=draft_data["body"],
        model=draft_data["model"],
        task_id=outcome.task_id,
    )


@router.post("/compose", response_model=EmailDraftOut)
async def compose_draft(body: EmailCompose,
                        user: User = Depends(get_current_user),
                        model_router: ModelRouter = Depends(get_router),
                        db: Session = Depends(get_db)):
    arguments = {
        "to": body.to,
        "subject": body.subject,
        "body": body.body,
        "attachments": body.attachments,
        "cc": body.cc,
        "bcc": body.bcc,
    }
    outcome, _ = await _execute_canonical_email_tool(
        user=user,
        model_router=model_router,
        arguments=arguments,
        request_message=(
            f"Save the email draft to {body.to} with subject {body.subject}."
        ),
        tool_name="compose_email",
        description=f"Save the email draft for {body.to}.",
        success_message=f"Saved the email draft for {body.to}.",
        require_approval=False,
    )
    if outcome.status != "completed":
        from app.errors import JaloraError

        raise JaloraError(
            502,
            "EMAIL_DRAFT_NOT_COMPLETED",
            outcome.reply or "The email draft was not saved.",
        )
    db.expire_all()
    step = db.scalar(
        select(TaskStep)
        .where(
            TaskStep.task_id == outcome.task_id,
            TaskStep.tool_id == "compose_email",
        )
        .order_by(TaskStep.idx.desc())
    )
    execution_result = (
        step.result_data.get("execution_result", {})
        if step is not None and isinstance(step.result_data, dict)
        else {}
    )
    draft_data = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    return EmailDraftOut(
        id=str(draft_data.get("draft_id") or ""),
        to=email_service.normalize_email_addresses(body.to, required=True),
        cc=email_service.normalize_email_addresses(body.cc),
        bcc=email_service.normalize_email_addresses(body.bcc),
        subject=body.subject,
        body=body.body,
        status="draft",
        attachments=draft_data.get("attachments", body.attachments),
    )


@router.post("/send", response_model=EmailSendResult)
async def send(
    body: EmailSend,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    _ensure_send_allowed(user)
    arguments = {
        "to": body.to,
        "subject": body.subject,
        "body": body.body,
        "cc": body.cc,
        "bcc": body.bcc,
        "regards": body.regards,
        "attachments": body.attachments,
    }
    outcome, approval = await _execute_canonical_email_tool(
        user=user,
        model_router=model_router,
        arguments=arguments,
        request_message=(
            f"Send the reviewed email to {body.to} with subject {body.subject}."
        ),
        description=f"Send the reviewed email to {body.to}.",
        success_message=(
            f"The email provider accepted the message to {body.to}."
        ),
    )
    return EmailSendResult(
        status=outcome.status,
        approval=approval,
    )


def _owned_pending_send(db: Session, approval_id: str, user: User) -> Approval:
    row = db.get(Approval, approval_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Approval not found.")
    if row.tool_name != "send_email" or row.status != "pending":
        raise BadRequestError(
            "This approval is not a pending email send.", "INVALID_APPROVAL"
        )
    return row


def _claim_pending_send(db: Session, approval_id: str, user: User) -> Approval:
    row = _owned_pending_send(db, approval_id, user)
    claim_approval(db, row, actor_id=user.id, decision="approved")
    db.refresh(row)
    db.commit()
    return row


@router.patch("/send/{approval_id}")
def edit_send(approval_id: str, body: EmailCompose,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Update a standalone email approval with the exact reviewed draft."""
    row = _owned_pending_send(db, approval_id, user)
    if row.run_id and row.task_id:
        from app.services.chat_service import update_pending_approval

        return update_pending_approval(
            user_id=user.id,
            approval_id=approval_id,
            arguments=body.model_dump(mode="json"),
        )
    edit_approval_payload(
        db,
        row,
        arguments=body.model_dump(mode="json"),
        editor_user_id=user.id,
    )
    db.commit()
    db.refresh(row)
    return {
        "id": row.id, "run_id": row.run_id, "tool_name": row.tool_name,
        "arguments": row.arguments, "level": row.level,
        "kind": row.kind, "status": row.status,
        "payload_version": row.payload_version,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/send/{approval_id}/approve", response_model=EmailSendResult)
async def approve_send(approval_id: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db),
                       model_router: ModelRouter = Depends(get_router)):
    _ensure_send_allowed(user)
    row = _owned_pending_send(db, approval_id, user)
    if row.run_id and row.task_id:
        from app.services.chat_service import resume_approval

        outcome = await resume_approval(
            user_id=user.id,
            approval_id=approval_id,
            approve=True,
            router=model_router,
        )
        db.expire_all()
        decided = db.get(Approval, approval_id)
        result = decided.result if decided and isinstance(decided.result, dict) else {}
        receipt = result.get("provider_receipt")
        if outcome.status == "completed" and isinstance(receipt, dict):
            arguments = decided.arguments if decided and isinstance(
                decided.arguments, dict
            ) else {}
            receipt = {
                **receipt,
                "to": arguments.get("to", receipt.get("to", "")),
                "cc": arguments.get("cc", receipt.get("cc", "")),
                "bcc": arguments.get("bcc", receipt.get("bcc", "")),
                "subject": arguments.get("subject", receipt.get("subject", "")),
                "attachments": arguments.get(
                    "attachments", receipt.get("attachments", [])
                ),
            }
            return EmailSendResult(
                status="sent",
                sent=EmailSentOut(**receipt),
            )
        return EmailSendResult(
            status=(
                "outcome_unknown"
                if decided and decided.status == "outcome_unknown"
                else outcome.status
            ),
            approval=outcome.approval,
        )

    row = _claim_pending_send(db, approval_id, user)
    args = row.arguments or {}
    try:
        receipt = await email_service.send_email(
            user_id=user.id, recipient=str(args.get("to", "")),
            cc=str(args.get("cc", "")), bcc=str(args.get("bcc", "")),
            subject=str(args.get("subject", "")),
            body=email_service.append_regards(
                str(args.get("body", "")), str(args.get("regards", ""))
            ),
            attachments=args.get("attachments") or [],
        )
    except email_service.EmailError as exc:
        finalize_approval(
            db, row, status="failed",
            result={"status": "failed", "error_code": exc.code},
        )
        db.commit()
        raise
    except Exception:
        # Network/provider exceptions can leave the provider outcome unknown;
        # never retry automatically or report a successful send.
        finalize_approval(
            db, row, status="outcome_unknown",
            result={"status": "outcome_unknown"},
        )
        db.commit()
        raise
    finalize_approval(
        db,
        row,
        status="approved",
        result={
            "status": "provider_confirmed",
            "action_outcome": (
                "provider_accepted"
                if receipt.provider else "succeeded"
            ),
            "provider_receipt": receipt.as_dict(),
        },
    )
    db.commit()
    return EmailSendResult(status="sent", sent=EmailSentOut(**receipt.as_dict()))


@router.post("/send/{approval_id}/reconcile")
async def reconcile_send(
    approval_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reconcile an uncertain email outcome without ever retrying the send."""
    row = db.get(Approval, approval_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Approval not found.")
    if row.tool_name != "send_email" or row.status != "outcome_unknown":
        raise BadRequestError(
            "Only an email with an uncertain provider outcome can be reconciled.",
            "APPROVAL_NOT_RECONCILABLE",
        )

    args = row.arguments if isinstance(row.arguments, dict) else {}
    try:
        outcome = await email_service.reconcile_sent_email(
            user_id=user.id,
            recipient=str(args.get("to") or ""),
            subject=str(args.get("subject") or ""),
            body=str(args.get("body") or ""),
            regards=str(args.get("regards") or ""),
            submitted_at=row.decided_at or row.created_at,
        )
    except email_service.EmailError as exc:
        raise ServiceUnavailableError(exc.detail, exc.code) from exc

    if outcome.get("status") == "provider_confirmed":
        result = {
            "status": "provider_confirmed",
            "action_outcome": "provider_accepted",
            "provider_receipt": outcome.get("provider_receipt"),
        }
        reconcile_unknown_approval(
            db, row, actor_id=user.id, result=result
        )
        if row.task_id and row.run_id and row.task_step_id:
            from app.services.execution_lifecycle import (
                reconcile_unknown_attempt_as_provider_accepted,
            )

            reconcile_unknown_attempt_as_provider_accepted(
                db=db,
                task_id=row.task_id,
                run_id=row.run_id,
                step_id=row.task_step_id,
                approval_id=row.id,
                provider_receipt=result["provider_receipt"],
            )
        db.commit()
        return {
            "id": approval_id,
            "status": "provider_confirmed",
            "provider_receipt": result.get("provider_receipt"),
        }

    return {
        "id": approval_id,
        "status": "outcome_unknown",
        "matches": int(outcome.get("matches") or 0),
        "retry_allowed": False,
    }


@router.post("/send/{approval_id}/reject", response_model=EmailSendResult)
async def reject_send(approval_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db),
                      model_router: ModelRouter = Depends(get_router)):
    row = _owned_pending_send(db, approval_id, user)
    if row.run_id and row.task_id:
        from app.services.chat_service import resume_approval

        await resume_approval(
            user_id=user.id,
            approval_id=approval_id,
            approve=False,
            router=model_router,
        )
        return EmailSendResult(status="rejected")

    claim_approval(db, row, actor_id=user.id, decision="rejected")
    db.refresh(row)
    db.commit()
    return EmailSendResult(status="rejected")


# ------------------------------------------------------------------------------------------------------------------------------------------
# Direct email task execution with auto-draft generation
# ------------------------------------------------------------------------------------------------------------------------------------------

@router.post("/task", response_model=EmailTaskResult)
async def create_email_task(
    body: EmailTaskRequest,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """Create an email send task from natural language with auto-generated content.

    This endpoint extracts email parameters and generates subject/body automatically,
    enabling direct task execution without relying on the LLM to do it.

    Example:
        POST /api/emails/task
        {"request": "Send an email to test@example.com and ask how he is getting on with Jalora"}

    Returns:
        - status: "paused_approval" while waiting for explicit review
        - draft: Contains the generated, editable email content
        - approval: Contains approval info for the review form
    """
    # Generate email draft from the request
    draft_data = await email_service.generate_email_draft_with_model(
        recipient=body.recipient or "",
        request_text=body.request,
        prefs=user.prefs or {},
    )

    if not draft_data.get("to") or not draft_data["to"].strip():
        return EmailTaskResult(
            status="error",
            draft=None,
            approval=None,
            sent=None,
            error={"code": "INVALID_RECIPIENT", "detail": "A valid recipient is required."},
        )

    if not draft_data.get("valid", True):
        return EmailTaskResult(
            status="error",
            draft={
                "to": draft_data.get("to", ""),
                "cc": draft_data.get("cc", ""),
                "bcc": draft_data.get("bcc", ""),
                "subject": draft_data.get("subject", ""),
                "body": draft_data.get("body", ""),
                "confidence": draft_data.get("confidence", 0.0),
            },
            approval=None,
            sent=None,
            error={
                "code": "INVALID_DRAFT",
                "detail": "The generated draft contains incomplete or unresolved content.",
            },
        )

    # Respect deny policies, but never let autonomy bypass email review.
    _ensure_send_allowed(user)

    arguments = {
        "to": draft_data["to"],
        "cc": draft_data.get("cc", ""),
        "bcc": draft_data.get("bcc", ""),
        "subject": draft_data["subject"],
        "body": draft_data["body"],
        "regards": draft_data.get("regards", "Regards,"),
        "attachments": body.attachments,
    }
    outcome, approval = await _execute_canonical_email_tool(
        user=user,
        model_router=model_router,
        arguments=arguments,
        request_message=(
            f"Prepare the reviewed email task for {arguments['to']} "
            f"with subject {arguments['subject']}."
        ),
        description=f"Send the reviewed email to {arguments['to']}.",
        success_message=(
            f"The email provider accepted the message to {arguments['to']}."
        ),
    )
    if approval is not None:
        approval["generation_method"] = draft_data.get(
            "generation_method", "heuristic"
        )
        approval["source"] = "email_task"

    return EmailTaskResult(
        status=outcome.status,
        draft={
            "to": draft_data["to"],
            "cc": draft_data.get("cc", ""),
            "bcc": draft_data.get("bcc", ""),
            "subject": draft_data["subject"],
            "body": draft_data["body"],
            "attachments": body.attachments,
            "confidence": draft_data.get("confidence", 0.0),
        },
        approval=approval,
    )
