"""Phase L audit completeness + secret redaction tests (§74/75)."""
from __future__ import annotations

from app.services.audit import log_activity, redact
from app.database.session import SessionLocal


def test_redact_additional_token_formats():
    """Phase L (§74): redaction covers AWS keys, Slack tokens, JWTs, Fernet."""
    # AWS access key (canonical example key format, not real)
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    assert "redacted" in redact(aws_key)
    assert aws_key not in redact(aws_key)
    # Slack token (test format, not real)
    slack = "xoxb-" + "test-token-abcdef1234567890"
    assert "redacted" in redact(slack)
    assert slack not in redact(slack)
    # JWT (test format, not real)
    jwt_tok = "eyJhbGci.eyJzdWI.SflKxwRJSMeKKF2QT4fwpMeJf3qh"
    assert "redacted" in redact(jwt_tok)
    assert jwt_tok not in redact(jwt_tok)
    # Fernet token (credential store envelope encryption)
    fernet_tok = "gAAAAABmTestFernetTokenData1234567890abcdefghijkl"
    assert "redacted" in redact(fernet_tok)
    assert fernet_tok not in redact(fernet_tok)
    # Private key block
    private_key = (
        "-----BEGIN " "PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoICAQDd7Y5xV2tB9\n"
        "-----END PRIVATE KEY-----"
    )
    assert "redacted" in redact(private_key)
    assert "MIIEvQIBADAN" not in redact(private_key)


def test_redact_nested_structures_with_new_patterns():
    payload = {
        "config": {
            "aws_access_key": "AKIA" + "IOSFODNN7EXAMPLE",
            "region": "us-east-1",
        },
        "webhook": "https://hooks.slack.com/xoxb-test-token-abcdef",
    }
    out = redact(payload)
    assert out["config"]["aws_access_key"] == "***redacted***"
    assert out["config"]["region"] == "us-east-1"
    assert "***redacted***" in out["webhook"]


def test_log_activity_with_provider_and_error_code(client, auth_headers):
    """log_activity accepts the new provider/error_code params (§75)."""
    headers = auth_headers()
    me = client.get("/api/auth/me", headers=headers).json()

    with SessionLocal() as db:
        entry = log_activity(
            db,
            user_id=me["id"],
            tool="send_message",
            arguments={"api_key": "sk-live-test-key-123456789", "body": "hi"},
            result_summary="Delivered with Bearer abcdef123456",
            success=False,
            provider="openai",
            error_code="RATE_LIMITED",
        )
        db.refresh(entry)

    assert entry.provider == "openai"
    assert entry.error_code == "RATE_LIMITED"
    assert entry.success is False
    assert entry.arguments["api_key"] == "***redacted***"


def test_log_activity_does_not_leak_fernet_tokens(client, auth_headers):
    """Fernet-encrypted credential store tokens must be redacted from the
    audit trail (§74/75)."""
    headers = auth_headers()
    me = client.get("/api/auth/me", headers=headers).json()

    with SessionLocal() as db:
        entry = log_activity(
            db,
            user_id=me["id"],
            tool="credentials_approve",
            arguments={"encrypted_secret": "gAAAAABmTestFernetTokenData1234567890abcdef"},
            result_summary="ok",
        )
        db.refresh(entry)

    assert entry.arguments["encrypted_secret"] == "***redacted***"
    assert "gAAAAABmTest" not in (entry.arguments.get("encrypted_secret") or "")


def test_log_activity_does_not_log_plaintext_passwords(client, auth_headers):
    """Passwords in any form must never appear in the audit log."""
    headers = auth_headers()
    me = client.get("/api/auth/me", headers=headers).json()

    with SessionLocal() as db:
        entry = log_activity(
            db,
            user_id=me["id"],
            tool="login",
            arguments={
                "password": "supersecret123",
                "passwd": "another_secret",
                "nested": {"secret": "nested_secret"},
            },
        )
        db.refresh(entry)

    assert entry.arguments["password"] == "***redacted***"
    assert entry.arguments["passwd"] == "***redacted***"
    assert entry.arguments["nested"]["secret"] == "***redacted***"
