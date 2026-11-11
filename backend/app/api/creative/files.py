"""Owner-only media streaming for the Creative Studio.

Serves an asset's file (or its thumbnail with ``?variant=thumb``). Because
``<img>``/``<video>`` elements cannot send an ``Authorization`` header, this
route also accepts the bearer token as a ``?token=`` query parameter.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.database.session import get_db
from app.errors import AuthError, NotFoundError
from app.models.creative import MediaAsset
from app.models.user import User
from app.services.creative import storage

router = APIRouter()


def _user_from_request(request: Request, db: Session, token: str | None) -> User:
    raw = token
    if not raw:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            raw = header.removeprefix("Bearer ").strip()
    if not raw:
        raise AuthError("Missing token.", "NO_TOKEN")
    payload = decode_access_token(raw)
    user = db.get(User, str(payload.get("sub", "")))
    if user is None or not user.is_active:
        raise AuthError("Account not found or deactivated.", "USER_INACTIVE")
    return user


@router.get("/files/{asset_id}")
def get_creative_file(asset_id: str, request: Request, variant: str | None = Query(None),
                      token: str | None = Query(None), db: Session = Depends(get_db)):
    user = _user_from_request(request, db, token)
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise NotFoundError("Asset not found.", "ASSET_NOT_FOUND")

    root = storage.artifacts.user_upload_dir(user.id).resolve()
    rel = asset.file_path
    if variant == "thumb" and asset.thumbnail_path:
        rel = asset.thumbnail_path
    path = (root / rel).resolve()
    expected = str(root) + os.sep
    if not path.is_file() or not str(path).startswith(expected):
        raise NotFoundError("Asset file not found.", "FILE_NOT_FOUND")

    from app.api.creative._helpers import asset_mime
    media_type = asset_mime(asset)
    return FileResponse(path, media_type=media_type, filename=Path(path).name)
