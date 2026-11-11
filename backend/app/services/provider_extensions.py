"""Provider extension module (§4).

Adds five AI providers — LMStudio, Jan.ai, Cohere, Cerebras Cloud, and
Perplexity — to the model router's spec table and auto-routing priority, without
modifying the restricted ``model_router.py`` / ``provider_state.py`` /
``integration_registry.py`` source files.

Because every new provider is OpenAI-compatible at the HTTP level, the existing
``JaloraChatModel`` handles request/response formatting already; the extension
only needs to register ``ProviderSpec`` entries (with the correct base URL,
model, and key requirement) and make the router aware of them.

This module also wires up **per-user API keys** (spec §provider-keys): when a
user supplies their own key from the Settings UI, the key is decrypted at
request time and injected into the ``ProviderSpec`` so the model actually uses
it instead of the (absent) server-wide env var. The router's ``is_configured``
and ``healthcheck`` are patched to honour a thread-/context-safe store of
per-user key presence so that auto-routing considers those providers.

This module MUST be imported (e.g. from ``app/main.py``) before any
``ModelRouter`` singleton is created so the patched ``build_provider_specs`` and
``AUTO_PRIORITY`` are in effect for the first routing decision.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import replace
from typing import Any

import httpx

from app.config import Settings
from app.services.model_router import (
    AUTO_PRIORITY as _orig_auto_priority,
)
from app.services.model_router import (
    ModelRouter,
    ProviderSpec,
    TaskRoute,
    get_async_client,
)
from app.services.model_router import (
    build_provider_specs as _orig_build_provider_specs,
)

logger = logging.getLogger("jalora.provider_extensions")


def _loaded_lmstudio_models(spec: ProviderSpec) -> list[tuple[str, set[str]]]:
    """Return loaded LM Studio chat models and their accepted identifiers."""
    base_url = spec.base_url.rstrip("/")
    base_url = base_url.removesuffix("/v1")
    try:
        response = httpx.get(f"{base_url}/api/v1/models", timeout=2.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("LM Studio model discovery failed: %s", type(exc).__name__)
        return []

    models = payload.get("models", []) if isinstance(payload, dict) else []
    loaded: list[tuple[str, set[str]]] = []
    for model in models:
        if not isinstance(model, dict) or model.get("type") != "llm":
            continue
        instances = model.get("loaded_instances")
        if not isinstance(instances, list) or not instances:
            continue
        identifiers = {
            value
            for value in (
                model.get("key"),
                *(item.get("id") for item in instances if isinstance(item, dict)),
            )
            if isinstance(value, str) and value.strip()
        }
        if identifiers:
            loaded.append((str(model.get("key") or min(identifiers)), identifiers))
    return loaded


def _use_loaded_lmstudio_model(route: TaskRoute) -> TaskRoute:
    if route.provider.name != "lmstudio":
        return route
    loaded_models = _loaded_lmstudio_models(route.provider)
    if not loaded_models:
        return route

    selected = next(
        (model_id for model_id, aliases in loaded_models if route.model in aliases),
        loaded_models[0][0],
    )
    if selected == route.model:
        return route

    logger.warning(
        "Configured LM Studio model %s is not loaded; using loaded chat model %s",
        route.model,
        selected,
    )
    return TaskRoute(
        route.provider,
        selected,
        route.reason,
        route.memory_mb,
    )


# The base router uses configured model names without checking the local
# server's loaded models. Resolve only its implicit LM Studio choice; explicit
# model overrides remain authoritative.
_orig_route = ModelRouter.route


def _route_with_loaded_lmstudio_model(
    self: ModelRouter, *args: Any, **kwargs: Any
) -> TaskRoute:
    route = _orig_route(self, *args, **kwargs)
    if kwargs.get("model_override") is not None:
        return route
    return _use_loaded_lmstudio_model(route)


ModelRouter.route = _route_with_loaded_lmstudio_model  # type: ignore[assignment]

_orig_route_chain = ModelRouter.route_chain


def _route_chain_with_loaded_lmstudio_model(
    self: ModelRouter, *args: Any, **kwargs: Any
) -> list[TaskRoute]:
    routes = _orig_route_chain(self, *args, **kwargs)
    if kwargs.get("model_override") is not None:
        return routes
    return [_use_loaded_lmstudio_model(route) for route in routes]


ModelRouter.route_chain = _route_chain_with_loaded_lmstudio_model  # type: ignore[assignment]

#: New providers added by this extension, in the order they are appended to
#: the auto-routing priority list.  Keyless / local providers come first
#: (LMStudio, Jan) so they are preferred for free budgets, followed by cloud
#: providers that require an API key.
_EXTENDED_PRIORITY: list[str] = [
    "lmstudio",  # local, keyless — OpenAI-compatible at localhost:1234/v1
    "jan",  # local/remote, keyless — user-configured OpenAI-compatible URL
    "cohere",  # cloud — https://api.cohere.ai/v1
    "cerebras",  # cloud — https://api.cerebras.ai/v1
    "perplexity",  # cloud — https://api.perplexity.ai/v1
    "poolside",  # cloud — https://inference.poolside.ai/v1
]

# ---------------------------------------------------------------------------
# Per-user API key context
# ---------------------------------------------------------------------------
# A context variable (async-safe) that carries the *names* of providers for
# which the current user has stored a per-user key.  ``chat_service.py`` sets
# this before calling ``router.route()`` so the patched ``is_configured`` /
# ``healthcheck`` / ``overview`` recognise those providers as available even
# when no env-var key is present.
_user_keys_ctx: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "jalora_user_provider_keys", default=frozenset()
)

# The *decrypted* key values for the current request.  This is set by
# ``chat_service.py`` after it decrypts the user's stored keys, so the patched
# ``route()`` / model creation can inject the correct key into the spec.
_user_keys_values_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "jalora_user_provider_key_values", default={}
)


def set_active_user_keys(key_names: frozenset[str]) -> None:
    """Tell the router that *key_names* are "configured" via per-user keys."""
    _user_keys_ctx.set(key_names)


def set_active_user_key_values(keys: dict[str, str]) -> None:
    """Set decrypted key values so ``route()`` can inject them into specs."""
    _user_keys_values_ctx.set(keys)


def clear_active_user_keys() -> None:
    """Reset the per-user key context (call after routing is done)."""
    _user_keys_ctx.set(frozenset())
    _user_keys_values_ctx.set({})


def apply_user_key_to_route(route: TaskRoute) -> TaskRoute:
    """If the routed provider has a per-user key, return a new TaskRoute whose
    ProviderSpec carries the user's key (falling back to the env-var key)."""
    values = _user_keys_values_ctx.get()
    if route.provider.name in values:
        new_spec = replace(route.provider, api_key=values[route.provider.name])
        route = TaskRoute(new_spec, route.model, route.reason)
    return route


