"""Secret redaction in the audit trail (spec §25)."""
from __future__ import annotations

from app.database.session import SessionLocal
from app.services.audit import log_activity, redact


def test_redacts_sensitive_keys_recursively():
    payload = {
        "password": "hunter2",
        "api_key": "value-1",
        "Authorization": "keep-me",
        "nested": {"token": "t-123", "keep": "y"},
        "list": [{"secret": "s-1"}, "plain"],
    }
    out = redact(payload)
    assert out["password"] == "***redacted***"
    assert out["api_key"] == "***redacted***"
    # key match is case-insensitive
    assert out["Authorization"] == "***redacted***"
    assert out["nested"]["token"] == "***redacted***"
    assert out["nested"]["keep"] == "y"
    assert out["list"][0]["secret"] == "***redacted***"
    assert out["list"][1] == "plain"


def test_redacts_bearer_and_known_token_formats_in_strings():
    text = "call with Bearer abc123def456 then sk-abcdefghijklmnop and ghp_abcdefghijklmnopqrstuvwx"
    out = redact(text)
    assert "Bearer ***redacted***" in out
    assert "sk-abcdefghijklmnop" not in out
    assert "ghp_abcdefghijklmnopqrstuvwx" not in out
    assert out.count("***redacted***") == 3


def test_redact_passes_through_scalars_and_numbers():
    assert redact(42) == 42
    assert redact(None) is None
    assert redact(True) is True


def test_log_activity_persists_redacted_arguments(client, auth_headers):
    headers = auth_headers()
    me = client.get("/api/auth/me", headers=headers).json()

    with SessionLocal() as db:
        entry = log_activity(
            db,
            user_id=me["id"],
            tool="send_message",
            arguments={"api_key": "sk-live-abcdefghijklmnop", "body": "hi"},
            result_summary="Delivered with Bearer abcdef123456",
        )
        db.refresh(entry)

    assert entry.arguments["api_key"] == "***redacted***"
    assert entry.arguments["body"] == "hi"
    assert "Bearer abcdef123456" not in entry.result_summary
    assert "***redacted***" in entry.result_summary

    # the activity API serves the redacted entry
    rows = client.get("/api/activity?limit=5", headers=headers).json()
    mine = [r for r in rows if r.get("tool") == "send_message"]
    assert len(mine) == 1
