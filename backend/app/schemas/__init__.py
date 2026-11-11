from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- auth -----------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    success: bool = True
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    prefs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfessionalProfileUpdate(BaseModel):
    courses: list[str] = Field(default_factory=list, max_length=20)
    qualifications: list[str] = Field(default_factory=list, max_length=30)
    skills: list[str] = Field(default_factory=list, max_length=60)
    phone: str = Field(default="", max_length=50)
    discoverable: bool = False
    share_email: bool = False
    share_phone: bool = False

    @field_validator("courses", "qualifications", "skills")
    @classmethod
    def clean_profile_items(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split()).strip()
            if not item or len(item) > 120:
                raise ValueError("Profile items must contain 1-120 characters.")
            if item.casefold() not in seen:
                seen.add(item.casefold())
                cleaned.append(item)
        return cleaned

    @field_validator("phone")
    @classmethod
    def clean_profile_phone(cls, value: str) -> str:
        return value.strip()


class ProfessionalProfileOut(BaseModel):
    name: str
    email: str
    courses: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    phone: str = ""
    discoverable: bool = False
    share_email: bool = False
    share_phone: bool = False


class PeopleSearchResults(BaseModel):
    query: str
    results: list[dict[str, Any]] = Field(default_factory=list)


# ---- conversations --------------------------------------------------------
class ConversationOut(BaseModel):
    id: str
    title: str
    project: str | None = None
    archived: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", max_length=255)
    project: str | None = Field(default=None, max_length=255)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    archived: bool | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool_calls: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- chat -----------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = None
    stream: bool = False
    model_provider: str | None = None
    model_name: str | None = None
    media_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    mode: str | None = None
    intent_hint: Literal[
        "web_search", "email", "math", "task", "coding", "research",
    ] | None = None
    capability_hint: str | None = Field(default=None, max_length=64)
    math_level: str | None = None
    show_working: bool | None = None


class WorkspaceChatRequest(ChatRequest):
    external_agent_confirmed: bool = False


class SourceRef(BaseModel):
    title: str
    url: str
    snippet: str = ""


class ChatResponse(BaseModel):
    success: bool = True
    conversation_id: str
    run_id: str
    task_id: str = ""
    reply: str
    sources: list[SourceRef] = Field(default_factory=list)
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] | None = None
    status: str = "completed"
    intent: str = ""
    plan: list[dict[str, Any]] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list["CitationRef"] = Field(default_factory=list)
    diagrams: list["DiagramRef"] = Field(default_factory=list)

    @classmethod
    def from_outcome(cls, outcome: Any) -> "ChatResponse":
        data = outcome.as_response()
        return cls(
            conversation_id=data["conversation_id"],
            run_id=data["run_id"],
            task_id=data.get("task_id", "") or "",
            reply=data["reply"],
            sources=[SourceRef(**s) for s in data["sources"]],
            model=data["model"],
            usage=data["usage"],
            approval=data["approval"],
            status=data["status"],
            intent=data.get("intent", ""),
            plan=data.get("plan"),
            artifacts=data.get("artifacts", []),
            citations=data.get("citations", []),
            diagrams=data.get("diagrams", []),
        )


class CitationRef(BaseModel):
    """A numbered, hyperlinked research reference (spec §visual)."""
    index: int
    title: str
    url: str
    snippet: str = ""


class DiagramRef(BaseModel):
    """A vector/raster diagram extracted from the reply (spec §visual)."""
    id: str
    kind: str
    title: str = ""
    content: str


# ---- approvals / credentials ----------------------------------------------
class CredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=512)
    secret: str = Field(min_length=1, max_length=4096)


class ApproveRequest(BaseModel):
    credentials: CredentialsIn | None = None
    # Email approval edits are sent through a separate PATCH endpoint. This
    # remains optional so existing credential approvals are unchanged.
    arguments: dict[str, Any] | None = None


class ApprovalEditRequest(BaseModel):
    arguments: dict[str, Any]
    payload_version: int | None = Field(default=None, ge=1)


