from __future__ import annotations

import hmac

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credential import Credential
from app.services.credential_store import _derive_key


class CredentialKeyRotationError(RuntimeError):
    """Credential key rotation failed without exposing stored plaintext."""


def rotate_credential_key(
    db: Session,
    *,
    new_key: str,
    jwt_secret: str,
    old_key: str | None = None,
) -> int:
    """Re-encrypt every credential atomically and return the number changed.

    When old_key is omitted, the development-era JWT-derived key is used.
    """
    try:
        old_material = old_key.encode("ascii") if old_key else _derive_key(jwt_secret)
        new_material = new_key.encode("ascii")
        old_fernet = Fernet(old_material)
        new_fernet = Fernet(new_material)
    except (UnicodeEncodeError, ValueError) as exc:
        raise CredentialKeyRotationError("A supplied Fernet key is invalid.") from exc

    if hmac.compare_digest(old_material, new_material):
        raise CredentialKeyRotationError("The new key must differ from the old key.")

    rows = db.scalars(select(Credential).with_for_update()).all()
    try:
        replacements = [
            (
                row,
                new_fernet.encrypt(old_fernet.decrypt(row.secret.encode("ascii"))).decode(
                    "ascii"
                ),
            )
            for row in rows
        ]
    except (InvalidToken, UnicodeEncodeError) as exc:
        db.rollback()
        raise CredentialKeyRotationError(
            "Stored credentials could not be decrypted with the old key; no rows were changed."
        ) from exc

    try:
        for row, ciphertext in replacements:
            row.secret = ciphertext
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(replacements)
