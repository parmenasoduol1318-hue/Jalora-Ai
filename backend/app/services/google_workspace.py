"""Bounded Google Drive and Workspace API connector.

The connector uses the user's connected ``drive`` integration, whose OAuth
tokens are Fernet-encrypted and refreshed by the shared OAuth service. It does
not register scopes or create OAuth state; OAuth scope registration belongs in
``oauth.py`` and is intentionally kept separate.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import Integration
from app.services import oauth as oauth_service

DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SLIDES_SCOPE = "https://www.googleapis.com/auth/presentations"

DRIVE_API = "https://www.googleapis.com/drive/v3"
DOCS_API = "https://docs.googleapis.com/v1"
SHEETS_API = "https://sheets.googleapis.com/v4"
SLIDES_API = "https://slides.googleapis.com/v1"

REQUEST_TIMEOUT_SECONDS = 15.0
MAX_FILE_BYTES = 1_000_000
MAX_JSON_BYTES = 2_000_000
MAX_TEXT_LENGTH = 100_000
MAX_SHEET_CELLS = 20_000


class GoogleWorkspaceError(Exception):
    """A safe, stable connector error suitable for callers to map to an API."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class GoogleWorkspaceConnector:
    """Operations for the current user's Google Workspace account.

    ``http_client`` is owned by the caller and is intentionally injected so
    tests can use ``httpx.MockTransport`` and applications can control pooling.
    """

    def __init__(self, db: Session, *, user_id: str,
                 http_client: httpx.AsyncClient):
        if not user_id:
            raise GoogleWorkspaceError("NOT_CONNECTED",
                                      "Google Drive is not connected for this user.")
        self._db = db
        self._user_id = user_id
        self._client = http_client
        rows = db.scalars(select(Integration).where(
            Integration.user_id == user_id,
            Integration.provider.in_(
                ("drive", "google_docs", "google_sheets", "google_slides")
            ),
            Integration.status == "connected",
        )).all()
        self._integrations = {
            row.provider: row for row in rows
        }
        if not self._integrations:
            raise GoogleWorkspaceError("NOT_CONNECTED",
                                      "Google Workspace is not connected for this user.")

    async def _access_token(self, required_scope: str) -> str:
        integration = next(
            (
                row for row in self._integrations.values()
                if required_scope in (
                    row.scopes if isinstance(row.scopes, list) else []
                )
            ),
            None,
        )
        if integration is None:
            raise GoogleWorkspaceError(
                "SCOPE_REQUIRED",
                "Connect the Google Workspace service and grant its required permission.",
            )
        if integration.user_id != self._user_id:
            raise GoogleWorkspaceError("NOT_CONNECTED",
                                      "Google Workspace is not connected for this user.")
        try:
            token = await oauth_service.ensure_access_token(self._db, integration)
        except Exception as exc:  # provider and OAuth errors may include secrets
            raise GoogleWorkspaceError(
                "AUTH_FAILED",
                "Google Workspace authorization could not be refreshed; reconnect the integration.",
            ) from exc
        if not token:
            raise GoogleWorkspaceError(
                "AUTH_FAILED",
                "Google Workspace authorization is unavailable; reconnect the integration.",
            )
        return token

    async def _request(self, method: str, url: str, *, scope: str,
                       params: dict[str, Any] | None = None,
                       body: dict[str, Any] | None = None,
                       max_response_bytes: int = MAX_JSON_BYTES) -> httpx.Response:
        token = await self._access_token(scope)
        try:
            async with self._client.stream(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": "JaloraAI/0.1",
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status_code = response.status_code
                headers = response.headers
                request = response.request
                if status_code in (401, 403):
                    raise GoogleWorkspaceError(
                        "PERMISSION_DENIED",
                        "Google Workspace denied access to this resource or operation.",
                    )
                if status_code == 404:
                    raise GoogleWorkspaceError("NOT_FOUND", "Google Workspace resource not found.")
                if status_code == 429:
                    raise GoogleWorkspaceError(
                        "RATE_LIMITED", "Google Workspace rate limit reached; retry later."
                    )
                if status_code >= 500:
                    raise GoogleWorkspaceError(
                        "PROVIDER_UNAVAILABLE", "Google Workspace is temporarily unavailable."
                    )
                if status_code >= 300:
                    raise GoogleWorkspaceError(
                        "PROVIDER_ERROR", "Google Workspace rejected the request."
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_response_bytes:
                        raise GoogleWorkspaceError(
                            "RESPONSE_TOO_LARGE",
                            "Google Workspace response exceeded the size limit.",
                        )
                response = httpx.Response(
                    status_code, headers=headers, content=bytes(content), request=request,
                )
        except httpx.TimeoutException as exc:
            raise GoogleWorkspaceError(
                "PROVIDER_TIMEOUT", "Google Workspace did not respond in time."
            ) from exc
        except httpx.HTTPError as exc:
            raise GoogleWorkspaceError(
                "PROVIDER_UNAVAILABLE", "Google Workspace is temporarily unavailable."
            ) from exc

        return response

    async def _json(self, method: str, url: str, *, scope: str,
                    params: dict[str, Any] | None = None,
                    body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._request(method, url, scope=scope,
                                       params=params, body=body)
        if len(response.content) > MAX_JSON_BYTES:
            raise GoogleWorkspaceError(
                "RESPONSE_TOO_LARGE", "Google Workspace response exceeded the size limit."
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleWorkspaceError(
                "PROVIDER_ERROR", "Google Workspace returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise GoogleWorkspaceError(
                "PROVIDER_ERROR", "Google Workspace returned an invalid response."
            )
        return payload

    @staticmethod
    def _resource_id(resource_id: str) -> str:
        value = (resource_id or "").strip()
        if not value or len(value) > 512:
            raise GoogleWorkspaceError("INVALID_RESOURCE", "A valid Google resource ID is required.")
        return quote(value, safe="")

    @staticmethod
    def _title(title: str) -> str:
        value = (title or "").strip()
        if not value or len(value) > 255:
            raise GoogleWorkspaceError(
                "INVALID_INPUT", "A title between 1 and 255 characters is required."
            )
        return value

    async def search_drive(self, query: str = "", *, limit: int = 20) -> list[dict[str, Any]]:
        """Search files owned by the connected account (not merely shared files)."""
        query = (query or "").strip()
        if len(query) > 200:
            raise GoogleWorkspaceError("INVALID_INPUT", "Search query is too long.")
        clauses = ["trashed = false", "'me' in owners"]
        if query:
            escaped = query.replace("\\", "\\\\").replace("'", "\\'")
            clauses.append(f"(name contains '{escaped}' or fullText contains '{escaped}')")
        payload = await self._json(
            "GET",
            f"{DRIVE_API}/files",
            scope=DRIVE_READ_SCOPE,
            params={
                "q": " and ".join(clauses),
                "pageSize": max(1, min(int(limit or 20), 100)),
                "orderBy": "name",
                "fields": (
                    "files(id,name,mimeType,size,modifiedTime,webViewLink,"
                    "owners(emailAddress,me)),nextPageToken"
                ),
            },
        )
        files = payload.get("files", [])
        if not isinstance(files, list):
            raise GoogleWorkspaceError("PROVIDER_ERROR", "Google Drive returned invalid file data.")
        return [item for item in files if isinstance(item, dict)]

    async def _drive_metadata(self, file_id: str) -> dict[str, Any]:
        payload = await self._json(
            "GET",
            f"{DRIVE_API}/files/{self._resource_id(file_id)}",
            scope=DRIVE_READ_SCOPE,
            params={"fields": "id,name,mimeType,size,modifiedTime,webViewLink,owners(emailAddress,me)"},
        )
        owners = payload.get("owners") or []
        if not any(isinstance(owner, dict) and owner.get("me") is True
                   for owner in owners):
            raise GoogleWorkspaceError("NOT_FOUND", "Google Drive file not found.")
        return payload

    async def _assert_owned(self, resource_id: str, expected_mime: str) -> str:
        metadata = await self._drive_metadata(resource_id)
        if metadata.get("mimeType") != expected_mime:
            raise GoogleWorkspaceError("NOT_FOUND", "Google Workspace resource not found.")
        return self._resource_id(resource_id)

    async def read_drive_file(self, file_id: str) -> dict[str, Any]:
        """Read a small text file or the structured content of a native file."""
        metadata = await self._drive_metadata(file_id)
        resource_id = self._resource_id(file_id)
        mime_type = metadata.get("mimeType", "")
        if mime_type == "application/vnd.google-apps.document":
            content = await self._get_document(resource_id)
        elif mime_type == "application/vnd.google-apps.spreadsheet":
            content = await self._get_spreadsheet(resource_id)
        elif mime_type == "application/vnd.google-apps.presentation":
            content = await self._get_presentation(resource_id)
        else:
            if not (mime_type.startswith("text/") or mime_type in {
                "application/json", "application/xml", "application/javascript",
            }):
                raise GoogleWorkspaceError(
                    "UNSUPPORTED_FILE_TYPE",
                    "Drive read supports Google Docs, Sheets, Slides, and text files only.",
                )
            size = metadata.get("size")
            if size is not None and int(size) > MAX_FILE_BYTES:
                raise GoogleWorkspaceError("FILE_TOO_LARGE", "Drive file exceeds the read size limit.")
            response = await self._request(
                "GET",
                f"{DRIVE_API}/files/{resource_id}",
                scope=DRIVE_READ_SCOPE,
                params={"alt": "media"},
                max_response_bytes=MAX_FILE_BYTES,
            )
            try:
                content = response.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GoogleWorkspaceError(
                    "UNSUPPORTED_FILE_TYPE", "Drive file is not valid UTF-8 text."
                ) from exc
        return {"file": metadata, "content": content}

    async def create_document(self, title: str, *, text: str = "") -> dict[str, Any]:
        if len(text) > MAX_TEXT_LENGTH:
            raise GoogleWorkspaceError("INVALID_INPUT", "Document text exceeds the size limit.")
        document = await self._json(
            "POST", f"{DOCS_API}/documents", scope=DOCS_SCOPE,
            body={"title": self._title(title)},
        )
        document_id = document.get("documentId")
        if not document_id:
            raise GoogleWorkspaceError("PROVIDER_ERROR", "Google Docs did not return a document ID.")
        if text:
            try:
                await self._json(
                    "POST", f"{DOCS_API}/documents/{self._resource_id(document_id)}:batchUpdate",
                    scope=DOCS_SCOPE,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]},
                )
            except GoogleWorkspaceError as exc:
                raise GoogleWorkspaceError(
                    "PARTIAL_WRITE",
                    f"Google Doc {document_id} was created, but initial text was not confirmed. "
                    "Do not retry creation automatically.",
                ) from exc
        readback = await self._get_document(self._resource_id(document_id))
        if readback.get("documentId") != document_id or (
            text and text not in _google_text(readback)
        ):
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                f"Google Doc {document_id} was created but read-back verification did not match.",
            )
        return {
            **document,
            "verification": {"verified": True, "method": "read_after_write"},
        }

    async def get_document(self, document_id: str) -> dict[str, Any]:
        resource_id = await self._assert_owned(
            document_id, "application/vnd.google-apps.document",
        )
        return await self._get_document(resource_id)

    async def _get_document(self, resource_id: str) -> dict[str, Any]:
        return await self._json(
            "GET", f"{DOCS_API}/documents/{resource_id}", scope=DOCS_SCOPE,
        )

    async def replace_document_text(self, document_id: str, *, find_text: str,
                                    replace_text: str) -> dict[str, Any]:
        if not find_text or len(find_text) > MAX_TEXT_LENGTH or len(replace_text) > MAX_TEXT_LENGTH:
            raise GoogleWorkspaceError("INVALID_INPUT", "Document text is invalid or exceeds the size limit.")
        resource_id = await self._assert_owned(
            document_id, "application/vnd.google-apps.document",
        )
        result = await self._json(
            "POST", f"{DOCS_API}/documents/{resource_id}:batchUpdate",
            scope=DOCS_SCOPE,
            body={"requests": [{
                "replaceAllText": {
                    "containsText": {"text": find_text, "matchCase": True},
                    "replaceText": replace_text,
                },
            }]},
        )
        readback = await self._get_document(resource_id)
        if (
            not result.get("replies")
            or not any(
                isinstance(reply, dict)
                and (reply.get("replaceAllText") or {}).get("occurrencesChanged", 0) > 0
                for reply in result["replies"]
            )
            or replace_text not in _google_text(readback)
        ):
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                "Google Docs did not confirm the requested text replacement.",
            )
        return {
            **result,
            "verification": {"verified": True, "method": "read_after_write"},
        }

    async def create_spreadsheet(self, title: str) -> dict[str, Any]:
        spreadsheet = await self._json(
            "POST", f"{SHEETS_API}/spreadsheets", scope=SHEETS_SCOPE,
            body={"properties": {"title": self._title(title)}},
        )
        spreadsheet_id = spreadsheet.get("spreadsheetId")
        if not spreadsheet_id:
            raise GoogleWorkspaceError("PROVIDER_ERROR", "Google Sheets did not return a spreadsheet ID.")
        readback = await self._get_spreadsheet(self._resource_id(spreadsheet_id))
        if readback.get("spreadsheetId") != spreadsheet_id:
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                f"Google Sheet {spreadsheet_id} was created but read-back verification failed.",
            )
        return {
            **spreadsheet,
            "verification": {"verified": True, "method": "read_after_write"},
        }

    async def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        resource_id = await self._assert_owned(
            spreadsheet_id, "application/vnd.google-apps.spreadsheet",
        )
        return await self._get_spreadsheet(resource_id)

    async def _get_spreadsheet(self, resource_id: str) -> dict[str, Any]:
        return await self._json(
            "GET", f"{SHEETS_API}/spreadsheets/{resource_id}",
            scope=SHEETS_SCOPE,
        )

    async def read_sheet_values(self, spreadsheet_id: str, range_name: str) -> dict[str, Any]:
        if not range_name or len(range_name) > 512:
            raise GoogleWorkspaceError("INVALID_INPUT", "A valid spreadsheet range is required.")
        resource_id = await self._assert_owned(
            spreadsheet_id, "application/vnd.google-apps.spreadsheet",
        )
        return await self._json(
            "GET",
            f"{SHEETS_API}/spreadsheets/{resource_id}"
            f"/values/{quote(range_name, safe='')}",
            scope=SHEETS_SCOPE,
        )

    async def update_sheet_values(self, spreadsheet_id: str, range_name: str,
                                  values: Sequence[Sequence[Any]]) -> dict[str, Any]:
        if not range_name or len(range_name) > 512:
            raise GoogleWorkspaceError("INVALID_INPUT", "A valid spreadsheet range is required.")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise GoogleWorkspaceError("INVALID_INPUT", "Spreadsheet values must be a table.")
        if len(values) > MAX_SHEET_CELLS or sum(
                len(row) for row in values if isinstance(row, Sequence)) > MAX_SHEET_CELLS:
            raise GoogleWorkspaceError("INVALID_INPUT", "Spreadsheet update exceeds the cell limit.")
        if any(not isinstance(row, Sequence) or isinstance(row, (str, bytes))
               for row in values):
            raise GoogleWorkspaceError("INVALID_INPUT", "Spreadsheet values must be a table.")
        resource_id = await self._assert_owned(
            spreadsheet_id, "application/vnd.google-apps.spreadsheet",
        )
        result = await self._json(
            "PUT",
            f"{SHEETS_API}/spreadsheets/{resource_id}"
            f"/values/{quote(range_name, safe='')}",
            scope=SHEETS_SCOPE,
            params={"valueInputOption": "RAW"},
            body={"majorDimension": "ROWS", "values": [list(row) for row in values]},
        )
        readback = await self.read_sheet_values(spreadsheet_id, range_name)
        if readback.get("values", []) != [list(row) for row in values]:
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                "Google Sheets did not confirm the requested cell values.",
            )
        return {
            **result,
            "verification": {"verified": True, "method": "read_after_write"},
        }

    async def create_presentation(self, title: str) -> dict[str, Any]:
        presentation = await self._json(
            "POST", f"{SLIDES_API}/presentations", scope=SLIDES_SCOPE,
            body={"title": self._title(title)},
        )
        presentation_id = presentation.get("presentationId")
        if not presentation_id:
            raise GoogleWorkspaceError("PROVIDER_ERROR", "Google Slides did not return a presentation ID.")
        readback = await self._get_presentation(self._resource_id(presentation_id))
        if readback.get("presentationId") != presentation_id:
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                f"Google Slides presentation {presentation_id} was created but read-back verification failed.",
            )
        return {
            **presentation,
            "verification": {"verified": True, "method": "read_after_write"},
        }

    async def get_presentation(self, presentation_id: str) -> dict[str, Any]:
        resource_id = await self._assert_owned(
            presentation_id, "application/vnd.google-apps.presentation",
        )
        return await self._get_presentation(resource_id)

    async def _get_presentation(self, resource_id: str) -> dict[str, Any]:
        return await self._json(
            "GET", f"{SLIDES_API}/presentations/{resource_id}",
            scope=SLIDES_SCOPE,
        )

    async def replace_presentation_text(self, presentation_id: str, *,
                                        find_text: str, replace_text: str) -> dict[str, Any]:
        if not find_text or len(find_text) > MAX_TEXT_LENGTH or len(replace_text) > MAX_TEXT_LENGTH:
            raise GoogleWorkspaceError("INVALID_INPUT", "Presentation text is invalid or exceeds the size limit.")
        resource_id = await self._assert_owned(
            presentation_id, "application/vnd.google-apps.presentation",
        )
        result = await self._json(
            "POST",
            f"{SLIDES_API}/presentations/{resource_id}:batchUpdate",
            scope=SLIDES_SCOPE,
            body={"requests": [{
                "replaceAllText": {
                    "containsText": {"text": find_text, "matchCase": True},
                    "replaceText": replace_text,
                },
            }]},
        )
        readback = await self._get_presentation(resource_id)
        if (
            not result.get("replies")
            or not any(
                isinstance(reply, dict)
                and (reply.get("replaceAllText") or {}).get("occurrencesChanged", 0) > 0
                for reply in result["replies"]
            )
            or replace_text not in _google_text(readback)
        ):
            raise GoogleWorkspaceError(
                "WRITE_NOT_VERIFIED",
                "Google Slides did not confirm the requested text replacement.",
            )
        return {
            **result,
            "verification": {"verified": True, "method": "read_after_write"},
        }


def _google_text(value: Any) -> str:
    """Collect provider text-run values for post-write verification only."""
    parts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            text_run = node.get("textRun")
            if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                parts.append(text_run["content"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return "".join(parts)
