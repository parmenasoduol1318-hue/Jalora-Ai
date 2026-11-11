from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.config import get_settings
from app.database.session import get_db
from app.errors import AuthError, BadRequestError, ForbiddenError
from app.models.user import User
from app.services.model_router import ModelRouter, get_model_router

__all__ = ["get_db", "get_current_user", "get_router", "rate_limit"]


def get_router() -> ModelRouter:
    return get_model_router()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthError("Missing bearer token.", "NO_TOKEN")
    payload = decode_access_token(header.removeprefix("Bearer ").strip())
    user = db.get(User, str(payload.get("sub", "")))
    if user is None or not user.is_active:
        raise AuthError("Account not found or deactivated.", "USER_INACTIVE")
    return user


# --- lightweight in-memory rate limiting (Phase 8 moves this to Redis) ------
_windows: dict[str, deque[float]] = defaultdict(deque)
_windows_lock = Lock()


def rate_limit(request: Request, key: str, limit_per_min: int | None = None) -> None:
    limit = limit_per_min or get_settings().rate_limit_auth_per_min
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    bucket = f"{key}:{client}"
    now = time.monotonic()
    with _windows_lock:
        q = _windows[bucket]
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= limit:
            raise ForbiddenError(
                "Too many requests. Please wait a moment and try again.", "RATE_LIMITED"
            )
        q.append(now)


def require_json_fields(data: dict, *fields: str) -> None:
    for f in fields:
        if not data.get(f):
            raise BadRequestError(f"Field '{f}' is required.", "MISSING_FIELD")
