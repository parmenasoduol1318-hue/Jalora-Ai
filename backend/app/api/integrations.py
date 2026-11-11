"""Integration lifecycle + Gmail OAuth + inbound webhook (§34-38, §60, §76).

Secret hygiene: token/secret/state values live in the row's `config`, are
Fernet-encrypted (tokens) or hashed (state), and every serializer strips
secret-ish keys. The public `/gmail/callback` and `/gmail/inbound` endpoints
carry no auth header by design — the former verifies the OAuth `state`, the
latter a shared push token.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib.parse import urlencode

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.database.session import SessionLocal
from app.errors import BadRequestError, ForbiddenError, JaloraError, NotFoundError
from app.models.integration import Integration
from app.models.user import User
from app.schemas import (
    GmailStartOut,
    InboundResult,
    IntegrationCreate,
    IntegrationOut,
    IntegrationPatch,
    IntegrationTestOut,
)
from app.services import email_inbound
from app.services import email as email_service
from app.services import integration_registry
from app.services import oauth as oauth_service

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_CONFIG_FIELDS = ("email_address", "label")


def _serialize(row: Integration) -> IntegrationOut:
    return IntegrationOut(
        id=row.id,
        provider=row.provider,
        status=row.status,
        scopes=list(row.scopes or []),
        config=email_service.integration_public_config(row),
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (config or {}).items() if k in _CONFIG_FIELDS}


@router.get("", response_model=list[IntegrationOut])
def list_integrations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Integration).where(Integration.user_id == user.id)
        .order_by(Integration.provider)
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/providers")
def list_providers(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """The integration registry (§76): every capability with secret-free status."""
    return {"providers": integration_registry.provider_registry(db, user.id)}


def _get_or_create(db: Session, user_id: str, provider: str) -> Integration:
    row = db.scalar(
        select(Integration).where(
            Integration.user_id == user_id, Integration.provider == provider
        )
    )
    if row is None:
        row = Integration(user_id=user_id, provider=provider)
        db.add(row)
    return row


@router.post("", response_model=IntegrationOut)
def create_integration(body: IntegrationCreate,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    provider = (body.provider or "").strip().lower()
    if not integration_registry.is_connectable(provider):
        raise BadRequestError(
            f"Unknown integration provider '{provider}'.", "UNKNOWN_PROVIDER"
        )
    if provider in ("email_mock", "gmail"):
        row = _get_or_create(db, user.id, provider)
        if provider == "email_mock":
            row.status = "connected"
            row.config = {
                **_sanitize_config(body.config),
                "email_address": (body.config.get("email_address")
                                  or get_settings().gmail_from),
            }
            row.error = None
        else:
            row.status = "disabled"
            row.config = {**_sanitize_config(body.config), **(row.config or {})}
        if body.scopes:
            row.scopes = list(body.scopes)
        db.commit()
        db.refresh(row)
        return _serialize(row)
    row = integration_registry.connect(
        db, user_id=user.id, provider=provider, config=body.config)
    if body.scopes:
        row.scopes = list(body.scopes)
        db.commit()
        db.refresh(row)
    return _serialize(row)


@router.get("/{integration_id}", response_model=IntegrationOut)
def get_integration(integration_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Integration not found.")
    return _serialize(row)


@router.patch("/{integration_id}", response_model=IntegrationOut)
def patch_integration(integration_id: str, body: IntegrationPatch,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Integration not found.")
    if body.config is not None:
        row.config = {**(row.config or {}), **_sanitize_config(body.config)}
    if body.scopes is not None:
        row.scopes = list(body.scopes)
    if body.status is not None:
        if body.status not in ("disabled", "connected", "error"):
            raise BadRequestError(f"Invalid status '{body.status}'.", "INVALID_STATUS")
        row.status = body.status
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/{integration_id}", status_code=204)
def delete_integration(integration_id: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Integration not found.")
    db.delete(row)
    db.commit()


@router.post("/{provider}/connect")
def connect_provider(provider: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Start a connection.

    OAuth providers return ``{"status": "redirect", "authorization_url": ...}``
    for the SPA to open; mock/keyless providers connect immediately and return
    ``{"status": "connected", ...}``.
    """
    provider = (provider or "").strip().lower()
    if oauth_service.is_oauth_provider(provider):
        row, url = integration_registry.begin_oauth(
            db, user_id=user.id, provider=provider)
        return {
            "status": "redirect",
            "provider": provider,
            "integration_id": row.id,
            "authorization_url": url,
        }
    if not integration_registry.is_connectable(provider):
        raise BadRequestError(
            f"Unknown integration provider '{provider}'.", "UNKNOWN_PROVIDER")
    row = integration_registry.connect(db, user_id=user.id, provider=provider)
    return {"status": "connected", "provider": provider, "integration_id": row.id}


