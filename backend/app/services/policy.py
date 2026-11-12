"""Action policy engine (spec §10, §68).

Crosses user-configurable per-tool policies with the autonomy level:

  L0 Chat        — no tools (internal planner tool exempt)
  L1 Assisted    — read-only tools only (static AUTO/NOTIFY)
  L2 Tool Agent  — CONFIRM actions require approval
  L3 Autonomous  — interactive CONFIRM actions run automatically unless explicitly set to approval
  L4 Scheduled   — L3 rules plus automatic CONFIRM actions in scheduled runs

STRICT tools (payments, deletion) always require approval — never loosened.
Policy values: "automatic" | "approval" | "denied".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tools.base import PermissionLevel

AUTONOMY_LEVELS = {
    0: "chat",
    1: "assisted",
    2: "tool_agent",
    3: "autonomous",
    4: "scheduled",
}
DEFAULT_AUTONOMY = 2

POLICY_AUTOMATIC = "automatic"
POLICY_APPROVAL = "approval"
POLICY_DENY = "denied"
VALID_POLICIES = frozenset({POLICY_AUTOMATIC, POLICY_APPROVAL, POLICY_DENY})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    require_approval: bool
    code: str = ""        # denial code when not allowed
    detail: str = ""      # human explanation for the model


def normalize_autonomy(value: object) -> int:
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_AUTONOMY
    return level if level in AUTONOMY_LEVELS else DEFAULT_AUTONOMY


def normalize_tool_policies(value: object) -> dict[str, str]:
    """Keep only {tool_name: valid_policy} entries; never trust raw prefs."""
    if not isinstance(value, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in value.items()
        if str(v) in VALID_POLICIES
    }


def decide_for_user(
    *,
    permission: PermissionLevel,
    preferences: dict[str, Any] | None,
    tool_name: str,
    tags: list[str] | None = None,
) -> Decision:
    """Resolve a capability decision from the same validated user policy snapshot."""
    prefs = preferences if isinstance(preferences, dict) else {}
    policies = normalize_tool_policies(prefs.get("tool_policies"))
    return decide(
        permission=permission,
        autonomy=normalize_autonomy(prefs.get("autonomy")),
        policy=policies.get(tool_name, ""),
        tags=tags,
    )


def decide(
    *,
    permission: PermissionLevel,
    autonomy: int,
    policy: str,
    tags: list[str] | None = None,
) -> Decision:
    """Effective decision for one tool call under the user's policy snapshot."""
    if "internal" in (tags or []):
        return Decision(allowed=True, require_approval=False)

    if autonomy <= 0:
        return Decision(
            allowed=False, require_approval=False,
            code="AUTONOMY_DISABLED",
            detail="Tools are disabled (autonomy level 0 = chat only).",
        )

    if policy == POLICY_DENY:
        return Decision(
            allowed=False, require_approval=False,
            code="TOOL_DENIED",
            detail="The user's policy denies this tool.",
        )

    if autonomy <= 1 and permission > PermissionLevel.NOTIFY:
        return Decision(
            allowed=False, require_approval=False,
            code="AUTONOMY_TOO_LOW",
            detail="Assisted mode allows read-only tools only; ask the user to "
                   "raise the autonomy level to use action tools.",
        )

    require_approval = permission >= PermissionLevel.CONFIRM

    if policy == POLICY_APPROVAL:
        require_approval = True
    elif permission >= PermissionLevel.STRICT:
        require_approval = True
    elif autonomy >= 3 and permission >= PermissionLevel.CONFIRM:
        require_approval = False

    return Decision(allowed=True, require_approval=require_approval)
