"""Authenticated artifact serving (spec §16).

Screenshots and downloads live under ``data/uploads/<user_id>/`` and are
served by this endpoint. Access is owner-only, the path is resolved inside
the user's own folder (traversal-safe) and the file must exist.
"""
from __future__ import annotations

import mimetypes
import os

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.config import get_settings
from app.errors import ForbiddenError, NotFoundError
from app.models.user import User

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{user_id}/{file_path:path}")
def get_artifact(user_id: str, file_path: str,
                 download: bool = False,
                 user: User = Depends(get_current_user)):
    """Serve one artifact: screenshots/plots render inline, downloads are fetched.

    Pass ``?download=1`` to force a ``Content-Disposition: attachment`` response
    (browser "Save as") with the original file name; without it, images and
    other viewable types are served inline.
    """
    if user_id != user.id:
        raise ForbiddenError("You can only access your own artifacts.", "NOT_OWNER")
    root = (get_settings().upload_dir / user_id).resolve()
    path = (root / file_path).resolve()
    expected = str(root) + os.sep
    if not path.is_file() or not str(path).startswith(expected):
        raise NotFoundError("Artifact not found.", "ARTIFACT_NOT_FOUND")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if download or media_type == "application/octet-stream":
        return FileResponse(path, media_type=media_type, filename=path.name,
                            content_disposition_type="attachment")
    return FileResponse(path, media_type=media_type)