def _extra_specs(s: Settings) -> dict[str, ProviderSpec]:
    """Build ``ProviderSpec`` entries for providers added by this extension.

    All use ``api_style="openai"`` (standard OpenAI-compatible chat completions),
    so the existing ``JaloraChatModel`` formats payloads and parses responses
    without modification.
    """
    return {
        # LMStudio — local, keyless. The server typically does not require an
        # Authorization header, so requires_key=False (same as Ollama).
        "lmstudio": ProviderSpec(
            name="lmstudio",
            api_style="openai",
            base_url=s.lmstudio_base_url.rstrip("/"),
            api_key=None,
            default_model=s.lmstudio_model,
            strong_model=s.lmstudio_model,
            requires_key=False,
        ),
        # Jan.ai — OpenAI-compatible at a user-configurable URL. Keyless.
        "jan": ProviderSpec(
            name="jan",
            api_style="openai",
            base_url=(s.jan_base_url or "").rstrip("/") if s.jan_base_url else "",
            api_key=None,
            default_model=s.jan_model,
            strong_model=s.jan_model,
            requires_key=False,
        ),
        # Cohere — cloud, OpenAI-compatible endpoint.
        "cohere": ProviderSpec(
            name="cohere",
            api_style="openai",
            base_url="https://api.cohere.ai/v1",
            api_key=s.cohere_api_key,
            default_model=s.cohere_model,
            strong_model=s.cohere_model,
        ),
        # Cerebras Cloud — cloud, OpenAI-compatible endpoint.
        "cerebras": ProviderSpec(
            name="cerebras",
            api_style="openai",
            base_url="https://api.cerebras.ai/v1",
            api_key=s.cerebras_api_key,
            default_model=s.cerebras_model,
            strong_model=s.cerebras_model,
        ),
        # Perplexity — cloud, OpenAI-compatible endpoint.
        "perplexity": ProviderSpec(
            name="perplexity",
            api_style="openai",
            base_url="https://api.perplexity.ai/v1",
            api_key=s.perplexity_api_key,
            default_model=s.perplexity_model,
            strong_model=s.perplexity_strong_model,
        ),
        "poolside": ProviderSpec(
            name="poolside",
            api_style="openai",
            base_url="https://inference.poolside.ai/v1",
            api_key=s.poolside_api_key,
            default_model=s.poolside_model,
            strong_model=s.poolside_model,
        ),
    }


