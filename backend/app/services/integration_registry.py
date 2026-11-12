"""Integration registry (§76).

A single, secret-free view of every external capability the assistant can use,
so the UI can render provider cards with status. Also centralises connect /
disconnect / test behaviour for the personal-assistant providers built in §63-68.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.integration import Integration
from app.services import oauth as oauth_service

# Providers a user can explicitly connect; the rest are keyless/local and are
# always "available" (mock-first).
MOCK_PROVIDERS = {
    "email_mock", "calendar_mock", "drive_mock", "contacts", "maps", "weather",
    "translate", "trello",
}
# Providers that use the real OAuth2 redirect flow (see services/oauth.py).
OAUTH_PROVIDERS = set(oauth_service.OAUTH_SERVICES)

# Mock connects land on the canonical provider so the calendar/drive/email
# services (which key off "calendar", "drive", ...) see them.
_PROVIDER_ALIASES = {
    "calendar_mock": "calendar", "drive_mock": "drive",
    "slack": "slack", "github": "github", "notion": "notion",
    "spotify": "spotify", "trello": "trello", "dropbox": "dropbox",
}

# Logo identifier for each provider — maps to an SVG icon on the frontend.
LOGO_FOR_PROVIDER = {
    "gmail": "gmail", "calendar": "calendar", "drive": "drive",
    "contacts": "contacts", "maps": "maps", "weather": "weather",
    "translate": "translate", "twilio": "twilio", "telnyx": "telnyx",
    "search": "search",
    "ollama": "ollama", "db": "database", "redis": "redis",
    "slack": "slack", "github": "github", "notion": "notion",
    "spotify": "spotify", "trello": "trello", "dropbox": "dropbox",
    "nvidia": "nvidia",
}

SECRET_KEY_RE = (
    "token", "secret", "key", "password", "state", "nonce", "auth",
)
CONFIG_FIELDS = ("email_address", "label", "account", "location")


def _sanitize(config: dict | None) -> dict:
    out = {}
    for k, v in (config or {}).items():
        lowered = k.lower()
        if any(part in lowered for part in SECRET_KEY_RE):
            continue
        if k in CONFIG_FIELDS:
            out[k] = v
    return out


def connected_providers(db: Session, user_id: str) -> dict[str, Integration]:
    rows = db.scalars(select(Integration).where(Integration.user_id == user_id)).all()
    return {r.provider: r for r in rows}


def _db_backend(url: str) -> str:
    if url.startswith("sqlite"):
        return "sqlite"
    if url.startswith("postgres"):
        return "postgres"
    return "other"


def provider_registry(db: Session, user_id: str,
                      settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    rows = connected_providers(db, user_id)

    def status_for(provider: str) -> str:
        row = rows.get(provider)
        return row.status if row else "available"

    return [
        {
            "name": "gmail", "area": "email", "kind": "oauth",
            "provider": "gmail",
            "configured": bool(settings.gmail_client_id and settings.gmail_client_secret),
            "connected": (rows.get("gmail").status == "connected"
                          if rows.get("gmail") else False),
            "keyless": False, "connectable": True,
            "logo": LOGO_FOR_PROVIDER["gmail"],
            "description": "Gmail send/read via OAuth2.",
        },
        {
            "name": "calendar", "area": "calendar", "kind": "oauth",
            "provider": "calendar",
            "configured": oauth_service.is_configured("calendar", settings),
            "connected": (rows["calendar"].status == "connected"
                          if rows.get("calendar") else False),
            "keyless": False, "connectable": True,
            "logo": LOGO_FOR_PROVIDER["calendar"],
            "description": "Google Calendar OAuth2 (built-in mock until connected).",
        },
        {
            "name": "drive", "area": "drive", "kind": "oauth",
            "provider": "drive",
            "configured": oauth_service.is_configured("drive", settings),
            "connected": (rows["drive"].status == "connected"
                          if rows.get("drive") else False),
            "keyless": False, "connectable": True,
            "logo": LOGO_FOR_PROVIDER["drive"],
            "description": "Google Drive OAuth2 (built-in mock until connected).",
        },
        {
            "name": "contacts", "area": "contacts", "kind": "local",
            "provider": settings.contacts_provider or "auto",
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["contacts"],
            "description": "Local address book (feeds SMS/calls/email).",
        },
        {
            "name": "maps", "area": "maps", "kind": "keyless",
            "provider": settings.maps_provider or "auto",
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["maps"],
            "description": "OpenStreetMap + OSRM places/routing.",
        },
        {
            "name": "weather", "area": "weather", "kind": "keyless",
            "provider": settings.weather_provider or "auto",
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["weather"],
            "description": "Open-Meteo forecasts.",
        },
        {
            "name": "translate", "area": "translate", "kind": "provider",
            "provider": settings.translate_provider or "auto",
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["translate"],
            "description": "Provider-based translation (offline mock fallback).",
        },
        {
            "name": "telnyx", "area": "comms", "kind": "keys",
            "provider": "telnyx",
            "configured": bool(settings.telnyx_api_key and settings.telnyx_account_sid),
            "connected": bool(settings.telnyx_api_key and settings.telnyx_account_sid),
            "keyless": False, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["telnyx"],
            "description": "SMS / WhatsApp / voice calls (primary).",
        },
        {
            "name": "twilio", "area": "comms", "kind": "keys",
            "provider": "twilio",
            "configured": bool(settings.twilio_account_sid and settings.twilio_auth_token),
            "connected": bool(settings.twilio_account_sid and settings.twilio_auth_token),
            "keyless": False, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["twilio"],
            "description": "SMS / WhatsApp / voice calls (fallback).",
        },
        {
            "name": "search", "area": "search", "kind": "provider",
            "provider": settings.search_provider or "none",
            "configured": True,
            "connected": (settings.search_provider or "none") not in ("none", ""),
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["search"],
            "description": "Web search provider.",
        },
        {
            "name": "ollama", "area": "llm", "kind": "local",
            "provider": settings.ollama_model,
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["ollama"],
            "description": "Local models via Ollama.",
        },
        {
            "name": "db", "area": "storage", "kind": "local",
            "provider": _db_backend(settings.database_url),
            "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["db"],
            "description": "Primary database.",
        },
        {
            "name": "redis", "area": "queue", "kind": "deferred",
            "provider": settings.env,
            "configured": False, "connected": False,
            "keyless": False, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["redis"],
            "description": "Queue/cache — deferred to production.",
        },
        # --- Additional personal-assistant integrations (§68) ---
        # OAuth-capable providers light up once their client id/secret are set;
        # Trello remains a keyless mock (its API is OAuth1).
        *_oauth_card("slack", "comms", "Slack", rows, settings),
        *_oauth_card("github", "code", "GitHub", rows, settings),
        *_oauth_card("notion", "docs", "Notion", rows, settings),
        *_oauth_card("spotify", "media", "Spotify", rows, settings),
        *_oauth_card("dropbox", "files", "Dropbox", rows, settings),
        {
            "name": "trello", "area": "tasks", "kind": "local",
            "provider": "mock", "configured": True, "connected": True,
            "keyless": True, "connectable": False,
            "logo": LOGO_FOR_PROVIDER["trello"],
            "description": "Trello board & card management (mock-first).",
        },
    ]


def _oauth_card(name: str, area: str, title: str,
                rows: dict[str, Integration], settings: Settings) -> list[dict]:
    row = rows.get(name)
    connected = bool(row and row.status == "connected")
    return [{
        "name": name, "area": area, "kind": "oauth",
        "provider": name,
        "configured": oauth_service.is_configured(name, settings),
        "connected": connected,
        "keyless": False, "connectable": True,
        "logo": LOGO_FOR_PROVIDER.get(name, name),
        "description": f"{title} OAuth2 (mock-first until connected).",
    }]


def is_connectable(provider: str) -> bool:
    provider = (provider or "").strip().lower()
    return provider in MOCK_PROVIDERS or provider in OAUTH_PROVIDERS


def connect(db: Session, *, user_id: str, provider: str,
            config: dict | None = None) -> Integration:
    """Connect a mock/keyless provider directly.

    OAuth providers must go through the redirect flow (``begin_oauth``); this
    raises a clear error instead of silently parking the row in "disabled".
    """
    provider = (provider or "").strip().lower()
    canonical = _PROVIDER_ALIASES.get(provider, provider)
    wants_mock = provider.endswith("_mock") or provider in MOCK_PROVIDERS
    if canonical in OAUTH_PROVIDERS and not wants_mock:
        from app.errors import BadRequestError

        raise BadRequestError(
            f"{provider} uses OAuth; start the authorization flow instead.",
            "OAUTH_FLOW_REQUIRED",
        )
    row = db.scalar(select(Integration).where(
        Integration.user_id == user_id, Integration.provider == canonical))
    if row is None:
        row = Integration(user_id=user_id, provider=canonical)
        db.add(row)
    if wants_mock:
        row.status = "connected"
        row.config = {**_sanitize(config), **(row.config or {})}
        row.error = None
    else:
        from app.errors import BadRequestError

        raise BadRequestError(
            f"Unknown integration provider '{provider}'.", "UNKNOWN_PROVIDER")
    db.commit()
    db.refresh(row)
    return row


def begin_oauth(db: Session, *, user_id: str, provider: str,
                settings: Settings | None = None) -> tuple[Integration, str]:
    """Create/find the pending row and return it with its authorization URL.

    Stores only the SHA-256 of the CSRF ``state`` (never the state itself, §34).
    """
    import hmac as _hmac  # noqa: F401  (kept for parity with callback verify)

    from app.errors import BadRequestError

    settings = settings or get_settings()
    provider = (provider or "").strip().lower()
    if not oauth_service.is_oauth_provider(provider):
        raise BadRequestError(f"'{provider}' is not an OAuth provider.",
                              "NOT_OAUTH_PROVIDER")
    if not oauth_service.is_configured(provider, settings):
        raise BadRequestError(
            f"{oauth_service.display_name(provider)} OAuth is not configured. Set "
            f"JALORA_{provider.upper()}_CLIENT_ID and "
            f"JALORA_{provider.upper()}_CLIENT_SECRET, then retry.",
            "PROVIDER_NOT_CONFIGURED",
        )
    row = db.scalar(select(Integration).where(
        Integration.user_id == user_id, Integration.provider == provider))
    if row is None:
        row = Integration(user_id=user_id, provider=provider)
        db.add(row)
    from app.services.email import new_oauth_state, oauth_state_hash

    state = new_oauth_state()
    cfg = dict(row.config or {})
    cfg["oauth_state_hash"] = oauth_state_hash(state)
    row.config = cfg
    row.scopes = list(oauth_service.OAUTH_SERVICES[provider][1])
    if row.status != "connected":
        row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    url = oauth_service.authorization_url(provider, state, settings=settings)
    return row, url


def test_connection(db: Session, *, user_id: str, provider: str) -> dict:
    """Synchronous probe for the personal-assistant providers (§63-68).

    Email providers are handled by the email service in the API layer.
    """
    row = db.scalar(select(Integration).where(
        Integration.user_id == user_id, Integration.provider == provider))
    if row is None:
        return {"ok": False, "provider": provider, "status": "missing",
                "detail": f"No {provider} integration for this user."}
    ok = row.status == "connected"
    return {"ok": ok, "provider": provider, "status": row.status,
            "detail": f"{provider} is {row.status}."}
