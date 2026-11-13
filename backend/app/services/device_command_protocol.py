"""Versioned, idempotent command delivery for outbound device companions."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from app.config import get_settings
from app.context import ToolContext
from app.models.agent import AgentRun
from app.models.device import DeviceConnection, DeviceRemoteCommand
from app.models.task import Task, TaskStep
from app.services.url_safety import validate_external_url
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PROTOCOL_VERSION = 1
DEVICE_ACCESS_TTL = timedelta(minutes=5)
DELIVERY_LEASE = timedelta(seconds=45)
EXECUTION_LEASE = timedelta(minutes=2)
MAX_DELIVERY_BATCH = 20
MAX_RECEIPT_BYTES = 16_384

_OPERATION_SCOPES = {
    "browser.open_url": "remote.browser.open_url",
    "windows.app.launch": "windows.apps.launch",
    "windows.app.close": "windows.apps.launch",
    "windows.screen.inspect": "windows.screen.read",
    "windows.screen.ocr": "windows.screen.read",
    "windows.screen.click": "windows.screen.control",
    "windows.screen.type": "windows.screen.control",
    "windows.screen.key": "windows.screen.control",
    "android.app.launch": "android.apps.launch",
    "android.screen.inspect": "android.screen.read",
    "android.screen.click": "android.screen.control",
    "android.screen.type": "android.screen.control",
}
_RECEIPT_STATUSES = {"succeeded", "failed", "uncertain"}
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_APP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUPPORTED_KEYS = {
    "Enter", "Tab", "Esc", "Backspace", "Space", "Up", "Down", "Left", "Right"
}
_EVIDENCE_KEYS = {
    "browser_open_receipt",
    "opened_url_host",
    "reason",
    "operation_result",
    "accessible_controls",
    "screen_ocr_text",
}


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
    device: DeviceConnection | None = None,
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
    platform = "windows" if action.startswith("windows.") else (
        "android" if action.startswith("android.") else None
    )
    if platform is None:
        raise DeviceProtocolError("This device operation is not supported.")
    if device is None or device.platform != platform:
        raise DeviceProtocolError(f"A paired {platform.title()} companion is required.")

    manifest = device.capability_manifest or {}
    if not isinstance(manifest, dict):
        raise DeviceProtocolError("The device companion manifest is invalid.")
    applications = manifest.get("applications", [])
    if not isinstance(applications, list):
        raise DeviceProtocolError("The device companion manifest is invalid.")

    app_id = arguments.get("app_id")
    if not isinstance(app_id, str) or not _APP_ID.fullmatch(app_id):
        raise DeviceProtocolError("The native command requires a valid application ID.")
    app = next(
        (item for item in applications if isinstance(item, dict) and item.get("id") == app_id),
        None,
    )
    if app is None:
        raise DeviceProtocolError("The requested app is not in the companion allowlist.")

    capability = {
        "windows.app.launch": "application.launch",
        "windows.app.close": "application.close",
        "windows.screen.inspect": "screen.accessibility.inspect",
        "windows.screen.ocr": "screen.accessibility.inspect",
        "windows.screen.click": "screen.control",
        "windows.screen.type": "screen.control",
        "windows.screen.key": "screen.control",
        "android.app.launch": "application.launch",
        "android.screen.inspect": "screen.accessibility.inspect",
        "android.screen.click": "screen.control",
        "android.screen.type": "screen.control",
    }.get(action)
    if capability is None or capability not in app.get("capabilities", []):
        raise DeviceProtocolError("The requested app operation is not available.")
    if action in {
        "windows.screen.click",
        "windows.screen.type",
        "windows.screen.key",
        "android.screen.click",
        "android.screen.type",
    } and (
        f"{platform}.screen.read" not in (device.scopes or [])
        or "screen.accessibility.inspect" not in app.get("capabilities", [])
    ):
        raise DeviceProtocolError(
            f"{platform.title()} screen control requires screen-read permission and capability."
        )

    if action in {
        "windows.app.launch",
        "windows.app.close",
        "windows.screen.inspect",
        "android.app.launch",
        "windows.screen.ocr",
        "android.screen.inspect",
    }:
        if set(arguments) != {"app_id"}:
            raise DeviceProtocolError("The native command has unexpected arguments.")
        return required_scope, {"app_id": app_id}
    if action in {"windows.screen.click", "android.screen.click"}:
        if set(arguments) - {"app_id", "name", "automation_id"}:
            raise DeviceProtocolError("The native click has unexpected arguments.")
        name = arguments.get("name", "")
        automation_id = arguments.get("automation_id", "")
        if (
            not isinstance(name, str)
            or len(name) > 300
            or not isinstance(automation_id, str)
            or len(automation_id) > 200
            or not (name.strip() or automation_id.strip())
        ):
            raise DeviceProtocolError("The native click requires a control name or ID.")
        return required_scope, {
            "app_id": app_id,
            "name": name.strip(),
            "automation_id": automation_id.strip(),
        }
    if action in {"windows.screen.type", "android.screen.type"}:
        if set(arguments) - {"app_id", "text", "name", "automation_id"}:
            raise DeviceProtocolError("The native typing command has unexpected arguments.")
        text = arguments.get("text")
        name = arguments.get("name", "")
        automation_id = arguments.get("automation_id", "")
        if (
            not isinstance(text, str)
            or not text
            or len(text) > 2000
            or not isinstance(name, str)
            or len(name) > 300
            or not isinstance(automation_id, str)
            or len(automation_id) > 200
            or not (name.strip() or automation_id.strip())
        ):
            raise DeviceProtocolError(
                "The native typing command requires text and a target control."
            )
        return required_scope, {
            "app_id": app_id,
            "text": text,
            "name": name.strip(),
            "automation_id": automation_id.strip(),
        }
    if action == "windows.screen.key":
        key = arguments.get("key")
        if (
            set(arguments) != {"app_id", "key"}
            or not isinstance(key, str)
            or key not in _SUPPORTED_KEYS
        ):
            raise DeviceProtocolError("The requested Windows key is not supported.")
        return required_scope, {"app_id": app_id, "key": key}
    raise DeviceProtocolError("This device operation is not supported.")


def _expected_evidence(action: str) -> list[str]:
    if action == "browser.open_url":
        return ["target_approval", "browser_open_receipt"]
    expected = ["target_approval", "operation_result"]
    if action in {
        "windows.screen.inspect", "windows.screen.ocr", "android.screen.inspect"
    }:
        expected.append("accessible_controls")
    if action == "windows.screen.ocr":
        expected.append("screen_ocr_text")
    return expected


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
    required_scope, safe_arguments = _normalized_command(action, arguments, device)
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
        target_url=safe_arguments.get("url", ""),
        protocol_version=PROTOCOL_VERSION,
        arguments=safe_arguments,
        required_scope=required_scope,
        command_nonce=secrets.token_urlsafe(32),
        idempotency_key=idempotency_key,
        payload_digest=digest,
        expected_evidence=_expected_evidence(action),
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


def command_envelope(
    command: DeviceRemoteCommand,
    device: DeviceConnection | None = None,
) -> dict[str, Any]:
    required_scope, safe_arguments = _normalized_command(
        command.action, dict(command.arguments or {}), device
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
        or safe_arguments.get("url", "") != command.target_url
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
    *,
    client_type: str = "browser",
) -> list[dict[str, Any]]:
    """Claim queued commands; never redeliver an expired in-flight action."""
    if client_type == "browser":
        supported_operations = ("browser.open_url",)
    elif client_type == "windows" and device.platform == "windows":
        supported_operations = (
            "browser.open_url",
            "windows.app.launch",
            "windows.app.close",
            "windows.screen.inspect",
            "windows.screen.ocr",
            "windows.screen.click",
            "windows.screen.type",
            "windows.screen.key",
        )
    elif client_type == "android" and device.platform == "android":
        supported_operations = (
            "android.app.launch",
            "android.screen.inspect",
            "android.screen.click",
            "android.screen.type",
        )
    else:
        return []
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
            DeviceRemoteCommand.action.in_(supported_operations),
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
                DeviceRemoteCommand.action.in_(supported_operations),
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
    claimed_command_ids: list[str] = []
    for candidate in candidates:
        try:
            command_envelope(candidate, device)
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
        if claim.rowcount == 1:
            claimed_command_ids.append(candidate.id)
    db.commit()
    if not claimed_command_ids:
        return []
    commands = db.scalars(
        select(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.id.in_(claimed_command_ids),
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.protocol_status == "delivered",
            DeviceRemoteCommand.lease_expires_at > now,
            DeviceRemoteCommand.required_scope.in_(device.scopes or []),
            DeviceRemoteCommand.action.in_(supported_operations),
            DeviceRemoteCommand.expires_at > now,
        )
        .order_by(DeviceRemoteCommand.created_at)
        .limit(MAX_DELIVERY_BATCH)
        .execution_options(populate_existing=True)
    ).all()
    return [command_envelope(command, device) for command in commands]


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
    if (
        command.action == "browser.open_url"
        and status == "succeeded"
        and evidence.get("browser_open_receipt") is not True
    ):
        raise DeviceProtocolError("A successful receipt must confirm the browser handoff.")
    if (
        command.action == "browser.open_url"
        and status == "failed"
        and evidence.get("browser_open_receipt") is True
    ):
        raise DeviceProtocolError("A failed receipt cannot confirm a successful handoff.")
    if (
        command.action == "browser.open_url"
        and status == "uncertain"
        and evidence.get("browser_open_receipt") is True
    ):
        raise DeviceProtocolError("An uncertain receipt cannot claim a successful handoff.")
    if "operation_result" in evidence:
        result = evidence["operation_result"]
        allowed_result_keys = {
            "ok",
            "app_name",
            "status",
            "verified",
            "control_count",
            "truncated",
            "characters",
            "key",
            "reason",
        }
        if not isinstance(result, dict) or set(result) - allowed_result_keys:
            raise DeviceProtocolError("The operation result has unsupported fields.")
        if not isinstance(result.get("ok"), bool):
            raise DeviceProtocolError("The operation result must report whether it succeeded.")
        if status == "succeeded" and result["ok"] is not True:
            raise DeviceProtocolError("A successful receipt must confirm the operation.")
        if status == "failed" and result["ok"] is True:
            raise DeviceProtocolError("A failed receipt cannot confirm the operation.")
        if "app_name" in result and (
            not isinstance(result["app_name"], str) or len(result["app_name"]) > 120
        ):
            raise DeviceProtocolError("The reported app name is invalid.")
        if "status" in result and (
            not isinstance(result["status"], str) or len(result["status"]) > 40
        ):
            raise DeviceProtocolError("The reported operation status is invalid.")
        if "verified" in result and not isinstance(result["verified"], bool):
            raise DeviceProtocolError("The verification flag must be a boolean.")
        if "control_count" in result and (
            not isinstance(result["control_count"], int)
            or not 0 <= result["control_count"] <= 300
        ):
            raise DeviceProtocolError("The reported accessibility count is invalid.")
        if "truncated" in result and not isinstance(result["truncated"], bool):
            raise DeviceProtocolError("The truncation flag must be a boolean.")
        if "characters" in result and (
            not isinstance(result["characters"], int)
            or not 0 <= result["characters"] <= 2000
        ):
            raise DeviceProtocolError("The reported character count is invalid.")
        if "key" in result and (
            not isinstance(result["key"], str) or result["key"] not in _SUPPORTED_KEYS
        ):
            raise DeviceProtocolError("The reported key is invalid.")
        if "reason" in result and (
            not isinstance(result["reason"], str) or len(result["reason"]) > 500
        ):
            raise DeviceProtocolError("The operation result reason is too long.")
    if "accessible_controls" in evidence:
        controls = evidence["accessible_controls"]
        if (
            not isinstance(controls, list)
            or len(controls) > 40
            or any(not isinstance(control, dict) for control in controls)
        ):
            raise DeviceProtocolError("The accessible controls are invalid.")
        for control in controls:
            if set(control) - {
                "name",
                "control_type",
                "automation_id",
                "enabled",
                "visible",
                "value",
                "value_status",
            }:
                raise DeviceProtocolError("An accessible control has unsupported fields.")
            if any(
                not isinstance(control.get(key, ""), str)
                or len(control.get(key, "")) > maximum
                for key, maximum in (
                    ("name", 100),
                    ("control_type", 80),
                    ("automation_id", 100),
                )
            ):
                raise DeviceProtocolError("An accessible control field is invalid.")
            if any(
                key in control and not isinstance(control[key], bool)
                for key in ("enabled", "visible")
            ):
                raise DeviceProtocolError("An accessible control flag is invalid.")
            if "value" in control and (
                not isinstance(control["value"], str)
                or len(control["value"]) > 500
            ):
                raise DeviceProtocolError("An accessible control value is invalid.")
            if "value_status" in control and control["value_status"] not in {
                "available",
                "redacted_password",
                "unavailable",
            }:
                raise DeviceProtocolError("An accessible control value status is invalid.")
    expected = set(command.expected_evidence or [])
    for key in ("operation_result", "accessible_controls", "screen_ocr_text"):
        if key in evidence and key not in expected:
            raise DeviceProtocolError("The operation evidence was not requested.")
    if command.action.startswith(("windows.", "android.")) and status == "succeeded":
        if not isinstance(evidence.get("operation_result"), dict):
            raise DeviceProtocolError("A successful native receipt needs an operation result.")
        if command.action in {
            "windows.screen.inspect", "windows.screen.ocr", "android.screen.inspect"
        } and not isinstance(
            evidence.get("accessible_controls"), list
        ):
            raise DeviceProtocolError("A screen inspection needs accessible controls.")
        if command.action == "windows.screen.ocr" and not isinstance(
            evidence.get("screen_ocr_text"), str
        ):
            raise DeviceProtocolError("A successful OCR receipt needs OCR text.")
    if "screen_ocr_text" in evidence and (
        not isinstance(evidence["screen_ocr_text"], str)
        or len(evidence["screen_ocr_text"]) > 10_000
    ):
        raise DeviceProtocolError("The OCR text is invalid or too long.")
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
    if command.task_id and command.task_step_id:
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.id == command.task_step_id,
                TaskStep.task_id == command.task_id,
            )
        )
        if step is not None:
            stored_result = step.result_data
            result_data = dict(stored_result) if isinstance(stored_result, dict) else {}
            stored_execution = result_data.get("execution_result")
            execution_result = (
                dict(stored_execution)
                if isinstance(stored_execution, dict)
                else {}
            )
            stored_evidence = execution_result.get("evidence")
            step_evidence = (
                list(stored_evidence[-39:])
                if isinstance(stored_evidence, list)
                else []
            )
            step_evidence.append({
                "type": "remote_device_receipt",
                "command_id": command.id,
                "action": command.action,
                "status": final_status,
                "error_code": error,
                "evidence": receipt["evidence"],
                "received_at": receipt["received_at"],
            })
            execution_result["evidence"] = step_evidence
            execution_result["outcome"] = {
                "succeeded": "verified",
                "failed": "failed",
                "uncertain": "outcome_unknown",
            }[final_status]
            execution_result["verification"] = {
                "succeeded": "verified",
                "failed": "failed",
                "uncertain": "unavailable",
            }[final_status]
            execution_result["safe_summary"] = (
                f"Remote device action receipt: {final_status}."
            )
            result_data["execution_result"] = execution_result
            step.result_data = result_data
            step.outcome_status = execution_result["outcome"]
            step.verification_status = execution_result["verification"]
            step.error_code = error
            step.status = (
                "done" if final_status == "succeeded"
                else "failed" if final_status == "failed"
                else "active"
            )
            step.completed_at = None if final_status == "uncertain" else now
    db.commit()
    return final_status, True
