from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from app.database.session import SessionLocal
from app.models.integration import Integration
from app.models.user import User
from app.services.credential_store import fernet_encrypt
from app.services.google_workspace import (
    DOCS_SCOPE,
    DRIVE_READ_SCOPE,
    SHEETS_SCOPE,
    SLIDES_SCOPE,
    GoogleWorkspaceConnector,
    GoogleWorkspaceError,
)


ALL_SCOPES = [DRIVE_READ_SCOPE, DOCS_SCOPE, SHEETS_SCOPE, SLIDES_SCOPE]


def _connector(handler, *, scopes=ALL_SCOPES, provider="drive", status="connected"):
    db = SessionLocal()
    user = User(email=f"workspace-{time.time_ns()}@test.local",
                name="Workspace Test", password_hash="not-a-real-password")
    db.add(user)
    db.flush()
    integration = Integration(
        user_id=user.id,
        provider=provider,
        status=status,
        scopes=list(scopes),
        config={
            "access_token": fernet_encrypt("mock-access-token"),
            "token_expires_at": time.time() + 3600,
        },
    )
    db.add(integration)
    db.commit()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return db, user.id, integration, client


@pytest.mark.asyncio
async def test_drive_search_uses_owned_files_query_and_bounded_timeout():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["request"] = request
        observed["query"] = parse_qs(urlparse(str(request.url)).query)
        return httpx.Response(200, json={
            "files": [{
                "id": "owned-1",
                "name": "A's file",
                "mimeType": "text/plain",
                "owners": [{"me": True}],
            }],
        })

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        files = await connector.search_drive("A's")
        request = observed["request"]
        query = observed["query"]["q"][0]
        assert files[0]["id"] == "owned-1"
        assert "'me' in owners" in query
        assert "trashed = false" in query
        assert "A\\'s" in query
        assert request.headers["Authorization"] == "Bearer mock-access-token"
        assert request.extensions["timeout"]["read"] == 15.0
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_drive_read_requires_account_ownership_before_downloading():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={
            "id": "shared",
            "name": "shared.txt",
            "mimeType": "text/plain",
            "owners": [{"me": False}],
        })

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        with pytest.raises(GoogleWorkspaceError) as error:
            await connector.read_drive_file("shared")
        assert error.value.code == "NOT_FOUND"
        assert len(paths) == 1
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_drive_read_downloads_small_owned_text_file():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"safe text")
        return httpx.Response(200, json={
            "id": "text-1",
            "name": "notes.txt",
            "mimeType": "text/plain",
            "size": "9",
            "owners": [{"me": True}],
        })

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        result = await connector.read_drive_file("text-1")
        assert result["content"] == "safe text"
        assert paths == ["/drive/v3/files/text-1", "/drive/v3/files/text-1"]
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_documents_create_read_and_edit_contract():
    requests = []
    document_text = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal document_text
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path.startswith("/drive/v3/files/"):
            return httpx.Response(200, json={
                "id": "doc-1",
                "mimeType": "application/vnd.google-apps.document",
                "owners": [{"me": True}],
            })
        if request.method == "POST" and request.url.path.endswith("/documents"):
            return httpx.Response(200, json={"documentId": "doc-1", "title": "Draft"})
        if request.url.path.endswith(":batchUpdate"):
            batch_request = body["requests"][0]
            if "insertText" in batch_request:
                document_text = batch_request["insertText"]["text"]
                return httpx.Response(200, json={"replies": [{}]})
            change = batch_request["replaceAllText"]
            occurrences = document_text.count(
                change["containsText"]["text"]
            )
            document_text = document_text.replace(
                change["containsText"]["text"], change["replaceText"]
            )
            return httpx.Response(200, json={"replies": [{
                "replaceAllText": {"occurrencesChanged": occurrences},
            }]})
        return httpx.Response(200, json={
            "documentId": "doc-1",
            "title": "Draft",
            "body": {"content": [{
                "paragraph": {"elements": [{
                    "textRun": {"content": document_text},
                }]},
            }]},
        })

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        created = await connector.create_document("Draft", text="Hello")
        read = await connector.get_document(created["documentId"])
        await connector.replace_document_text(
            "doc-1", find_text="Hello", replace_text="Hi",
        )
        api_requests = [request for request in requests
                        if not request[1].startswith("/drive/v3/files/")]
        assert read["documentId"] == "doc-1"
        assert created["verification"]["verified"] is True
        assert api_requests[0][2] == {"title": "Draft"}
        assert api_requests[1][2]["requests"][0]["insertText"]["text"] == "Hello"
        assert api_requests[2][0] == "GET"
        assert api_requests[4][2]["requests"][0]["replaceAllText"]["replaceText"] == "Hi"
        assert api_requests[5][0] == "GET"
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_sheets_and_slides_create_read_edit_contracts():
    requests = []
    sheet_values = [["ok"]]
    slide_text = "old"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sheet_values, slide_text
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, str(request.url), body))
        if request.url.path.startswith("/drive/v3/files/"):
            file_id = request.url.path.rsplit("/", 1)[-1]
            mime_type = (
                "application/vnd.google-apps.spreadsheet"
                if file_id == "sheet-1"
                else "application/vnd.google-apps.presentation"
            )
            return httpx.Response(200, json={
                "id": file_id, "mimeType": mime_type, "owners": [{"me": True}],
            })
        if request.method == "POST" and "spreadsheets" in request.url.path:
            return httpx.Response(200, json={
                "spreadsheetId": "sheet-1", "properties": {"title": "Numbers"},
            })
        if request.method == "POST" and request.url.path.endswith("/presentations"):
            return httpx.Response(200, json={"presentationId": "slides-1"})
        if "/values/" in request.url.path:
            if request.method == "PUT":
                sheet_values = body["values"]
                return httpx.Response(200, json={"updatedCells": 1})
            return httpx.Response(200, json={"values": sheet_values})
        if request.url.path.endswith(":batchUpdate"):
            change = body["requests"][0]["replaceAllText"]
            occurrences = slide_text.count(change["containsText"]["text"])
            slide_text = slide_text.replace(
                change["containsText"]["text"], change["replaceText"]
            )
            return httpx.Response(200, json={"replies": [{
                "replaceAllText": {"occurrencesChanged": occurrences},
            }]})
        if "/spreadsheets/" in request.url.path:
            return httpx.Response(200, json={"spreadsheetId": "sheet-1"})
        if "/presentations/" in request.url.path:
            return httpx.Response(200, json={
                "presentationId": "slides-1",
                "slides": [{"pageElements": [{
                    "shape": {"text": {"textElements": [{
                        "textRun": {"content": slide_text},
                    }]}},
                }]}],
            })
        return httpx.Response(200, json={})

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        sheet = await connector.create_spreadsheet("Numbers")
        await connector.get_spreadsheet(sheet["spreadsheetId"])
        values = await connector.read_sheet_values("sheet-1", "Sheet1!A1:B2")
        await connector.update_sheet_values("sheet-1", "Sheet1!A1", [["ok"]])

        presentation = await connector.create_presentation("Deck")
        await connector.get_presentation(presentation["presentationId"])
        await connector.replace_presentation_text(
            "slides-1", find_text="old", replace_text="new",
        )
        api_requests = [request for request in requests
                        if "/drive/v3/files/" not in request[1]]
        assert values["values"] == [["ok"]]
        assert sheet["verification"]["verified"] is True
        assert presentation["verification"]["verified"] is True
        assert api_requests[0][2] == {"properties": {"title": "Numbers"}}
        assert api_requests[4][0] == "PUT"
        assert api_requests[4][2] == {"majorDimension": "ROWS", "values": [["ok"]]}
        assert api_requests[4][2]["majorDimension"] == "ROWS"
        assert api_requests[6][2] == {"title": "Deck"}
        assert api_requests[9][2]["requests"][0]["replaceAllText"]["replaceText"] == "new"
        assert unquote(urlparse(api_requests[3][1]).path).endswith("/values/Sheet1!A1:B2")
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_missing_scope_fails_closed_before_api_request():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    db, user_id, _, client = _connector(handler, scopes=[DRIVE_READ_SCOPE])
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        with pytest.raises(GoogleWorkspaceError) as error:
            await connector.create_spreadsheet("No authorization")
        assert error.value.code == "SCOPE_REQUIRED"
        assert not called
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_workspace_services_use_their_own_oauth_grants():
    tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers["Authorization"])
        if request.url.path.startswith("/drive/v3/files/"):
            return httpx.Response(200, json={
                "id": "doc-1",
                "mimeType": "application/vnd.google-apps.document",
                "owners": [{"me": True}],
            })
        return httpx.Response(200, json={"documentId": "doc-1"})

    db, user_id, drive_integration, client = _connector(
        handler, scopes=[DRIVE_READ_SCOPE]
    )
    docs_integration = Integration(
        user_id=user_id,
        provider="google_docs",
        status="connected",
        scopes=[DOCS_SCOPE],
        config={
            "access_token": fernet_encrypt("docs-access"),
            "token_expires_at": time.time() + 3600,
        },
    )
    drive_integration.config = {
        "access_token": fernet_encrypt("drive-access"),
        "token_expires_at": time.time() + 3600,
    }
    db.add(docs_integration)
    db.commit()
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        document = await connector.get_document("doc-1")
        assert document["documentId"] == "doc-1"
        assert tokens == ["Bearer drive-access", "Bearer docs-access"]
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_provider_error_mapping_does_not_expose_response_body():
    secret_like_body = "refresh_token=do-not-return"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=secret_like_body)

    db, user_id, _, client = _connector(handler)
    try:
        connector = GoogleWorkspaceConnector(db, user_id=user_id, http_client=client)
        with pytest.raises(GoogleWorkspaceError) as error:
            await connector.search_drive()
        assert error.value.code == "PERMISSION_DENIED"
        assert secret_like_body not in error.value.detail
    finally:
        await client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_another_users_integration_is_not_used():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"files": []})

    db, owner_id, _, client = _connector(handler)
    other_user = User(email=f"other-{time.time_ns()}@test.local",
                      name="Other User", password_hash="not-a-real-password")
    db.add(other_user)
    db.commit()
    try:
        with pytest.raises(GoogleWorkspaceError) as error:
            GoogleWorkspaceConnector(db, user_id=other_user.id, http_client=client)
        assert error.value.code == "NOT_CONNECTED"
        assert other_user.id != owner_id
        assert not called
    finally:
        await client.aclose()
        db.close()
