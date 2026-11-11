"""Generated-file serving (spec §55).

`create_pdf/create_docx/create_xlsx/create_csv` write to
``data/outputs/<user_id>/`` and the sidecar ``/api/files/{user_id}/{name}``
serves them for download. Access is owner-only and the path is resolved
inside the user's own outputs folder (traversal-safe).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.config import get_settings
from app.errors import ForbiddenError, NotFoundError
from app.models.user import User

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/{user_id}/{file_name}")
def get_file(user_id: str, file_name: str, user: User = Depends(get_current_user)):
    """Serve one generated output file (auth-checked download)."""
    if user_id != user.id:
        raise ForbiddenError("You can only access your own files.", "NOT_OWNER")
    root = (get_settings().outputs_dir / user_id).resolve()
    path = (root / file_name).resolve()
    if not path.is_file() or not str(path).startswith(str(root) + os.sep):
        raise NotFoundError("File not found.", "FILE_NOT_FOUND")
    media_type = "text/markdown" if path.suffix.lower() == ".md" else (
        "application/octet-stream"
    )
    if media_type == "application/octet-stream":
        return FileResponse(path, media_type=media_type, filename=path.name)
    return FileResponse(path, media_type=media_type, filename=path.name)