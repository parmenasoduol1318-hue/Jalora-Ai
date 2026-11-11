"""Tests for travel & hotel tools (Priority 6)."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.errors import ToolResult
from app.services import travel
from app.agents.graph import (
    _hotel_booking_details_request,
    _hotel_booking_recovery,
)
from app.tools import registry as real_registry
from app.tools import travel_tools
from app.tools.routing import route_request


def _run(coro):
    """Helper to run an async coroutine in tests."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# --- service layer (travel.py): bus inventory safety -----------------------

def test_search_buses_hands_off_to_official_route_without_fake_offers():
    result = _run(travel.search_buses(
        origin="Mombasa",
        destination="Nairobi",
        date="2026-10-31",
    ))

    assert result["provider"] == "buupass"
    assert result["status"] == "provider_handoff"
    assert result["offers"] == []
    assert result["booking_url"] == "https://buupass.com/Mombasa-to-Nairobi"
    assert "select the requested travel date" in result["notice"]
    assert "payment" in result["notice"]
    assert "did not retrieve live bus options" in result["notice"]


def test_search_buses_encodes_user_route_as_a_safe_buupass_url():
    result = _run(travel.search_buses(
        origin="city/one",
        destination="Two & Three",
        date="2026-10-31",
    ))

    assert result["booking_url"] == (
        "https://buupass.com/City%2FOne-to-Two%20%26%20Three"
    )


def test_search_buses_rejects_invalid_date():
    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_buses(
            origin="Mombasa",
            destination="Nairobi",
            date="2026-13-40",
        ))

    assert exc_info.value.code == "INVALID_DATE"


def test_search_buses_async_requires_args():
    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_buses(origin="", destination="MBA", date="2025-01-15"))
    assert exc_info.value.code == "INVALID_ARGS"


def test_search_buses_tool_returns_only_verified_buupass_route_cards(monkeypatch):
    cards = [
        (
            "MashPoa\n9:00 AM\nMombasa\n8h 00m\n4 seats left\n"
            "5:00 PM\nNairobi\nFromKSH 1,600\nper seat\n"
            "Route MOMBASA - NAIROBI"
        ),
        (
            "MashPoa\n12:30 PM\nMombasa\n17h 30m\n8 seats left\n"
            "6:00 AM\nNairobi\nFromKSH 1,500\nper seat\n"
            "Route MOMBASA - PORT VICTORIA"
        ),
    ]

    async def browser_open(url):
        assert url == "https://buupass.com/Mombasa-to-Nairobi"
        return ToolResult.ok("Opened BuuPass.")

    async def browser_wait(seconds):
        assert seconds == 5
        return ToolResult.ok("Waited.")

    async def browser_extract(format):
        assert format == "cards"
        return ToolResult.ok("Extracted.", {
            "url": "https://buupass.com/Mombasa-to-Nairobi",
            "text": "Mombasa Nairobi Sat, 31 Oct · One way",
            "cards": cards,
        })

    monkeypatch.setattr(travel_tools.browser_tools, "browser_open", browser_open)
    monkeypatch.setattr(travel_tools.browser_tools, "browser_wait", browser_wait)
    monkeypatch.setattr(travel_tools.browser_tools, "browser_extract", browser_extract)
    result = _run(travel_tools.search_buses(
        origin="Mombasa",
        destination="Nairobi",
        date="2026-10-31",
    ))

    assert result.success is True
    assert result.data["status"] == "live_inventory"
    assert len(result.data["offers"]) == 1
    assert result.data["offers"][0] == {
        "operator": "MashPoa",
        "departure": "9:00 AM",
        "arrival": "5:00 PM",
        "duration": "8h 00m",
        "seats_left": 4,
        "starting_fare": "1,600",
        "provider_card_index": 0,
    }
    assert "no booking or payment was made." in result.message.lower()
    assert "Reply with the option number" in result.message


