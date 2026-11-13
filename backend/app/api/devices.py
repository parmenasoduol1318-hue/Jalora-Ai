from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode, urlsplit

import jwt
from app.api.deps import get_current_user, get_db, get_router
from app.config import get_settings
from app.errors import (
    AuthError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    JaloraError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.device import (
    DeviceConnection,
    DeviceMessage,
    DevicePairingRequest,
    DeviceRemoteCommand,
    LocalDeviceApp,
)
from app.models.task import TaskStep
from app.models.user import User, utcnow
from app.services import local_device_control
from app.services.device_command_protocol import (
    DEVICE_ACCESS_TTL,
    DeviceProtocolError,
    approve_device_command,
    claim_device_commands,
    issue_device_access_token,
    record_device_receipt,
    verify_command_nonce,
)
from app.services.direct_action import execute_direct_action
from app.services.model_router import ModelRouter
from app.services.url_safety import validate_external_url
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/devices", tags=["devices"])

DEVICE_SCOPES = frozenset(
    {
        "messages.read",
        "messages.draft",
        "messages.send",
        "remote.browser.open_url",
        "windows.apps.launch",
        "windows.screen.read",
        "windows.screen.control",
        "android.apps.launch",
        "android.screen.read",
        "android.screen.control",
    }
)
PAIR_REQUEST_TTL = timedelta(minutes=10)
DEVICE_ONLINE_WINDOW = timedelta(seconds=30)
DEVICE_SCOPE_LABELS = {
    "messages.read": "Read message notifications",
    "messages.draft": "Prepare editable reply drafts",
    "messages.send": "Send replies after your approval",
    "remote.browser.open_url": (
        "Approve opening external web links in the target browser"
    ),
    "windows.apps.launch": "Launch only applications added to the Windows companion allowlist",
    "windows.screen.read": (
        "Read accessibility controls or OCR from an approved Windows app"
    ),
    "windows.screen.control": (
        "Click, scroll, or type into selected Windows app controls"
    ),
    "android.apps.launch": "Launch only applications selected in the Android companion",
    "android.screen.read": "Read accessibility controls from the foreground approved Android app",
    "android.screen.control": (
        "Click, scroll, or type into the foreground approved Android app"
    ),
}


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["android", "ios", "windows", "macos", "linux"]
    scopes: list[str] = Field(min_length=1, max_length=11)
    consent_confirmed: bool


class DevicePairRequestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["android", "ios", "windows", "macos", "linux"]
    scopes: list[str] = Field(min_length=1, max_length=11)
    consent_confirmed: bool


class DevicePairApproval(BaseModel):
    confirm: bool
    pairing_code: str = Field(min_length=20, max_length=100)


class DevicePairRejection(BaseModel):
    pairing_code: str = Field(min_length=20, max_length=100)


class DevicePairTargetLookup(BaseModel):
    pairing_code: str = Field(min_length=20, max_length=100)


class DeviceRemoteCommandCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    confirm: bool


class DeviceRemoteCommandDecision(BaseModel):
    approve: bool


class DeviceRemoteCommandOpened(BaseModel):
    opened: bool


class DeviceProtocolDecision(BaseModel):
    nonce: str = Field(min_length=32, max_length=64)
    approve: bool


class DeviceProtocolReceipt(BaseModel):
    nonce: str = Field(min_length=32, max_length=64)
    status: Literal["succeeded", "failed", "uncertain"]
    evidence: dict[str, object] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=64)


