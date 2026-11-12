"""Phase N: generic OAuth framework + Google Calendar view/reminders (§34-38, §63)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.integration import Integration
from app.models.schedule import Schedule
from app.models.user import User
from app.services import oauth as oauth_service
from app.services.credential_store import fernet_encrypt


@pytest.fixture
def oauth_settings(monkeypatch):
    """Apply provider/env vars for one test, always restoring cached settings."""
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _register(client, auth_headers, email: str = "user@test.com") -> dict:
    headers = auth_headers(email)
    return {"headers": headers}


def _approve_calendar_action(client, headers, response):
    assert response.status_code == 202, response.text
    pending = response.json()
    assert pending["status"] == "paused_approval"
    approval = pending["approval"]
    assert approval["task_id"]
    assert approval["run_id"]
    shared = client.get("/api/approvals", headers=headers)
    assert shared.status_code == 200
    assert any(
        row["id"] == approval["id"]
        and row["task_id"] == approval["task_id"]
        and row["run_id"] == approval["run_id"]
        for row in shared.json()
    )
    approved = client.post(
        f"/api/approvals/{approval['id']}/approve", headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    return pending


def _calendar_event(client, headers, title):
    response = client.get("/api/calendar", headers=headers)
    assert response.status_code == 200, response.text
    return next(event for event in response.json()["events"] if event["title"] == title)


# --- registry ---------------------------------------------------------------
def test_registry_marks_oauth_providers(client, auth_headers):
    reg = client.get("/api/integrations/providers", headers=auth_headers())
    assert reg.status_code == 200, reg.text
    cards = {c["name"]: c for c in reg.json()["providers"]}
    for name in ("calendar", "drive", "slack", "github", "notion", "spotify", "dropbox"):
        assert cards[name]["kind"] == "oauth"
        assert cards[name]["connectable"] is True
    # No client credentials in tests -> not configured, so the UI shows
    # "Add credentials to connect" rather than a working Connect.
    assert cards["calendar"]["configured"] is False
    assert cards["slack"]["configured"] is False


def test_connect_requires_credentials(client, auth_headers):
    res = client.post("/api/integrations/calendar/connect", headers=auth_headers())
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


# --- OAuth redirect + callback ---------------------------------------------
def test_oauth_start_returns_authorization_url(client, auth_headers, oauth_settings):
    oauth_settings(JALORA_GMAIL_CLIENT_ID="cid_test",
                   JALORA_GMAIL_CLIENT_SECRET="csec_test")
    res = client.post("/api/integrations/calendar/connect", headers=auth_headers())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "redirect"
    assert body["authorization_url"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth")
    assert "calendar.readonly" in body["authorization_url"]
    # A pending row holds only the hashed state.
    with SessionLocal() as db:
        row = db.scalar(select(Integration).where(
            Integration.provider == "calendar"))
        assert row is not None and row.status == "pending"
        assert row.config.get("oauth_state_hash")
        assert "oauth_state_hash" not in res.text


def test_oauth_callback_stores_encrypted_tokens(client, auth_headers,
                                                oauth_settings, monkeypatch):
    oauth_settings(JALORA_GMAIL_CLIENT_ID="cid_test",
                   JALORA_GMAIL_CLIENT_SECRET="csec_test")
    start = client.post("/api/integrations/calendar/connect", headers=auth_headers())
    url = start.json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]

    async def _fake_exchange(provider, code, **kwargs):
        assert provider == "calendar" and code == "the-code"
        return {"access_token": "plain-access", "refresh_token": "plain-refresh",
                "expires_in": 3600}

    async def _fake_account(provider, token):
        return "me@example.com"

    monkeypatch.setattr(oauth_service, "exchange_code", _fake_exchange)
    monkeypatch.setattr(oauth_service, "fetch_account", _fake_account)

    res = client.get(
        "/api/integrations/oauth/callback",
        params={"provider": "calendar", "code": "the-code", "state": state},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "connected"

    with SessionLocal() as db:
        row = db.scalar(select(Integration).where(
            Integration.provider == "calendar"))
        assert row.status == "connected"
        # Tokens are never stored or returned in plaintext.
        assert row.config["access_token"] != "plain-access"
        assert row.config["refresh_token"] != "plain-refresh"
        assert "plain-access" not in res.text


def test_oauth_browser_callback_returns_to_integrations_and_shows_connected(
        client, auth_headers, oauth_settings, monkeypatch):
    headers = auth_headers("calendar-browser-oauth@test.com")
    oauth_settings(
        JALORA_GMAIL_CLIENT_ID="cid_test",
        JALORA_GMAIL_CLIENT_SECRET="csec_test",
        JALORA_PUBLIC_BASE_URL="https://jalora-ai.onrender.com",
        JALORA_FRONTEND_BASE_URL="",
    )
    start = client.post(
        "/api/integrations/calendar/connect", headers=headers
    )
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    async def _fake_exchange(provider, code, **kwargs):
        assert provider == "calendar" and code == "the-code"
        return {
            "access_token": "plain-access",
            "refresh_token": "plain-refresh",
            "expires_in": 3600,
        }

    async def _fake_account(provider, token):
        return "me@example.com"

    monkeypatch.setattr(oauth_service, "exchange_code", _fake_exchange)
    monkeypatch.setattr(oauth_service, "fetch_account", _fake_account)

    response = client.get(
        "/api/integrations/oauth/callback",
        params={"provider": "calendar", "code": "the-code", "state": state},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://jalora-ai.vercel.app/"
        "?integration=connected&provider=calendar"
    )
    cards = client.get(
        "/api/integrations/providers", headers=headers
    ).json()["providers"]
    calendar = next(card for card in cards if card["name"] == "calendar")
    assert calendar["connected"] is True


def test_oauth_callback_rejects_bad_state(client, auth_headers, oauth_settings):
    oauth_settings(JALORA_GMAIL_CLIENT_ID="cid_test",
                   JALORA_GMAIL_CLIENT_SECRET="csec_test")
    client.post("/api/integrations/calendar/connect", headers=auth_headers())
    res = client.get(
        "/api/integrations/oauth/callback",
        params={"provider": "calendar", "code": "x", "state": "forged"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "INVALID_STATE"


# --- calendar reminders -----------------------------------------------------
def test_event_create_schedules_two_reminders(client, auth_headers):
    headers = auth_headers()
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    res = client.post("/api/calendar/events", headers=headers, json={
        "title": "Team sync", "start": start.isoformat(),
    })
    _approve_calendar_action(client, headers, res)
    event = _calendar_event(client, headers, "Team sync")
    assert event["timezone"] == "Africa/Nairobi"

    with SessionLocal() as db:
        rows = db.scalars(select(Schedule).where(
            Schedule.notify_only.is_(True))).all()
        linked = [r for r in rows if (r.spec or {}).get("event_id") == event["id"]]
        assert len(linked) == 2
        assert all(r.kind == "once" for r in linked)
        assert "Team sync" in linked[0].task_template


def test_event_timezone_is_validated_and_persisted(client, auth_headers):
    headers = auth_headers()
    start = (datetime.now(timezone.utc) + timedelta(hours=6)).replace(microsecond=0)
    created = client.post("/api/calendar/events", headers=headers, json={
        "title": "Nairobi review", "start": start.isoformat(),
        "timezone": "Africa/Nairobi",
    })
    _approve_calendar_action(client, headers, created)
    assert _calendar_event(client, headers, "Nairobi review")["timezone"] == "Africa/Nairobi"

    invalid = client.post("/api/calendar/events", headers=headers, json={
        "title": "Invalid zone", "start": start.isoformat(),
        "timezone": "Not/AZone",
    })
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_TIMEZONE"


def test_event_delete_removes_reminders(client, auth_headers):
    headers = auth_headers()
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).replace(microsecond=0)
    created = client.post("/api/calendar/events", headers=headers, json={
        "title": "Dentist", "start": start.isoformat(),
    })
    _approve_calendar_action(client, headers, created)
    event_id = _calendar_event(client, headers, "Dentist")["id"]

    with SessionLocal() as db:
        before = [r for r in db.scalars(select(Schedule)).all()
                  if (r.spec or {}).get("event_id") == event_id]
        assert len(before) == 2

    deleted = client.delete(f"/api/calendar/events/{event_id}", headers=headers)
    _approve_calendar_action(client, headers, deleted)

    with SessionLocal() as db:
        after = [r for r in db.scalars(select(Schedule)).all()
                 if (r.spec or {}).get("event_id") == event_id]
        assert after == []


def test_event_patch_updates_owned_event(client, auth_headers):
    headers = auth_headers()
    start = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(microsecond=0)
    created = client.post("/api/calendar/events", headers=headers, json={
        "title": "Before", "start": start.isoformat(),
    })
    _approve_calendar_action(client, headers, created)
    event_id = _calendar_event(client, headers, "Before")["id"]
    updated = client.patch(f"/api/calendar/events/{event_id}", headers=headers,
                           json={"title": "After"})
    _approve_calendar_action(client, headers, updated)
    assert _calendar_event(client, headers, "After")["id"] == event_id


def test_calendar_action_respects_denied_tool_policy(client, auth_headers):
    headers = auth_headers()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@test.com"))
        user.prefs = {
            **(user.prefs or {}),
            "autonomy": 3,
            "tool_policies": {"create_event": "denied"},
        }
        db.commit()

    start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    response = client.post("/api/calendar/events", headers=headers, json={
        "title": "Blocked event", "start": start.isoformat(),
    })
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "TOOL_DENIED"
    assert client.get("/api/approvals", headers=headers).json() == []


def test_list_calendar_merges_google_events(client, auth_headers, oauth_settings,
                                            monkeypatch):
    oauth_settings(JALORA_CALENDAR_PROVIDER="google")
    headers = auth_headers()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@test.com"))
        db.add(Integration(
            user_id=user.id, provider="calendar", status="connected",
            config={"access_token": fernet_encrypt("tok"),
                    "token_expires_at": 9_999_999_999},
        ))
        db.commit()

    remote = {
        "id": "g:remote-1", "title": "Google standup",
        "start": "2026-10-20T09:00:00", "end": "2026-10-20T09:30:00",
        "location": "Meet", "description": "", "attendees": [],
        "provider": "google", "status": "confirmed", "external_id": "remote-1",
    }
    monkeypatch.setattr("app.services.calendar._google_list_events",
                        lambda *a, **k: [remote])

    res = client.get("/api/calendar", headers=headers,
                     params={"start": "2026-10-19T00:00:00Z",
                             "end": "2026-10-21T00:00:00Z"})
    assert res.status_code == 200, res.text
    ids = [e["id"] for e in res.json()["events"]]
    assert "g:remote-1" in ids


def test_google_create_uses_event_timezone_wall_time(client, auth_headers,
                                                     oauth_settings, monkeypatch):
    from app.services import calendar as calendar_service

    oauth_settings(JALORA_CALENDAR_PROVIDER="google")
    headers = auth_headers()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@test.com"))
        db.add(Integration(
            user_id=user.id, provider="calendar", status="connected",
            config={"access_token": fernet_encrypt("tok"),
                    "token_expires_at": 9_999_999_999},
        ))
        db.commit()

    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""
        content = b'{"id":"remote-created"}'

        def json(self):
            return {"id": "remote-created"}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@test.com"))
    created = calendar_service._google_create_event(
        user.id, "Review", datetime(2026, 10, 20, 9, 0),
        datetime(2026, 10, 20, 10, 0), "", "", [], "Africa/Nairobi")
    assert created == "remote-created"
    assert captured["json"]["start"] == {
        "dateTime": "2026-10-20T12:00:00+03:00", "timeZone": "Africa/Nairobi",
    }