def _patched_build_provider_specs(s: Settings) -> dict[str, ProviderSpec]:
    """Append the extension providers to the original spec table."""
    specs = _orig_build_provider_specs(s)
    specs.update(_extra_specs(s))
    return specs


# --- Apply patches ----------------------------------------------------------

# 1. Replace build_provider_specs so every ModelRouter instance picks up the
#    new ProviderSpec entries.
import app.services.model_router as _model_router_mod  # noqa: E402

_model_router_mod.build_provider_specs = _patched_build_provider_specs  # type: ignore[assignment]

# 2. Extend AUTO_PRIORITY so auto-routing and route_chain() consider the new
#    providers.  Local providers are ordered **Ollama-first** (the project's
#    Ollama-first rule): when no explicit provider is configured, the router
#    prefers the local Ollama daemon and only falls back to LM Studio / Jan
#    when Ollama is unavailable.  This prevents auto-routing from ever landing
#    on a dead LM Studio server (localhost:1234) and surfacing
#    "The local LM Studio model couldn't finish the reply."  Cloud providers
#    that require an API key remain first in the list but are skipped by
#    is_configured() when no key/env secret is present, so Ollama (keyless)
#    is the effective primary local provider.
_extended = list(_orig_auto_priority)
# Remove ollama from its original (last) position — we re-add it FIRST among
# the local providers below so it is preferred over LM Studio / Jan.
_extended = [n for n in _extended if n != "ollama"]
_model_router_mod.AUTO_PRIORITY = _extended + [
    "ollama",
    "lmstudio",
    "jan",
    "cohere",
    "cerebras",
    "perplexity",
    "poolside",
]
# Remove any accidental duplicates while preserving order.
_seen: set[str] = set()
_model_router_mod.AUTO_PRIORITY = [
    n for n in _model_router_mod.AUTO_PRIORITY if not (n in _seen or _seen.add(n))
]

# 3. Update provider_state.PROVIDER_NAMES (a module-level tuple derived from
#    AUTO_PRIORITY at import time) so per-user disconnect lists recognise the
#    new providers.
import app.services.provider_state as _provider_state_mod  # noqa: E402

_provider_state_mod.PROVIDER_NAMES = tuple(_model_router_mod.AUTO_PRIORITY) + (
    "copilot",
    "codex",
)

# 4. Register logo identifiers for the Integrations / Settings UI.
import app.services.integration_registry as _integration_registry_mod  # noqa: E402

_integration_registry_mod.LOGO_FOR_PROVIDER.update(
    {
        "lmstudio": "lmstudio",
        "jan": "jan",
        "cohere": "cohere",
        "cerebras": "cerebras",
        "perplexity": "perplexity",
        "poolside": "poolside",
    }
)

# 5. Patch ``ModelRouter.is_configured`` so providers with a per-user key (set
#    via the context variable by ``chat_service.py``) are treated as available
#    during auto-routing and health checks, even when no env-var key exists.
_orig_is_configured = ModelRouter.is_configured


def _is_configured_patched(self, name: str) -> bool:  # noqa: ANN001
    if name in ("lmstudio", "jan"):
        spec = self.specs.get(name)
        return bool(spec and spec.base_url)
    if _orig_is_configured(self, name):
        return True
    # Per-user key present → treat as configured for this request.
    if name in _user_keys_ctx.get() and name in self.specs:
        return True
    return False


ModelRouter.is_configured = _is_configured_patched  # type: ignore[assignment]

# 6. Patch ``healthcheck`` similarly so the availability endpoint reports a
#    provider with a per-user key as available.
_orig_healthcheck = ModelRouter.healthcheck


