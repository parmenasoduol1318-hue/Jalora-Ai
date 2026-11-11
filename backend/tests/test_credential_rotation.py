from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

import pytest

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.credential import Credential
from app.models.user import User
from app.services.credential_rotation import (
    CredentialKeyRotationError,
    rotate_credential_key,
)
from app.services.credential_store import _derive_key


def _insert_credential(db, *, username: str, encrypted_secret: str) -> Credential:
    user = User(email=f"{username}@rotation.test", password_hash="test-hash")
    db.add(user)
    db.flush()
    credential = Credential(
        user_id=user.id,
        domain="example.test",
        username=username,
        secret=encrypted_secret,
    )
    db.add(credential)
    db.flush()
    return credential


def test_key_rotation_reencrypts_all_credentials_and_preserves_values():
    settings = get_settings()
    old_fernet = Fernet(_derive_key(settings.jwt_secret))
    new_key = Fernet.generate_key().decode("ascii")
    new_fernet = Fernet(new_key.encode("ascii"))

    with SessionLocal() as db:
        original = {
            "alice": "alice-secret",
            "bob": "bob-secret",
        }
        rows = {
            username: _insert_credential(
                db,
                username=username,
                encrypted_secret=old_fernet.encrypt(secret.encode()).decode("ascii"),
            )
            for username, secret in original.items()
        }
        db.commit()

        changed = rotate_credential_key(
            db,
            new_key=new_key,
            jwt_secret=settings.jwt_secret,
        )

        assert changed == 2
        for username, row in rows.items():
            db.refresh(row)
            assert new_fernet.decrypt(row.secret.encode()).decode() == original[username]
            with pytest.raises(InvalidToken):
                old_fernet.decrypt(row.secret.encode())


def test_wrong_old_key_does_not_partially_reencrypt_credentials():
    settings = get_settings()
    actual_old_fernet = Fernet(_derive_key(settings.jwt_secret))
    wrong_old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")

    with SessionLocal() as db:
        row = _insert_credential(
            db,
            username="wrong-key",
            encrypted_secret=actual_old_fernet.encrypt(b"keep-me").decode("ascii"),
        )
        db.commit()
        original_ciphertext = row.secret

        with pytest.raises(CredentialKeyRotationError, match="no rows were changed"):
            rotate_credential_key(
                db,
                old_key=wrong_old_key,
                new_key=new_key,
                jwt_secret=settings.jwt_secret,
            )

        db.refresh(row)
        assert row.secret == original_ciphertext
