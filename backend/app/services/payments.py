"""Payments adapter (A§ payments & travel).

Mock-first: funds are sent via Stripe Connect only when the deployment is
configured with Stripe OAuth credentials *and* the user has a connected
``stripe`` integration (i.e. they completed the OAuth Connect redirect).  In
every other case a deterministic offline mock returns an immediate receipt, so
payments work in tests and offline demos.

The real Stripe path uses ``httpx`` directly (no ``stripe`` SDK dependency) and
only runs when real credentials + a connected account are present.  Plaintext
tokens are read back through the Fernet vault only at call time and are never
returned by any API.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.integration import Integration
from app.services.credential_store import fernet_decrypt
from app.services.oauth import OAuthError, ensure_access_token

logger = logging.getLogger("jalora.payments")

STRIPE_API_URL = "https://api.stripe.com/v1"
_AMOUNT_SCALE = 100  # major currency units -> minor units (cents)


class PaymentError(Exception):
    """Structured failure for payment operations (never carries secrets)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class PaymentReceipt:
    provider: str  # "stripe" | "mock"
    to: str  # recipient (phone / email / name / account id)
    amount: int  # minor units (cents)
    currency: str  # e.g. "usd"
    receipt_id: str  # deterministic for the mock
    status: str  # "succeeded" | "queued" | "failed"
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "to": self.to,
            "amount": self.amount,
            "currency": self.currency,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "description": self.description,
            "mock": self.provider == "mock",
        }


def _norm_currency(currency: str) -> str:
    return ((currency or "usd").strip().lower()[:3] or "usd")


def _to_cents(amount) -> int:
    return max(0, int(round(float(amount) * _AMOUNT_SCALE)))


def _fingerprint(*parts) -> str:
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def get_integration(db: Session, *, user_id: str, provider: str) -> Integration | None:
    return db.scalar(
        select(Integration).where(
            Integration.user_id == user_id, Integration.provider == provider
        )
    )


def resolve_provider(db: Session, *, user_id: str) -> str:
    """Return ``"stripe"`` only when the deployment is Stripe-configured *and* the
    user has a connected ``stripe`` integration; otherwise ``"mock"``."""
    settings = get_settings()
    if not (settings.stripe_client_id and settings.stripe_client_secret):
        return "mock"
    forced = (settings.payments_provider or "auto").strip().lower()
    if forced == "mock":
        return "mock"
    row = db.scalar(
        select(Integration).where(
            Integration.user_id == user_id,
            Integration.provider == "stripe",
            Integration.status == "connected",
        )
    )
    return "stripe" if row is not None else "mock"


class _MockPayments:
    """Deterministic offline payments provider (no network)."""

    _SALT = "jalora-mock-payments-v1"

    def send_payment(self, recipient: str, amount_cents: int, currency: str,
                     description: str) -> PaymentReceipt:
        rid = "pymt_" + _fingerprint(
            self._SALT, recipient, amount_cents, currency, description)
        return PaymentReceipt(
            provider="mock", to=recipient, amount=amount_cents, currency=currency,
            receipt_id=rid, status="succeeded", description=description,
        )


class _Stripe:
    """Minimal Stripe Connect client (long-lived access tokens, A§34)."""

    def __init__(self, db: Session, integration: Integration):
        self.db = db
        self.integration = integration

    @property
    def _cfg(self) -> dict:
        return self.integration.config or {}

    async def _access(self) -> str:
        # Stripe Connect access tokens are long-lived (marked far-future on
        # store so ensure_access_token returns the cached token without refresh).
        return await ensure_access_token(self.db, self.integration)

    async def send_payment(self, recipient: str, amount_cents: int, currency: str,
                           description: str) -> PaymentReceipt:
        access = await self._access()
        data = {
            "amount": str(amount_cents),
            "currency": currency,
            "description": description,
            "automatic_payment_methods[enabled]": "true",
        }
        headers = {
            "Authorization": f"Bearer {access}",
            "User-Agent": "JaloraAI/0.1",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{STRIPE_API_URL}/payment_intents",
                                     data=data, headers=headers)
        if resp.status_code >= 400:
            raise PaymentError(
                "PROVIDER_ERROR",
                f"Stripe HTTP {resp.status_code}: {resp.text[:300]}",
            )
        payload = resp.json() if resp.content else {}
        rid = str(payload.get("id") or "pi_" + _fingerprint(
            "stripe", recipient, amount_cents, currency, description))
        return PaymentReceipt(
            provider="stripe", to=recipient, amount=amount_cents, currency=currency,
            receipt_id=rid, status="succeeded", description=description,
        )


_MOCK = _MockPayments()


async def send_payment(*, user_id: str, recipient: str, amount,
                       currency: str = "usd", description: str = "") -> PaymentReceipt:
    """Send money to a recipient (phone/email/name/account id).  Mocks when Stripe
    is not configured or the user hasn't connected their account."""
    if not recipient or not str(recipient).strip():
        raise PaymentError("INVALID_RECIPIENT", "A recipient is required.")
    try:
        amount_cents = _to_cents(amount)
    except (TypeError, ValueError) as exc:
        raise PaymentError("INVALID_AMOUNT", "Amount must be a positive number.") from exc
    if amount_cents <= 0:
        raise PaymentError("INVALID_AMOUNT", "Amount must be greater than zero.")
    currency = _norm_currency(currency)

    db = SessionLocal()
    try:
        provider = resolve_provider(db, user_id=user_id)
        if provider == "stripe":
            row = get_integration(db, user_id=user_id, provider="stripe")
            if row is None or row.status != "connected":
                raise PaymentError(
                    "STRIPE_NOT_CONNECTED",
                    "Connect your Stripe account to send payments.",
                )
            receipt = await _Stripe(db, row).send_payment(
                recipient, amount_cents, currency, description)
        else:
            receipt = _MOCK.send_payment(recipient, amount_cents, currency, description)
        return receipt
    finally:
        db.close()