@router.get("/oauth/callback")
async def oauth_callback(request: Request, provider: str = Query(""),
                         code: str = Query(""), state: str = Query(""),
                         error: str = Query("")):
    """Generic OAuth redirect target for every non-Gmail provider.

    Verifies the CSRF ``state`` before any token exchange, then Fernet-encrypts
    and stores the tokens on the Integration row.
    """
    browser = _wants_html(request)
    provider = (provider or "").strip().lower()

    def _fail(reason: str):
        if browser:
            return _spa_redirect(integration="error", provider=provider,
                                 reason=reason)
        raise JaloraError(400, "OAUTH_ERROR",
                          f"{provider or 'provider'} OAuth failed: {reason}")

    if error:
        return _fail(error)
    if not code:
        return _fail("missing_code")
    if not state:
        return _fail("missing_state")
    if not oauth_service.is_oauth_provider(provider):
        return _fail("unknown_provider")
    db = SessionLocal()
    try:
        row = _match_oauth_state(db, provider, state)
        payload = await oauth_service.exchange_code(provider, code)
        access = payload.get("access_token")
        account = await oauth_service.fetch_account(provider, access) if access else ""
        oauth_service.store_tokens(db, row, payload, account=account)
        if browser:
            return _spa_redirect(integration="connected", provider=provider)
        return {"success": True, "provider": provider, "status": "connected",
                "account": account}
    except (JaloraError, oauth_service.OAuthError) as exc:
        if browser:
            return _spa_redirect(integration="error", provider=provider,
                                 reason=str(getattr(exc, "code", "oauth_error")))
        raise
    finally:
        db.close()


def _match_oauth_state(db: Session, provider: str, state: str) -> Integration:
    if not state:
        raise BadRequestError("Missing OAuth state.", "MISSING_STATE")
    target = email_service.oauth_state_hash(state)
    rows = db.scalars(
        select(Integration).where(
            Integration.provider == provider,
            Integration.status != "connected",
        )
    ).all()
    if not rows:
        raise BadRequestError(
            "No pending OAuth flow found; start the connection again.",
            "NO_PENDING_FLOW",
        )
    for row in rows:
        if hmac.compare_digest(
                str((row.config or {}).get("oauth_state_hash", "")), target):
            return row
    raise ForbiddenError(
        "OAuth state does not match; the flow may have been tampered with.",
        "INVALID_STATE",
    )


