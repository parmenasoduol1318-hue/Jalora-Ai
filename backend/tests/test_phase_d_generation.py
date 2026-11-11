"""Phase D generation + document + knowledge tests (§39–41, §55, §69).

Covers the acceptance flow: *"Read this DOCX, find the budget table, and make
me an XLSX summary"* — upload/extract/search an office file, then generate a
downloadable XLSX through the agent and fetch it back over /api/files."""
import asyncio
import io
import json

from langchain_core.messages import AIMessage


def _headers(client, email="gen@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Gen User",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _docx_bytes():
    import docx

    doc = docx.Document()
    doc.add_paragraph("Q3 Budget Report")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Category"
    table.cell(0, 1).text = "Amount"
    table.cell(1, 0).text = "Marketing"
    table.cell(1, 1).text = "45000"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Category", "Amount"])
    ws.append(["Rent", "20000"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --- documents & search (§40-41) ------------------------------------------- #
def test_upload_docx_extracts_table(client):
    headers, _ = _headers(client, "docx@test.com")
    up = client.post("/api/documents", headers=headers, files={
        "file": ("budget.docx", io.BytesIO(_docx_bytes()),
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["status"] == "ready", doc.get("error")
    assert doc["progress"] == 100
    assert doc["mime_type"].endswith("wordprocessingml.document")


def test_search_endpoint_finds_docx_table_text(client):
    headers, _ = _headers(client, "find@test.com")
    up = client.post("/api/documents", headers=headers, files={
        "file": ("budget.docx", io.BytesIO(_docx_bytes()), "application/octet-stream"),
    })
    assert up.status_code == 201, up.text
    assert up.json()["status"] == "ready", up.json().get("error")

    results = client.get("/api/documents/search", headers=headers,
                         params={"q": "marketing"}).json()
    assert len(results["results"]) >= 1
    assert "Marketing" in results["results"][0]["text"]


def test_spoofed_docx_rejected(client):
    headers, _ = _headers(client, "spoof@test.com")
    resp = client.post("/api/documents", headers=headers, files={
        "file": ("fake.docx", io.BytesIO(b"this is just plain text pretending"),
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MIME_MISMATCH"


def test_xlsx_upload_and_search_tool(client):
    from app.context import ToolContext, set_tool_context

    headers, user_id = _headers(client, "xl@test.com")
    up = client.post("/api/documents", headers=headers, files={
        "file": ("sheet.xlsx", io.BytesIO(_xlsx_bytes()),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })
    assert up.status_code == 201, up.text
    assert up.json()["status"] == "ready", up.json().get("error")

    from app.tools import registry

    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("search_files")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({"query": "rent"})))
    finally:
        set_tool_context(None)
    assert parsed["success"] is True
    assert any("Rent" in r["text"] for r in parsed["data"]["results"])
    assert any("Rent" in r["text"] for r in parsed["data"]["results"])


# --- generation (§55) ------------------------------------------------------- #
def test_create_xlsx_tool_produces_downloadable_file(client):
    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    headers, user_id = _headers(client, "make@test.com")
    set_tool_context(ToolContext(user_id=user_id, run_id="r1"))
    try:
        spec = registry.get("create_xlsx")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({
            "rows": [["Category", "Amount"], ["Rent", "20000"], ["Salaries", "120000"]],
            "filename": "budget-summary.xlsx",
        })))
    finally:
        set_tool_context(None)

    assert parsed["success"] is True
    artifact = parsed["data"]["artifact"]
    assert artifact["kind"] == "file"
    assert artifact["url"] == f"/api/files/{user_id}/budget-summary.xlsx"

    dl = client.get(artifact["url"], headers=headers)
    assert dl.status_code == 200, dl.text
    assert dl.headers["content-disposition"].find("budget-summary.xlsx") >= 0
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(dl.content), data_only=True)
    ws = wb.active
    assert any(row[0] == "Salaries" for row in ws.iter_rows(values_only=True))


def test_agent_generates_pdf_from_request_and_returns_downloadable_artifact(
    client, fake_router
):
    headers, user_id = _headers(client, "pdf-e2e@test.com")
    request = "Turn these meeting notes into a downloadable PDF file."
    content = (
        "Project Review\n\nDecisions\n- Approve the revised timeline.\n\n"
        "| Month | Payment (KES) |\n|-------|---------------|\n"
        "| 1 | 171,861.14 |\n| 2 | 171,861.14 |"
    )
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [
            {
                "description": "Create a PDF from the supplied meeting notes",
                "tool": "create_pdf",
                "arguments": {
                    "content": content,
                    "filename": "project-review.pdf",
                    "title": "Project Review & Decisions",
                },
            },
            {
                "description": "Report the generated PDF",
                "kind": "response",
            },
        ]},
        "id": "call_pdf_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "create_pdf",
        "args": {
            "content": content,
            "filename": "project-review.pdf",
            "title": "Project Review & Decisions",
        },
        "id": "call_pdf_1",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="Done: I created the PDF document."))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": request},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed", data.get("error")
    artifacts = data.get("artifacts") or []
    assert len(artifacts) == 1
    assert artifacts[0]["url"] == f"/api/files/{user_id}/project-review.pdf"
    assert artifacts[0]["mime"] == "application/pdf"

    download = client.get(artifacts[0]["url"], headers=headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-disposition"].find("project-review.pdf") >= 0
    assert download.content.startswith(b"%PDF-")


def test_agent_generates_word_document_and_returns_downloadable_artifact(
    client, fake_router
):
    headers, user_id = _headers(client, "docx-e2e@test.com")
    request = "Turn these project notes into a Word document."
    content = (
        "Project Review\n\nDecisions\n- Approve the revised timeline.\n\n"
        "| Month | Payment (KES) |\n|-------|---------------|\n"
        "| 1 | 171,861.14 |\n| 2 | 171,861.14 |"
    )
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [
            {
                "description": "Create a Word document from the project notes",
                "tool": "create_docx",
                "arguments": {
                    "content": content,
                    "filename": "project-review.docx",
                    "title": "Project Review",
                },
            },
            {
                "description": "Report the generated Word document",
                "kind": "response",
            },
        ]},
        "id": "call_docx_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "create_docx",
        "args": {
            "content": content,
            "filename": "project-review.docx",
            "title": "Project Review",
        },
        "id": "call_docx_1",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="Done: I created the Word document."))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": request},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed", data.get("error")
    artifacts = data.get("artifacts") or []
    assert len(artifacts) == 1
    assert artifacts[0]["url"] == f"/api/files/{user_id}/project-review.docx"
    assert artifacts[0]["mime"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    download = client.get(artifacts[0]["url"], headers=headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-disposition"].find("project-review.docx") >= 0
    assert download.content.startswith(b"PK")

    import docx

    generated = docx.Document(io.BytesIO(download.content))
    paragraphs = [paragraph.text for paragraph in generated.paragraphs]
    assert "Project Review" in paragraphs
    assert "Decisions" in paragraphs
    assert "- Approve the revised timeline." in paragraphs
    assert len(generated.tables) == 1
    assert [
        [cell.text for cell in row.cells] for row in generated.tables[0].rows
    ] == [
        ["Month", "Payment (KES)"],
        ["1", "171,861.14"],
        ["2", "171,861.14"],
    ]


def test_supplied_rate_loan_request_generates_complete_docx_without_model(
    client, fake_router
):
    headers, user_id = _headers(client, "loan-docx-e2e@test.com")
    request = (
        "Model a corporate project financing loan of KES 5,000,000 taken at "
        "the commercial bank average lending rate of 14.4% over a 3-year term "
        "(compounded monthly). Calculate the exact monthly amortized payment, "
        "total interest payable, and generate a Word document (.docx) named "
        '"Loan_Amortization_Schedule.docx" containing the complete itemized '
        "monthly table without aborting midway."
    )

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": request},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed", data.get("error")
    assert data["model"] == "finance"
    assert "171,861.14" in data["reply"]
    assert len(data["artifacts"]) == 1
    artifact = data["artifacts"][0]
    assert artifact["url"] == (
        f"/api/files/{user_id}/Loan_Amortization_Schedule.docx"
    )
    assert len(fake_router.script) == 0

    download = client.get(artifact["url"], headers=headers)
    assert download.status_code == 200, download.text
    import docx

    document = docx.Document(io.BytesIO(download.content))
    assert len(document.tables) == 1
    rows = [[cell.text for cell in row.cells] for row in document.tables[0].rows]
    assert len(rows) == 38
    assert rows[0] == [
        "Month",
        "Beginning balance (KES)",
        "Payment (KES)",
        "Principal (KES)",
        "Interest (KES)",
        "Ending balance (KES)",
    ]
    assert rows[1][0] == "1"
    assert rows[-2][0] == "36"
    assert rows[-2][-1] == "0.00"
    assert rows[-1][0] == "Total"
    assert "Total interest payable: KES" in "\n".join(
        paragraph.text for paragraph in document.paragraphs
    )


def test_generated_files_are_owner_only(client):
    from app.context import ToolContext, set_tool_context
    from app.tools import registry

    h1, uid1 = _headers(client, "own@test.com")
    h2, _ = _headers(client, "other@test.com")
    set_tool_context(ToolContext(user_id=uid1, run_id="r1"))
    try:
        spec = registry.get("create_csv")
        parsed = json.loads(asyncio.run(spec.tool.ainvoke({
            "rows": [["k", "v"], ["secret", "x"]],
            "filename": "data.csv",
        })))
    finally:
        set_tool_context(None)
    url = parsed["data"]["artifact"]["url"]

    assert client.get(url, headers=h2).status_code == 403
    assert client.get(url, headers=h1).status_code == 200


def test_agent_generates_xlsx_from_docx_and_captures_artifact(client, fake_router):
    """Acceptance flow: read DOCX budget table, produce an XLSX summary."""
    headers, user_id = _headers(client, "e2e@test.com")
    up = client.post("/api/documents", headers=headers, files={
        "file": ("budget.docx", io.BytesIO(_docx_bytes()), "application/octet-stream"),
    })
    assert up.status_code == 201 and up.json()["status"] == "ready", up.text

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [
            {
                "description": "Create a summary spreadsheet from the budget",
                "tool": "create_xlsx",
                "arguments": {
                    "rows": [["Category", "Amount"], ["Marketing", "45000"]],
                    "filename": "budget-summary.xlsx",
                },
            },
            {
                "description": "Report the created spreadsheet",
                "kind": "response",
            },
        ]},
        "id": "call_xlsx_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "create_xlsx",
        "args": {
            "rows": [["Category", "Amount"], ["Marketing", "45000"]],
            "filename": "budget-summary.xlsx",
        },
        "id": "call_xlsx_1", "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="Done: I made the summary spreadsheet."))

    resp = client.post("/api/chat", headers=headers, json={
        "message": "Read the Q3 budget docx and make me an xlsx summary.",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed", data.get("error")
    artifacts = data.get("artifacts") or []
    assert len(artifacts) == 1
    assert artifacts[0]["url"] == f"/api/files/{user_id}/budget-summary.xlsx"

    dl = client.get(artifacts[0]["url"], headers=headers)
    assert dl.status_code == 200
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(dl.content), data_only=True)
    assert wb.active["A1"].value == "Category"


# --- knowledge base (§69) ---------------------------------------------------- #
def test_ingest_source_store_and_search_knowledge(client):
    from app.database.session import SessionLocal
    from app.services.knowledge import ingest_source, search_knowledge

    headers, user_id = _headers(client, "kb@test.com")
    asyncio.run(_ingest_and_search(user_id, ingest_source, search_knowledge))
    # a memory row now exists and is searchable
    memories = client.get("/api/memory", headers=headers).json()
    assert len(memories) == 1
    assert memories[0]["kind"] == "note"


async def _ingest_and_search(user_id, ingest_source, search_knowledge):
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        stored = await ingest_source(
            db, user_id=user_id, source_type="note",
            content="The user's favourite colour is forest green.",
        )
        assert stored["kind"] == "note" and stored["id"]
        blocked = await ingest_source(
            db, user_id=user_id, source_type="note",
            content="credit card 4111 1111 1111 1111 expiry 12/28",
        )
        assert blocked["kind"] == "blocked"
        db.commit()
    hits = await search_knowledge(user_id=user_id, query="favourite colour")
    assert any("forest green" in h["text"] for h in hits)
    assert hits[0]["source"] == "memory"