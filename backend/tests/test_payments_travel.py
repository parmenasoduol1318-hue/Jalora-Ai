"""P14 (Phase N): payments (Stripe Connect) + travel (Amadeus) integrations.

Mock-first, fully offline: the conftest blanks every real credential and forces
``JALORA_PAYMENTS_PROVIDER=auto`` / ``JALORA_TRAVEL_PROVIDER=auto``, so the
payment & travel adapters resolve to their deterministic offline mocks. No real
httpx calls are made against Stripe or Amadeus here; we instead assert the
contract (registry cards, resolve_provider, tool outputs, permission gating,
determinism, and clean error paths) — mirroring test_phase_i_integrations.py.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from sqlalchemy import select

from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.user import User
from app.services import payments, search_service, travel
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
# permissions + registration                                                   #
# --------------------------------------------------------------------------- #
def test_payments_travel_tools_registered_with_expected_permissions():
    expected = {
        "send_payment": PermissionLevel.STRICT,
        "search_flights": PermissionLevel.AUTO,
        "book_flight": PermissionLevel.CONFIRM,
        "book_reservation": PermissionLevel.CONFIRM,
    }
    for name, perm in expected.items():
        spec = registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.permission == perm, (name, spec.permission, perm)


# --------------------------------------------------------------------------- #
# registry cards (§76)                                                        #
# --------------------------------------------------------------------------- #
def test_payments_travel_cards_listed(client, auth_headers):
    headers = auth_headers()
    resp = client.get("/api/integrations/providers", headers=headers)
    assert resp.status_code == 200, resp.text
    cards = {c["name"]: c for c in resp.json()["providers"]}

    # Stripe: per-user OAuth (Stripe Connect).
    assert "stripe" in cards, "stripe card missing from /providers"
    stripe = cards["stripe"]
    assert stripe["area"] == "payments"
    assert stripe["kind"] == "oauth"
    assert stripe["connectable"] is True
    assert stripe["configured"] is False  # no creds in tests -> mock
    assert stripe["connected"] is False
    assert stripe["logo"] == "stripe"

    # Amadeus: env-keyed (client-credentials), no per-user connect flow.
    assert "amadeus" in cards, "amadeus card missing from /providers"
    amadeus = cards["amadeus"]
    assert amadeus["area"] == "travel"
    assert amadeus["kind"] == "keys"
    assert amadeus["connectable"] is False
    assert amadeus["configured"] is False  # no creds in tests
    assert amadeus["connected"] is False
    assert amadeus["logo"] == "amadeus"


# --------------------------------------------------------------------------- #
# provider resolution (mock-first)                                            #
# --------------------------------------------------------------------------- #
def test_payments_resolve_provider_is_mock(client, auth_headers):
    auth_headers()
    db = SessionLocal()
    try:
        assert payments.resolve_provider(db, user_id=_first_user_id()) == "mock"
    finally:
        db.close()


def test_travel_resolve_provider_is_mock():
    assert travel.resolve_provider() == "mock"


def test_flight_provider_requires_configured_amadeus_outside_tests(monkeypatch):
    monkeypatch.setattr(travel, "get_settings", lambda: SimpleNamespace(
        env="production",
        travel_provider="auto",
        amadeus_client_id=None,
        amadeus_client_secret=None,
        amadeus_base_url="https://test.api.amadeus.com",
    ))

    try:
        travel.resolve_provider()
    except travel.TravelError as exc:
        assert exc.code == "PROVIDER_NOT_CONFIGURED"
    else:
        raise AssertionError("Live flight search must not fall back to mock offers.")


def test_flight_provider_rejects_unapproved_hosts(monkeypatch):
    monkeypatch.setattr(travel, "get_settings", lambda: SimpleNamespace(
        env="production",
        travel_provider="amadeus",
        amadeus_client_id="configured",
        amadeus_client_secret="configured",
        amadeus_base_url="https://amadeus.example.org",
    ))

    try:
        travel.resolve_provider()
    except travel.TravelError as exc:
        assert exc.code == "UNAPPROVED_FLIGHT_SOURCE"
    else:
        raise AssertionError("Unapproved flight API hosts must be rejected.")


def test_bus_search_never_uses_generic_web_index_as_live_inventory(monkeypatch):
    async def unexpected_search(*args, **kwargs):
        raise AssertionError("Generic web search cannot serve as bus inventory.")

    monkeypatch.setattr(search_service, "search", unexpected_search)
    result = asyncio.run(travel.search_buses(
        origin="Mombasa", destination="Nairobi", date="2026-10-31",
    ))

    assert result["status"] == "provider_handoff"
    assert result["offers"] == []
    assert result["booking_url"] == "https://buupass.com/Mombasa-to-Nairobi"


# --------------------------------------------------------------------------- #
# payments (§A send_payment, user: Stripe Connect mock)                       #
# --------------------------------------------------------------------------- #
def test_send_payment_mock_receipt(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("send_payment", {"recipient": "John Doe", "amount": 10})
    assert result["success"] is True, result
    data = result["data"]
    assert data["provider"] == "mock"
    assert data["mock"] is True
    assert data["to"] == "John Doe"
    assert data["amount"] == 1000          # 10.00 major -> 1000 minor (cents)
    assert data["currency"] == "usd"
    assert data["receipt_id"].startswith("pymt_")
    assert data["status"] == "succeeded"


def test_send_payment_amount_in_cents(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("send_payment", {"recipient": "+254700111222", "amount": 12.50})
    assert result["success"] is True, result
    assert result["data"]["amount"] == 1250
    assert result["data"]["currency"] == "usd"


def test_send_payment_is_deterministic(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    a = _invoke("send_payment", {"recipient": "John", "amount": 10})
    b = _invoke("send_payment", {"recipient": "John", "amount": 10})
    assert a["data"]["receipt_id"] == b["data"]["receipt_id"]


def test_send_payment_different_recipient_different_id(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    a = _invoke("send_payment", {"recipient": "John", "amount": 10})
    b = _invoke("send_payment", {"recipient": "Jane", "amount": 10})
    assert a["data"]["receipt_id"] != b["data"]["receipt_id"]


def test_send_payment_requires_recipient(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("send_payment", {"recipient": "", "amount": 10})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_RECIPIENT"


def test_send_payment_requires_positive_amount(client, auth_headers):
    auth_headers()
    _ctx(_first_user_id())
    result = _invoke("send_payment", {"recipient": "John", "amount": 0})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_AMOUNT"


# --------------------------------------------------------------------------- #
# travel (§A search_flights / book_flight / book_reservation, mock)           #
# --------------------------------------------------------------------------- #
def test_search_flights_mock_returns_offers():
    result = _invoke("search_flights", {
        "origin": "Nairobi", "destination": "Mombasa", "date": "2026-10-20"})
    assert result["success"] is True, result
    data = result["data"]
    assert data["provider"] == "mock"
    assert data["origin"] == "NBO"
    assert data["destination"] == "MBA"
    assert data["offers"], "expected at least one mock flight offer"
    offer = data["offers"][0]
    assert offer["offer_id"].startswith("flt_")
    assert offer["carrier"] in ("KQ", "KS")
    assert offer["price"] > 0
    assert offer["currency"] == "usd"


def test_search_flights_default_route_is_deterministic():
    a = _invoke("search_flights", {"origin": "JFK", "destination": "LHR",
                                   "date": "2026-10-20"})
    b = _invoke("search_flights", {"origin": "JFK", "destination": "LHR",
                                   "date": "2026-10-20"})
    assert a["data"]["offers"][0]["offer_id"] == b["data"]["offers"][0]["offer_id"]


def test_search_flights_requires_args():
    result = _invoke("search_flights", {"origin": "", "destination": "", "date": ""})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_ARGS"


def test_book_flight_mock_deterministic():
    search = _invoke("search_flights", {"origin": "JFK", "destination": "LHR",
                                        "date": "2026-10-20"})
    offer_id = search["data"]["offers"][0]["offer_id"]

    a = _invoke("book_flight", {"flight_offer_id": offer_id,
                                "passenger_name": "Alice"})
    b = _invoke("book_flight", {"flight_offer_id": offer_id,
                                "passenger_name": "Alice"})
    assert a["success"] is True, a
    assert a["data"]["status"] == "confirmed"
    assert a["data"]["provider"] == "mock"
    assert a["data"]["booking_id"].startswith("res_")
    assert a["data"]["passenger"] == "Alice"
    assert a["data"]["offer_id"] == offer_id
    assert a["data"]["booking_id"] == b["data"]["booking_id"]


def test_book_flight_requires_passenger_name():
    result = _invoke("book_flight", {"flight_offer_id": "flt_x", "passenger_name": ""})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_ARGS"


def test_book_reservation_mock_deterministic():
    a = _invoke("book_reservation", {"venue": "The Carnivore",
                                     "date": "2026-10-20", "party_size": 4})
    b = _invoke("book_reservation", {"venue": "The Carnivore",
                                     "date": "2026-10-20", "party_size": 4})
    assert a["success"] is True, a
    data = a["data"]
    assert data["provider"] == "mock"
    assert data["status"] == "confirmed"
    assert data["venue"] == "The Carnivore"
    assert data["party_size"] == 4
    assert data["booking_id"].startswith("res_")
    assert a["data"]["booking_id"] == b["data"]["booking_id"]