class DeviceManifestApplication(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[
        Literal[
            "application.launch",
            "application.close",
            "screen.accessibility.inspect",
            "screen.control",
        ]
    ] = Field(max_length=4)


class DeviceCapabilityManifest(BaseModel):
    schema_version: Literal[1]
    applications: list[DeviceManifestApplication] = Field(max_length=100)

    def normalized(self) -> dict:
        rows = [app.model_dump() for app in self.applications]
        ids = [row["id"] for row in rows]
        if len(set(ids)) != len(ids):
            raise BadRequestError(
                "The device companion manifest contains duplicate app IDs.",
                "INVALID_DEVICE_MANIFEST",
            )
        return {"schema_version": 1, "applications": rows}


class DeviceMessageIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    source_app: str = Field(min_length=1, max_length=120)
    sender: str = Field(default="", max_length=255)
    content: str = Field(min_length=1, max_length=10000)
    received_at: datetime | None = None


class DeviceDraftPatch(BaseModel):
    draft_reply: str = Field(max_length=4000)


class DeviceAssistRequest(BaseModel):
    mode: Literal["summary", "draft"]


class DeviceReplyApproval(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    confirm_send: bool


class LocalAppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    executable_path: str = Field(min_length=1, max_length=1024)
    consent_confirmed: bool


class LocalAppAction(BaseModel):
    confirm: bool


class LocalAppInteraction(BaseModel):
    action: Literal["click", "type", "key"]
    confirm: bool
    x: int | None = Field(default=None, ge=0, le=10000)
    y: int | None = Field(default=None, ge=0, le=10000)
    text: str | None = Field(default=None, max_length=2000)
    key: Literal[
        "Enter", "Tab", "Esc", "Backspace", "Space", "Up", "Down", "Left", "Right"
    ] | None = None


class LocalAppInspection(BaseModel):
    mode: Literal["accessibility", "screenshot_ocr"] = "accessibility"
    confirm: bool


class LocalAppAccessibleClick(BaseModel):
    name: str = Field(default="", max_length=300)
    automation_id: str | None = Field(default=None, max_length=200)
    confirm: bool


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _device_out(device: DeviceConnection) -> dict:
    last_seen_at = device.last_seen_at
    connectivity = (
        "online"
        if device.status == "active"
        and last_seen_at is not None
        and not _timestamp_is_expired(last_seen_at + DEVICE_ONLINE_WINDOW)
        else "offline"
    )
    return {
        "id": device.id,
        "name": device.name,
        "platform": device.platform,
        "status": device.status,
        "connectivity": connectivity,
        "scopes": list(device.scopes or []),
        "last_seen_at": last_seen_at,
        "created_at": device.created_at,
    }


def _message_out(message: DeviceMessage) -> dict:
    return {
        "id": message.id,
        "device_id": message.device_id,
        "source_app": message.source_app,
        "sender": message.sender,
        "content": message.content,
        "draft_reply": message.draft_reply,
        "action_status": message.action_status,
        "received_at": message.received_at,
        "delivered_at": message.delivered_at,
    }


def _pair_request_out(pair: DevicePairingRequest) -> dict:
    return {
        "id": pair.id,
        "name": pair.name,
        "platform": pair.platform,
        "scopes": list(pair.scopes or []),
        "status": pair.status,
        "expires_at": pair.expires_at,
        "approved_device_id": pair.approved_device_id,
    }


def _remote_command_out(command: DeviceRemoteCommand) -> dict:
    return {
        "id": command.id,
        "device_id": command.device_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "task_step_id": command.task_step_id,
        "action": command.action,
        "url": command.target_url,
        "arguments": dict(command.arguments or {}),
        "status": command.status,
        "protocol_version": command.protocol_version,
        "protocol_status": command.protocol_status,
        "delivery_count": command.delivery_count,
        "receipt": command.receipt,
        "error_code": command.error_code,
        "created_at": command.created_at,
        "expires_at": command.expires_at,
        "delivered_at": command.delivered_at,
        "lease_expires_at": command.lease_expires_at,
        "decided_at": command.decided_at,
        "opened_at": command.opened_at,
        "completed_at": command.completed_at,
    }


async def _execute_device_tool(
    *,
    user: User,
    model_router: ModelRouter,
    tool_name: str,
    arguments: dict,
    description: str,
    require_approval: bool = True,
    response: Response | None = None,
):
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name=tool_name,
        arguments=arguments,
        request_message=description,
        description=description,
        success_message=description,
        require_approval=require_approval,
    )
    if outcome.status == "error" and outcome.error:
        raise JaloraError(
            502,
            outcome.error.get("code", "DEVICE_ACTION_FAILED"),
            outcome.error.get("details", "The device action could not be prepared."),
        )
    if outcome.status == "paused_approval" and response is not None:
        response.status_code = 202
    return outcome.as_response()


def _pair_request_is_expired(pair: DevicePairingRequest) -> bool:
    return _timestamp_is_expired(pair.expires_at)


def _timestamp_is_expired(expiry: datetime) -> bool:
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= utcnow()


def _pair_request_for_user(
    request_id: str, user: User, db: Session
) -> DevicePairingRequest:
    pair = db.scalar(
        select(DevicePairingRequest).where(
            DevicePairingRequest.id == request_id,
            DevicePairingRequest.user_id == user.id,
        )
    )
    if pair is None:
        raise NotFoundError("Device pairing request not found.")
    if pair.status == "pending" and _pair_request_is_expired(pair):
        pair.status = "expired"
        db.commit()
        db.refresh(pair)
    return pair


def _verify_pairing_code(pair: DevicePairingRequest, code: str) -> None:
    if not hmac.compare_digest(pair.pairing_code_hash, _token_digest(code)):
        raise ForbiddenError(
            "The device pairing link is invalid.", "DEVICE_PAIR_CODE_INVALID"
        )


def _validate_device_scopes(
    scopes: list[str],
    platform: str,
) -> None:
    invalid_scopes = set(scopes) - DEVICE_SCOPES
    if invalid_scopes:
        raise BadRequestError(
            f"Unsupported device permissions: {', '.join(sorted(invalid_scopes))}.",
            "INVALID_DEVICE_SCOPE",
        )
    if platform != "windows" and any(scope.startswith("windows.") for scope in scopes):
        raise BadRequestError(
            "Windows application permissions can be granted only to a Windows device.",
            "DEVICE_PLATFORM_SCOPE_MISMATCH",
        )
    if platform != "android" and any(scope.startswith("android.") for scope in scopes):
        raise BadRequestError(
            "Android application permissions can be granted only to an Android device.",
            "DEVICE_PLATFORM_SCOPE_MISMATCH",
        )
    if "messages.send" in scopes and "messages.read" not in scopes:
        raise BadRequestError(
            "Message sending requires the message-reading permission.",
            "DEVICE_SCOPE_DEPENDENCY",
        )
    if "windows.screen.read" in scopes and "windows.apps.launch" not in scopes:
        raise BadRequestError(
            "Windows screen reading is limited to apps Jalora is allowed to launch.",
            "DEVICE_SCOPE_DEPENDENCY",
        )
    if "windows.screen.control" in scopes and not {
        "windows.apps.launch", "windows.screen.read"
    }.issubset(scopes):
        raise BadRequestError(
            "Windows screen control requires app-launch and screen-read permissions.",
            "DEVICE_SCOPE_DEPENDENCY",
        )
    if "android.screen.read" in scopes and "android.apps.launch" not in scopes:
        raise BadRequestError(
            "Android screen reading is limited to apps selected in the companion.",
            "DEVICE_SCOPE_DEPENDENCY",
        )
    if "android.screen.control" in scopes and not {
        "android.apps.launch", "android.screen.read"
    }.issubset(scopes):
        raise BadRequestError(
            "Android screen control requires app-launch and screen-read permissions.",
            "DEVICE_SCOPE_DEPENDENCY",
        )


def _require_local_windows(request: Request) -> None:
    if sys.platform != "win32":
        raise ServiceUnavailableError(
            "Local app control currently supports Windows only.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    if not _local_control_available(request):
        raise ForbiddenError(
            "Local app control requests must come from this computer.",
            "LOCAL_APP_CONTROL_LOCALHOST_ONLY",
        )


def _local_control_available(request: Request) -> bool:
    if sys.platform != "win32":
        return False
    if get_settings().env.lower() == "test":
        return True
    client_host = request.client.host if request.client else ""
    return _is_loopback_host(client_host) and _is_loopback_origin(request)


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    hostname = urlsplit(origin).hostname
    return bool(hostname and _is_loopback_host(hostname))


def _local_app_out(app: LocalDeviceApp) -> dict:
    return {
        "id": app.id,
        "name": app.name,
        "executable_path": app.executable_path,
        "enabled": app.enabled,
        "running": local_device_control.is_running(app.user_id, app.id),
    }


def _require_scope(device: DeviceConnection, scope: str) -> None:
    if scope not in (device.scopes or []):
        raise ForbiddenError(
            f"This device has not been granted the '{scope}' permission.",
            "DEVICE_SCOPE_REQUIRED",
        )


def _authenticate_device(
    authorization: str | None, db: Session
) -> DeviceConnection:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing device bearer token.", "NO_DEVICE_TOKEN")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthError("Missing device bearer token.", "NO_DEVICE_TOKEN")
    device = db.scalar(
        select(DeviceConnection).where(
            DeviceConnection.token_hash == _token_digest(token),
            DeviceConnection.status == "active",
        )
    )
    if device is None:
        raise AuthError("Device token is invalid or revoked.", "DEVICE_TOKEN_INVALID")
    device.last_seen_at = utcnow()
    db.commit()
    return device


def _authenticate_protocol_device(
    authorization: str | None,
    db: Session,
) -> DeviceConnection:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing device access token.", "NO_DEVICE_TOKEN")
    token = authorization.removeprefix("Bearer ").strip()
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience="jalora-device-protocol",
            issuer="jalora-device-protocol",
            options={"require": ["exp", "iat", "jti", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(
            "The companion access token expired; exchange its paired-device credential again.",
            "DEVICE_ACCESS_TOKEN_EXPIRED",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(
            "The companion access token is invalid.",
            "DEVICE_ACCESS_TOKEN_INVALID",
        ) from exc
    if payload.get("typ") != "device_access":
        raise AuthError(
            "The companion access token is invalid.",
            "DEVICE_ACCESS_TOKEN_INVALID",
        )
    device = db.scalar(
        select(DeviceConnection).where(
            DeviceConnection.id == str(payload["sub"]),
            DeviceConnection.status == "active",
        )
    )
    if device is None:
        raise AuthError("Device token is invalid or revoked.", "DEVICE_TOKEN_INVALID")
    device.last_seen_at = utcnow()
    db.commit()
    return device


@router.get("/scopes", dependencies=[Depends(get_current_user)])
def list_device_scopes():
    return {
        "scopes": [
            {"id": scope, "label": label}
            for scope, label in DEVICE_SCOPE_LABELS.items()
        ],
        "note": (
            "The browser companion supports approved public links. Paired Windows "
                "and Android companions can separately operate user-selected apps "
                "through their respective native accessibility interfaces."
        ),
    }


@router.get("/capabilities")
def get_device_capabilities(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.device_capabilities import discover_device_capabilities

    return discover_device_capabilities(
        db,
        user.id,
        local_windows_available=_local_control_available(request),
    )


@router.get("/local/status", dependencies=[Depends(get_current_user)])
def local_control_status(
    request: Request,
):
    supported = _local_control_available(request)
    return {
        "available": supported,
        "platform": sys.platform,
        "message": (
            "App launching is available only when Jalora's backend runs on this Windows PC "
            "and the request comes from this PC."
            if not supported
            else "Only explicitly registered apps can be launched or receive confirmed input. "
            "Input and inspection are limited to the selected foreground app Jalora launched."
        ),
    }


@router.get("")
def list_devices(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    devices = db.scalars(
        select(DeviceConnection)
        .where(DeviceConnection.user_id == user.id)
        .order_by(DeviceConnection.created_at.desc())
    ).all()
    return [_device_out(device) for device in devices]


@router.post("")
def create_device(
    body: DeviceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.consent_confirmed:
        raise BadRequestError(
            "Confirm that you authorize the selected device permissions.",
            "DEVICE_CONSENT_REQUIRED",
        )
    scopes = list(dict.fromkeys(body.scopes))
    _validate_device_scopes(scopes, body.platform)
    if "remote.browser.open_url" in scopes or any(
        scope.startswith(("windows.", "android.")) for scope in scopes
    ):
        raise BadRequestError(
            "Native companion permissions must be approved on the target device.",
            "TARGET_DEVICE_APPROVAL_REQUIRED",
        )
    token = secrets.token_urlsafe(32)
    device = DeviceConnection(
        user_id=user.id,
        name=body.name.strip(),
        platform=body.platform,
        status="active",
        token_hash=_token_digest(token),
        scopes=scopes,
    )
    if not device.name:
        raise BadRequestError("Device name cannot be blank.", "INVALID_DEVICE_NAME")
    db.add(device)
    db.commit()
    db.refresh(device)
    return {**_device_out(device), "device_token": token}


@router.post("/pair-requests")
def create_device_pair_request(
    body: DevicePairRequestCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.consent_confirmed:
        raise BadRequestError(
            "Confirm that you authorize the selected device permissions.",
            "DEVICE_CONSENT_REQUIRED",
        )
    name = body.name.strip()
    if not name:
        raise BadRequestError("Device name cannot be blank.", "INVALID_DEVICE_NAME")
    scopes = list(dict.fromkeys(body.scopes))
    _validate_device_scopes(scopes, body.platform)
    code = secrets.token_urlsafe(32)
    pair = DevicePairingRequest(
        user_id=user.id,
        pairing_code_hash=_token_digest(code),
        name=name,
        platform=body.platform,
        scopes=scopes,
        status="pending",
        expires_at=utcnow() + PAIR_REQUEST_TTL,
    )
    db.add(pair)
    db.commit()
    db.refresh(pair)
    base_url = get_settings().spa_base_url
    pairing_fragment = urlencode({
        "pair_request_id": pair.id,
        "pair_code": code,
    })
    pairing_url = f"{base_url}/device-companion#{pairing_fragment}"
    return {
        **_pair_request_out(pair),
        "pairing_url": pairing_url,
    }


@router.get("/pair-requests/{request_id}")
def get_device_pair_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pair = _pair_request_for_user(request_id, user, db)
    return _pair_request_out(pair)


@router.post("/pair-requests/{request_id}/target")
def get_target_device_pair_request(
    request_id: str,
    body: DevicePairTargetLookup,
    db: Session = Depends(get_db),
):
    pair = db.get(DevicePairingRequest, request_id)
    if pair is None:
        raise NotFoundError("Device pairing request not found.")
    _verify_pairing_code(pair, body.pairing_code)
    if pair.status == "pending" and _pair_request_is_expired(pair):
        pair.status = "expired"
        db.commit()
        db.refresh(pair)
    requester = db.get(User, pair.user_id)
    return {
        **_pair_request_out(pair),
        "requested_by": requester.email if requester else "unknown",
    }


@router.post("/pair-requests/{request_id}/approve")
def approve_device_pair_request(
    request_id: str,
    body: DevicePairApproval,
    db: Session = Depends(get_db),
):
    if not body.confirm:
        raise BadRequestError(
            "Confirm pairing on the target device before continuing.",
            "DEVICE_PAIR_CONFIRMATION_REQUIRED",
        )
    pair = db.get(DevicePairingRequest, request_id)
    if pair is None:
        raise NotFoundError("Device pairing request not found.")
    _verify_pairing_code(pair, body.pairing_code)
    if pair.status == "pending" and _pair_request_is_expired(pair):
        pair.status = "expired"
        db.commit()
        raise ConflictError("This device pairing request has expired.")
    if pair.status != "pending":
        raise ConflictError(
            f"This device pairing request is {pair.status}.",
            "DEVICE_PAIR_REQUEST_NOT_PENDING",
        )

    token = secrets.token_urlsafe(32)
    device = DeviceConnection(
        user_id=pair.user_id,
        name=pair.name,
        platform=pair.platform,
        status="active",
        token_hash=_token_digest(token),
        scopes=list(pair.scopes or []),
    )
    db.add(device)
    db.flush()
    claimed = db.execute(
        update(DevicePairingRequest)
        .where(
            DevicePairingRequest.id == pair.id,
            DevicePairingRequest.status == "pending",
            DevicePairingRequest.pairing_code_hash == _token_digest(body.pairing_code),
        )
        .values(status="approved", approved_device_id=device.id)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise ConflictError(
            "This device pairing request is no longer pending.",
            "DEVICE_PAIR_REQUEST_NOT_PENDING",
        )
    db.commit()
    db.refresh(device)
    db.refresh(pair)
    return {
        "device": _device_out(device),
        "device_token": token,
        "pair_request": _pair_request_out(pair),
    }


@router.post("/pair-requests/{request_id}/reject")
def reject_device_pair_request(
    request_id: str,
    body: DevicePairRejection,
    db: Session = Depends(get_db),
):
    pair = db.get(DevicePairingRequest, request_id)
    if pair is None:
        raise NotFoundError("Device pairing request not found.")
    _verify_pairing_code(pair, body.pairing_code)
    if pair.status == "pending" and _pair_request_is_expired(pair):
        pair.status = "expired"
        db.commit()
        raise ConflictError("This device pairing request has expired.")
    if pair.status != "pending":
        raise ConflictError(
            f"This device pairing request is {pair.status}.",
            "DEVICE_PAIR_REQUEST_NOT_PENDING",
        )
    rejected = db.execute(
        update(DevicePairingRequest)
        .where(
            DevicePairingRequest.id == pair.id,
            DevicePairingRequest.status == "pending",
            DevicePairingRequest.pairing_code_hash == _token_digest(body.pairing_code),
        )
        .values(status="rejected")
    )
    if rejected.rowcount != 1:
        db.rollback()
        raise ConflictError(
            "This device pairing request is no longer pending.",
            "DEVICE_PAIR_REQUEST_NOT_PENDING",
        )
    db.commit()
    db.refresh(pair)
    return _pair_request_out(pair)


@router.post("/{device_id}/remote-commands", status_code=202)
async def create_remote_url_command(
    device_id: str,
    body: DeviceRemoteCommandCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    if not body.confirm:
        raise BadRequestError(
            "Confirm before sending a command to another device.",
            "REMOTE_DEVICE_CONFIRMATION_REQUIRED",
        )
    device = db.get(DeviceConnection, device_id)
    if device is None or device.user_id != user.id:
        raise NotFoundError("Paired device not found.")
    if device.status != "active":
        raise ConflictError("The paired device is not active.")
    if "remote.browser.open_url" not in (device.scopes or []):
        raise ForbiddenError(
            "This device has not approved remote browser links.",
            "DEVICE_SCOPE_REQUIRED",
        )
    target_url = body.url.strip()
    if validate_external_url(target_url) is not None:
        raise BadRequestError(
            "Only public external HTTP or HTTPS links can be opened.",
            "INVALID_REMOTE_URL",
        )
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="request_remote_url",
        arguments={
            "device_name": device.name,
            "device_id": device.id,
            "url": target_url,
        },
        description=f"Request opening a web link on paired device '{device.name}'.",
    )


@router.get("/remote-command-history")
def list_remote_command_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    commands = db.scalars(
        select(DeviceRemoteCommand)
        .where(DeviceRemoteCommand.user_id == user.id)
        .order_by(DeviceRemoteCommand.created_at.desc())
        .limit(50)
    ).all()
    return [_remote_command_out(command) for command in commands]


@router.post("/remote-command-history/{command_id}/cancel")
def cancel_remote_command(
    command_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    command = db.scalar(
        select(DeviceRemoteCommand).where(
            DeviceRemoteCommand.id == command_id,
            DeviceRemoteCommand.user_id == user.id,
        )
    )
    if command is None:
        raise NotFoundError("Remote command not found.")
    now = utcnow()
    if command.status == "pending" and command.protocol_status in {
        "queued",
        "delivered",
    }:
        cancelled = db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.id == command.id,
                DeviceRemoteCommand.user_id == user.id,
                DeviceRemoteCommand.status == "pending",
                DeviceRemoteCommand.protocol_status.in_(("queued", "delivered")),
            )
            .values(
                status="cancelled",
                protocol_status="cancelled",
                error_code="USER_CANCELLED",
                completed_at=now,
            )
        )
        if cancelled.rowcount != 1:
            raise ConflictError("The remote command changed while cancellation was requested.")
        db.commit()
        return {"status": "cancelled", "cancellation_confirmed": True}
    if command.status == "approved" and command.protocol_status == "running":
        marked_uncertain = db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.id == command.id,
                DeviceRemoteCommand.user_id == user.id,
                DeviceRemoteCommand.status == "approved",
                DeviceRemoteCommand.protocol_status == "running",
            )
            .values(
                protocol_status="uncertain",
                error_code="USER_CANCELLED_DURING_EXECUTION",
                completed_at=now,
            )
        )
        if marked_uncertain.rowcount != 1:
            raise ConflictError("The remote command changed while cancellation was requested.")
        db.commit()
        return {"status": "uncertain", "cancellation_confirmed": False}
    raise ConflictError(
        "This remote command is already terminal and cannot be cancelled.",
        "DEVICE_COMMAND_NOT_CANCELLABLE",
    )


@router.post("/protocol/v1/session")
def create_protocol_session(
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    device = _authenticate_device(authorization, db)
    access_token, expires_at = issue_device_access_token(device.id)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "expires_in": int(DEVICE_ACCESS_TTL.total_seconds()),
        "protocol_version": 1,
    }


@router.put("/protocol/v1/manifest")
def update_protocol_manifest(
    body: DeviceCapabilityManifest,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    device = _authenticate_protocol_device(authorization, db)
    if device.platform not in {"windows", "android"}:
        raise ForbiddenError(
            "Only a paired native companion may report this manifest.",
            "DEVICE_PLATFORM_UNSUPPORTED",
        )
    granted_scopes = set(device.scopes or [])
    platform_prefix = f"{device.platform}."
    native_scopes = {
        f"{platform_prefix}apps.launch",
        f"{platform_prefix}screen.read",
        f"{platform_prefix}screen.control",
    }
    manifest = body.normalized()
    notification_only = (
        device.platform == "android"
        and "messages.read" in granted_scopes
        and not manifest["applications"]
    )
    if not granted_scopes.intersection(native_scopes) and not notification_only:
        raise ForbiddenError(
            "The native companion has no application permissions.",
            "DEVICE_SCOPE_REQUIRED",
        )
    capability_scopes = {
        "application.launch": f"{platform_prefix}apps.launch",
        "application.close": f"{platform_prefix}apps.launch",
        "screen.accessibility.inspect": f"{platform_prefix}screen.read",
        "screen.control": f"{platform_prefix}screen.control",
    }
    for application in manifest["applications"]:
        if device.platform == "android" and "application.close" in application["capabilities"]:
            raise BadRequestError(
                "The Android companion does not support terminating applications.",
                "ANDROID_OPERATION_UNSUPPORTED",
            )
        if any(
            capability_scopes[capability] not in granted_scopes
            for capability in application["capabilities"]
        ):
            raise ForbiddenError(
                "The reported application capabilities exceed this device's permissions.",
                "DEVICE_SCOPE_REQUIRED",
            )
    device.capability_manifest = manifest
    device.last_seen_at = utcnow()
    db.commit()
    return {
        "accepted": True,
        "schema_version": manifest["schema_version"],
        "application_count": len(manifest["applications"]),
        "last_seen_at": device.last_seen_at,
    }


@router.get("/protocol/v1/commands")
def poll_protocol_commands(
    response: Response,
    wait_seconds: int = Query(default=20, ge=0, le=25),
    client_type: Literal["browser", "windows", "android"] = Query(default="browser"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    deadline = time.monotonic() + wait_seconds
    while True:
        device = _authenticate_protocol_device(authorization, db)
        if client_type == "browser":
            _require_scope(device, "remote.browser.open_url")
        elif client_type == "windows":
            if device.platform != "windows":
                raise ForbiddenError(
                    "Only a paired Windows device may poll Windows companion commands.",
                    "DEVICE_PLATFORM_UNSUPPORTED",
                )
            supported_scopes = {
                "remote.browser.open_url",
                "windows.apps.launch",
                "windows.screen.read",
                "windows.screen.control",
            }
            platform_name = "Windows"
        else:
            if device.platform != "android":
                raise ForbiddenError(
                    "Only a paired Android device may poll Android companion commands.",
                    "DEVICE_PLATFORM_UNSUPPORTED",
                )
            supported_scopes = {
                "remote.browser.open_url",
                "android.apps.launch",
                "android.screen.read",
                "android.screen.control",
                "messages.read",
            }
            platform_name = "Android"
        if client_type != "browser" and not set(device.scopes or []).intersection(
            supported_scopes
        ):
            raise ForbiddenError(
                f"This {platform_name} companion has no supported command permissions.",
                "DEVICE_SCOPE_REQUIRED",
            )
        commands = claim_device_commands(
            db,
            device,
            client_type=client_type,
        )
        if commands or time.monotonic() >= deadline:
            return {
                "protocol_version": 1,
                "commands": commands,
            }
        time.sleep(min(1, max(0, deadline - time.monotonic())))


@router.post("/protocol/v1/commands/{command_id}/decision")
def decide_protocol_command(
    command_id: str,
    body: DeviceProtocolDecision,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    device = _authenticate_protocol_device(authorization, db)
    command = db.scalar(
        select(DeviceRemoteCommand).where(
            DeviceRemoteCommand.id == command_id,
            DeviceRemoteCommand.device_id == device.id,
        )
    )
    if command is None:
        raise NotFoundError("Remote command not found for this device.")
    _require_scope(device, command.required_scope)
    if not verify_command_nonce(command, body.nonce):
        raise ForbiddenError("The command nonce is invalid.", "DEVICE_COMMAND_NONCE_INVALID")
    try:
        status, url, execute = approve_device_command(
            db,
            command=command,
            device=device,
            nonce=body.nonce,
            approve=body.approve,
        )
    except DeviceProtocolError as exc:
        raise ConflictError(str(exc), "DEVICE_COMMAND_PROTOCOL_CONFLICT") from exc
    return {
        "status": status,
        "execute": execute,
        "url": url,
    }


@router.post("/protocol/v1/commands/{command_id}/receipt")
async def record_protocol_command_receipt(
    command_id: str,
    body: DeviceProtocolReceipt,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    response.headers["Cache-Control"] = "no-store, private"
    device = _authenticate_protocol_device(authorization, db)
    command = db.scalar(
        select(DeviceRemoteCommand).where(
            DeviceRemoteCommand.id == command_id,
            DeviceRemoteCommand.device_id == device.id,
        )
    )
    if command is None:
        raise NotFoundError("Remote command not found for this device.")
    _require_scope(device, command.required_scope)
    if not verify_command_nonce(command, body.nonce):
        raise ForbiddenError("The command nonce is invalid.", "DEVICE_COMMAND_NONCE_INVALID")
    try:
        status, recorded = record_device_receipt(
            db,
            command=command,
            device=device,
            nonce=body.nonce,
            status=body.status,
            evidence=body.evidence,
            error_code=body.error_code,
        )
    except DeviceProtocolError as exc:
        raise ConflictError(str(exc), "DEVICE_COMMAND_PROTOCOL_CONFLICT") from exc
    task_resume = await _update_task_from_device_receipt(
        db,
        command=command,
        status=status,
        model_router=model_router,
    )
    return {
        "status": status,
        "recorded": recorded,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "task_step_id": command.task_step_id,
        "task_resume": task_resume,
    }


async def _update_task_from_device_receipt(
    db: Session,
    *,
    command: DeviceRemoteCommand,
    status: str,
    model_router: ModelRouter,
) -> dict[str, str] | None:
    if (
        status != "succeeded"
        or not command.run_id
        or not command.task_id
        or not command.task_step_id
    ):
        return None

    from app.errors import ToolResult
    from langchain_core.messages import ToolMessage

    from app.models.agent import AgentRun
    from app.models.task import TaskStepAttempt
    from app.services.chat_service import (
        _finalize_task,
        deserialize_state,
        resume_run,
        serialize_state,
    )

    run = db.get(AgentRun, command.run_id)
    task_step = db.get(TaskStep, command.task_step_id)
    if (
        run is None
        or run.user_id != command.user_id
        or run.status != "partial"
        or not run.state_json
        or task_step is None
        or task_step.task_id != command.task_id
    ):
        return None

    state = deserialize_state(run.state_json)
    if task_step.plan_step_id:
        for step in state.get("plan") or []:
            if isinstance(step, dict) and step.get("id") == task_step.plan_step_id:
                step["status"] = "done"
                break
    attempt = db.scalar(
        select(TaskStepAttempt)
        .where(
            TaskStepAttempt.task_id == command.task_id,
            TaskStepAttempt.run_id == command.run_id,
            TaskStepAttempt.step_id == task_step.id,
        )
        .order_by(TaskStepAttempt.attempt_number.desc())
    )
    if attempt is not None and attempt.tool_call_id:
        receipt_result = {
            "command_id": command.id,
            "action": command.action,
            "status": "verified",
            "evidence": (
                command.receipt.get("evidence", {})
                if isinstance(command.receipt, dict)
                else {}
            ),
        }
        for message in state.get("messages", []):
            if (
                isinstance(message, ToolMessage)
                and message.tool_call_id == attempt.tool_call_id
            ):
                message.content = ToolResult.ok(
                    "Paired device completed and verified this action.",
                    receipt_result,
                ).model_dump_json()
                break
    run.state_json = serialize_state(state)
    db.commit()

    pending_step = db.scalar(
        select(TaskStep.id)
        .where(
            TaskStep.task_id == command.task_id,
            TaskStep.status.notin_(("done", "skipped")),
        )
        .limit(1)
    )
    unknown_outcome = db.scalar(
        select(TaskStep.id)
        .where(
            TaskStep.task_id == command.task_id,
            TaskStep.outcome_status == "outcome_unknown",
        )
        .limit(1)
    )
    if pending_step is None and unknown_outcome is None:
        reply = f"The paired device completed and verified '{command.action}'."
        run.status = "completed"
        run.error = None
        run.state_json = None
        run.updated_at = utcnow()
        _finalize_task(
            db,
            task_id=command.task_id,
            status="completed",
            reply=reply,
        )
        db.commit()
        return {
            "status": "completed",
            "reply": reply,
            "task_id": command.task_id,
            "run_id": run.id,
        }
    if unknown_outcome is not None:
        reply = "A device action still has an unknown outcome. Reconcile it before continuing."
    else:
        try:
            outcome = await resume_run(
                user_id=command.user_id,
                run_id=command.run_id,
                router=model_router,
            )
        except BadRequestError as exc:
            if exc.code != "RUN_NOT_RESUMABLE":
                raise
            db.refresh(run)
            if run.status not in {"running", "paused_approval", "completed"}:
                raise
            return {
                "status": run.status,
                "reply": run.error or "The task is already continuing.",
                "task_id": command.task_id,
                "run_id": command.run_id,
            }
        return {
            "status": outcome.status,
            "reply": outcome.reply,
            "task_id": command.task_id,
            "run_id": command.run_id,
        }
    return {
        "status": "partial",
        "reply": reply,
        "task_id": command.task_id,
        "run_id": run.id,
    }


@router.get("/remote-commands/target")
def list_target_remote_commands(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "remote.browser.open_url")
    expired_commands = db.scalars(
        select(DeviceRemoteCommand).where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.expires_at <= utcnow(),
        )
    ).all()
    for command in expired_commands:
        command.status = "expired"
        command.protocol_status = "expired"
        command.error_code = "DEVICE_COMMAND_EXPIRED"
        command.completed_at = utcnow()
    if expired_commands:
        db.commit()
    commands = db.scalars(
        select(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.expires_at > utcnow(),
            DeviceRemoteCommand.action == "browser.open_url",
        )
        .order_by(DeviceRemoteCommand.created_at)
        .limit(20)
    ).all()
    return [_remote_command_out(command) for command in commands]


@router.post("/remote-commands/{command_id}/decision")
def decide_remote_command(
    command_id: str,
    body: DeviceRemoteCommandDecision,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "remote.browser.open_url")
    command = db.get(DeviceRemoteCommand, command_id)
    if command is None or command.device_id != device.id:
        raise NotFoundError("Remote command not found for this device.")
    if command.action != "browser.open_url":
        raise ConflictError("This remote command is not supported by this companion.")
    if command.status != "pending":
        raise ConflictError("This remote command is no longer pending.")
    now = utcnow()
    if _timestamp_is_expired(command.expires_at):
        expired = db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.id == command.id,
                DeviceRemoteCommand.device_id == device.id,
                DeviceRemoteCommand.status == "pending",
                DeviceRemoteCommand.expires_at <= now,
            )
            .values(
                status="expired",
                protocol_status="expired",
                error_code="DEVICE_COMMAND_EXPIRED",
                decided_at=now,
                completed_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if expired.rowcount != 1:
            raise ConflictError("This remote command is no longer pending.")
        raise ConflictError("This remote command has expired.")
    claimed = db.execute(
        update(DeviceRemoteCommand)
        .where(
            DeviceRemoteCommand.id == command.id,
            DeviceRemoteCommand.device_id == device.id,
            DeviceRemoteCommand.action == "browser.open_url",
            DeviceRemoteCommand.status == "pending",
            DeviceRemoteCommand.expires_at > now,
        )
        .values(
            status="approved" if body.approve else "rejected",
            protocol_status="running" if body.approve else "cancelled",
            decided_at=now,
            running_at=now if body.approve else None,
            completed_at=None if body.approve else now,
            lease_expires_at=(
                now + timedelta(minutes=2) if body.approve else None
            ),
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise ConflictError("This remote command is no longer pending.")
    db.commit()
    if not body.approve:
        return {"status": "rejected"}
    return {"status": "approved", "url": command.target_url}


@router.post("/remote-commands/{command_id}/opened")
async def mark_remote_command_opened(
    command_id: str,
    body: DeviceRemoteCommandOpened,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "remote.browser.open_url")
    command = db.get(DeviceRemoteCommand, command_id)
    if command is None or command.device_id != device.id:
        raise NotFoundError("Remote command not found for this device.")
    if command.status != "approved":
        raise ConflictError("Approve this remote command before opening its link.")
    try:
        status, recorded = record_device_receipt(
            db,
            command=command,
            device=device,
            nonce=command.command_nonce or "",
            status="succeeded" if body.opened else "failed",
            evidence={"browser_open_receipt": body.opened},
            error_code=None if body.opened else "DEVICE_OPEN_FAILED",
        )
    except DeviceProtocolError as exc:
        raise ConflictError(
            str(exc), "DEVICE_COMMAND_PROTOCOL_CONFLICT"
        ) from exc
    task_resume = await _update_task_from_device_receipt(
        db,
        command=command,
        status=status,
        model_router=model_router,
    )
    return {
        "status": "opened" if status == "succeeded" else "failed",
        "task_resume": task_resume,
    }


@router.delete("/{device_id}")
def revoke_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = db.get(DeviceConnection, device_id)
    if device is None or device.user_id != user.id:
        raise NotFoundError("Device connection not found.")
    if device.status != "revoked":
        device.status = "revoked"
        device.revoked_at = utcnow()
        now = utcnow()
        db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.device_id == device.id,
                DeviceRemoteCommand.status == "pending",
                DeviceRemoteCommand.protocol_status.in_(("queued", "delivered")),
            )
            .values(
                status="cancelled",
                protocol_status="cancelled",
                error_code="DEVICE_REVOKED",
                completed_at=now,
            )
        )
        db.execute(
            update(DeviceRemoteCommand)
            .where(
                DeviceRemoteCommand.device_id == device.id,
                DeviceRemoteCommand.status == "approved",
                DeviceRemoteCommand.protocol_status == "running",
            )
            .values(
                protocol_status="uncertain",
                error_code="DEVICE_REVOKED_DURING_EXECUTION",
                completed_at=now,
            )
        )
        db.commit()
    return {"success": True, "status": device.status}


@router.post("/inbox")
def receive_device_message(
    body: DeviceMessageIn,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "messages.read")
    external_id = body.external_id.strip()
    source_app = body.source_app.strip()
    if not external_id or not source_app:
        raise BadRequestError(
            "Message ID and source app must not be blank.", "INVALID_DEVICE_MESSAGE"
        )
    existing = db.scalar(
        select(DeviceMessage).where(
            DeviceMessage.device_id == device.id,
            DeviceMessage.external_id == external_id,
        )
    )
    if existing is not None:
        return _message_out(existing)
    message = DeviceMessage(
        device_id=device.id,
        user_id=device.user_id,
        external_id=external_id,
        source_app=source_app,
        sender=body.sender.strip(),
        content=body.content,
        received_at=body.received_at or utcnow(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_out(message)


@router.post("/inbox/{message_id}/assist")
async def assist_with_device_message(
    message_id: str,
    body: DeviceAssistRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    message = db.get(DeviceMessage, message_id)
    if message is None or message.user_id != user.id:
        raise NotFoundError("Device message not found.")
    device = db.get(DeviceConnection, message.device_id)
    if device is None or device.status != "active":
        raise ForbiddenError("The source device has been revoked.", "DEVICE_REVOKED")
    _require_scope(device, "messages.read")
    if body.mode == "draft":
        _require_scope(device, "messages.draft")
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="assist_device_message",
        arguments={"message_id": message.id, "mode": body.mode},
        request_message=(
            f"{'Summarize' if body.mode == 'summary' else 'Draft a reply to'} "
            f"the paired-device message from {message.sender or 'an unknown sender'}."
        ),
        description=(
            f"{'Summarize' if body.mode == 'summary' else 'Draft an editable reply to'} "
            f"paired-device message {message.id}."
        ),
        success_message=(
            "Prepared a device-message response. It was not sent."
            if body.mode == "draft"
            else "Summarized the device message."
        ),
        require_approval=False,
    )
    if outcome.status != "completed":
        error = outcome.error if isinstance(outcome.error, dict) else {}
        raise ServiceUnavailableError(
            error.get("details", "Device-message assistance did not complete."),
            error.get("code", "DEVICE_ASSIST_FAILED"),
        )
    db.expire_all()
    step = db.scalar(
        select(TaskStep)
        .where(
            TaskStep.task_id == outcome.task_id,
            TaskStep.tool_id == "assist_device_message",
        )
        .order_by(TaskStep.idx.desc())
    )
    result_data = step.result_data if step and isinstance(step.result_data, dict) else {}
    execution_result = result_data.get("execution_result", {})
    output = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    if not isinstance(output, dict) or not str(output.get("reply") or "").strip():
        raise ServiceUnavailableError(
            "Device-message assistance did not produce a verified response.",
            "DEVICE_ASSIST_RESULT_MISSING",
        )
    return {
        "mode": body.mode,
        "reply": output["reply"],
        "model": output.get("model", ""),
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
    }


@router.get("/inbox")
def list_device_messages(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    messages = db.scalars(
        select(DeviceMessage)
        .where(DeviceMessage.user_id == user.id)
        .order_by(DeviceMessage.received_at.desc())
        .limit(200)
    ).all()
    return [_message_out(message) for message in messages]


@router.patch("/inbox/{message_id}")
async def update_device_message_draft(
    message_id: str,
    body: DeviceDraftPatch,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    message = db.get(DeviceMessage, message_id)
    if message is None or message.user_id != user.id:
        raise NotFoundError("Device message not found.")
    device = db.get(DeviceConnection, message.device_id)
    if device is None or device.status != "active":
        raise ForbiddenError("The source device has been revoked.", "DEVICE_REVOKED")
    if not {"messages.draft", "messages.send"}.intersection(device.scopes or []):
        raise ForbiddenError(
            "This device has not been granted reply-drafting permission.",
            "DEVICE_SCOPE_REQUIRED",
        )
    if message.action_status != "none":
        raise ConflictError("This message already has a queued or delivered reply.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="save_device_reply_draft",
        arguments={
            "message_id": message.id,
            "draft_reply": body.draft_reply,
        },
        description=f"Save the edited reply draft for device message {message.id}.",
        require_approval=False,
        response=response,
    )


@router.post("/inbox/{message_id}/approve", status_code=202)
async def approve_device_reply(
    message_id: str,
    body: DeviceReplyApproval,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    if not body.confirm_send:
        raise BadRequestError(
            "Explicit confirmation is required before queuing a reply.",
            "SEND_CONFIRMATION_REQUIRED",
        )
    message = db.get(DeviceMessage, message_id)
    if message is None or message.user_id != user.id:
        raise NotFoundError("Device message not found.")
    device = db.get(DeviceConnection, message.device_id)
    if device is None or device.status != "active":
        raise ForbiddenError("The source device has been revoked.", "DEVICE_REVOKED")
    _require_scope(device, "messages.send")
    if message.action_status != "none":
        raise ConflictError("A reply has already been queued or delivered.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="queue_device_reply",
        arguments={"message_id": message.id, "reply": body.reply},
        description=(
            f"Queue the reviewed device reply for message {message.id}. "
            "Delivery is not confirmed until the companion acknowledges it."
        ),
    )


@router.get("/actions")
def list_device_actions(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "messages.send")
    messages = db.scalars(
        select(DeviceMessage)
        .where(
            DeviceMessage.device_id == device.id,
            DeviceMessage.action_status == "queued",
        )
        .order_by(DeviceMessage.created_at.asc())
        .limit(100)
    ).all()
    return [
        {
            "id": message.id,
            "external_id": message.external_id,
            "source_app": message.source_app,
            "reply": message.queued_reply,
        }
        for message in messages
    ]


@router.post("/actions/{message_id}/ack")
def acknowledge_device_action(
    message_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _authenticate_device(authorization, db)
    _require_scope(device, "messages.send")
    message = db.get(DeviceMessage, message_id)
    if message is None or message.device_id != device.id:
        raise NotFoundError("Device action not found.")
    if message.action_status == "delivered":
        return {"success": True, "status": "delivered"}
    if message.action_status != "queued":
        raise ConflictError("There is no queued reply to acknowledge.")
    message.action_status = "delivered"
    message.delivered_at = utcnow()
    db.commit()
    return {"success": True, "status": message.action_status}


@router.get("/local/apps")
def list_local_apps(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_local_windows(request)
    apps = db.scalars(
        select(LocalDeviceApp)
        .where(LocalDeviceApp.user_id == user.id)
        .order_by(LocalDeviceApp.name)
    ).all()
    return [_local_app_out(app) for app in apps]


@router.post("/local/apps")
def register_local_app(
    body: LocalAppCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_local_windows(request)
    if not body.consent_confirmed:
        raise BadRequestError(
            "Confirm that Jalora may launch this selected app.",
            "LOCAL_APP_CONSENT_REQUIRED",
        )
    name = body.name.strip()
    if not name:
        raise BadRequestError("App name cannot be blank.", "INVALID_APP_NAME")
    executable_path = local_device_control.validate_executable_path(body.executable_path)
    existing = db.scalar(
        select(LocalDeviceApp).where(
            LocalDeviceApp.user_id == user.id,
            LocalDeviceApp.executable_path == executable_path,
        )
    )
    if existing is not None:
        existing.name = name
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return _local_app_out(existing)
    app = LocalDeviceApp(
        user_id=user.id,
        name=name,
        executable_path=executable_path,
        enabled=True,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return _local_app_out(app)


@router.post("/local/apps/{app_id}/launch", status_code=202)
async def launch_local_app(
    app_id: str,
    body: LocalAppAction,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _require_local_windows(request)
    if not body.confirm:
        raise BadRequestError(
            "Confirm before launching the selected app.", "APP_ACTION_CONFIRMATION_REQUIRED"
        )
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if not app.enabled:
        raise ForbiddenError("This app's Jalora permission is disabled.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="open_local_app",
        arguments={"app_name": app.name},
        description=f"Launch approved local app '{app.name}'.",
    )


@router.post("/local/apps/{app_id}/close", status_code=202)
async def close_local_app(
    app_id: str,
    body: LocalAppAction,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _require_local_windows(request)
    if not body.confirm:
        raise BadRequestError(
            "Confirm before requesting the app to close.",
            "APP_ACTION_CONFIRMATION_REQUIRED",
        )
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if not app.enabled:
        raise ForbiddenError("This app's Jalora permission is disabled.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="close_local_app",
        arguments={"app_name": app.name},
        description=f"Close approved local app '{app.name}'.",
    )


@router.post("/local/apps/{app_id}/interact", status_code=202)
async def interact_with_local_app(
    app_id: str,
    body: LocalAppInteraction,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _require_local_windows(request)
    if not body.confirm:
        raise BadRequestError(
            "Confirm before sending input to the selected app.",
            "APP_ACTION_CONFIRMATION_REQUIRED",
        )
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if not app.enabled:
        raise ForbiddenError("This app's Jalora permission is disabled.")

    if body.action == "click":
        if body.x is None or body.y is None or body.text is not None or body.key is not None:
            raise BadRequestError("Click requires only x and y coordinates.")
        action_args = {"x": body.x, "y": body.y}
    elif body.action == "type":
        if not body.text or body.x is not None or body.y is not None or body.key is not None:
            raise BadRequestError("Type requires non-empty text only.")
        action_args = {"text": body.text}
    else:
        if body.key is None or any(
            value is not None for value in (body.x, body.y, body.text)
        ):
            raise BadRequestError("Key action requires only a supported key.")
        action_args = {"key": body.key}
    tool_name = {
        "click": "click_local_app_coordinates",
        "type": "type_local_app_text",
        "key": "press_local_app_key",
    }[body.action]
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name=tool_name,
        arguments={"app_name": app.name, **action_args},
        description=f"Perform the approved {body.action} action in '{app.name}'.",
    )


@router.post("/local/apps/{app_id}/inspect", status_code=202)
async def inspect_local_app(
    app_id: str,
    body: LocalAppInspection,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _require_local_windows(request)
    if not body.confirm:
        raise BadRequestError(
            "Confirm before reading the selected app's visible content.",
            "APP_ACTION_CONFIRMATION_REQUIRED",
        )
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if not app.enabled:
        raise ForbiddenError("This app's Jalora permission is disabled.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="inspect_local_app",
        arguments={"app_name": app.name, "mode": body.mode},
        description=f"Inspect approved local app '{app.name}'.",
    )


@router.post("/local/apps/{app_id}/click-control", status_code=202)
async def click_local_accessible_control(
    app_id: str,
    body: LocalAppAccessibleClick,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _require_local_windows(request)
    if not body.confirm:
        raise BadRequestError(
            "Confirm before clicking an accessible control.",
            "APP_ACTION_CONFIRMATION_REQUIRED",
        )
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if not app.enabled:
        raise ForbiddenError("This app's Jalora permission is disabled.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="click_local_app_control",
        arguments={
            "app_name": app.name,
            "name": body.name,
            "automation_id": body.automation_id,
        },
        description=f"Click a reviewed control in approved local app '{app.name}'.",
    )


@router.delete("/local/apps/{app_id}")
def remove_local_app(
    app_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_local_windows(request)
    app = db.get(LocalDeviceApp, app_id)
    if app is None or app.user_id != user.id:
        raise NotFoundError("Approved local app not found.")
    if local_device_control.is_running(user.id, app.id):
        raise ConflictError("Close the app Jalora launched before removing its approval.")
    db.delete(app)
    db.commit()
    return {"success": True}


@router.delete("/inbox/{message_id}")
async def delete_device_message(
    message_id: str,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    message = db.get(DeviceMessage, message_id)
    if message is None or message.user_id != user.id:
        raise NotFoundError("Device message not found.")
    return await _execute_device_tool(
        user=user,
        model_router=model_router,
        tool_name="delete_device_message",
        arguments={"message_id": message.id},
        description=f"Delete unsent paired-device message {message.id}.",
        response=response,
    )
