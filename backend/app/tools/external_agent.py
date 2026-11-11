"""Approval-gated external coding-model capability."""
from __future__ import annotations

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.conversation import Conversation
from app.models.user import User
from app.services import credential_store
from app.tools.base import PermissionLevel, registry

_STAGED_MUTATION_TOOLS = {"edit_code", "edit_code_multi", "create_project"}


async def run_external_coding_agent(provider: str, request_message: str) -> ToolResult:
    """Ask Copilot or Codex for a project-scoped proposal without applying edits.

    The resulting preview is stored in this canonical task's tool result.
    Apply accepted changes afterward through Jalora's normal coding tools.
    """
    if provider not in {"copilot", "codex"}:
        return ToolResult.fail(
            "UNSUPPORTED_CODING_AGENT",
            "Choose either Copilot or Codex.",
            message="That coding-agent provider is not supported.",
        )
    context = get_tool_context()
    if not context.project_id:
        return ToolResult.fail(
            "WORKSPACE_PROJECT_REQUIRED",
            "External coding agents require an owned workspace project.",
            message="Select a workspace project before starting this agent.",
        )
    with SessionLocal() as db:
        user = db.get(User, context.user_id)
        project = db.get(Conversation, context.project_id)
        if (
            user is None
            or project is None
            or project.user_id != context.user_id
            or project.project != "workspace"
            or project.archived
        ):
            return ToolResult.fail(
                "WORKSPACE_PROJECT_NOT_FOUND",
                "The workspace project is unavailable or not owned by this user.",
                message="The selected workspace project is unavailable.",
            )
        allowed, policy_error = (
            _copilot_staging_tools(user) if provider == "copilot" else (set(), None)
        )
    if policy_error:
        return ToolResult.fail(
            "WORKSPACE_AGENT_POLICY_BLOCKED",
            policy_error,
            message="Workspace-agent access is blocked by the current policy.",
        )
    try:
        from app.services.workspace_agents import (
            _agent_key,
            _run_codex,
            _run_copilot,
        )

        api_key = _agent_key(context.user_id, provider)
    except credential_store.CredentialStoreError:
        return ToolResult.fail(
            "AGENT_CREDENTIAL_INVALID",
            f"The saved {provider} credential could not be decrypted.",
            message=f"Reconnect {provider} in Settings before retrying.",
        )
    if not api_key:
        return ToolResult.fail(
            "AGENT_NOT_CONFIGURED",
            f"No usable {provider} credential is configured.",
            message=f"Connect {provider} in Settings before retrying.",
        )

    changes: list[dict[str, object]] = []
    if provider == "copilot":
        reply = await _run_copilot(
            context.user_id,
            context.project_id,
            (
                request_message
                + "\n\nThis is a preview-only request. Do not edit files. "
                  "Return a concise proposed unified diff and explain the "
                  "files you would change."
            ),
            api_key,
            changes,
            allowed,
        )
    else:
        reply = await _run_codex(
            context.user_id,
            context.project_id,
            request_message,
            api_key,
            changes,
        )

    return ToolResult.ok(
        "Prepared an external coding-agent proposal; no project files were changed.",
        {
            "provider": provider,
            "project_id": context.project_id,
            "preview_only": True,
            "file_changes": changes,
            "reply": reply[:50_000],
            "reply_truncated": len(reply) > 50_000,
        },
    )


def _copilot_staging_tools(user: User) -> tuple[set[str], str | None]:
    from app.services.policy import decide, normalize_autonomy, normalize_tool_policies
    from app.services.workspace_agents import _coding_tool_specs

    preferences = user.prefs or {}
    autonomy = normalize_autonomy(preferences.get("autonomy"))
    policies = normalize_tool_policies(preferences.get("tool_policies"))
    allowed = {
        spec.name
        for spec in _coding_tool_specs()
        if (
            spec.permission <= PermissionLevel.NOTIFY
            or spec.name in _STAGED_MUTATION_TOOLS
        )
        and decide(
            permission=spec.permission,
            autonomy=autonomy,
            policy=policies.get(spec.name, ""),
            tags=spec.tags,
        ).allowed
    }
    has_read_tool = any(
        spec.name in allowed and spec.permission <= PermissionLevel.NOTIFY
        for spec in _coding_tool_specs()
    )
    if not has_read_tool:
        return set(), "The current user policy does not permit workspace read tools."
    return allowed, None


registry.register(
    run_external_coding_agent,
    permission=PermissionLevel.CONFIRM,
    category="coding",
    requires_internet=True,
    tags=["coding", "external", "preview"],
    side_effect="external",
    idempotent=False,
    retryable=False,
)
