from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

from app.config import get_settings
from app.errors import AuthError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "iss": "jalora",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="jalora",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired. Please log in again.", "TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid or malformed session token.", "TOKEN_INVALID") from exc


# ---- refresh tokens (Phase L, sec 74) -------------------------------------

def hash_refresh_token(raw: str) -> str:
    """SHA-256 hex digest of a raw refresh token for server-side storage."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_refresh_token(raw: str, stored_hash: str) -> bool:
    """Constant-time comparison of a raw token against its stored hash."""
    return secrets.compare_digest(hash_refresh_token(raw), stored_hash)


def create_refresh_token(user_id: str, secret_key: str | None = None) -> str:
    """Generate a cryptographically-random raw refresh token.

    Returns the *raw* token (to send to the client).  The caller must
    hash it via ``hash_refresh_token`` before persisting.
    """
    return secrets.token_urlsafe(32)
