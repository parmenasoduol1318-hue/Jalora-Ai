from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, rate_limit
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_refresh_token,
)
from app.config import get_settings
from app.errors import AuthError, ConflictError
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _create_token_pair(user: User, db: Session) -> TokenResponse:
    """Generate an access + refresh token pair. The refresh token hash is
    persisted to the database; only the raw token is returned to the client."""
    settings = get_settings()
    access = create_access_token(user.id, user.email)
    raw_refresh = create_refresh_token(user.id)
    hashed = hash_refresh_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hashed,
        expires_at=expires_at,
    ))
    db.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "register")
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise ConflictError("An account with this email already exists.", "EMAIL_TAKEN")
    user = User(
        email=payload.email.lower(),
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        prefs={"memory_enabled": True, "locale": "en"},
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _create_token_pair(user, db)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "login")
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthError("Invalid email or password.", "INVALID_CREDENTIALS")
    if not user.is_active:
        raise AuthError("This account is deactivated.", "USER_INACTIVE")
    return _create_token_pair(user, db)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "refresh")
    stored = db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(payload.refresh_token))
        .where(RefreshToken.revoked.is_(False))
    )
    if stored is None:
        raise AuthError("Invalid or expired refresh token.", "TOKEN_INVALID")
    if stored.is_expired():
        stored.revoked = True
        db.commit()
        raise AuthError("Refresh token expired. Please log in again.", "TOKEN_EXPIRED")
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise AuthError("Account not found or deactivated.", "USER_INACTIVE")
    # Rotate: revoke the old refresh token and issue a new one.
    stored.revoked = True
    db.commit()
    return _create_token_pair(user, db)


@router.post("/logout")
def logout(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Server-side revocation of a refresh token (Phase L, sec 74).

    The access token cannot be revoked before expiry (by design of JWT), but
    revoking the refresh token prevents further token rotation.
    """
    rate_limit(request, "logout")
    stored = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    if stored is not None:
        stored.revoked = True
        db.commit()
    return {"success": True, "message": "Logged out successfully."}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the user's password and revoke all their refresh tokens (§74).

    The caller must present a valid access token.  After a password change
    every active refresh token for the user is revoked, forcing re-login
    everywhere.
    """
    rate_limit(request, "change-password")
    if not verify_password(payload.current_password, user.password_hash):
        raise AuthError("Current password is incorrect.", "INVALID_CREDENTIALS")
    user.password_hash = hash_password(payload.new_password)
    # Revoke all outstanding refresh tokens for this user.
    db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.user_id == user.id)
        .where(RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    db.commit()
    db.refresh(user)
    return {"success": True, "message": "Password changed. All sessions revoked."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)
