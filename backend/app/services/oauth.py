"""Generic OAuth2 authorization-code framework for integrations (§34-38, §68).

A single implementation of the browser redirect flow used by every
OAuth-capable provider. Each integration declares which upstream OAuth service
it uses plus its scopes; the upstream endpoints live in a small static registry
and the client credentials are resolved from Settings (env
``JALORA_<PROVIDER>_CLIENT_ID/SECRET`` — Google reuses the Gmail client).

A provider is *configured* only when both its client id and secret are present;
otherwise the UI shows "Add credentials to connect" and the Connect endpoint
returns a clear error. Tokens are stored Fernet-encrypted on the Integration row
(`access_token` / `refresh_token` / `token_expires_at`), matching the Gmail and
Calendar adapters that read them back.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.integration import Integration
from app.services.credential_store import fernet_decrypt, fernet_encrypt

logger = logging.getLogger("jalora.oauth")


class OAuthError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProviderEndpoints:
    """Upstream OAuth service endpoints + defaults (provider-agnostic)."""

    auth_url: str
    token_url: str
    extra_auth_params: dict = field(default_factory=dict)
    token_auth_style: str = "body"  # body | basic
    # Google-style flows accept offline access via these params.
    userinfo_url: str | None = None


_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

_SERVICES: dict[str, ProviderEndpoints] = {
    "google": ProviderEndpoints(
        _GOOGLE_AUTH_URL, _GOOGLE_TOKEN_URL,
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
        userinfo_url="https://www.googleapis.com/oauth2/v2/userinfo",
    ),
    "slack": ProviderEndpoints(
        "https://slack.com/oauth/v2/authorize",
        "https://slack.com/api/oauth.v2.access",
    ),
    "github": ProviderEndpoints(
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
    ),
    "notion": ProviderEndpoints(
        "https://api.notion.com/v1/oauth/authorize",
        "https://api.notion.com/v1/oauth/token",
        token_auth_style="basic",
    ),
    "spotify": ProviderEndpoints(
        "https://accounts.spotify.com/authorize",
        "https://accounts.spotify.com/api/token",
        token_auth_style="basic",
        userinfo_url="https://api.spotify.com/v1/me",
    ),
    "dropbox": ProviderEndpoints(
        "https://www.dropbox.com/oauth2/authorize",
        "https://www.dropbox.com/oauth2/token",
        extra_auth_params={"token_access_type": "offline"},
    ),
    # Stripe Connect (§76): per-user OAuth2 authorization-code flow. Tokens are
    # long-lived (no refresh token), so store_tokens marks them far-future.
    "stripe": ProviderEndpoints(
        "https://connect.stripe.com/oauth/authorize",
        "https://connect.stripe.com/oauth/token",
        token_auth_style="body",
    ),
}

# Integration provider -> (upstream service, scopes).
OAUTH_SERVICES: dict[str, tuple[str, tuple[str, ...]]] = {
    "gmail": ("google", (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    )),
    "calendar": ("google", (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )),
    "drive": ("google", ("https://www.googleapis.com/auth/drive.readonly",)),
    "slack": ("slack", ("channels:read", "chat:write", "users:read")),
    "github": ("github", ("read:user", "repo")),
    "notion": ("notion", ()),
    "spotify": ("spotify", ("user-read-email", "playlist-read-private")),
    "dropbox": ("dropbox", ("files.metadata.read", "files.content.read")),
    "stripe": ("stripe", ("read_write",)),
}

_DISPLAY_NAMES = {
    "gmail": "Gmail", "calendar": "Google Calendar", "drive": "Google Drive",
    "slack": "Slack", "github": "GitHub", "notion": "Notion",
    "spotify": "Spotify", "dropbox": "Dropbox", "stripe": "Stripe",
}


def is_oauth_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in OAUTH_SERVICES


def display_name(provider: str) -> str:
    return _DISPLAY_NAMES.get(provider, provider.title())


def _credentials(provider: str, settings: Settings) -> tuple[str, str]:
    table = {
        "gmail": (settings.gmail_client_id, settings.gmail_client_secret),
        "calendar": (settings.gmail_client_id, settings.gmail_client_secret),
        "drive": (settings.gmail_client_id, settings.gmail_client_secret),
        "slack": (settings.slack_client_id, settings.slack_client_secret),
        "github": (settings.github_client_id, settings.github_client_secret),
        "notion": (settings.notion_client_id, settings.notion_client_secret),
        "spotify": (settings.spotify_client_id, settings.spotify_client_secret),
        "dropbox": (settings.dropbox_client_id, settings.dropbox_client_secret),
        "stripe": (settings.stripe_client_id, settings.stripe_client_secret),
    }
    cid, secret = table.get(provider, (None, None))
    return (cid or "", secret or "")


def is_configured(provider: str, settings: Settings | None = None) -> bool:
    if not is_oauth_provider(provider):
        return False
    cid, secret = _credentials(provider, settings or get_settings())
    return bool(cid and secret)


def redirect_uri(settings: Settings | None = None) -> str:
    return (settings or get_settings()).oauth_callback_url


def authorization_url(provider: str, state: str,
                      settings: Settings | None = None) -> str:
    provider = (provider or "").strip().lower()
    if provider == "gmail":
        # Gmail keeps its dedicated, already-working redirect endpoint.
        from app.services import email as email_service

        return email_service.gmail_authorization_url(state)
    settings = settings or get_settings()
    if not is_configured(provider, settings):
        raise OAuthError(
            "PROVIDER_NOT_CONFIGURED",
            f"{display_name(provider)} OAuth is not configured. Set "
            f"JALORA_{provider.upper()}_CLIENT_ID and "
            f"JALORA_{provider.upper()}_CLIENT_SECRET in the environment.",
        )
    service, scopes = OAUTH_SERVICES[provider]
    endpoints = _SERVICES[service]
    cid, _ = _credentials(provider, settings)
    params: dict[str, str] = {
        "client_id": cid,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",
        "state": state,
        **endpoints.extra_auth_params,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{endpoints.auth_url}?{urlencode(params)}"


async def _token_request(provider: str, data: dict, *,
                         settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    cid, secret = _credentials(provider, settings)
    if not (cid and secret):
        raise OAuthError(
            "PROVIDER_NOT_CONFIGURED",
            f"{display_name(provider)} OAuth is not configured (set "
            f"JALORA_{provider.upper()}_CLIENT_ID and "
            f"JALORA_{provider.upper()}_CLIENT_SECRET).",
        )
    service, _ = OAUTH_SERVICES[provider]
    endpoints = _SERVICES[service]
    headers = {"User-Agent": "JaloraAI/0.1", "Accept": "application/json"}
    payload = dict(data)
    if endpoints.token_auth_style == "basic":
        token = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    else:
        payload["client_id"] = cid
        payload["client_secret"] = secret
    # redirect_uri must mirror the authorization request for code exchange.
    if payload.get("grant_type") == "authorization_code" and provider != "gmail":
        payload["redirect_uri"] = redirect_uri(settings)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(endpoints.token_url, data=payload, headers=headers)
    if resp.status_code >= 400:
        raise OAuthError(
            "PROVIDER_ERROR",
            f"{display_name(provider)} token endpoint HTTP {resp.status_code}: "
            f"{resp.text[:300]}",
        )
    try:
        return resp.json()
    except ValueError as exc:  # non-JSON error body
        raise OAuthError("PROVIDER_ERROR",
                         f"{display_name(provider)} returned a non-JSON token response.") from exc


async def exchange_code(provider: str, code: str, *,
                        settings: Settings | None = None) -> dict:
    return await _token_request(provider, {"code": code,
                                           "grant_type": "authorization_code"},
                                settings=settings)


async def refresh_access(provider: str, refresh_token: str, *,
                         settings: Settings | None = None) -> dict:
    return await _token_request(provider, {"refresh_token": refresh_token,
                                           "grant_type": "refresh_token"},
                                settings=settings)


async def fetch_account(provider: str, access_token: str) -> str:
    """Best-effort human label for the connected account (never fatal)."""
    if not is_oauth_provider(provider):
        return ""
    service, _ = OAUTH_SERVICES[provider]
    endpoints = _SERVICES[service]
    if not endpoints.userinfo_url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                endpoints.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}",
                         "User-Agent": "JaloraAI/0.1",
                         "Accept": "application/json"},
            )
        if resp.status_code >= 400:
            return ""
        data = resp.json()
    except Exception:  # noqa: BLE001 - account label is cosmetic
        logger.debug("account lookup failed for %s", provider)
        return ""
    for key in ("email", "login", "id", "account_id", "display_name"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def store_tokens(db: Session, integration: Integration, payload: dict,
                 account: str = "") -> Integration:
    """Persist Fernet-encrypted tokens on the Integration row (§34)."""
    access = payload.get("access_token")
    if not access:
        raise OAuthError("PROVIDER_ERROR",
                         f"{display_name(integration.provider)} did not return an "
                         "access token.")
    cfg = dict(integration.config or {})
    cfg["access_token"] = fernet_encrypt(access)
    refresh = payload.get("refresh_token")
    if refresh:
        cfg["refresh_token"] = fernet_encrypt(refresh)
    elif "refresh_token" not in cfg:
        cfg["refresh_token"] = fernet_encrypt("missing")
    # Stripe Connect tokens are long-lived and have no refresh token; mark them
    # far-future so ensure_access_token never attempts a refresh.
    if integration.provider == "stripe":
        cfg["token_expires_at"] = time.time() + 30 * 365 * 86400
    else:
        cfg["token_expires_at"] = time.time() + int(payload.get("expires_in", 3600))
    cfg.pop("oauth_state_hash", None)
    if account:
        cfg["account"] = account
        cfg.setdefault("email_address", account)
    integration.config = cfg
    integration.status = "connected"
    integration.error = None
    db.commit()
    db.refresh(integration)
    return integration


async def ensure_access_token(db: Session, integration: Integration) -> str:
    """Return a valid access token, refreshing it in place when expired."""
    cfg = dict(integration.config or {})
    expires = cfg.get("token_expires_at") or 0
    if cfg.get("access_token") and expires > time.time() + 30:
        return fernet_decrypt(cfg["access_token"])
    plain_refresh = fernet_decrypt(cfg.get("refresh_token", ""))
    if not plain_refresh or plain_refresh == "missing":
        raise OAuthError("PROVIDER_ERROR",
                         f"{display_name(integration.provider)} is not connected "
                         "(missing refresh token); reconnect the integration.")
    payload = await refresh_access(integration.provider, plain_refresh)
    access = payload.get("access_token")
    if not access:
        raise OAuthError("PROVIDER_ERROR",
                         f"{display_name(integration.provider)} token refresh failed.")
    new_cfg = dict(cfg)
    new_cfg["access_token"] = fernet_encrypt(access)
    new_cfg["token_expires_at"] = time.time() + int(payload.get("expires_in", 3600))
    integration.config = new_cfg
    db.commit()
    return access
