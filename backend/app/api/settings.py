from __future__ import annotations

import hmac
import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.errors import BadRequestError, ForbiddenError, JaloraError, NotFoundError
from app.models.integration import Integration
from app.models.user import User
from app.schemas import (
    SettingsOut, SettingsPatch, VoiceSettings,
    ProviderKeyPatch, ProviderKeyOut,
    ModelsResponse, ModelInfo,
    CustomIntegrationCreate, CustomIntegrationOut,
    IntegrationTestOut,
)
from app.services import credential_store
from app.services import email as email_service
from app.services.policy import (
    AUTONOMY_LEVELS,
    DEFAULT_AUTONOMY,
    VALID_POLICIES,
    normalize_autonomy,
    normalize_tool_policies,
)
from app.services.provider_state import PROVIDER_NAMES, normalize_disabled
from app.services.provider_extensions import (
    set_active_user_keys,
    set_active_user_key_values,
)
from app.database.session import SessionLocal
from app.tools.base import registry

import httpx

logger = logging.getLogger("jalora.settings")

router = APIRouter(prefix="/api", tags=["settings"])

# Providers that require an API key but have no key configured via the
# environment (server-wide). Users can supply their own per-user key.
KEY_REQUIRED_PROVIDERS = {"mistral", "cohere", "cerebras", "perplexity", "openai",
                          "anthropic", "google", "deepseek", "groq", "openrouter",
                          "poolside", "copilot", "codex"}

# Dashboard URLs for keyless/open providers so the UI can link to usage pages.
PROVIDER_DASHBOARD_URLS = {
    "poolside": "https://platform.poolside.ai/",
    "copilot": "https://github.com/settings/tokens",
    "codex": "https://platform.openai.com/api-keys",
    "lmstudio": "http://localhost:1234",
    "ollama": "http://localhost:11434",
    "jan": None,  # Jan has no standard dashboard URL; user configures manually
}


def _manifest() -> list[dict]:
    return registry.manifest()


def _voice_prefs(raw: dict | None) -> VoiceSettings:
    """Normalise the stored voice bundle (Phase H §31-33) with safe defaults."""
    raw = raw or {}
    return VoiceSettings(
        enabled=bool(raw.get("enabled", False)),
        auto_speak=bool(raw.get("auto_speak", True)),
        continuous=bool(raw.get("continuous", False)),
        voice=str(raw.get("voice", ""))[:120],
    )


