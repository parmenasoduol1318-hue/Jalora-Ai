"""P13 (Phase I): personal-assistant integrations (§63-68, §76).

Covers the mock calendar conflict logic, contact resolution feeding SMS, map
distance math, weather parsing, the translation round-trip, the integration
registry and disconnect/revoke behaviour, drive read/write, plus the acceptance
chain (contacts -> calendar -> SMS).
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.user import User
from app.services import calendar as calendar_service
from app.services import contacts as contacts_service
from app.services import maps as maps_service
from app.tools import registry
from app.tools.base import PermissionLevel


def _invoke(name: str, args: dict) -> dict:
    spec = registry.get(name)
    assert spec is not None, f"tool {name} is not registered"
    raw = asyncio.run(spec.tool.ainvoke(args))
    return json.loads(raw)


def _ctx(user_id: str) -> None:
    set_tool_context(ToolContext(user_id=user_id, run_id=None,
                                 task_id=None, conversation_id=None))


def _first_user_id() -> str:
    db = SessionLocal()
    try:
        return db.scalar(select(User.id).limit(1))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# registry (§76)                                                              #
# --------------------------------------------------------------------------- #
def test_provider_registry_requires_auth(client):
    assert client.get("/api/integrations/providers").status_code == 401


def test_provider_registry_lists_assistant_providers(client, auth_headers):
    headers = auth_headers()
    resp = client.get("/api/integrations/providers", headers=headers)
    assert resp.status_code == 200, resp.text
    names = {p["name"] for p in resp.json()["providers"]}
    assert {"gmail", "calendar", "drive", "contacts", "maps", "weather",
            "translate", "twilio", "telnyx", "search", "ollama", "db",
            "redis"} <= names


def test_tools_registered_with_expected_permissions():
    expected = {
        "find_events": PermissionLevel.AUTO,
        "create_event": PermissionLevel.CONFIRM,
        "update_event": PermissionLevel.CONFIRM,
        "delete_event": PermissionLevel.CONFIRM,
        "search_drive": PermissionLevel.AUTO,
        "read_drive_file": PermissionLevel.AUTO,
        "find_contact": PermissionLevel.AUTO,
        "find_place": PermissionLevel.AUTO,
        "get_directions": PermissionLevel.AUTO,
        "calculate_distance": PermissionLevel.AUTO,
        "find_nearby": PermissionLevel.AUTO,
        "get_weather": PermissionLevel.AUTO,
        "translate": PermissionLevel.AUTO,
    }
    for name, perm in expected.items():
        spec = registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.permission == perm, (name, spec.permission, perm)


# --------------------------------------------------------------------------- #
# calendar (§63)                                                             #
# --------------------------------------------------------------------------- #
def test_calendar_creates_then_detects_conflict(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    first = _invoke("create_event", {
        "title": "Team sync", "start": "2026-10-10T14:00:00",
        "end": "2026-10-10T15:00:00"})
    assert first["success"] is True, first
    assert first["data"]["status"] == "created"
    assert first["data"]["event"]["title"] == "Team sync"

    clash = _invoke("create_event", {
        "title": "Overlapping", "start": "2026-10-10T14:30:00",
        "end": "2026-10-10T15:30:00"})
    assert clash["success"] is True, clash
    assert clash["data"]["status"] == "conflict"
    assert clash["data"]["conflicts"]
    assert clash["data"]["suggested"]["start"], "a free alternative is proposed"


def test_calendar_find_events_lists_created(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    _invoke("create_event", {"title": "Lunch", "start": "2026-10-11T12:00:00"})
    found = _invoke("find_events", {"start": "2026-10-11T00:00:00",
                                    "end": "2026-10-11T23:59:59"})
    assert found["success"] is True
    titles = [e["title"] for e in found["data"]["events"]]
    assert "Lunch" in titles


# --------------------------------------------------------------------------- #
# contacts (§65) + resolution feeding comms                                   #
# --------------------------------------------------------------------------- #
def test_contact_resolution_names_to_number(client, auth_headers):
    auth_headers()
    uid = _first_user_id()
    _ctx(uid)
    saved = _invoke("create_contact", {
        "name": "John Doe", "phone": "+254712345678",
        "email": "john@example.com"})
    assert saved["success"] is True, saved
    assert saved["data"]["name"] == "John Doe"

    assert contacts_service.resolve_phone_number(uid, "John") == "+254712345678"
    found = _invoke("find_contact", {"name": "john"})
    assert found["success"] is True
    assert found["data"]["contacts"][0]["phone"] == "+254712345678"


def test_sms_tool_resolves_contact_name(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    _invoke("create_contact", {"name": "John Doe", "phone": "+254712345678"})
    result = _invoke("send_sms", {"phone_number": "John", "message": "See you!"})
    assert result["success"] is True, result
    assert result["data"]["to"] == "+254712345678"
    assert result["data"]["provider"] == "mock"


def test_sms_unknown_contact_fails_cleanly(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("send_sms", {"phone_number": "Nobody", "message": "hi"})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_PHONE"


# --------------------------------------------------------------------------- #
# drive (§64)                                                                 #
# --------------------------------------------------------------------------- #
def test_drive_create_search_read_roundtrip(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    created = _invoke("create_drive_file", {
        "name": "meeting-notes.txt", "content": "agenda: budget"})
    assert created["success"] is True, created
    file_id = created["data"]["id"]

    found = _invoke("search_drive", {"query": "meeting"})
    assert found["success"] is True
    assert any(f["id"] == file_id for f in found["data"]["files"])

    read = _invoke("read_drive_file", {"file_id": file_id})
    assert read["success"] is True
    assert read["data"]["content"] == "agenda: budget"


# --------------------------------------------------------------------------- #
# maps (§66)                                                                  #
# --------------------------------------------------------------------------- #
def test_haversine_nairobi_mombasa():
    nairobi = (-1.286389, 36.817223)
    mombasa = (-4.043477, 39.668206)
    distance = maps_service.haversine_km(*nairobi, *mombasa)
    assert 400 < distance < 500, distance


def test_directions_math_via_tool():
    result = _invoke("get_directions", {"origin": "Nairobi", "destination": "Mombasa"})
    assert result["success"] is True, result
    assert result["data"]["distance_km"] > 300
    assert result["data"]["duration_min"] > 0

    distance = _invoke("calculate_distance",
                       {"origin": "Nairobi", "destination": "Mombasa"})
    assert distance["success"] is True
    assert 400 < distance["data"]["distance_km"] < 480


# --------------------------------------------------------------------------- #
# weather (§67)                                                              #
# --------------------------------------------------------------------------- #
def test_weather_parsing(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("get_weather", {"location": "Nairobi"})
    assert result["success"] is True, result
    current = result["data"]["current"]
    assert isinstance(current["temp_c"], (int, float))
    assert current["condition"]
    assert len(result["data"]["daily"]) == 3


def test_weather_requires_a_location(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    out = _invoke("get_weather", {})
    assert out["success"] is False
    assert out["error"]["code"] == "NO_LOCATION"


# --------------------------------------------------------------------------- #
# translation (§68)                                                          #
# --------------------------------------------------------------------------- #
def test_translate_round_trip(client):
    to_sw = _invoke("translate", {"text": "hello", "target": "swahili"})
    assert to_sw["success"] is True, to_sw
    assert to_sw["data"]["text"] == "jambo"
    assert to_sw["data"]["target"] == "sw"

    to_en = _invoke("translate", {"text": "jambo", "target": "english"})
    assert to_en["success"] is True, to_en
    assert to_en["data"]["text"] == "hello"


# --------------------------------------------------------------------------- #
# disconnect / revoke (§76)                                                   #
# --------------------------------------------------------------------------- #
def test_disconnect_revokes_provider_status(client, auth_headers):
    headers = auth_headers()
    created = client.post("/api/integrations",
                          json={"provider": "calendar_mock", "config": {}},
                          headers=headers)
    assert created.status_code == 200, created.text
    integration_id = created.json()["id"]

    listed = client.get("/api/integrations/providers", headers=headers).json()["providers"]
    calendar = next(p for p in listed if p["name"] == "calendar")
    assert calendar["connected"] is True

    test = client.post(f"/api/integrations/{integration_id}/test", headers=headers)
    assert test.status_code == 200 and test.json()["ok"] is True

    assert client.delete(f"/api/integrations/{integration_id}",
                         headers=headers).status_code == 204
    listed = client.get("/api/integrations/providers", headers=headers).json()["providers"]
    calendar = next(p for p in listed if p["name"] == "calendar")
    assert calendar["connected"] is False


def test_calendar_auto_falls_back_to_mock_without_integration(client, auth_headers):
    auth_headers()
    db = SessionLocal()
    try:
        assert calendar_service.resolve_provider(
            db, user_id=_first_user_id()) == "mock"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# acceptance chain: "Schedule a meeting ... with John ... and text him"       #
# --------------------------------------------------------------------------- #
def test_acceptance_contacts_calendar_sms_chain(client, auth_headers):
    auth_headers()
    user_id = _first_user_id()
    _ctx(user_id)

    # 1) contacts — John is on file with a number.
    saved = _invoke("create_contact", {"name": "John", "phone": "+254700111222"})
    assert saved["success"] is True, saved

    # 2) calendar — book the meeting (conflict-free slot).
    event = _invoke("create_event", {
        "title": "Meeting with John", "start": "2026-10-11T14:00:00",
        "end": "2026-10-11T14:30:00",
        "attendees": ["John"]})
    assert event["success"] is True and event["data"]["status"] == "created", event

    # 3) comms — resolve "John" and text the invite.
    sms = _invoke("send_sms", {
        "phone_number": "John",
        "message": "Invite: meeting 2026-10-11 14:00"})
    assert sms["success"] is True, sms
    assert sms["data"]["to"] == "+254700111222"

    assert contacts_service.resolve_phone_number(user_id, "John") == "+254700111222"
