import io

from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.agent import AgentRun
from app.models.task import Task, TaskStep


def _headers(client, email="doc@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Doc User",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_upload_txt_and_query(client, fake_router):
    headers = _headers(client)
    content = ("Employment Contract Termination Clause. "
               "Either party may terminate this agreement with 30 days written notice. "
               "The notice must be delivered to the registered office.")
    upload = client.post("/api/documents", headers=headers, files={
        "file": ("contract.txt", io.BytesIO(content.encode("utf-8")), "text/plain"),
    })
    assert upload.status_code == 201, upload.text
    doc = upload.json()
    assert doc["status"] == "ready", doc.get("error")
    assert doc["page_count"] >= 1
    assert doc["task_id"]
    with SessionLocal() as db:
        task = db.get(Task, doc["task_id"])
        run = db.scalar(select(AgentRun).where(AgentRun.task_id == doc["task_id"]))
        step = db.scalar(select(TaskStep).where(
            TaskStep.task_id == doc["task_id"],
            TaskStep.tool_id == "process_uploaded_document",
        ))
        assert task is not None and task.status == "completed"
        assert run is not None and run.status == "completed"
        assert step is not None and step.status == "done"

    listed = client.get("/api/documents", headers=headers).json()
    assert len(listed) == 1

    fake_router.script.append(AIMessage(
        content="The contract requires 30 days written notice [p.1]."
    ))
    answer = client.post(f"/api/documents/{doc['id']}/query",
                         json={"question": "What is the termination notice period?"},
                         headers=headers)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert "30 days" in body["answer"]
    assert len(body["retrieved"]) >= 1
    assert "30 days" in body["retrieved"][0]["text"]


def test_unsupported_format_reports_honestly(client):
    headers = _headers(client, "doc2@test.com")
    resp = client.post("/api/documents", headers=headers, files={
        "file": ("notes.xyz", io.BytesIO(b"some unknown content"),
                 "application/octet-stream"),
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_pdf_upload_uses_configured_pdf_size_limit(client, monkeypatch):
    from app.services import document_service

    monkeypatch.setitem(document_service.MAX_BYTES_BY_EXTENSION, ".pdf", 4)
    headers = _headers(client, "pdf-size@test.com")
    resp = client.post("/api/documents", headers=headers, files={
        "file": ("large.pdf", io.BytesIO(b"12345"), "application/pdf"),
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert "4 bytes upload limit" in resp.json()["error"]["details"]


def test_tabular_document_analysis_is_deterministic_and_owner_scoped(
    client, fake_router,
):
    headers = _headers(client, "csv-analysis-owner@test.com")
    other_headers = _headers(client, "csv-analysis-other@test.com")
    upload = client.post("/api/documents", headers=headers, files={
        "file": ("totals.csv", io.BytesIO(b"amount,note\n2,first\n4,second\n"),
                 "text/csv"),
    })
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]

    result = client.post(
        f"/api/documents/{document_id}/analyze", headers=headers,
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["format"] == "csv"
    assert data["provider"] == "local_deterministic"
    amount = data["sheets"][0]["columns"][0]
    assert amount["mean"] == 3.0
    assert "first" not in result.text
    assert "second" not in result.text

    denied = client.post(
        f"/api/documents/{document_id}/analyze", headers=other_headers,
    )
    assert denied.status_code == 404


def test_tabular_analysis_rejects_non_tabular_document(client, fake_router):
    headers = _headers(client, "text-analysis@test.com")
    upload = client.post("/api/documents", headers=headers, files={
        "file": ("notes.txt", io.BytesIO(b"plain text"), "text/plain"),
    })
    assert upload.status_code == 201, upload.text
    response = client.post(
        f"/api/documents/{upload.json()['id']}/analyze", headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_ANALYSIS_FORMAT"


def test_query_unknown_document_404(client):
    headers = _headers(client, "doc3@test.com")
    resp = client.post("/api/documents/deadbeef/query", json={"question": "x"},
                       headers=headers)
    assert resp.status_code == 404


def test_document_delete_uses_shared_approval(client):
    headers = _headers(client, "doc-delete@test.com")
    upload = client.post("/api/documents", headers=headers, files={
        "file": ("delete-me.txt", io.BytesIO(b"document content"), "text/plain"),
    })
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]

    pending = client.delete(f"/api/documents/{document_id}", headers=headers)
    assert pending.status_code == 202, pending.text
    payload = pending.json()
    assert payload["status"] == "paused_approval"
    approval = payload["approval"]
    assert approval["task_id"] == payload["task_id"]
    assert approval["run_id"] == payload["run_id"]
    assert len(client.get("/api/documents", headers=headers).json()) == 1

    approved = client.post(
        f"/api/approvals/{approval['id']}/approve", headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert client.get("/api/documents", headers=headers).json() == []


def test_document_isolation_between_users(client):
    h1 = _headers(client, "owner@test.com")
    h2 = _headers(client, "intruder@test.com")
    upload = client.post("/api/documents", headers=h1, files={
        "file": ("secret.txt", io.BytesIO(b"top secret content"), "text/plain"),
    })
    doc_id = upload.json()["id"]
    resp = client.post(f"/api/documents/{doc_id}/query",
                       json={"question": "what?"}, headers=h2)
    assert resp.status_code == 404
    assert client.delete(f"/api/documents/{doc_id}", headers=h2).status_code == 404