@router.post("/gmail/start", response_model=GmailStartOut)
def gmail_start(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    if not (settings.gmail_client_id and settings.gmail_client_secret):
        raise BadRequestError(
            "Gmail OAuth is not configured. Set JALORA_GMAIL_CLIENT_ID and "
            "JALORA_GMAIL_CLIENT_SECRET in the environment.",
            "PROVIDER_NOT_CONFIGURED",
        )
    row = db.scalar(
        select(Integration).where(
            Integration.user_id == user.id, Integration.provider == "gmail"
        )
    )
    if row is None:
        row = Integration(user_id=user.id, provider="gmail")
        db.add(row)
    state = email_service.new_oauth_state()
    cfg = dict(row.config or {})
    # Only the SHA-256 of the state is stored, and it is never serialized.
    cfg["oauth_state_hash"] = email_service.oauth_state_hash(state)
    cfg.setdefault("email_address", "")
    row.config = cfg
    row.scopes = list(email_service.GMAIL_SCOPES)
    row.error = None
    db.commit()
    return GmailStartOut(
        integration_id=row.id, provider="gmail",
        authorization_url=email_service.gmail_authorization_url(state),
    )


@router.get("/gmail/callback")
async def gmail_callback(request: Request, code: str = Query(""), state: str = Query(""),
                         error: str = Query(""), scope: str = Query("")):
    """OAuth redirect target. Verifies `state` before any token exchange (CSRF).

    A browser (``Accept: text/html``) is sent back to the SPA with a
    ``?gmail=connected|error`` status so the UI can show a toast instead of
    rendering raw JSON; API clients keep getting the structured JSON response.
    """
    settings = get_settings()
    browser = _wants_html(request)

    if error:
        if browser:
            return _spa_redirect(gmail="error", reason=error)
        raise JaloraError(400, "OAUTH_DENIED",
                          f"Google returned an error: {error}")
    if not code:
        if browser:
            return _spa_redirect(gmail="error", reason="missing_code")
        raise BadRequestError("Missing OAuth authorization code.", "MISSING_CODE")
    if not state:
        if browser:
            return _spa_redirect(gmail="error", reason="missing_state")
        raise BadRequestError("Missing OAuth state.", "MISSING_STATE")
    db = SessionLocal()
    try:
        row = _match_state(db, state)
        await email_service.exchange_code(code, db, row)
        email_address = (row.config or {}).get("email_address") or settings.gmail_from
        if browser:
            return _spa_redirect(gmail="connected", email=email_address)
        return {
            "success": True,
            "provider": "gmail",
            "email_address": email_address,
            "status": "connected",
        }
    except (JaloraError, email_service.EmailError) as exc:
        if browser:
            return _spa_redirect(gmail="error",
                                 reason=str(getattr(exc, "code", "oauth_error")))
        raise
    finally:
        db.close()


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _spa_redirect(**params: str) -> RedirectResponse:
    """Send the browser back to the SPA with a status the UI can toast."""
    base = get_settings().public_base_url.rstrip("/")
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(f"{base}/?{query}", status_code=302)


def _match_state(db: Session, state: str) -> Integration:
    if not state:
        raise BadRequestError("Missing OAuth state.", "MISSING_STATE")
    target = email_service.oauth_state_hash(state)
    rows = db.scalars(
        select(Integration).where(
            Integration.provider == "gmail",
            Integration.status != "connected",
        )
    ).all()
    if not rows:
        raise BadRequestError(
            "No pending Gmail OAuth flow found; start a new connection.",
            "NO_PENDING_FLOW",
        )
    for row in rows:
        if hmac.compare_digest(str((row.config or {}).get("oauth_state_hash", "")),
                               target):
            return row
    raise ForbiddenError(
        "OAuth state does not match; the flow may have been tampered with.",
        "INVALID_STATE",
    )


@router.post("/{integration_id}/test", response_model=IntegrationTestOut)
async def test_integration(integration_id: str,
                           user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    row = db.get(Integration, integration_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Integration not found.")
    if row.provider in ("gmail", "email_mock"):
        result = await email_service.test_integration(
            user_id=user.id, provider=row.provider)
    else:
        result = integration_registry.test_connection(
            db, user_id=user.id, provider=row.provider)
    return IntegrationTestOut(**result)


@router.post("/gmail/inbound", response_model=InboundResult)
async def gmail_inbound(payload: dict, token: str = Query("")):
    """Signed push webhook for inbound email (§60). When a push token is set it
    MUST be present and match, or the webhook is rejected."""
    settings = get_settings()
    if settings.gmail_push_token:
        expected = settings.gmail_push_token
        if not token or not hmac.compare_digest(token, expected):
            raise ForbiddenError("Webhook signature is missing or invalid.",
                                 "INVALID_WEBHOOK_SIGNATURE")
    result = await email_inbound.process_inbound_email(
        to=str(payload.get("to", "")),
        sender=str(payload.get("from_addr") or payload.get("sender") or ""),
        subject=str(payload.get("subject", "")),
        body=str(payload.get("body", "")),
        message_id=str(payload.get("message_id", "")),
    )
    return InboundResult(**result)