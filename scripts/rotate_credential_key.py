#!/usr/bin/env python
"""Re-encrypt the credential vault with JALORA_CREDENTIALS_ENCRYPTION_KEY."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import get_settings
from app.database.session import SessionLocal
from app.services.credential_rotation import rotate_credential_key


def main() -> int:
    settings = get_settings()
    if not settings.credentials_encryption_key:
        print(
            "Set JALORA_CREDENTIALS_ENCRYPTION_KEY to the new Fernet key first.",
            file=sys.stderr,
        )
        return 2
    with SessionLocal() as db:
        count = rotate_credential_key(
            db,
            old_key=os.environ.get("JALORA_CREDENTIALS_OLD_KEY") or None,
            new_key=settings.credentials_encryption_key,
            jwt_secret=settings.jwt_secret,
        )
    print(f"Re-encrypted {count} credential record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