def test_search_buses_tool_refuses_wrong_provider_date(monkeypatch):
    async def browser_open(url):
        return ToolResult.ok("Opened BuuPass.")

    async def browser_wait(seconds):
        return ToolResult.ok("Waited.")

    async def browser_extract(format):
        return ToolResult.ok("Extracted.", {
            "url": "https://buupass.com/Mombasa-to-Nairobi",
            "text": "Mombasa Nairobi Sun, 1 Nov",
            "cards": [],
        })

    monkeypatch.setattr(travel_tools.browser_tools, "browser_open", browser_open)
    monkeypatch.setattr(travel_tools.browser_tools, "browser_wait", browser_wait)
    monkeypatch.setattr(travel_tools.browser_tools, "browser_extract", browser_extract)
    result = _run(travel_tools.search_buses(
        origin="Mombasa",
        destination="Nairobi",
        date="2026-10-31",
    ))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "LIVE_INVENTORY_UNVERIFIED"
    assert result.data["offers"] == []
    assert result.data["booking_url"] == "https://buupass.com/Mombasa-to-Nairobi"


def test_search_hotels_async():
    result = _run(travel.search_hotels(
        location="Nairobi, Kenya",
        check_in="2026-12-10",
        check_out="2026-12-13",
        adults=2,
        rooms=1,
        children_ages=[5, 10],
    ))
    parsed = urlsplit(result["booking_url"])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.booking.com"
    assert parsed.path == "/searchresults.html"
    assert result["provider"] == "booking.com"
    assert result["status"] == "search_ready"
    assert query["ss"] == ["Nairobi, Kenya"]
    assert query["checkin"] == ["2026-12-10"]
    assert query["checkout"] == ["2026-12-13"]
    assert query["group_adults"] == ["2"]
    assert query["group_children"] == ["2"]
    assert query["age"] == ["5,10"]
    assert query["no_rooms"] == ["1"]
    assert "No reservation has been made" in result["notice"]


def test_search_hotels_rejects_bad_dates_and_guest_counts():
    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_hotels(
            location="Nairobi",
            check_in="2026-12-13",
            check_out="2026-12-10",
            adults=2,
            rooms=1,
        ))
    assert exc_info.value.code == "INVALID_DATE"

    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_hotels(
            location="Nairobi",
            check_in="2026-12-10",
            check_out="2026-12-13",
            adults=0,
            rooms=1,
        ))
    assert exc_info.value.code == "INVALID_GUESTS"

    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_hotels(
            location="Nairobi",
            check_in="20261210",
            check_out="2026-12-13",
            adults=2,
            rooms=1,
        ))
    assert exc_info.value.code == "INVALID_DATE"

    with pytest.raises(travel.TravelError) as exc_info:
        _run(travel.search_hotels(
            location="Nairobi",
            check_in="2026-12-10",
            check_out="2026-12-13",
            adults=2,
            rooms=1,
            children_ages=[18],
        ))
    assert exc_info.value.code == "INVALID_CHILD_AGES"


def test_hotel_tool_returns_checklist_when_required_details_are_missing():
    for tool_name in ("search_hotels", "book_hotel"):
        spec = real_registry.get(tool_name)
        result = json.loads(asyncio.run(spec.tool.ainvoke({
            "location": "Sarova Whitesands Beach Resort & Spa, Mombasa",
        })))
        assert result["success"] is True
        data = result["data"]
        assert data["status"] == "needs_details"
        assert data["missing_details"] == [
            "Check-in date (YYYY-MM-DD)",
            "Check-out date (YYYY-MM-DD)",
            "Number of adults",
            "Number of rooms",
        ]


