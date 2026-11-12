"""User-scoped inventory of device and application operations Jalora can use."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import DeviceConnection, LocalDeviceApp
from app.services.integration_registry import provider_registry
from app.tools.base import registry

DEVICE_ONLINE_WINDOW = timedelta(seconds=30)

_REMOTE_OPERATIONS = (
    {
        "operation": "browser.open_url",
        "label": "Open an approved public link",
        "scope": "remote.browser.open_url",
        "approval_required": True,
    },
    {
        "operation": "messages.receive",
        "label": "Receive supported message notifications",
        "scope": "messages.read",
        "approval_required": False,
    },
    {
        "operation": "messages.draft",
        "label": "Prepare an editable message reply",
        "scope": "messages.draft",
        "approval_required": False,
    },
    {
        "operation": "messages.send",
        "label": "Deliver an approved message reply",
        "scope": "messages.send",
        "approval_required": True,
    },
)

_NATIVE_OPERATIONS = (
    ("application.launch", "Launch an approved application"),
    ("screen.accessibility.inspect", "Inspect accessible application controls"),
    ("screen.capture_ocr", "Capture a screenshot for OCR"),
    ("screen.control", "Click, type, or use supported keyboard controls"),
)


def _online(device: DeviceConnection, now: datetime) -> bool:
    if device.status != "active" or device.last_seen_at is None:
        return False
    last_seen = device.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return last_seen >= now - DEVICE_ONLINE_WINDOW


def discover_device_capabilities(
    db: Session,
    user_id: str,
    *,
    local_windows_available: bool,
) -> dict[str, Any]:
    """Describe registered, connected, and unsupported execution surfaces.

    A registered device scope is not treated as proof that an app or action
    succeeded. The companion must be online, and external actions retain their
    existing individual approval gates.
    """
    now = datetime.now(timezone.utc)
    devices = db.scalars(
        select(DeviceConnection)
        .where(DeviceConnection.user_id == user_id)
        .order_by(DeviceConnection.created_at.desc())
    ).all()
    local_apps = db.scalars(
        select(LocalDeviceApp)
        .where(
            LocalDeviceApp.user_id == user_id,
            LocalDeviceApp.enabled.is_(True),
        )
        .order_by(LocalDeviceApp.name)
    ).all()

    paired_devices: list[dict[str, Any]] = []
    for device in devices:
        online = _online(device, now)
        granted_scopes = set(device.scopes or [])
        operations: list[dict[str, Any]] = []
        for operation in _REMOTE_OPERATIONS:
            scope_granted = operation["scope"] in granted_scopes
            if device.status != "active":
                status = "revoked"
            elif not scope_granted:
                status = "permission_required"
            elif not online:
                status = "offline"
            elif operation["operation"] == "browser.open_url":
                status = "available"
            else:
                status = "registered_unavailable"
            operations.append({
                **operation,
                "status": status,
                "reason": (
                    "The paired device is connected and has this permission."
                    if status == "available"
                    else "The server endpoint is registered, but the shipped browser "
                    "companion cannot read notifications or deliver message replies."
                    if status == "registered_unavailable"
                    else "The paired device companion has not checked in recently."
                    if status == "offline"
                    else "This device has not granted the required permission."
                    if status == "permission_required"
                    else "This device connection has been revoked."
                ),
            })
        paired_devices.append({
            "id": device.id,
            "name": device.name,
            "platform": device.platform,
            "status": "online" if online else (
                "offline" if device.status == "active" else "revoked"
            ),
            "last_seen_at": device.last_seen_at,
            "companion_capabilities": operations,
            "unsupported_operations": [
                {
                    "operation": operation,
                    "label": label,
                    "status": "unsupported",
                    "reason": (
                        "The currently shipped paired-device companion does not "
                        "provide native application or screen-control operations."
                    ),
                }
                for operation, label in _NATIVE_OPERATIONS
            ],
        })

    platform = {
        "win32": "windows",
        "darwin": "macos",
        "linux": "linux",
    }.get(sys.platform, sys.platform)
    host_apps: list[dict[str, Any]] = []
    if local_windows_available:
        from app.services import local_device_control

        host_apps = [
            {
                "id": app.id,
                "name": app.name,
                "running": local_device_control.is_running(user_id, app.id),
                "capabilities": [
                    "application.launch",
                    "screen.accessibility.inspect",
                    "screen.capture_ocr",
                    "screen.control",
                ],
            }
            for app in local_apps
        ]
    local_host = {
        "name": "Jalora backend host",
        "platform": platform,
        "status": "available" if local_windows_available else "unavailable",
        "applications": host_apps,
        "reason": (
            "Windows local controls are limited to individually registered apps "
            "and require approval for each launch, inspection, or interaction."
            if local_windows_available
            else "Windows UI Automation runs only when Jalora's backend is on the "
            "Windows device; a cloud backend cannot control this user's desktop."
        ),
    }

    connected_integrations = [
        {
            "provider": integration["name"],
            "area": integration["area"],
            "access": integration["description"],
        }
        for integration in provider_registry(db, user_id)
        if integration.get("connected") is True
    ]
    browser_tools = sorted(
        spec.name
        for spec in registry.all()
        if spec.category in {"browser", "research"}
    )
    return {
        "schema_version": 1,
        "devices": paired_devices,
        "local_host": local_host,
        "cloud_integrations": connected_integrations,
        "cloud_browser": {
            "status": "registered" if browser_tools else "unavailable",
            "operations": browser_tools,
            "note": (
                "Cloud browser and web research run on Jalora's server, not in "
                "the user's local browser session."
            ),
        },
        "limitations": [
            "Installed applications are not enumerated on paired devices.",
            "The current paired browser companion cannot inspect screens or operate native apps.",
            "Connected API integrations are separate from physical-device application access.",
        ],
    }
