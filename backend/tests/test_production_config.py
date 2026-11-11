from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_settings


def _production_settings(tmp_path, **overrides) -> Settings:
    values = {
        "env": "production",
        "jwt_secret": "jwt-secret-with-at-least-32-characters",
        "credentials_encryption_key": None,
        "openai_api_key": "test-provider-key",
        "data_dir": tmp_path,
        "upload_dir": tmp_path / "uploads",
        "outputs_dir": tmp_path / "outputs",
        "workspaces_dir": tmp_path / "workspaces",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_a_dedicated_credential_encryption_key(tmp_path):
    settings = _production_settings(tmp_path)

    with pytest.raises(RuntimeError, match="JALORA_CREDENTIALS_ENCRYPTION_KEY"):
        validate_settings(settings)


@pytest.mark.parametrize("key", ["invalid-key", "not-a-fernet-key"])
def test_production_rejects_invalid_credential_encryption_key(tmp_path, key):
    settings = _production_settings(
        tmp_path,
        credentials_encryption_key=key,
    )

    with pytest.raises(RuntimeError, match="valid Fernet key"):
        validate_settings(settings)


def test_production_requires_credential_key_to_be_separate_from_jwt_key(tmp_path):
    key = Fernet.generate_key().decode("ascii")
    settings = _production_settings(
        tmp_path,
        jwt_secret=key,
        credentials_encryption_key=key,
    )

    with pytest.raises(RuntimeError, match="must be separate"):
        validate_settings(settings)


def test_production_accepts_valid_dedicated_credential_encryption_key(tmp_path):
    settings = _production_settings(
        tmp_path,
        credentials_encryption_key=Fernet.generate_key().decode("ascii"),
    )

    warnings = validate_settings(settings)
    assert isinstance(warnings, list)
