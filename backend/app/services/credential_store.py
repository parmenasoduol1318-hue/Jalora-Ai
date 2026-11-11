"""Encrypted-at-rest credential vault for login automation (spec §18).

Stores one credential per (user, domain, username). The secret is encrypted
with a Fernet key (JALORA_CREDENTIALS_ENCRYPTION_KEY, else derived from the
JWT secret for dev convenience). Plaintext secrets never enter logs, the LLM
context or API responses — tools inject them straight into the browser.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.credential import Credential

logger = logging.getLogger("jalora.credentials")


class CredentialStoreError(RuntimeError):
    """Structured failure for credential operations (never carries secrets)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _derive_key(seed: str) -> bytes:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet():
    from cryptography.fernet import Fernet

    settings = get_settings()
    raw = settings.credentials_encryption_key
    key = raw.encode("utf-8") if raw else _derive_key(settings.jwt_secret)
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialStoreError(
            "INVALID_ENCRYPTION_KEY",
            "JALORA_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key.",
        ) from exc


def fernet_encrypt(secret: str) -> str:
    return get_fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def fernet_decrypt(token: str) -> str:
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - bad token / rotated key
        raise CredentialStoreError(
            "CREDENTIAL_DECRYPT_FAILED",
            "A stored credential could not be decrypted (was the key rotated?).",
        ) from exc


def normalize_domain(domain: str) -> str:
    """Lowercase, strip port + trailing dot so the same site matches always."""
    if not domain:
        raise CredentialStoreError("INVALID_DOMAIN", "A domain is required.")
    host = domain.strip().lower().rstrip(".")
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def save_credential(db: Session, *, user_id: str, domain: str,
                    username: str, secret: str) -> Credential:
    """Upsert a credential. The secret is encrypted before any write."""
    if not secret:
        raise CredentialStoreError("INVALID_SECRET", "A secret value is required.")
    if not username:
        raise CredentialStoreError("INVALID_USERNAME", "A username is required.")
    domain = normalize_domain(domain)
    row = db.scalar(
        select(Credential).where(
            Credential.user_id == user_id,
            Credential.domain == domain,
            Credential.username == username,
        )
    )
    token = fernet_encrypt(secret)
    if row is None:
        row = Credential(user_id=user_id, domain=domain, username=username,
                         secret=token)
        db.add(row)
    else:
        row.secret = token
        row.username = username
    db.commit()
    db.refresh(row)
    # Never return the secret — the caller already knows it.
    return row


def get_credential(db: Session, *, user_id: str, domain: str,
                   username: str = "") -> Credential | None:
    """Fetch the matching credential (latest row for the domain when the
    username is omitted). The returned row still holds the encrypted secret."""
    domain = normalize_domain(domain)
    query = select(Credential).where(
        Credential.user_id == user_id, Credential.domain == domain
    )
    if username:
        query = query.where(Credential.username == username)
    else:
        query = query.order_by(Credential.updated_at.desc())
    return db.scalar(query.limit(1))


def decrypt_row(row: Credential) -> tuple[str, str]:
    """Return (username, plaintext_secret) for an encrypted row."""
    return row.username, fernet_decrypt(row.secret)


def list_domains(db: Session, *, user_id: str) -> list[str]:
    rows = db.scalars(
        select(Credential.domain).where(Credential.user_id == user_id).distinct()
    ).all()
    return sorted(rows)


def delete_credential(db: Session, *, user_id: str, domain: str,
                      username: str = "") -> bool:
    domain = normalize_domain(domain)
    query = select(Credential).where(
        Credential.user_id == user_id, Credential.domain == domain
    )
    if username:
        query = query.where(Credential.username == username)
    row = db.scalar(query.limit(1))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True