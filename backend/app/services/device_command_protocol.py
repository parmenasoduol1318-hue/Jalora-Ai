"""Versioned, idempotent command delivery for outbound device companions."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import jwt

from app.config import get_settings
from app.context import ToolContext
from app.models.agent import AgentRun
from app.models.device import DeviceConnection, DeviceRemoteCommand
from app.models.task import Task, TaskStep
from app.services.url_safety import validate_external_url

PROTOCOL_VERSION = 1
DEVICE_ACCESS_TTL = timedelta(minutes=5)
DELIVERY_LEASE = timedelta(seconds=45)
EXECUTION_LEASE = timedelta(minutes=2)
MAX_DELIVERY_BATCH = 20
MAX_RECEIPT_BYTES = 2048

_OPERATION_SCOPES = {
    "browser.open_url": "remote.browser.open_url",
}
_RECEIPT_STATUSES = {"succeeded", "failed", "uncertain"}
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_EVIDENCE_KEYS = {"browser_open_receipt", "opened_url_host", "reason"}


class DeviceProtocolError(ValueError):
    """A command cannot safely enter or progress through the protocol."""


class DeviceIdempotencyConflict(DeviceProtocolError):
    """An idempotency key was reused with a different command payload."""


def issue_device_access_token(device_id: str) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + DEVICE_ACCESS_TTL
    token = jwt.encode(
        {
            "sub": device_id,
            "typ": "device_access",
            "iss": "jalora-device-protocol",
            "aud": "jalora-device-protocol",
            "iat": now,
            "exp": expires_at,
            "jti": secrets.token_urlsafe(18),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _payload_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _validate_task_context(
    db: Session,
    user_id: str,
    context: ToolContext,
) -> None:
    if context.step_id and not context.task_id:
        raise DeviceProtocolError("A task step cannot be attached without its task.")
    if context.task_id:
        task = db.scalar(
            select(Task).where(
                Task.id == context.task_id,
                Task.user_id == user_id,
            )
        )
        if task is None:
            raise DeviceProtocolError("The task context is not owned by this user.")
        if context.run_id and task.run_id != context.run_id:
            raise DeviceProtocolError("The task and run context do not match.")
        if context.step_id:
            step = db.scalar(
                select(TaskStep).where(
                    TaskStep.id == context.step_id,
                    TaskStep.task_id == task.id,
                )
            )
            if step is None:
                raise DeviceProtocolError("The task step does not belong to this task.")
    elif context.run_id:
        run = db.scalar(
            select(AgentRun).where(
                AgentRun.id == context.run_id,
                AgentRun.user_id == user_id,
            )
        )
        if run is None:
            raise DeviceProtocolError("The run context is not owned by this user.")


def _normalized_command(
    action: str,
    arguments: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    required_scope = _OPERATION_SCOPES.get(action)
    if required_scope is None:
        raise DeviceProtocolError("This device operation is not supported.")
    if action == "browser.open_url":
        if set(arguments) != {"url"} or not isinstance(arguments.get("url"), str):
            raise DeviceProtocolError("The browser command requires only a URL.")
        url = arguments["url"].strip()
        if not url or len(url) > 2000 or validate_external_url(url) is not None:
            raise DeviceProtocolError("Only public external HTTP(S) links are allowed.")
        return required_scope, {"url": url}
    raise DeviceProtocolError("This device operation is not supported.")


def queue_device_command(
    db: Session,
    *,
    user_id: str,
    device: DeviceConnection,
    action: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> tuple[DeviceRemoteCommand, bool]:
    """Create one immutable command; return (command, already_existed)."""
    if device.user_id != user_id or device.status != "active":
        raise DeviceProtocolError("The paired device is not active for this user.")
    required_scope, safe_arguments = _normalized_command(action, arguments)
    if required_scope not in (device.scopes or []):
        raise DeviceProtocolError("The paired device has not granted this operation.")
    _validate_task_context(db, user_id, context)

    idempotency_key = context.idempotency_key or secrets.token_hex(32)
    if len(idempotency_key) > 64:
        raise DeviceProtocolError("The task idempotency key is invalid.")
    digest_payload = {
        "action": action,
        "arguments": safe_arguments,
        "device_id": device.id,
        "required_scope": required_scope,
        "run_id": context.run_id,
        "task_id": context.task_id,
        "task_step_id": context.step_id,
        "user_id": user_id,
    }
    digest = _payload_digest(digest_payload)
    existing = db.scalar(
        select(DeviceRemoteCommand).where(
            DeviceRemoteCommand.user_id == user_id,
            DeviceRemoteCommand.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.payload_digest != digest:
            raise DeviceIdempotencyConflict(
                "The task idempotency key was reused for a different device command."
            )
        return existing, True

    command = DeviceRemoteCommand(
        user_id=user_id,
        device_id=device.id,
        action=action,
        target_url=safe_arguments["url"],
        protocol_version=PROTOCOL_VERSION,
        arguments=safe_arguments,
        required_scope=required_scope,
        command_nonce=secrets.token_urlsafe(32),
        idempotency_key=idempotency_key,
        payload_digest=digest,
        expected_evidence=["target_approval", "browser_open_receipt"],
        task_id=context.task_id,
        run_id=context.run_id,
        task_step_id=context.step_id,
        status="pending",
        protocol_status="queued",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(command)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(DeviceRemoteCommand).where(
                DeviceRemoteCommand.user_id == user_id,
                DeviceRemoteCommand.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        if existing.payload_digest != digest:
            raise DeviceIdempotencyConflict(
                "The task idempotency key was reused for a different device command."
            )
        return existing, True
    return command, False


def command_envelope(command: DeviceRemoteCommand) -> dict[str, Any]:
    required_scope, safe_arguments = _normalized_command(
        command.action, dict(command.arguments or {})
    )
    digest_payload = {
        "action": command.action,
        "arguments": safe_arguments,
        "device_id": command.device_id,
        "required_scope": required_scope,
        "run_id": command.run_id,
        "task_id": command.task_id,
        "task_step_id": command.task_step_id,
        "user_id": command.user_id,
    }
    if (
        required_scope != command.required_scope
        or safe_arguments.get("url") != command.target_url
        or not hmac.compare_digest(
            _payload_digest(digest_payload), command.payload_digest
        )
    ):
        raise DeviceProtocolError("The stored device command failed validation.")
    return {
        "protocol_version": command.protocol_version,
        "command_id": command.id,
        "device_id": command.device_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "task_step_id": command.task_step_id,
        "operation": command.action,
        "arguments": dict(command.arguments or {}),
        "required_scope": command.required_scope,
        "issued_at": command.created_at,
        "expires_at": command.expires_at,
        "nonce": command.command_nonce,
        "idempotency_key": command.idempotency_key,
        "expected_evidence": list(command.expected_evidence or []),
        "payload_digest": command.payload_digest,
        "delivery_count": command.delivery_count,
        "lease_expires_at": command.lease_expires_at,
    }


def claim_device_commands(
    db: Session,
    device: DeviceConnection,
) -> list[dict[str, Any]]:
    """Claim queued commands; never redeliver an expired in-flight action."""
    now = datetime.now(timezone.utc)
    db.execute(
        update(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.expires_at <= now,
            DeviceRemoteCommand.protocol_status.in_(("queued", "delivered")),
        )
        .values(
            status="expired",
            protocol_status="expired",
            error_code="DEVICE_COMMAND_EXPIRED",
            completed_at=now,
        )
    )
    db.execute(
        update(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "approved",
            DeviceRemoteCommand.protocol_status == "running",
            DeviceRemoteCommand.lease_expires_at <= now,
        )
        .values(
            protocol_status="uncertain",
            error_code="DEVICE_EXECUTION_LEASE_EXPIRED",
            completed_at=now,
        )
    )

    active_deliveries = db.scalars(
        select(DeviceRemoteCommand.id)
        .where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.protocol_status == "delivered",
            DeviceRemoteCommand.lease_expires_at > now,
            DeviceRemoteCommand.required_scope.in_(device.scopes or []),
            DeviceRemoteCommand.expires_at > now,
        )
        .order_by(DeviceRemoteCommand.created_at)
        .limit(MAX_DELIVERY_BATCH)
    ).all()
    remaining_slots = MAX_DELIVERY_BATCH - len(active_deliveries)
    candidates = (
        db.scalars(
            select(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.device_id == device.id,
                DeviceRemoteCommand.status == "pending",
                DeviceRemoteCommand.required_scope.in_(device.scopes or []),
                DeviceRemoteCommand.expires_at > now,
                or_(
                    DeviceRemoteCommand.protocol_status == "queued",
                    and_(
                        DeviceRemoteCommand.protocol_status == "delivered",
                        DeviceRemoteCommand.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(DeviceRemoteCommand.created_at)
            .limit(remaining_slots)
        ).all()
        if remaining_slots
        else []
    )
    for candidate in candidates:
        try:
            command_envelope(candidate)
        except DeviceProtocolError:
            db.execute(
                update(DeviceRemoteCommand)
                .where(
                    DeviceRemoteCommand.id == candidate.id,
                    DeviceRemoteCommand.device_id == device.id,
                    DeviceRemoteCommand.status == "pending",
                    DeviceRemoteCommand.protocol_status.in_(("queued", "delivered")),
                )
                .values(
                    status="failed",
                    protocol_status="failed",
                    error_code="INVALID_STORED_DEVICE_COMMAND",
                    completed_at=now,
                )
            )
            continue
        claim = db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.id == candidate.id,
                DeviceRemoteCommand.device_id == device.id,
                DeviceRemoteCommand.status == "pending",
                DeviceRemoteCommand.expires_at > now,
                or_(
                    DeviceRemoteCommand.protocol_status == "queued",
                    and_(
                        DeviceRemoteCommand.protocol_status == "delivered",
                        DeviceRemoteCommand.lease_expires_at <= now,
                    ),
                ),
            )
            .values(
                protocol_status="delivered",
                delivery_count=DeviceRemoteCommand.delivery_count + 1,
                delivered_at=now,
                lease_expires_at=now + DELIVERY_LEASE,
            )
            .execution_options(synchronize_session=False)
        )
    db.commit()
    commands = db.scalars(
        select(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.protocol_status == "delivered",
            DeviceRemoteCommand.lease_expires_at > now,
            DeviceRemoteCommand.required_scope.in_(device.scopes or []),
            DeviceRemoteCommand.expires_at > now,
        )
        .order_by(DeviceRemoteCommand.created_at)
        .limit(MAX_DELIVERY_BATCH)
        .execution_options(populate_existing=True)
    ).all()
    return [command_envelope(command) for command in commands]


def verify_command_nonce(command: DeviceRemoteCommand, nonce: str) -> bool:
    return hmac.compare_digest(command.command_nonce, nonce)


def approve_device_command(
    db: Session,
    *,
    command: DeviceRemoteCommand,
    device: DeviceConnection,
    nonce: str,
    approve: bool,
) -> tuple[str, str | None, bool]:
    """Return status, approved URL, and whether this call claimed the action."""
    if not verify_command_nonce(command, nonce):
        raise DeviceProtocolError("The command nonce is invalid.")
    now = datetime.now(timezone.utc)
    if command.status == "pending" and _timestamp(command.expires_at) <= now:
        db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.id == command.id,
                DeviceRemoteCommand.status == "pending",
            )
            .values(
                status="expired",
                protocol_status="expired",
                error_code="DEVICE_COMMAND_EXPIRED",
                completed_at=now,
            )
        )
        db.commit()
        raise DeviceProtocolError("The device command has expired.")

    if (
        command.status in {"approved", "rejected"}
        and command.protocol_status in {"running", "cancelled"}
    ):
        previous_approval = command.status == "approved"
        if previous_approval != approve:
            raise DeviceProtocolError("This command already has a different decision.")
        return command.status, None, False
    if command.status != "pending" or command.protocol_status != "delivered":
        raise DeviceProtocolError("This command is not available for a decision.")

    new_status = "approved" if approve else "rejected"
    new_protocol_status = "running" if approve else "cancelled"
    claim = db.execute(
        update(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.id == command.id,
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.protocol_status == "delivered",
            DeviceRemoteCommand.expires_at > now,
            DeviceRemoteCommand.command_nonce == nonce,
        )
        .values(
            status=new_status,
            protocol_status=new_protocol_status,
            decided_at=now,
            running_at=now if approve else None,
            completed_at=None if approve else now,
            lease_expires_at=now + EXECUTION_LEASE if approve else None,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        raise DeviceProtocolError("This command is no longer available for a decision.")
    db.commit()
    return new_status, command.target_url if approve else None, True


def _normalize_receipt(
    *,
    command: DeviceRemoteCommand,
    status: str,
    evidence: dict[str, Any],
    error_code: str | None,
) -> dict[str, Any]:
    if status not in _RECEIPT_STATUSES:
        raise DeviceProtocolError("The receipt status is not supported.")
    if set(evidence) - _EVIDENCE_KEYS:
        raise DeviceProtocolError("The receipt contains unsupported evidence fields.")
    expected = set(command.expected_evidence or [])
    if "browser_open_receipt" in evidence and "browser_open_receipt" not in expected:
        raise DeviceProtocolError("The receipt evidence was not requested.")
    if "browser_open_receipt" in evidence and not isinstance(
        evidence["browser_open_receipt"], bool
    ):
        raise DeviceProtocolError("The browser receipt must be a boolean.")
    if status == "succeeded" and evidence.get("browser_open_receipt") is not True:
        raise DeviceProtocolError("A successful receipt must confirm the browser handoff.")
    if status == "failed" and evidence.get("browser_open_receipt") is True:
        raise DeviceProtocolError("A failed receipt cannot confirm a successful handoff.")
    if status == "uncertain" and evidence.get("browser_open_receipt") is True:
        raise DeviceProtocolError("An uncertain receipt cannot claim a successful handoff.")
    if "opened_url_host" in evidence:
        host = evidence["opened_url_host"]
        if not isinstance(host, str) or len(host) > 253:
            raise DeviceProtocolError("The reported URL host is invalid.")
    if "reason" in evidence:
        reason = evidence["reason"]
        if not isinstance(reason, str) or len(reason) > 500:
            raise DeviceProtocolError("The receipt reason is too long.")
    if error_code is not None and not _ERROR_CODE.fullmatch(error_code):
        raise DeviceProtocolError("The receipt error code is invalid.")
    receipt = {
        "status": status,
        "evidence": evidence,
        "error_code": error_code,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(_canonical_json(receipt).encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise DeviceProtocolError("The receipt exceeds the protocol size limit.")
    return receipt


def record_device_receipt(
    db: Session,
    *,
    command: DeviceRemoteCommand,
    device: DeviceConnection,
    nonce: str,
    status: str,
    evidence: dict[str, Any],
    error_code: str | None,
) -> tuple[str, bool]:
    if not verify_command_nonce(command, nonce):
        raise DeviceProtocolError("The command nonce is invalid.")
    receipt = _normalize_receipt(
        command=command,
        status=status,
        evidence=evidence,
        error_code=error_code,
    )
    if command.protocol_status in {"succeeded", "failed"}:
        prior = dict(command.receipt or {})
        prior.pop("received_at", None)
        comparable = dict(receipt)
        comparable.pop("received_at", None)
        if prior == comparable:
            return command.protocol_status, False
        raise DeviceProtocolError("This command already has a final receipt.")
    if command.protocol_status not in {"running", "uncertain"}:
        raise DeviceProtocolError("A receipt is not expected for this command state.")
    if command.status != "approved" or command.device_id != device.id:
        raise DeviceProtocolError("The approved command does not belong to this device.")

    final_status = status
    now = datetime.now(timezone.utc)
    error = error_code or (
        "DEVICE_OUTCOME_UNKNOWN" if status == "uncertain" else None
    )
    claim = db.execute(
        update(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.id == command.id,
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "approved",
            DeviceRemoteCommand.protocol_status.in_(("running", "uncertain")),
            DeviceRemoteCommand.command_nonce == nonce,
        )
        .values(
            protocol_status=final_status,
            status="opened" if final_status == "succeeded" else (
                "failed" if final_status == "failed" else "approved"
            ),
            receipt=receipt,
            error_code=error,
            completed_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        db.rollback()
        raise DeviceProtocolError("This command receipt has already been recorded.")
    db.commit()
    return final_status, True