def test_book_hotel_returns_booking_com_handoff_not_fake_confirmation():
    spec = real_registry.get("book_hotel")
    result = json.loads(asyncio.run(spec.tool.ainvoke({
        "location": "Mombasa",
        "check_in": "2026-12-10",
        "check_out": "2026-12-13",
        "adults": 2,
        "rooms": 1,
    })))
    assert result["success"] is True
    data = result["data"]
    assert data["provider"] == "booking.com"
    assert data["status"] == "booking_handoff"
    assert "booking.com" in data["booking_url"]
    assert "No reservation has been made" in data["notice"]
    assert data.get("booking_id") is None


# --- tool registration -------------------------------------------------------

def test_search_buses_registered():
    spec = real_registry.get("search_buses")
    assert spec is not None
    assert spec.permission.name == "AUTO"
    assert "official BuuPass" in spec.description


def test_search_hotels_registered():
    spec = real_registry.get("search_hotels")
    assert spec is not None
    assert spec.permission.name == "AUTO"


def test_book_hotel_registered_as_non_booking_handoff():
    spec = real_registry.get("book_hotel")
    assert spec is not None
    assert spec.permission.name == "AUTO"
    assert "hotels" in spec.tags
    assert "booking" in spec.tags
    assert "booking.com" in spec.tags


def test_search_buses_tagged_travel():
    spec = real_registry.get("search_buses")
    assert spec is not None
    assert "travel" in spec.tags
    assert "buses" in spec.tags
    assert spec.permission.name == "AUTO"


def test_all_hotel_tools_in_registry():
    names = {spec.name for spec in real_registry.all()}
    assert {"search_hotels", "book_hotel"}.issubset(names)


def test_hotel_booking_prompt_requires_all_trip_details_and_honest_handoff():
    from app.agents.prompts import build_system_prompt

    prompt = build_system_prompt()
    assert "destination" in prompt
    assert "number of adults" in prompt
    assert "Booking.com" in prompt
    assert "Never report a booking as confirmed from a handoff" in prompt
    assert "not a workflow" in prompt


def test_bus_booking_prompt_prevents_fake_inventory_and_claims():
    from app.agents.prompts import build_system_prompt

    prompt = build_system_prompt()
    assert 'status="live_inventory"' in prompt
    assert "Never invent bus operators" in prompt
    assert "the exact requested route and date" in prompt
    assert 'format="form"' in prompt
    assert "normal" in prompt and "confirmation approval" in prompt
    assert "Stop at BuuPass's payment prompt" in prompt
    assert "mobile-money PINs" in prompt


def test_incomplete_hotel_booking_plan_returns_required_details_not_plan_dump():
    response = _hotel_booking_recovery(
        "book for me this hotel Sarova Whitesands Beach Resort & Spa Mombasa"
    )
    assert response is not None
    assert "no reservation was made" in response
    assert "check-in date and check-out date" in response
    assert "number of adults" in response
    assert "number of rooms" in response
    assert "Booking.com link" in response
    assert "hotel ID" not in response


def test_hotel_recovery_does_not_match_non_booking_queries():
    assert _hotel_booking_recovery("Find hotels in Mombasa") is None


def test_hotel_booking_without_trip_details_is_asked_before_planning():
    response = _hotel_booking_details_request(
        "book for me this hotel Sarova Whitesands Beach Resort & Spa Mombasa"
    )
    assert response is not None
    assert "check-in and check-out dates (YYYY-MM-DD)" in response
    assert "number of adults" in response
    assert "number of rooms" in response
    assert "Booking.com" in response


def test_hotel_booking_with_complete_trip_details_can_use_booking_tool():
    response = _hotel_booking_details_request(
        "Book Sarova Whitesands Mombasa from 2026-12-10 to 2026-12-13 "
        "for 2 adults and 1 room."
    )
    assert response is None


def test_hotel_request_discovers_booking_com_tools():
    search = route_request("Find hotels in Nairobi for next week")
    assert search.tool_names is not None
    assert "search_hotels" in search.tool_names

    booking = route_request("Book a hotel in Nairobi")
    assert booking.tool_names is not None
    assert "book_hotel" in booking.tool_names