# ---- tasks ----------------------------------------------------------------
class TaskStepOut(BaseModel):
    idx: int
    description: str
    status: str
    detail: str | None = None

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: str
    conversation_id: str | None = None
    run_id: str | None = None
    goal: str
    status: str
    plan: list[dict[str, Any]] | None = None
    current_step: int = 0
    progress: int = 0
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskDetailOut(TaskOut):
    steps: list[TaskStepOut] = Field(default_factory=list)


# ---- settings -------------------------------------------------------------
class VoiceSettings(BaseModel):
    enabled: bool = False
    auto_speak: bool = True
    continuous: bool = False
    voice: str = Field(default="", max_length=120)


class SettingsPatch(BaseModel):
    autonomy: int | None = Field(default=None, ge=0, le=4)
    tool_policies: dict[str, str] | None = None
    memory_enabled: bool | None = None
    memory_retention_days: int | None = Field(default=None, gt=0, le=3650)
    locale: str | None = Field(default=None, max_length=8)
    voice: VoiceSettings | None = None
    response_length: str | None = Field(default=None, max_length=16)
    tone: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    preferred_provider: str | None = Field(default=None, max_length=32)
    disabled_providers: list[str] | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a valid IANA timezone")
        return value

    # Keep the preference as an IANA name so relative dates and DST behavior
    # remain unambiguous across clients and restarts.


class ProviderKeyPatch(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    api_key: str = Field(min_length=1, max_length=512)


class ProviderKeyOut(BaseModel):
    provider: str
    url: str | None = None


class ModelInfo(BaseModel):
    id: str
    name: str = ""
    size: int | None = None
    format: str | None = None
    modified_at: str | None = None
    details: dict[str, Any] | None = None


class ModelsResponse(BaseModel):
    provider: str
    models: list[ModelInfo] = Field(default_factory=list)
    available: bool = True
    error: str | None = None


class SettingsOut(BaseModel):
    autonomy: int
    tool_policies: dict[str, str] = Field(default_factory=dict)
    memory_enabled: bool = True
    memory_retention_days: int | None = None
    locale: str = "en"
    voice: "VoiceSettings" = Field(default_factory=VoiceSettings)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    response_length: str = "balanced"
    tone: str = "professional"
    timezone: str = "Africa/Nairobi"
    preferred_provider: str = "auto"
    disabled_providers: list[str] = Field(default_factory=list)
    provider_keys: list[str] = Field(default_factory=list)


# ---- memory ---------------------------------------------------------------
class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: str = Field(default="fact", max_length=32)


class MemoryOut(BaseModel):
    id: str
    kind: str
    content: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MemorySearchResult(BaseModel):
    memory: MemoryOut
    score: float


class MemorySettings(BaseModel):
    memory_enabled: bool
    memory_retention_days: int | None = Field(default=None, gt=0, le=3650)


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: str | None = Field(default=None, max_length=32)


# ---- documents ------------------------------------------------------------
class DocumentOut(BaseModel):
    id: str
    task_id: str | None = None
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    error: str | None = None
    page_count: int = 0
    progress: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentQueryResponse(BaseModel):
    success: bool = True
    document_id: str
    answer: str
    retrieved: list[dict[str, Any]] = Field(default_factory=list)


# ---- media (§42-44) --------------------------------------------------------
class MediaUploadOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    kind: str
    size_bytes: int
    url: str
    created_at: datetime


class MediaFileOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    kind: str
    size_bytes: int
    url: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- voice (§31-33) -------------------------------------------------------
class TranscribeOut(BaseModel):
    transcript: str
    provider: str


class VoiceConfigOut(BaseModel):
    stt: bool = True
    tts: str = "server"
    provider: str = "auto"
    max_clip_bytes: int = 25 * 1024 * 1024
    live: bool = False
    stt_provider: str = "auto"
    tts_provider: str = "auto"
    sample_rate: int = 16000
    tts_sample_rate: int = 22050
    max_seconds: int = 600


class VoiceCallStart(BaseModel):
    purpose: str = Field(default="", max_length=500)
    conversation_id: str | None = None
    task_id: str | None = None


# ---- runs / approvals / activity -----------------------------------------
class ApprovalOut(BaseModel):
    id: str
    run_id: str
    tool_name: str
    arguments: dict[str, Any]
    level: int
    kind: str = "tool"
    status: str
    created_at: datetime
    task_id: str | None = None
    task_step_id: str | None = None
    decision_status: str = "pending"
    claim_status: str = "unclaimed"
    payload_version: int = 1
    payload_digest: str | None = None
    expires_at: datetime | None = None
    decided_at: datetime | None = None

    model_config = {"from_attributes": True}


class ActivityOut(BaseModel):
    id: str
    agent: str
    tool: str
    arguments: dict[str, Any] | None = None
    result_summary: str
    success: bool
    approval_status: str | None = None
    duration_ms: int
    provider: str | None = None
    error_code: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str
    database: str
    providers: dict[str, dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime
    capabilities: dict[str, Any] = Field(default_factory=dict)


# ---- integrations / email (Phase E §34-38, §60, §76) ---------------------
class IntegrationCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)


class IntegrationPatch(BaseModel):
    config: dict[str, Any] | None = None
    scopes: list[str] | None = None
    status: str | None = Field(default=None, max_length=16)


class IntegrationOut(BaseModel):
    id: str
    provider: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class GmailStartOut(BaseModel):
    integration_id: str
    provider: str = "gmail"
    authorization_url: str


class IntegrationTestOut(BaseModel):
    ok: bool
    provider: str
    status: str
    detail: str = ""


class CustomIntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    auth_url: str = Field(min_length=1, max_length=512)
    token_url: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str = Field(min_length=1, max_length=512)
    redirect_uri: str = Field(default="", max_length=512)
    scopes: list[str] = Field(default_factory=list)
    token_auth_style: str = Field(default="body", max_length=16)
    userinfo_url: str | None = None
    extra_auth_params: dict[str, str] = Field(default_factory=dict)


class CustomIntegrationOut(BaseModel):
    id: str
    provider: str
    name: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    error: str | None = None


class EmailCompose(BaseModel):
    to: str = Field(min_length=1, max_length=4000)
    cc: str = Field(default="", max_length=4000)
    bcc: str = Field(default="", max_length=4000)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100_000)
    regards: str = Field(default="", max_length=2_000)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class EmailBatchSend(BaseModel):
    emails: list[EmailCompose] = Field(min_length=2, max_length=25)