def _settings_out(prefs: dict) -> SettingsOut:
    provider_keys = sorted(
        k for k in (prefs.get("provider_keys") or {}) if isinstance(k, str)
    )
    return SettingsOut(
        autonomy=normalize_autonomy(prefs.get("autonomy")),
        tool_policies=normalize_tool_policies(prefs.get("tool_policies")),
        memory_enabled=bool(prefs.get("memory_enabled", get_settings().memory_enabled)),
        memory_retention_days=prefs.get("memory_retention_days"),
        locale=str(prefs.get("locale", "en")),
        voice=_voice_prefs(prefs.get("voice")),
        tools=_manifest(),
        response_length=str(prefs.get("response_length", "balanced")),
        tone=str(prefs.get("tone", "professional")),
        timezone=str(prefs.get("timezone", "Africa/Nairobi")),
        preferred_provider=str(prefs.get("preferred_provider", "auto")),
        disabled_providers=normalize_disabled(prefs.get("disabled_providers")),
        provider_keys=provider_keys,
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings_api(user: User = Depends(get_current_user)):
    return _settings_out(user.prefs or {})


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(body: SettingsPatch, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    prefs = dict(user.prefs or {})

    if body.autonomy is not None:
        if body.autonomy not in AUTONOMY_LEVELS:
            raise BadRequestError(
                f"autonomy must be one of {sorted(AUTONOMY_LEVELS)}.",
                "INVALID_AUTONOMY",
            )
        prefs["autonomy"] = int(body.autonomy)

    if body.tool_policies is not None:
        known = {spec.name for spec in registry.all()}
        for name, value in body.tool_policies.items():
            if name not in known:
                raise BadRequestError(f"Unknown tool '{name}'.", "UNKNOWN_TOOL")
            if value not in VALID_POLICIES:
                raise BadRequestError(
                    f"Policy for '{name}' must be one of {sorted(VALID_POLICIES)}.",
                    "INVALID_POLICY",
                )
        prefs["tool_policies"] = dict(body.tool_policies)

    if body.memory_enabled is not None:
        prefs["memory_enabled"] = bool(body.memory_enabled)
    if "memory_retention_days" in body.model_fields_set:
        prefs["memory_retention_days"] = body.memory_retention_days
    if body.locale is not None:
        prefs["locale"] = body.locale.lower()
    if body.voice is not None:
        current = dict(prefs.get("voice") or {})
        current.update(body.voice.model_dump(exclude_unset=True))
        prefs["voice"] = current

    # Phase K (§27, §70-72): preference fields
    for key in ("response_length", "tone", "timezone", "preferred_provider"):
        val = getattr(body, key)
        if val is not None:
            prefs[key] = val

    if body.disabled_providers is not None:
        unknown = [
            str(item).strip().lower()
            for item in body.disabled_providers
            if str(item).strip().lower() not in PROVIDER_NAMES
        ]
        if unknown:
            raise BadRequestError(
                f"Unknown provider(s): {', '.join(unknown)}.", "UNKNOWN_PROVIDER"
            )
        prefs["disabled_providers"] = normalize_disabled(body.disabled_providers)

    user.prefs = prefs
    db.commit()
    return _settings_out(prefs)


# ---------------------------------------------------------------------------
# Per-user provider API keys (spec §provider-keys) — encrypted at rest.
# These let a user connect a cloud provider from inside the app instead of
# requiring every key to come from the server's environment.
# ---------------------------------------------------------------------------

@router.get("/settings/provider-keys", response_model=list[ProviderKeyOut])
def list_provider_keys(user: User = Depends(get_current_user)):
    """List provider names + dashboard URLs for which the user has stored a key,
    plus keyless local providers that have an open dashboard link."""
    stored = sorted(
        k for k in (user.prefs or {}).get("provider_keys") or {} if isinstance(k, str)
    )
    out = [
        ProviderKeyOut(provider=name, url=PROVIDER_DASHBOARD_URLS.get(name))
        for name in stored
    ]
    return out


@router.post("/settings/provider-key")
def save_provider_key(
    body: ProviderKeyPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save (or replace) a per-user API key for a provider.

    The key is encrypted with the server's Fernet key before it touches the
    database and is never echoed back. Users obtain keys from the provider's
    own dashboard (e.g. console.mistral.ai) and paste them here."""
    if body.provider not in PROVIDER_NAMES:
        raise BadRequestError(
            f"Unknown provider '{body.provider}'.", "UNKNOWN_PROVIDER",
        )
    if body.provider not in KEY_REQUIRED_PROVIDERS:
        raise BadRequestError(
            f"Provider '{body.provider}' is keyless or configured via the "
            f"environment and cannot be overridden per-user.",
            "KEYLESS_PROVIDER",
        )
    prefs = dict(user.prefs or {})
    keys = dict(prefs.get("provider_keys") or {})
    keys[body.provider] = credential_store.fernet_encrypt(body.api_key)
    prefs["provider_keys"] = keys
    user.prefs = prefs
    db.commit()
    return {"success": True, "provider": body.provider,
            "message": f"API key saved for {body.provider}."}


@router.delete("/settings/provider-key/{provider}")
def delete_provider_key(
    provider: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a per-user API key, reverting to env-var / disconnect behaviour."""
    if provider not in PROVIDER_NAMES:
        raise BadRequestError(
            f"Unknown provider '{provider}'.", "UNKNOWN_PROVIDER",
        )
    prefs = dict(user.prefs or {})
    keys = dict(prefs.get("provider_keys") or {})
    if provider not in keys:
        return {"success": True, "provider": provider,
                "message": "No per-user key was stored for this provider."}
    del keys[provider]
    prefs["provider_keys"] = keys
    user.prefs = prefs
    db.commit()
    return {"success": True, "provider": provider,
            "message": f"Per-user key removed for {provider}."}


# ---------------------------------------------------------------------------
# Local-provider model listing (spec §lmstudio-models)
# Proxy to a local provider's /models endpoint so the Settings UI can show the
# available models and let the user pick one to load.
# ---------------------------------------------------------------------------

@router.get("/settings/lmstudio/models", response_model=ModelsResponse)
def list_lmstudio_models(
    user: User = Depends(get_current_user),
):
    """Fetch the list of loaded models from the LM Studio server.

    Returns an empty list (with ``available=False`` and a helpful error) when
    the LM Studio server isn't running, so the UI can prompt the user to start
    it instead of crashing."""
    settings = get_settings()
    base_url = settings.lmstudio_base_url.rstrip("/")
    try:
        resp = httpx.get(f"{base_url}/models", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        raw_models = data.get("data", []) if isinstance(data, dict) else data
        models = [
            ModelInfo(
                id=str(m.get("id", "")),
                name=str(m.get("name", "") or m.get("id", "")),
                size=m.get("size"),
                format=m.get("format") or m.get("quantitative_file_quantization"),
                modified_at=m.get("modified_at"),
                details=m if isinstance(m, dict) else None,
            )
            for m in raw_models
        ]
        return ModelsResponse(
            provider="lmstudio",
            models=models,
            available=True,
        )
    except httpx.HTTPError as exc:
        logger.warning("LM Studio models endpoint failed: %s", exc)
        return ModelsResponse(
            provider="lmstudio",
            models=[],
            available=False,
            error="LM Studio server is not running or unreachable. "
                  "Start LM Studio and reload this page.",
        )


@router.get("/settings/providers/availability", response_model=dict)
def provider_availability_for_user(
    user: User = Depends(get_current_user),
):
    """Per-user provider availability.

    Like ``/providers/availability`` but injects the user's per-provider API
    keys (from the Settings → Connect dialog) before probing, so providers the
    user personally configured are correctly reported as available even when
    the server-wide env key is missing."""
    settings = get_settings()
    prefs = dict(user.prefs or {})
    _load_keys = _load_user_provider_keys(prefs)
    set_active_user_keys(frozenset(_load_keys))
    set_active_user_key_values(_load_keys)
    try:
        from app.services.provider_state import overview as _overview
        disabled = normalize_disabled(prefs.get("disabled_providers", []))
        result = _overview(settings, disabled)
        # Inject per-user key availability flags
        stored_keys = prefs.get("provider_keys") or {}
        for name in PROVIDER_NAMES:
            if name in stored_keys and name in result:
                result[name]["has_user_key"] = True
        return result
    finally:
        set_active_user_keys(frozenset())
        set_active_user_key_values({})


def _load_user_provider_keys(prefs: dict) -> dict[str, str]:
    """Same logic as chat_service._load_user_provider_keys but standalone."""
    raw = prefs.get("provider_keys") or {}
    out: dict[str, str] = {}
    for name, token in raw.items():
        if not isinstance(token, str):
            continue
        try:
            out[name] = credential_store.fernet_decrypt(token)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# Custom integrations (any OAuth2 app on phone/laptop)
# Users supply their own OAuth2 endpoints + credentials; the server stores them
# encrypted-at-rest and drives the authorization-code flow on the user's behalf.
# ---------------------------------------------------------------------------

def _custom_provider_id(name: str) -> str:
    """Generate a unique provider id for a custom integration."""
    safe = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    return f"custom_{safe}"


def _serialize_custom(row: Integration) -> CustomIntegrationOut:
    cfg = row.config or {}
    return CustomIntegrationOut(
        id=row.id,
        provider=row.provider,
        name=cfg.get("custom_name", row.provider),
        status=row.status,
        scopes=list(row.scopes or []),
        error=row.error,
    )


def _spa_redirect(**params: str) -> RedirectResponse:
    """Send the browser back to the SPA with a status the UI can toast."""
    base = get_settings().public_base_url.rstrip("/")
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(f"{base}/?{query}", status_code=302)


def _custom_callback_url() -> str:
    """The Jalora backend OAuth callback URL for custom integrations.

    All custom integrations redirect back to this single endpoint; the
    ``integration_id`` is NOT in the URL (OAuth providers won't forward it).
    Instead the CSRF ``state`` token uniquely identifies the integration.
    """
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/integrations/custom/callback"


@router.get("/integrations/custom", response_model=list[CustomIntegrationOut])
def list_custom_integrations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all custom (user-registered) OAuth2 integrations for the current user."""
    rows = db.scalars(
        select(Integration).where(
            Integration.user_id == user.id,
            Integration.provider.like("custom_%"),
        ).order_by(Integration.provider)
    ).all()
    return [_serialize_custom(r) for r in rows]


@router.post("/integrations/custom", response_model=CustomIntegrationOut)
def create_custom_integration(
    body: CustomIntegrationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a new custom OAuth2 integration.

    The user supplies the OAuth2 authorization URL, token URL, client id,
    client secret, redirect URI and scopes. The secret is encrypted at rest.
    The resulting integration starts in "disabled" status; the user clicks
    Connect to begin the OAuth authorization-code flow.
    """
    provider = _custom_provider_id(body.name)
    if not provider or provider == "custom_":
        raise BadRequestError(
            "Integration name must contain at least one alphanumeric character.",
            "INVALID_NAME",
        )
    # Check for duplicate
    existing = db.scalar(
        select(Integration).where(
            Integration.user_id == user.id, Integration.provider == provider
        )
    )
    if existing is not None:
        raise BadRequestError(
            f"A custom integration named '{body.name}' already exists.",
            "DUPLICATE_INTEGRATION",
        )
    cfg: dict[str, Any] = {
        "custom_name": body.name,
        "custom_auth_url": body.auth_url,
        "custom_token_url": body.token_url,
        "custom_client_id": body.client_id,
        "custom_client_secret_enc": credential_store.fernet_encrypt(body.client_secret),
        "custom_redirect_uri": body.redirect_uri,
        "custom_scopes": list(body.scopes),
        "custom_token_auth_style": body.token_auth_style,
    }
    if body.userinfo_url:
        cfg["custom_userinfo_url"] = body.userinfo_url
    if body.extra_auth_params:
        cfg["custom_extra_auth_params"] = body.extra_auth_params

    row = Integration(
        user_id=user.id,
        provider=provider,
        status="disabled",
        config=cfg,
        scopes=list(body.scopes),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_custom(row)


@router.post("/integrations/custom/{integration_id}/connect")
def connect_custom_integration(
    integration_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start the OAuth2 authorization-code flow for a custom integration."""
    row = db.scalar(
        select(Integration).where(
            Integration.user_id == user.id, Integration.id == integration_id
        )
    )
    if row is None or not row.provider.startswith("custom_"):
        raise NotFoundError("Custom integration not found.")
    cfg = row.config or {}
    state = email_service.new_oauth_state()
    cfg["oauth_state_hash"] = email_service.oauth_state_hash(state)
    row.config = cfg
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)

    params: dict[str, str] = {
        "client_id": cfg.get("custom_client_id", ""),
        "redirect_uri": _custom_callback_url(),
        "response_type": "code",
        "state": state,
    }
    extra = cfg.get("custom_extra_auth_params") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            params[str(k)] = str(v)
    scopes = cfg.get("custom_scopes") or list(row.scopes or [])
    if scopes:
        params["scope"] = " ".join(scopes)
    auth_url = cfg.get("custom_auth_url", "")
    url = f"{auth_url}?{urlencode(params)}" if auth_url else ""
    return {
        "status": "redirect",
        "provider": row.provider,
        "integration_id": row.id,
        "authorization_url": url,
    }


@router.get("/integrations/custom/callback")
async def custom_oauth_callback(
    request: Request,
    provider: str = Query(""),
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
):
    """OAuth2 callback handler for custom integrations.

    Verifies the CSRF ``state`` before exchanging the code, then stores the
    encrypted tokens on the Integration row. Browsers are redirected back to
    the SPA with a status query param; API clients get JSON.
    """
    browser = "text/html" in request.headers.get("accept", "").lower()

    if error:
        if browser:
            return _spa_redirect(integration="error",
                                 provider=provider or "provider", reason=error)
        raise JaloraError(400, "OAUTH_ERROR",
                          f"{provider or 'provider'} OAuth failed: {error}")
    if not code:
        if browser:
            return _spa_redirect(integration="error",
                                 provider=provider or "provider",
                                 reason="missing_code")
        raise BadRequestError("Missing OAuth authorization code.", "MISSING_CODE")
    if not state:
        if browser:
            return _spa_redirect(integration="error",
                                 provider=provider or "provider",
                                 reason="missing_state")
        raise BadRequestError("Missing OAuth state.", "MISSING_STATE")

    db = SessionLocal()
    try:
        target = email_service.oauth_state_hash(state)
        # OAuth providers only send ``code`` + ``state`` back; ``provider`` is
        # not included, so we must discover the integration by matching the
        # CSRF state hash against pending custom-integration rows.
        rows = db.scalars(select(Integration).where(
            Integration.provider.like("custom_%"),
            Integration.status != "connected",
        )).all()
        row = None
        for candidate in rows:
            stored_hash = str(
                (candidate.config or {}).get("oauth_state_hash", ""))
            if stored_hash and hmac.compare_digest(stored_hash, target):
                row = candidate
                break
        if row is None:
            raise BadRequestError(
                "No pending custom OAuth flow found; start a new connection.",
                "NO_PENDING_FLOW",
            )
        stored_hash = str((row.config or {}).get("oauth_state_hash", ""))
        if not stored_hash or not hmac.compare_digest(stored_hash, target):
            raise ForbiddenError(
                "OAuth state does not match; the flow may have been tampered with.",
                "INVALID_STATE",
            )

        cfg = row.config or {}
        token_url = cfg.get("custom_token_url", "")
        client_id = cfg.get("custom_client_id", "")
        client_secret_enc = cfg.get("custom_client_secret_enc", "")
        token_auth_style = cfg.get("custom_token_auth_style", "body")

        token_data: dict[str, Any] = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _custom_callback_url(),
        }
        headers = {"User-Agent": "JaloraAI/0.1", "Accept": "application/json"}
        if token_auth_style == "basic":
            import base64 as _b64
            cred = _b64.b64encode(
                f"{client_id}:{credential_store.fernet_decrypt(client_secret_enc)}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {cred}"
        else:
            token_data["client_id"] = client_id
            token_data["client_secret"] = credential_store.fernet_decrypt(client_secret_enc)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=token_data, headers=headers)
        if resp.status_code >= 400:
            raise JaloraError(
                400, "TOKEN_EXCHANGE_FAILED",
                f"Token endpoint HTTP {resp.status_code}: {resp.text[:300]}",
            )
        payload = resp.json()

        access_token = payload.get("access_token")
        if not access_token:
            raise JaloraError(400, "NO_ACCESS_TOKEN",
                              "OAuth provider did not return an access token.")

        new_cfg = dict(cfg)
        new_cfg["access_token"] = credential_store.fernet_encrypt(access_token)
        refresh = payload.get("refresh_token")
        if refresh:
            new_cfg["refresh_token"] = credential_store.fernet_encrypt(refresh)
        elif "refresh_token" not in new_cfg:
            new_cfg["refresh_token"] = credential_store.fernet_encrypt("missing")
        import time as _time
        new_cfg["token_expires_at"] = _time.time() + int(payload.get("expires_in", 3600))
        new_cfg.pop("oauth_state_hash", None)

        row.config = new_cfg
        row.status = "connected"
        row.error = None
        db.commit()
        db.refresh(row)

        if browser:
            return _spa_redirect(integration="connected",
                                 provider=row.provider,
                                 name=row.config.get("custom_name", ""))
        return {"success": True, "provider": row.provider,
                "status": "connected", "integration_id": row.id}
    except (JaloraError, BadRequestError, ForbiddenError) as exc:
        row = locals().get("row")
        if row is not None and db.is_active:
            row.error = str(getattr(exc, "detail", exc))
            row.status = "error"
            db.commit()
        if browser:
            return _spa_redirect(integration="error",
                                 provider=provider or "provider",
                                 reason=str(getattr(exc, "code", "oauth_error")))
        raise
    finally:
        db.close()


@router.post("/integrations/custom/{integration_id}/test",
             response_model=IntegrationTestOut)
async def test_custom_integration(
    integration_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Test a custom OAuth2 integration by refreshing its access token."""
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Custom integration not found.")
    if not row.provider.startswith("custom_"):
        raise BadRequestError("Not a custom integration.", "NOT_CUSTOM")
    cfg = row.config or {}
    refresh_enc = cfg.get("refresh_token")
    if not refresh_enc:
        raise BadRequestError(
            "No refresh token stored; connect this integration first.",
            "NOT_CONNECTED",
        )
    plain_refresh = credential_store.fernet_decrypt(refresh_enc)
    if not plain_refresh or plain_refresh == "missing":
        raise BadRequestError(
            "No refresh token available for this integration.",
            "NO_REFRESH_TOKEN",
        )
    token_url = cfg.get("custom_token_url", "")
    client_id = cfg.get("custom_client_id", "")
    client_secret_enc = cfg.get("custom_client_secret_enc", "")
    token_auth_style = cfg.get("custom_token_auth_style", "body")

    token_data: dict[str, Any] = {
        "refresh_token": plain_refresh,
        "grant_type": "refresh_token",
    }
    headers = {"User-Agent": "JaloraAI/0.1", "Accept": "application/json"}
    if token_auth_style == "basic":
        import base64 as _b64
        cred = _b64.b64encode(
            f"{client_id}:{credential_store.fernet_decrypt(client_secret_enc)}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {cred}"
    else:
        token_data["client_id"] = client_id
        token_data["client_secret"] = credential_store.fernet_decrypt(client_secret_enc)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=token_data, headers=headers)
        if resp.status_code >= 400:
            raise BadRequestError(
                f"Token refresh failed: HTTP {resp.status_code} {resp.text[:300]}",
                "TOKEN_REFRESH_FAILED",
            )
        payload = resp.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise BadRequestError(
                "Token refresh did not return an access token.",
                "NO_ACCESS_TOKEN",
            )
        new_cfg = dict(cfg)
        new_cfg["access_token"] = credential_store.fernet_encrypt(access_token)
        new_cfg["token_expires_at"] = __import__("time").time() + int(
            payload.get("expires_in", 3600))
        row.config = new_cfg
        row.status = "connected"
        row.error = None
        db.commit()
        db.refresh(row)
        return IntegrationTestOut(
            ok=True, provider=row.provider, status="connected",
            detail="Connection verified — token refreshed successfully.",
        )
    except httpx.HTTPError as exc:
        return IntegrationTestOut(
            ok=False, provider=row.provider, status="error",
            detail=f"Network error: {exc}",
        )


@router.delete("/integrations/custom/{integration_id}", status_code=204)
def delete_custom_integration(
    integration_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a custom integration (removes encrypted credentials too)."""
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Custom integration not found.")
    if not row.provider.startswith("custom_"):
        raise BadRequestError("Not a custom integration.", "NOT_CUSTOM")
    db.delete(row)
    db.commit()
