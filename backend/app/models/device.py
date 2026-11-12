from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class DeviceConnection(Base):
    __tablename__ = "device_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DevicePairingRequest(Base):
    __tablename__ = "device_pairing_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    pairing_code_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(24))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_device_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("device_connections.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceMessage(Base):
    __tablename__ = "device_messages"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "external_id", name="uq_device_message_external_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    device_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("device_connections.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(255))
    source_app: Mapped[str] = mapped_column(String(120))
    sender: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text)
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    queued_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_status: Mapped[str] = mapped_column(String(16), default="none")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceRemoteCommand(Base):
    __tablename__ = "device_remote_commands"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key",
            name="uq_device_remote_commands_user_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    device_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("device_connections.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32))
    target_url: Mapped[str] = mapped_column(Text)
    protocol_version: Mapped[int] = mapped_column(Integer, default=1)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    required_scope: Mapped[str] = mapped_column(
        String(64), default="remote.browser.open_url"
    )
    command_nonce: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    payload_digest: Mapped[str] = mapped_column(String(64))
    expected_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    task_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tasks.id"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    task_step_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("task_steps.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    protocol_status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
    )
    delivery_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    running_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LocalDeviceApp(Base):
    __tablename__ = "local_device_apps"
    __table_args__ = (
        UniqueConstraint("user_id", "executable_path", name="uq_local_app_user_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    executable_path: Mapped[str] = mapped_column(String(1024))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
