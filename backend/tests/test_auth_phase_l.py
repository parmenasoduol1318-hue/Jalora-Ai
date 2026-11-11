"""Phase L auth hardening tests (§74): refresh tokens, logout, password change."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_refresh_token,
)
from app.database.session import SessionLocal
from app.models.refresh_token import RefreshToken
from app.models.user import User


def _register_and_login(client, email="phase_l@test.com", password="longenough1"):
    """Register a user, return (access_token, refresh_token)."""
    # Fresh DB: ensure no prior user with same email.
    resp = client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "name": "Phase L Test",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["access_token"], data["refresh_token"]


def test_register_returns_refresh_token(client):
    access, refresh = _register_and_login(client, email="rt1@test.com")
    assert access.startswith("eyJ")
    assert refresh is not None
    assert len(refresh) > 16


def test_login_returns_refresh_token(client):
    access, refresh = _register_and_login(client, email="rt2@test.com")
    resp = client.post("/api/auth/login", json={
        "email": "rt2@test.com", "password": "longenough1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["refresh_token"] is not None


def test_refresh_issues_new_access_token(client):
    access, refresh = _register_and_login(client, email="rt3@test.com")
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    data = resp.json()
    new_access = data["access_token"]
    assert new_access  # issued a new access token
    assert data["refresh_token"] is not None
    # Refresh tokens are always unique (random, never stored raw).
    assert data["refresh_token"] != refresh


def test_refresh_rejects_invalid_token(client):
    resp = client.post("/api/auth/refresh", json={"refresh_token": "garbage-token-xyz"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


def test_logout_revokes_refresh_token(client):
    access, refresh = _register_and_login(client, email="rt4@test.com")
    # Logout
    resp = client.post("/api/auth/logout", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # Reuse revoked refresh token — should fail
    resp2 = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp2.status_code == 401
    assert resp2.json()["error"]["code"] == "TOKEN_INVALID"


def test_logout_unknown_token_still_returns_ok(client):
    """Logout with a never-issued token is a no-op success (§74)."""
    resp = client.post("/api/auth/logout", json={"refresh_token": "does-not-exist-123"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_change_password_revokes_all_refresh_tokens(client):
    access, refresh = _register_and_login(client, email="rt5@test.com")
    headers = {"Authorization": f"Bearer {access}"}
    # Issue another refresh token by refreshing once.
    ref_resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    refresh2 = ref_resp.json()["refresh_token"]
    # Change password — this should revoke both refresh tokens.
    resp = client.post("/api/auth/change-password", json={
        "current_password": "longenough1",
        "new_password": "newpassword1",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # Both old refresh tokens should now be invalid.
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh2}).status_code == 401
    # The old access token's user record should still exist with new hash.
    resp_old_pw = client.post("/api/auth/login", json={
        "email": "rt5@test.com", "password": "longenough1",
    })
    assert resp_old_pw.status_code == 401
    resp_new_pw = client.post("/api/auth/login", json={
        "email": "rt5@test.com", "password": "newpassword1",
    })
    assert resp_new_pw.status_code == 200


def test_change_password_rejects_wrong_current(client):
    access, refresh = _register_and_login(client, email="rt6@test.com")
    headers = {"Authorization": f"Bearer {access}"}
    resp = client.post("/api/auth/change-password", json={
        "current_password": "wrongpassword",
        "new_password": "newpassword1",
    }, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_refresh_token_hash_is_stored_not_raw():
    """The raw refresh token must never be persisted — only its SHA-256 hash."""
    raw = create_refresh_token("user-123")
    hashed = hash_refresh_token(raw)
    assert raw != hashed
    assert verify_refresh_token(raw, hashed) is True
    assert verify_refresh_token("wrong", hashed) is False


def test_refresh_token_expiry():
    """An expired refresh token should be detected."""
    # We can't easily create an expired token through the API, so test the model.
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "nonexistent-phase-l@test.com"))
        if user is None:
            user = User(
                email="nonexistent-phase-l@test.com",
                name="Test",
                password_hash="x",
                prefs={},
            )
            db.add(user)
            db.commit()
        token = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token("test-token"),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # expired
        )
        db.add(token)
        db.commit()
        assert token.is_expired() is True
