"""Phase C: persistent browser automation (spec §16–§19).

Covers: the 14-tool registry, the offline fake driver, navigation history
(back/forward), SSRF-safe URL handling, encrypted §18 credentials and the
secure approval sub-form flow, screenshot/download artifacts, and browser
session lifecycle. All tests run on the offline fake driver.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.context import set_tool_context, ToolContext
from app.database.session import SessionLocal
from app.models.credential import Credential
from app.models.user import User
from app.services import browser_driver, browser_session, credential_store
from app.services.browser_driver import DriverError, FakeDriver
from app.services.browser_session import BrowserSession
from app.tools import registry
from app.tools.base import PermissionLevel

LOGIN_URL = "https://bank.example.com/login"
ACCOUNTS_URL = "https://bank.example.com/accounts"

LOGIN_PAGE = """<html><head><title>Bank Login</title></head><body>
<a href="/accounts">Accounts</a>
<input type="text" id="user" name="username" placeholder="Username">
<input type="password" id="pass" name="password">
<input type="submit">
</body></html>"""

ACCOUNTS_PAGE = ("<html><head><title>Accounts</title></head>"
                 "<body><p>Your balance: 1000</p></body></html>")

PAGES = {LOGIN_URL: LOGIN_PAGE, ACCOUNTS_URL: ACCOUNTS_PAGE}


def _invoke(name: str, args: dict) -> dict:
    spec = registry.get(name)
    assert spec is not None, f"tool {name} is not registered"
    raw = asyncio.run(spec.tool.ainvoke(args))
    return json.loads(raw)


def _user_id(email: str = "e2e@test.com") -> str:
    """Create (or reuse) a real user row so credential FK inserts succeed."""
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        if uid is None:
            row = User(email=email, name="Unit User", password_hash="!")
            db.add(row)
            db.commit()
            uid = row.id
        return uid


def _run_flow(user_id: str, steps: list[tuple[str, dict]]):
    """Execute several browser tool calls in ONE event loop (session history
    survives across calls because the loop does not change)."""
    async def runner():
        session: BrowserSession | None = None
        try:
            set_tool_context(ToolContext(user_id=user_id, run_id="r-unit"))
            out = []
            for name, args in steps:
                spec = registry.get(name)
                raw = await spec.tool.ainvoke(args)
                out.append(json.loads(raw))
            session = browser_session.get_session(user_id)
            return out, session
        finally:
            set_tool_context(None)

    return asyncio.run(runner())


def _attach(user_id: str, pages: dict[str, str]) -> BrowserSession:
    session = BrowserSession(user_id=user_id, driver=FakeDriver(pages=pages))
    browser_session.attach_session(session)
    return session


# --------------------------------------------------------------------------- #
# registry                                                                    #
# --------------------------------------------------------------------------- #
def test_browser_tools_registered_and_permission_gated():
    expected = {
        "browser_open": PermissionLevel.AUTO,
        "browser_navigate": PermissionLevel.AUTO,
        "browser_wait": PermissionLevel.AUTO,
        "browser_back": PermissionLevel.AUTO,
        "browser_forward": PermissionLevel.AUTO,
        "browser_close": PermissionLevel.NOTIFY,
        "browser_click": PermissionLevel.NOTIFY,
        "browser_type": PermissionLevel.NOTIFY,
        "browser_fill": PermissionLevel.CONFIRM,
        "browser_select": PermissionLevel.NOTIFY,
        "browser_scroll": PermissionLevel.NOTIFY,
        "browser_upload": PermissionLevel.CONFIRM,
        "browser_download": PermissionLevel.CONFIRM,
        "browser_screenshot": PermissionLevel.AUTO,
        "browser_extract": PermissionLevel.AUTO,
    }
    for name, level in expected.items():
        spec = registry.get(name)
        assert spec is not None, f"{name} missing"
        assert spec.permission == level, name
        assert spec.category == "browser", name
        assert spec.requires_internet is (name != "browser_close"), name
    manifest = {e["name"]: e for e in registry.manifest()}
    assert manifest["browser_fill"]["permission"] == 3
    assert manifest["browser_download"]["permission"] == 3
    assert manifest["browser_screenshot"]["permission"] == 1


def test_browser_wait_is_bounded_and_waits_for_the_open_page():
    user = "u-browser-wait"
    session = _attach(user, PAGES)

    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_wait", {"seconds": 2}),
    ])

    assert out[1]["success"] is True
    assert session.driver.waits == [2000]


def test_browser_wait_rejects_unbounded_delays():
    result = asyncio.run(registry.get("browser_wait").tool.ainvoke({
        "seconds": 60,
    }))
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "INVALID_WAIT"


# --------------------------------------------------------------------------- #
# fake driver unit behaviours                                                 #
# --------------------------------------------------------------------------- #
def test_fake_driver_goto_history_and_inputs():
    async def run():
        driver = FakeDriver(pages=PAGES)
        await driver.start()
        await driver.goto(LOGIN_URL)
        assert await driver.url() == LOGIN_URL
        assert await driver.title() == "Bank Login"
        inputs = driver.inputs()
        assert {i["name"] for i in inputs} == {"username", "password"}
        await driver.click("Accounts")
        assert driver.current_url == ACCOUNTS_URL
        assert await driver.title() == "Accounts"
        await driver.wait(5000)
        assert driver.waits == [5000]
        await driver.close()
    asyncio.run(run())


def test_fake_driver_not_open_raises_structured_error():
    async def run():
        driver = FakeDriver(pages=PAGES)
        # never started -> BROWSER_NOT_OPEN, not a raw attribute error
        with pytest.raises(DriverError) as exc:
            await driver.goto("https://example.com/x")
        assert exc.value.code == "BROWSER_NOT_OPEN"
    asyncio.run(run())


# --------------------------------------------------------------------------- #
# navigation (single loop: history survives)                                   #
# --------------------------------------------------------------------------- #
def test_open_navigate_back_forward_persists_history():
    user = "u-nav"
    _attach(user, PAGES)
    out, session = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_navigate", {"url": ACCOUNTS_URL}),
        ("browser_back", {}),
        ("browser_forward", {}),
    ])
    open_res = out[0]
    assert open_res["success"] is True
    assert open_res["data"]["browser"]["url"] == LOGIN_URL
    assert open_res["data"]["browser"]["title"] == "Bank Login"

    nav = out[1]
    assert nav["data"]["browser"]["url"] == ACCOUNTS_URL
    assert nav["data"]["browser"]["can_back"] is True

    back = out[2]
    assert back["data"]["browser"]["url"] == LOGIN_URL
    assert back["data"]["browser"]["can_back"] is False
    assert back["data"]["browser"]["can_forward"] is True

    fwd = out[3]
    assert fwd["data"]["browser"]["url"] == ACCOUNTS_URL

    assert session is not None and session.history == [LOGIN_URL, ACCOUNTS_URL]


def test_back_forward_flag_misuse_is_honest_failure():
    user = "u-back"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_back", {}),
        ("browser_forward", {}),
    ])
    assert out[1]["success"] is False
    assert out[1]["error"]["code"] == "NO_BACK_HISTORY"
    assert out[2]["success"] is False
    assert out[2]["error"]["code"] == "NO_FORWARD_HISTORY"


def test_browser_close_releases_session():
    user = "u-close"
    _attach(user, PAGES)
    async def run():
        set_tool_context(ToolContext(user_id=user))
        spec = registry.get("browser_close")
        raw = await spec.tool.ainvoke({})
        result = json.loads(raw)
        assert result["success"] is True
        assert browser_session.get_session(user) is None
        set_tool_context(None)
    asyncio.run(run())


# --------------------------------------------------------------------------- #
# SSRF guard on every URL                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,code", [
    ("http://127.0.0.1:8000/admin", "BLOCKED_PRIVATE"),
    ("http://10.0.0.5/internal", "BLOCKED_PRIVATE"),
    ("ftp://files.example.com/x", "BLOCKED_SCHEME"),
    ("https://user:pass@example.com/", "BLOCKED_CREDENTIALS"),
    ("no-scheme.example.com", "INVALID_URL"),
])
def test_browser_urls_blocked_by_guard(url, code):
    user = "u-guard"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [("browser_open", {"url": url})])
    assert out[0]["success"] is False
    assert out[0]["error"]["code"] == code
    session = browser_session.get_session(user)
    assert session is None or session.history == []  # nothing was navigated


def test_browser_navigate_and_download_blocked_too():
    user = "u-guard2"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_navigate", {"url": "http://192.168.1.1/x"}),
        ("browser_download", {"url": "http://localhost/x"}),
    ])
    assert out[1]["error"]["code"] == "BLOCKED_PRIVATE"
    assert out[2]["error"]["code"] == "BLOCKED_PRIVATE"


# --------------------------------------------------------------------------- #
# interaction                                                                 #
# --------------------------------------------------------------------------- #
def test_click_type_select_scroll_upload_records():
    user = "u-int"
    _attach(user, PAGES)
    out, session = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_click", {"selector": "Accounts"}),
        ("browser_open", {"url": LOGIN_URL}),  # back to the form
        ("browser_type", {"selector": "#user", "text": "type-user"}),
        ("browser_select", {"selector": "#sel", "value": "b"}),
        ("browser_scroll", {"direction": "down", "amount": 300}),
        ("browser_upload", {"selector": "input[type=file]", "path": "C:/x/y.txt"}),
    ])
    driver = session.driver if session else None
    assert driver is not None
    assert driver.clicked == ["Accounts"]
    assert ("#user", "type-user") in driver.typed
    assert ("#sel", "b") in driver.selected
    assert driver.scrolls == ["down"]
    assert ("input[type=file]", "C:/x/y.txt") in driver.uploaded
    assert ACCOUNTS_URL in driver.visited  # the click followed the link


def test_extract_text_links_and_form():
    user = "u-extract"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_extract", {"format": "links"}),
        ("browser_extract", {"format": "form"}),
        ("browser_navigate", {"url": ACCOUNTS_URL}),
        ("browser_extract", {"format": "text"}),
    ])
    links = out[1]["data"]
    assert links["untrusted"] is True
    assert any(l["url"] == ACCOUNTS_URL for l in links["links"])
    form = out[2]["data"]
    assert any(f["name"] == "username" for f in form["form"])
    text = out[4]["data"]
    assert text["untrusted"] is True
    assert text["title"] == "Accounts"
    assert "balance" in text["text"]


def test_extract_visible_article_cards():
    user = "u-extract-cards"
    page = (
        "<html><head><title>Results</title></head><body>"
        "<article><h2>Operator A</h2><p>Route MOMBASA - NAIROBI</p></article>"
        "</body></html>"
    )
    _attach(user, {LOGIN_URL: page})
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_extract", {"format": "cards"}),
    ])

    data = out[1]["data"]
    assert data["format"] == "cards"
    assert data["untrusted"] is True
    assert "Route MOMBASA - NAIROBI" in data["cards"][0]


def test_extract_awaits_async_real_driver_form_and_link_readers():
    user = "u-async-extract"
    session = _attach(user, PAGES)

    async def links():
        return [("Accounts", ACCOUNTS_URL)]

    async def inputs():
        return [{
            "selector": "select >> nth=0",
            "tag": "select",
            "label": "Pickup stop",
            "options": [{"label": "MOMBASA", "value": "MOMBASA"}],
        }]

    session.driver.links = links
    session.driver.inputs = inputs
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_extract", {"format": "links"}),
        ("browser_extract", {"format": "form"}),
    ])

    assert out[1]["data"]["links"] == [
        {"text": "Accounts", "url": ACCOUNTS_URL},
    ]
    assert out[2]["data"]["form"][0]["options"][0]["value"] == "MOMBASA"


def test_extract_invalid_format_fails_structured():
    user = "u-extract2"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_extract", {"format": "json"}),
    ])
    assert out[1]["success"] is False
    assert out[1]["error"]["code"] == "INVALID_FORMAT"


# --------------------------------------------------------------------------- #
# credentials (§18) — encrypted at rest, never exposed                        #
# --------------------------------------------------------------------------- #
def test_credential_marker_without_store_requests_credentials():
    user = _user_id("creds1@test.com")
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_fill", {"selector": "#user", "text": "@credential:username"}),
    ])
    result = out[1]
    assert result["success"] is False
    assert result["error"]["code"] == "CREDENTIALS_NEEDED"
    assert result["data"]["domain"] == "bank.example.com"
    assert result["data"]["field"] == "username"
    # the marker itself is never reflected back in the tool result
    assert "@credential" not in json.dumps(result)


def test_credential_store_encrypts_and_fill_injects_secret():
    user = _user_id("creds2@test.com")
    _attach(user, PAGES)
    with SessionLocal() as db:
        credential_store.save_credential(
            db, user_id=user, domain="bank.example.com",
            username="alice", secret="s3cret-pass",
        )
        raw = db.get(Credential, db.scalar(select(Credential.id))).secret
        assert "s3cret-pass" not in raw  # encrypted at rest
        assert raw != "s3cret-pass"

    out, session = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_fill", {"selector": "#user", "text": "@credential:username"}),
        ("browser_fill", {"selector": "#pass", "text": "@credential:password"}),
    ])
    filling = [r for r in out[1:] if r["success"]]
    assert len(filling) == 2
    # the real secret reached the browser driver but never the model result
    assert session is not None
    assert session.driver.filled == [("#user", "alice"), ("#pass", "s3cret-pass")]
    for result in out[1:]:
        blob = json.dumps(result)
        assert "s3cret-pass" not in blob
        assert "alice" not in blob
        assert "@credential:username" not in blob
        assert "credential_domain" in result["data"]

    # username/password markers with no matching domain are structured fails
    assert credential_store.get_credential(
        SessionLocal(), user_id=user, domain="other.example.com") is None


def test_credential_decrypt_error_is_structured():
    user = _user_id("creds3@test.com")
    with SessionLocal() as db:
        credential_store.save_credential(
            db, user_id=user, domain="bank.example.com",
            username="bob", secret="pw1",
        )
        row = credential_store.get_credential(
            db, user_id=user, domain="bank.example.com")
        # corrupt the token: must raise a structured error, never a stack
        row.secret = "not-a-valid-fernet-token-value"
        db.commit()
        with pytest.raises(credential_store.CredentialStoreError) as exc:
            credential_store.decrypt_row(row)
        assert exc.value.code == "CREDENTIAL_DECRYPT_FAILED"


def test_credential_domain_normalization():
    user = _user_id("creds4@test.com")
    with SessionLocal() as db:
        credential_store.save_credential(
            db, user_id=user, domain="Bank.Example.COM:8443/",
            username="u", secret="pw",
        )
        row = credential_store.get_credential(
            db, user_id=user, domain="bank.example.com")
        assert row is not None
        assert row.domain == "bank.example.com"


def test_upload_and_download_save_artifacts():
    user = "u-art"
    _attach(user, PAGES)
    out, session = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_screenshot", {"full_page": True}),
        ("browser_download", {"url": "https://files.example.com/report.pdf"}),
    ])
    shot = out[1]
    assert shot["success"] is True
    artifact = shot["data"]["artifact"]
    assert artifact["kind"] == "screenshot"
    assert artifact["mime"] == "image/png"
    assert artifact["url"].startswith("/api/artifacts/")
    assert artifact["size_bytes"] > 0
    assert session is not None and "screenshot" in session.driver.screenshots[0]

    dl = out[2]
    assert dl["success"] is True
    dl_artifact = dl["data"]["artifact"]
    assert dl_artifact["kind"] == "download"
    assert dl_artifact["url"] != artifact["url"]
    assert session.driver.downloaded == ["https://files.example.com/report.pdf"]


def test_idle_sweep_closes_stale_sessions():
    user = "u-idle"
    session = _attach(user, PAGES)
    async def run():
        from app.services import browser_session as bs
        await session.bind()
        session.last_used = time.monotonic() - 10_000  # well past idle
        closed = await bs.sweep_idle_sessions()
        assert closed == 1
        assert bs.get_session(user) is None
    asyncio.run(run())


# --------------------------------------------------------------------------- #
# end-to-end §18 approval flow (fake model through the real graph)            #
# --------------------------------------------------------------------------- #
def _approvals_for(client, headers):
    return client.get("/api/approvals", headers=headers).json()


def test_login_flow_credential_approval_end_to_end(client, fake_router,
                                                   auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_open", "args": {"url": LOGIN_URL},
            "id": "call_open1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_fill",
            "args": {"selector": "#user", "text": "@credential:username"},
            "id": "call_fill1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Logged you in securely."))

    first = client.post("/api/chat", json={
        "message": "log into my bank account",
    }, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "paused_approval"
    approval = body["approval"]
    assert approval is not None
    assert approval["tool_name"] == "browser_fill"
    assert approval["kind"] == "tool"
    assert approval["credential"]["domain"] == "bank.example.com"
    assert approval["field"] == "username"
    assert "hunter2" not in json.dumps(body)

    # approve the fill WITHOUT credentials -> the fill re-runs, finds nothing
    # stored and pauses for a dedicated credential entry (§18)
    resumed = client.post(f"/api/approvals/{approval['id']}/approve",
                          headers=headers)
    assert resumed.status_code == 200, resumed.text
    res = resumed.json()
    assert res["status"] == "paused_approval"
    assert res["approval"]["kind"] == "credentials"
    assert res["approval"]["field"] == "username"
    assert "hunter2" not in json.dumps(res)

    # approve WITH the credentials -> stored encrypted, fill injects them
    final = client.post(
        f"/api/approvals/{res['approval']['id']}/approve",
        headers=headers,
        json={"credentials": {"username": "bankuser", "secret": "hunter2"}},
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "completed"
    assert final.json()["reply"] == "Logged you in securely."
    assert "hunter2" not in json.dumps(final.json())
    assert "hunter2" not in json.dumps(_approvals_for(client, headers))
    activity = client.get("/api/activity", headers=headers).json()
    assert "hunter2" not in json.dumps(activity)

    with SessionLocal() as db:
        uid = db.scalar(
            select(User.id).where(User.email == "user@test.com"))
        row = credential_store.get_credential(
            db, user_id=uid, domain="bank.example.com")
        assert row is not None
        assert row.username == "bankuser"
        username, secret = credential_store.decrypt_row(row)
        assert username == "bankuser"
        assert secret == "hunter2"
        rows = db.scalars(
            select(Credential).where(Credential.user_id == uid)).all()
        assert all("hunter2" not in (r.secret or "") for r in rows)


def test_login_flow_credentials_supplied_on_first_approval(client, fake_router,
                                                           auth_headers):
    """The smoother UX: the fill approval already carries a credential
    sub-form; submitting credentials there stores them and the resume injects
    them in one go."""
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_open", "args": {"url": LOGIN_URL},
            "id": "call_open2", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_fill",
            "args": {"selector": "#pass", "text": "@credential:password"},
            "id": "call_fill2", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="You are signed in."))

    first = client.post("/api/chat", json={
        "message": "sign me into my bank",
    }, headers=headers)
    assert first.status_code == 200
    approval = first.json()["approval"]
    assert approval["kind"] == "tool"
    assert approval["credential"]["domain"] == "bank.example.com"

    final = client.post(
        f"/api/approvals/{approval['id']}/approve",
        headers=headers,
        json={"credentials": {"username": "bob", "secret": "p4ssword"}},
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "completed"
    assert "p4ssword" not in json.dumps(final.json())

    with SessionLocal() as db:
        uid = db.scalar(
            select(User.id).where(User.email == "user@test.com"))
        row = credential_store.get_credential(
            db, user_id=uid, domain="bank.example.com")
        assert row is not None
        assert credential_store.decrypt_row(row) == ("bob", "p4ssword")


def test_credentials_approval_cannot_be_approved_without_credentials(
        client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_open", "args": {"url": LOGIN_URL},
            "id": "call_open3", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_fill",
            "args": {"selector": "#user", "text": "@credential:username"},
            "id": "call_fill3", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="done."))

    first = client.post("/api/chat", json={"message": "login please"},
                        headers=headers)
    approval = first.json()["approval"]
    resumed = client.post(f"/api/approvals/{approval['id']}/approve",
                          headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["approval"]["kind"] == "credentials"

    # empty credential body must be rejected (no infinite resume loop) — the
    # pydantic schema rejects blanks before our handler ever runs
    rejected = client.post(
        f"/api/approvals/{resumed.json()['approval']['id']}/approve",
        headers=headers, json={"credentials": {"username": "", "secret": ""}})
    assert rejected.status_code == 422


# --------------------------------------------------------------------------- #
# artifacts endpoint (§16)                                                    #
# --------------------------------------------------------------------------- #
def test_artifact_endpoint_owner_only_and_traversal_safe(client, auth_headers):
    from sqlalchemy import select

    from app.config import get_settings

    from app.database.session import SessionLocal
    from app.models.user import User

    headers = auth_headers("owner@test.com")
    other = auth_headers("other@test.com")
    with SessionLocal() as db:
        owner_id = db.scalar(
            select(User.id).where(User.email == "owner@test.com"))
    user_folder = get_settings().upload_dir / owner_id
    user_folder.mkdir(parents=True, exist_ok=True)
    (user_folder / "shot.png").write_bytes(b"\x89PNG fake")

    png_url = f"/api/artifacts/{owner_id}/shot.png"
    ok = client.get(png_url, headers=headers)
    assert ok.status_code == 200, ok.text

    # a different user cannot read someone else's artifact
    assert client.get(png_url, headers=other).status_code == 403
    # traversal out of the upload root is refused
    assert client.get(f"/api/artifacts/{owner_id}/../other.png",
                      headers=headers).status_code == 404
    assert client.get(f"/api/artifacts/{owner_id}/missing.png",
                      headers=headers).status_code == 404


# --------------------------------------------------------------------------- #
# screenshots collected from a run (§16)                                       #
# --------------------------------------------------------------------------- #
def test_run_collects_artifacts_in_response(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_open", "args": {"url": LOGIN_URL},
            "id": "call_open4", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_screenshot", "args": {},
            "id": "call_shot1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Here is a screenshot."))

    resp = client.post("/api/chat", json={
        "message": "show me the accounts page",
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["artifacts"]) == 1
    artifact = body["artifacts"][0]
    assert artifact["kind"] == "screenshot"
    assert artifact["mime"] == "image/png"
    # and the file is actually retrievable though the authenticated endpoint
    from app.config import get_settings

    path = get_settings().upload_dir / body["artifacts"][0]["url"].split(
        "/api/artifacts/", 1)[1]
    assert path.exists()
    fetched = client.get(artifact["url"], headers=headers)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/png")


def test_browser_status_emits_through_stream(client, fake_router, auth_headers):
    headers = auth_headers()
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "browser_open", "args": {"url": "https://example.com/"},
            "id": "call_open5", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Done."))

    with client.stream("POST", "/api/chat", json={
        "message": "navigate to https://example.com/ in the browser",
        "stream": True,
    }, headers=headers) as resp:
        assert resp.status_code == 200
        events = []
        event_type = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data: "):
                events.append((event_type, line.split(":", 1)[1].strip()))
    types = {name for name, _ in events}
    assert "browser_status" in types
    payload = next(json.loads(d) for t, d in events if t == "browser_status")
    assert payload["browser"]["url"] == "https://example.com/"
    assert payload["browser"]["session_open"] is True
    assert json.dumps(events).count("browser_status") >= 1
    assert "intent" in types


def test_private_page_via_env_override_blocked_download():
    """browser_download URLs are validated like every other browser URL."""
    user = "u-dl-guard"
    _attach(user, PAGES)
    out, _ = _run_flow(user, [
        ("browser_open", {"url": LOGIN_URL}),
        ("browser_download", {"url": "ftp://files.example.com/x"}),
    ])
    assert out[1]["error"]["code"] == "BLOCKED_SCHEME"
    assert out[0]["success"] is True