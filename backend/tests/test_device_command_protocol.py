from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import parse_qs, urlsplit

import jwt

from app.config import get_settings
from app.context import ToolContext, bind_tool_execution
from app.database.session import SessionLocal
from app.models.agent import AgentRun
from app.models.device import DeviceRemoteCommand
from app.models.task import Task, TaskStep
from app.models.user import User, utcnow
from app.tools.device_tools import request_remote_url


def _approved_device(client, owner_headers):
    pair_response = client.post(
        "/api/devices/pair-requests",
        headers=owner_headers,
        json={
            "name": "Protocol test device",
            "platform": "android",
            "scopes": ["remote.browser.open_url"],
            "consent_confirmed": True,
        },
    )
    assert pair_response.status_code == 200, pair_response.text
    pair = pair_response.json()
    fragment = parse_qs(urlsplit(pair["pairing_url"]).fragment)
    approved = client.post(
        f"/api/devices/pair-requests/{fragment['pair_request_id'][0]}/approve",
        json={"confirm": True, "pairing_code": fragment["pair_code"][0]},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def test_protocol_v1_is_task_linked_idempotent_and_uncertain_safe(
    client, auth_headers
):
    owner_headers = auth_headers("protocol-v1@test.com")
    paired = _approved_device(client, owner_headers)
    device = paired["device"]
    bootstrap_headers = {"Authorization": f"Bearer {paired['device_token']}"}
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers=bootstrap_headers,
    )
    assert session.status_code == 200, session.text
    assert session.json()["expires_in"] == 300
    assert session.headers["cache-control"] == "no-store, private"
    protocol_headers = {
        "Authorization": f"Bearer {session.json()['access_token']}"
    }

    with SessionLocal() as db:
        user = db.query(User).filter_by(email="protocol-v1@test.com").one()
        task = Task(user_id=user.id, goal="Open a public project page")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user.id, task_id=task.id)
        db.add(run)
        db.flush()
        task.run_id = run.id
        step = TaskStep(
            task_id=task.id,
            idx=0,
            description="Request opening the project page",
            idempotency_key="a" * 64,
        )
        db.add(step)
        db.commit()
        context = ToolContext(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            step_id=step.id,
            idempotency_key=step.idempotency_key,
        )

    with bind_tool_execution(context):
        queued = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/project",
                device["id"],
            )
        )
        duplicate = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/project",
                device["id"],
            )
        )
        conflicting = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/other",
                device["id"],
            )
        )

    assert queued.success
    assert duplicate.success
    assert duplicate.data["already_queued"] is True
    assert duplicate.data["command_id"] == queued.data["command_id"]
    assert not conflicting.success
    assert conflicting.error.code == "IDEMPOTENCY_CONFLICT"

    unauthorized = client.get(
        "/api/devices/protocol/v1/commands",
        headers=owner_headers,
    )
    assert unauthorized.status_code == 401
    expired_at = datetime.now(timezone.utc)
    settings = get_settings()
    expired_token = jwt.encode(
        {
            "sub": device["id"],
            "typ": "device_access",
            "iss": "jalora-device-protocol",
            "aud": "jalora-device-protocol",
            "iat": expired_at - timedelta(minutes=2),
            "exp": expired_at - timedelta(minutes=1),
            "jti": "expired-device-token",
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    expired_access = client.get(
        "/api/devices/protocol/v1/commands",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired_access.status_code == 401
    assert expired_access.json()["error"]["code"] == "DEVICE_ACCESS_TOKEN_EXPIRED"

    read_only_device = client.post(
        "/api/devices",
        headers=owner_headers,
        json={
            "name": "Read-only companion",
            "platform": "android",
            "scopes": ["messages.read"],
            "consent_confirmed": True,
        },
    )
    assert read_only_device.status_code == 200, read_only_device.text
    read_only_session = client.post(
        "/api/devices/protocol/v1/session",
        headers={
            "Authorization": f"Bearer {read_only_device.json()['device_token']}"
        },
    )
    assert read_only_session.status_code == 200
    scope_denied = client.get(
        "/api/devices/protocol/v1/commands",
        headers={
            "Authorization": f"Bearer {read_only_session.json()['access_token']}"
        },
    )
    assert scope_denied.status_code == 403

    poll = client.get(
        "/api/devices/protocol/v1/commands",
        headers=protocol_headers,
    )
    assert poll.status_code == 200, poll.text
    assert poll.headers["cache-control"] == "no-store, private"
    envelope = poll.json()["commands"][0]
    assert poll.json()["protocol_version"] == 1
    assert envelope["protocol_version"] == 1
    assert envelope["command_id"] == queued.data["command_id"]
    assert envelope["device_id"] == device["id"]
    assert envelope["task_id"] == context.task_id
    assert envelope["run_id"] == context.run_id
    assert envelope["task_step_id"] == context.step_id
    assert envelope["operation"] == "browser.open_url"
    assert envelope["arguments"] == {"url": "https://example.com/project"}
    assert envelope["required_scope"] == "remote.browser.open_url"
    assert envelope["idempotency_key"] == "a" * 64
    assert envelope["nonce"]
    assert envelope["delivery_count"] == 1

    second_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0",
        headers=protocol_headers,
    )
    assert second_poll.status_code == 200
    assert second_poll.json()["commands"] == []
    with SessionLocal() as db:
        command = db.get(DeviceRemoteCommand, envelope["command_id"])
        assert command is not None
        command.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    reconnect_poll = client.get(
        "/api/devices/protocol/v1/commands",
        headers=protocol_headers,
    )
    assert reconnect_poll.status_code == 200
    redelivered = reconnect_poll.json()["commands"][0]
    assert redelivered["command_id"] == envelope["command_id"]
    assert redelivered["nonce"] == envelope["nonce"]
    assert redelivered["idempotency_key"] == envelope["idempotency_key"]
    assert redelivered["delivery_count"] == 2
    envelope = redelivered

    wrong_nonce = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/decision",
        headers=protocol_headers,
        json={"nonce": "x" * 43, "approve": True},
    )
    assert wrong_nonce.status_code == 403
    approved = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/decision",
        headers=protocol_headers,
        json={"nonce": envelope["nonce"], "approve": True},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json() == {
        "status": "approved",
        "execute": True,
        "url": "https://example.com/project",
    }
    replayed_decision = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/decision",
        headers=protocol_headers,
        json={"nonce": envelope["nonce"], "approve": True},
    )
    assert replayed_decision.status_code == 200
    assert replayed_decision.json() == {
        "status": "approved",
        "execute": False,
        "url": None,
    }

    invalid_receipt = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/receipt",
        headers=protocol_headers,
        json={
            "nonce": envelope["nonce"],
            "status": "succeeded",
            "evidence": {"screen_contents": "private text"},
        },
    )
    assert invalid_receipt.status_code == 409

    with SessionLocal() as db:
        command = db.get(DeviceRemoteCommand, envelope["command_id"])
        assert command is not None
        command.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    expired_lease_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0",
        headers=protocol_headers,
    )
    assert expired_lease_poll.status_code == 200
    assert expired_lease_poll.json()["commands"] == []
    history = client.get(
        "/api/devices/remote-command-history",
        headers=owner_headers,
    )
    assert history.json()[0]["protocol_status"] == "uncertain"
    assert history.json()[0]["error_code"] == "DEVICE_EXECUTION_LEASE_EXPIRED"

    receipt_body = {
        "nonce": envelope["nonce"],
        "status": "succeeded",
        "evidence": {
            "browser_open_receipt": True,
            "opened_url_host": "example.com",
        },
    }
    receipt = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/receipt",
        headers=protocol_headers,
        json=receipt_body,
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "succeeded"
    assert receipt.json()["recorded"] is True
    duplicate_receipt = client.post(
        f"/api/devices/protocol/v1/commands/{envelope['command_id']}/receipt",
        headers=protocol_headers,
        json=receipt_body,
    )
    assert duplicate_receipt.status_code == 200
    assert duplicate_receipt.json()["recorded"] is False
    history = client.get(
        "/api/devices/remote-command-history",
        headers=owner_headers,
    )
    assert history.json()[0]["protocol_status"] == "succeeded"
    assert history.json()[0]["task_id"] == context.task_id

    with SessionLocal() as db:
        second_step = TaskStep(
            task_id=context.task_id,
            idx=1,
            description="Expire an unclaimed device request",
            idempotency_key="b" * 64,
        )
        db.add(second_step)
        db.commit()
        second_context = ToolContext(
            user_id=context.user_id,
            task_id=context.task_id,
            run_id=context.run_id,
            step_id=second_step.id,
            idempotency_key=second_step.idempotency_key,
        )
    with bind_tool_execution(second_context):
        expiring = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/expire-me",
                device["id"],
            )
        )
    assert expiring.success
    with SessionLocal() as db:
        command = db.get(DeviceRemoteCommand, expiring.data["command_id"])
        assert command is not None
        command.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expired_command_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0",
        headers=protocol_headers,
    )
    assert expired_command_poll.status_code == 200
    assert expired_command_poll.json()["commands"] == []
    history = client.get(
        "/api/devices/remote-command-history",
        headers=owner_headers,
    )
    expired_command = next(
        row for row in history.json()
        if row["id"] == expiring.data["command_id"]
    )
    assert expired_command["status"] == "expired"
    assert expired_command["protocol_status"] == "expired"

    with SessionLocal() as db:
        third_step = TaskStep(
            task_id=context.task_id,
            idx=2,
            description="Cancel a queued device request",
            idempotency_key="c" * 64,
        )
        db.add(third_step)
        db.commit()
        third_context = ToolContext(
            user_id=context.user_id,
            task_id=context.task_id,
            run_id=context.run_id,
            step_id=third_step.id,
            idempotency_key=third_step.idempotency_key,
        )
    with bind_tool_execution(third_context):
        queued_for_cancel = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/cancel-me",
                device["id"],
            )
        )
    assert queued_for_cancel.success
    cancelled = client.post(
        f"/api/devices/remote-command-history/{queued_for_cancel.data['command_id']}/cancel",
        headers=owner_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {
        "status": "cancelled",
        "cancellation_confirmed": True,
    }
    assert client.post(
        f"/api/devices/remote-command-history/{queued_for_cancel.data['command_id']}/cancel",
        headers=owner_headers,
    ).status_code == 409
    no_cancelled_commands = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0",
        headers=protocol_headers,
    )
    assert no_cancelled_commands.status_code == 200
    assert no_cancelled_commands.json()["commands"] == []

    with SessionLocal() as db:
        fourth_step = TaskStep(
            task_id=context.task_id,
            idx=3,
            description="Reject a corrupted stored command",
            idempotency_key="d" * 64,
        )
        db.add(fourth_step)
        db.commit()
        fourth_context = ToolContext(
            user_id=context.user_id,
            task_id=context.task_id,
            run_id=context.run_id,
            step_id=fourth_step.id,
            idempotency_key=fourth_step.idempotency_key,
        )
    with bind_tool_execution(fourth_context):
        corrupted = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/validated-at-queue-time",
                device["id"],
            )
        )
    assert corrupted.success
    with SessionLocal() as db:
        command = db.get(DeviceRemoteCommand, corrupted.data["command_id"])
        assert command is not None
        command.arguments = {"url": "http://127.0.0.1/private"}
        db.commit()
    corrupted_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0",
        headers=protocol_headers,
    )
    assert corrupted_poll.status_code == 200
    assert corrupted_poll.json()["commands"] == []
    history = client.get(
        "/api/devices/remote-command-history",
        headers=owner_headers,
    )
    invalid_stored_command = next(
        row for row in history.json()
        if row["id"] == corrupted.data["command_id"]
    )
    assert invalid_stored_command["status"] == "failed"
    assert (
        invalid_stored_command["error_code"]
        == "INVALID_STORED_DEVICE_COMMAND"
    )

    with SessionLocal() as db:
        fifth_step = TaskStep(
            task_id=context.task_id,
            idx=4,
            description="Report running cancellation honestly",
            idempotency_key="e" * 64,
        )
        db.add(fifth_step)
        db.commit()
        fifth_context = ToolContext(
            user_id=context.user_id,
            task_id=context.task_id,
            run_id=context.run_id,
            step_id=fifth_step.id,
            idempotency_key=fifth_step.idempotency_key,
        )
    with bind_tool_execution(fifth_context):
        running_cancel = asyncio.run(
            request_remote_url(
                "Protocol test device",
                "https://example.com/running-cancel",
                device["id"],
            )
        )
    assert running_cancel.success
    running_poll = client.get(
        "/api/devices/protocol/v1/commands",
        headers=protocol_headers,
    )
    assert running_poll.status_code == 200
    running_envelope = running_poll.json()["commands"][0]
    started = client.post(
        f"/api/devices/protocol/v1/commands/{running_envelope['command_id']}/decision",
        headers=protocol_headers,
        json={"nonce": running_envelope["nonce"], "approve": True},
    )
    assert started.status_code == 200
    assert started.json()["execute"] is True
    uncertain_cancel = client.post(
        f"/api/devices/remote-command-history/{running_envelope['command_id']}/cancel",
        headers=owner_headers,
    )
    assert uncertain_cancel.status_code == 200
    assert uncertain_cancel.json() == {
        "status": "uncertain",
        "cancellation_confirmed": False,
    }
    wait_started = monotonic()
    empty_long_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=1",
        headers=protocol_headers,
    )
    assert empty_long_poll.status_code == 200
    assert empty_long_poll.json()["commands"] == []
    assert monotonic() - wait_started >= 0.8
    invalid_wait = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=26",
        headers=protocol_headers,
    )
    assert invalid_wait.status_code == 422

    revoked = client.delete(f"/api/devices/{device['id']}", headers=owner_headers)
    assert revoked.status_code == 200
    after_revoke = client.get(
        "/api/devices/protocol/v1/commands",
        headers=protocol_headers,
    )
    assert after_revoke.status_code == 401