async def _healthcheck_patched(self, provider: str):  # noqa: ANN001
    result = await _orig_healthcheck(self, provider)
    if not result.get("available") and provider in _user_keys_ctx.get():
        return {"available": True, "detail": "per-user API key configured"}
    # LM Studio / Jan liveness probe: hit the OpenAI-compatible /models endpoint
    # so we report whether the server is actually running, not just "keyless = ok".
    if provider in ("lmstudio", "jan") and provider in self.specs:
        spec = self.specs[provider]
        if not spec.base_url:
            return {"available": False, "detail": "base URL not configured"}
        try:
            client = get_async_client()
            resp = await client.get(f"{spec.base_url}/models", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in (data.get("data") or [])]
                label = "LM Studio" if provider == "lmstudio" else "Jan"
                return {
                    "available": True,
                    "detail": f"{label} server running | models: {', '.join(models[:8]) or 'none'}",
                    "models": models[:16],
                }
            return {
                "available": False,
                "detail": f"{provider} server returned HTTP {resp.status_code}",
            }
        except Exception as exc:  # noqa: BLE001
            label = "LM Studio" if provider == "lmstudio" else "Jan"
            return {
                "available": False,
                "detail": f"{label} server not running at {spec.base_url} "
                f"({type(exc).__name__})",
            }
    return result


ModelRouter.healthcheck = _healthcheck_patched  # type: ignore[assignment]

# 7. Patch ``provider_state.overview`` so the /api/providers endpoint (in the
#    restricted system.py) also reflects per-user keys when the context variable
#    is set (e.g. from the settings endpoint or any authenticated path that
#    loads the user's prefs).  When the context var is empty this is a no-op.
import app.services.provider_state as _provider_state_mod  # noqa: E402

_orig_overview = _provider_state_mod.overview


def _overview_patched(settings, disabled):  # noqa: ANN001
    result = _orig_overview(settings, disabled)
    # Fix the keyless flag: provider_state.overview() (restricted) hardcodes
    # keyless for Ollama only; extension providers LM Studio and Jan are also
    # keyless (no API key required).
    for name in result:
        if name in ("lmstudio", "jan", "ollama"):
            result[name]["keyless"] = True
    keys = _user_keys_ctx.get()
    if keys:
        for name in keys:
            if name in result and not result[name]["configured"]:
                result[name]["configured"] = True
                result[name]["usable"] = True
    return result


_provider_state_mod.overview = _overview_patched  # type: ignore[assignment]

logger.info(
    "Provider extensions loaded: added %s to AUTO_PRIORITY (total: %d providers)",
    ", ".join(_EXTENDED_PRIORITY),
    len(_model_router_mod.AUTO_PRIORITY),
)

# ---------------------------------------------------------------------------
# (A§ payments & travel) Append Stripe (per-user OAuth) + Amadeus (env-keyed)
# integration cards WITHOUT editing the restricted integration_registry.py
# source.  Stripe reuses the shared ``_oauth_card`` builder so its shape matches
# slack/github/etc.; Amadeus mirrors the telnyx/twilio ``kind="keys"`` shape
# (operator env, no connect button).
# ---------------------------------------------------------------------------
_integration_registry_mod.LOGO_FOR_PROVIDER.update(
    {
        "stripe": "stripe",
        "amadeus": "amadeus",
    }
)

_orig_provider_registry = _integration_registry_mod.provider_registry


def _provider_registry_with_payments_travel(db, user_id, settings=None):
    """Wrapper around the original ``provider_registry`` that appends the
    Stripe + Amadeus cards (A§ payments & travel).  Original cards untouched."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    cards = list(_orig_provider_registry(db, user_id, settings))
    rows = _integration_registry_mod.connected_providers(db, user_id)

    # Stripe — per-user OAuth (Stripe Connect). Reuses the shared OAuth card
    # builder so configured/connected/connectable match the other OAuth
    # providers exactly (is_configured reads JALORA_STRIPE_CLIENT_*).
    cards.extend(
        _integration_registry_mod._oauth_card(
            "stripe", "payments", "Stripe", rows, settings
        )
    )

    # Amadeus — env-keyed (client-credentials), no per-user connect flow.
    amadeus_configured = bool(
        settings.amadeus_client_id and settings.amadeus_client_secret
    )
    cards.append(
        {
            "name": "amadeus",
            "area": "travel",
            "kind": "keys",
            "provider": "amadeus",
            "configured": amadeus_configured,
            "connected": amadeus_configured,
            "keyless": False,
            "connectable": False,
            "logo": "amadeus",
            "description": "Book flights & reservations (Amadeus for Developers).",
        }
    )
    return cards


_integration_registry_mod.provider_registry = _provider_registry_with_payments_travel

logger.info(
    "Provider extensions loaded: appended Stripe (OAuth) + Amadeus "
    "(env-keyed) integration cards."
)
