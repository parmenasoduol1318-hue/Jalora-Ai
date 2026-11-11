"""Per-user AI-provider availability state (disconnect / unconfigure).

API keys live only in the environment, so "disconnecting" a provider means
recording it in the user's preferences so the router silently skips it. This
module is the single source of truth for that list and for the secret-free
provider overview the UI renders.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.integration_registry import LOGO_FOR_PROVIDER
from app.services.model_router import AUTO_PRIORITY

# Canonical provider names, in routing-priority order. Every chat provider the
# router knows about is present here. Includes locally-served and cloud
# providers that may not be in AUTO_PRIORITY but should still appear in the
# Settings UI so users can connect per-user keys or disable them.
_PROVIDERS_BEYOND_ROUTER = [
    "lmstudio", "jan", "cohere", "cerebras", "perplexity", "poolside",
    "copilot", "codex",
]
PROVIDER_NAMES: tuple[str, ...] = tuple(list(AUTO_PRIORITY) + _PROVIDERS_BEYOND_ROUTER)


def normalize_disabled(raw: object) -> list[str]:
    """Coerce a stored preference value into a clean, ordered list of names.

    Unknown names and duplicates are dropped; order follows routing priority so
    the persisted value is stable regardless of the order the UI sent.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    wanted = {str(item).strip().lower() for item in raw}
    return [name for name in PROVIDER_NAMES if name in wanted]


def disabled_for_user(db: Session, user_id: str) -> list[str]:
    """Read the disabled-provider list from a user's preferences."""
    from app.models.user import User

    user = db.get(User, user_id)
    if user is None:
        return []
    return normalize_disabled((user.prefs or {}).get("disabled_providers"))


def overview(settings: Settings, disabled: Iterable[str] | None) -> dict[str, dict]:
    """Secret-free per-provider status for the UI.

    `usable` is what routing actually honours: the provider must be configured
    (key present, or keyless Ollama) and not explicitly disabled by the user.
    """
    off = set(normalize_disabled(list(disabled or ())))
    out: dict[str, dict] = {}
    status = settings.provider_status()
    for name in PROVIDER_NAMES:
        info = status.get(name)
        configured = bool(info["configured"]) if info else False
        is_off = name in off
        out[name] = {
            "model": info["model"] if info else "",
            "configured": configured,
            "keyless": name in ("ollama", "lmstudio", "jan"),
            "disabled": is_off,
            "usable": configured and not is_off,
            "logo": LOGO_FOR_PROVIDER.get(name, ""),
        }
    return out