class EmailSend(EmailCompose):
    thread_id: str | None = None


class EmailOut(BaseModel):
    id: str
    thread_id: str
    from_addr: str
    to_addr: str
    subject: str
    body: str
    snippet: str = ""
    received_at: datetime
    sender: str = ""
    draft: bool = False
    mock: bool = False

    model_config = {"from_attributes": True}


class EmailThreadOut(BaseModel):
    id: str
    subject: str
    participants: list[str] = Field(default_factory=list)
    message_count: int = 0
    recipient: str | None = None
    sender: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EmailDraftOut(BaseModel):
    id: str = ""
    to: str
    cc: str = ""
    bcc: str = ""
    subject: str
    body: str
    status: str = "draft"
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EmailSentOut(BaseModel):
    message_id: str
    to: str = ""
    cc: str = ""
    bcc: str = ""
    subject: str = ""
    thread_id: str | None = None
    provider: str = "email_mock"
    mock: bool = True
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EmailSendResult(BaseModel):
    status: str
    draft: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    sent: EmailSentOut | None = None


# ---- auto-reply rules (§38) ---------------------------------------------
class RuleBase(BaseModel):
    name: str = Field(default="Auto-reply", max_length=120)
    enabled: bool = True
    sender_pattern: str = Field(default="", max_length=255)
    topic_pattern: str = Field(default="", max_length=255)
    time_window_start: int = Field(default=0)
    time_window_end: int = Field(default=1440)
    tone: str = Field(default="professional", max_length=16)
    template: str = Field(default="Thank you {name}, we received {subject}.")
    max_per_hour: int = Field(default=0)
    max_per_day: int = Field(default=10)
    requires_approval: bool = Field(default=True)
    priority: int = Field(default=0)


class RuleCreate(RuleBase):
    pass


class RulePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    sender_pattern: str | None = Field(default=None, max_length=255)
    topic_pattern: str | None = Field(default=None, max_length=255)
    time_window_start: int | None = Field(default=None, ge=0, le=1440)
    time_window_end: int | None = Field(default=None, ge=0, le=1440)
    tone: str | None = Field(default=None, max_length=16)
    template: str | None = None
    max_per_hour: int | None = Field(default=None, ge=0)
    max_per_day: int | None = Field(default=None, ge=0)
    requires_approval: bool | None = None
    priority: int | None = None


class RuleOut(RuleBase):
    id: str
    hourly_count: int = 0
    daily_count: int = 0
    last_hour_bucket: datetime | None = None
    last_day: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- email task request ---------------------------------------------------
class EmailTaskRequest(BaseModel):
    """Request to create an email task from natural language."""
    recipient: str | None = Field(default=None)
    request: str = Field(..., description="Natural language request for the email task")
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class EmailTaskResult(BaseModel):
    """Result of an email task execution."""
    status: str
    draft: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    sent: dict[str, Any] | None = None
    error: dict[str, str] | None = None


class EmailReplyDraftRequest(BaseModel):
    instruction: str = Field(
        default="Write a concise, professional reply that addresses the sender's message.",
        max_length=2000,
    )


class EmailReplyDraftResult(BaseModel):
    status: Literal["editable_draft"]
    to: str
    subject: str
    body: str
    model: str
    task_id: str


# ---- feedback (Phase K) -------------------------------------------------
class FeedbackCreate(BaseModel):
    task_id: str | None = None
    message_id: str | None = None
    run_id: str | None = None
    tool_name: str | None = None
    sentiment: str = "positive"
    content: str = Field(default="", max_length=4000)


class FeedbackOut(BaseModel):
    id: str
    user_id: str
    task_id: str | None = None
    message_id: str | None = None
    run_id: str | None = None
    tool_name: str | None = None
    sentiment: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- workspace (Phase J §53-54) ----------------------------------------
class WorkspaceTreeItem(BaseModel):
    name: str
    kind: str
    path: str
    size_bytes: int | None = None
    children: list["WorkspaceTreeItem"] | None = None

    model_config = {"from_attributes": True}


class WorkspaceTree(BaseModel):
    # Keep the API contract used by the React workspace and the sandbox
    # service: a flat, user-scoped list of file entries.
    files: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class WorkspaceFileOut(BaseModel):
    path: str
    content: str
    size: int = 0
    truncated: bool = False

    model_config = {"from_attributes": True}


class WorkspaceSaveIn(BaseModel):
    path: str
    content: str


class WorkspaceCreateIn(BaseModel):
    path: str
    content: str = ""


class WorkspaceRenameIn(BaseModel):
    path: str
    new_path: str


class WorkspaceRenameResult(BaseModel):
    path: str
    from_path: str
    kind: str
    error: str | None = None


class WorkspaceDeleteResult(BaseModel):
    path: str
    kind: str | None = None
    error: str | None = None


class WorkspaceWriteResult(BaseModel):
    path: str
    size: int = 0
    created: bool = False


class WorkspaceExecIn(BaseModel):
    argv: list[str]
    cwd: str | None = None
    timeout_ms: int | None = None


class WorkspaceExecOut(BaseModel):
    command: str = ""
    stdout: str
    stderr: str
    exit_code: int | None
    ok: bool = False
    timed_out: bool = False
    duration_ms: int
    truncated: bool = False


class WorkspaceDiffOut(BaseModel):
    edits: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceRollbackResult(BaseModel):
    restored: list[str] = Field(default_factory=list)
    error: str | None = None


class WorkspaceUploadOut(BaseModel):
    results: list[WorkspaceWriteResult] = Field(default_factory=list)
    rejected: list["WorkspaceUploadError"] = Field(default_factory=list)


class WorkspaceUploadError(BaseModel):
    filename: str
    detail: str


class WorkspaceSessionPatch(BaseModel):
    title: str


# ---- inbound email webhook (§60) -----------------------------------------
class InboundResult(BaseModel):
    """Result of inbound email webhook processing."""
    status: str
    message_id: str
    matched_user: bool = False
    classification: str | None = None
    summary: str | None = None
    auto_reply: Any | None = None
    auto_reply_task_id: str | None = None
    auto_reply_approval_id: str | None = None
