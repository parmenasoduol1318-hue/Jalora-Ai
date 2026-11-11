"""Travel tools (A§ payments & travel).

Flight search (`search_flights`) is read-only (AUTO).  Booking actions
(`book_flight`, `book_reservation`) write a reservation and require user approval
(CONFIRM). Bus requests hand the user off to the official BuuPass route page
until an authorized live booking integration is available.
"""
from __future__ import annotations

import re
from datetime import date as date_type

from app.errors import ToolResult
from app.services import travel
from app.tools import browser as browser_tools
from app.tools.base import PermissionLevel, registry


async def search_flights(origin: str, destination: str, date: str,
                         return_date: str = "", adults: int = 1,
                         max_results: int = 10) -> ToolResult:
    """Search flights through the approved Amadeus API only."""
    try:
        result = await travel.search_flights(
            origin=origin, destination=destination, date=date,
            return_date=return_date, adults=adults, max_results=max_results,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    offers = result.get("offers", [])
    if not offers:
        return ToolResult.ok(
            f"No flights found for {origin} -> {destination} on {date}.",
            result,
        )
    return ToolResult.ok(
        f"Found {len(offers)} flight option(s) from {origin} to {destination}.",
        result,
    )


async def book_flight(flight_offer_id: str, passenger_name: str,
                      contact: str = "") -> ToolResult:
    """Book a flight offer (by id from search_flights). May pause for confirmation
    per the user's approval settings before confirming the reservation."""
    try:
        result = await travel.book_flight(
            flight_offer_id=flight_offer_id,
            passenger_name=passenger_name,
            contact=contact,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Flight booked for {result['passenger']} ({result['offer_id']}) — "
        f"confirmation {result['booking_id']}.",
        result,
    )


async def book_reservation(venue: str, date: str, party_size: int = 1,
                           contact: str = "") -> ToolResult:
    """Book a restaurant / hotel / activity reservation. May pause for
    confirmation per the user's approval settings before confirming."""
    try:
        result = await travel.book_reservation(
            venue=venue, date=date, party_size=party_size, contact=contact,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"{party_size} seat(s) reserved at {result['venue']} on {date} "
        f"(confirmation {result['booking_id']}).",
        result,
    )


async def search_buses(origin: str, destination: str, date: str) -> ToolResult:
    """Search the official BuuPass site for current, date-verified bus listings without booking."""
    try:
        result = await travel.search_buses(
            origin=origin, destination=destination, date=date,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)

    booking_url = result["booking_url"]
    opened = await browser_tools.browser_open(booking_url)
    if not opened.success:
        return _buupass_inventory_unavailable(result, opened.message)
    waited = await browser_tools.browser_wait(5)
    if not waited.success:
        return _buupass_inventory_unavailable(result, waited.message)
    extracted = await browser_tools.browser_extract("cards")
    if not extracted.success:
        return _buupass_inventory_unavailable(result, extracted.message)

    page_data = extracted.data
    page_text = str(page_data.get("text", ""))
    page_url = str(page_data.get("url", ""))
    if not _is_buupass_url(page_url):
        return _buupass_inventory_unavailable(
            result, "The browser did not remain on the official BuuPass site."
        )
    try:
        requested_date = date_type.fromisoformat(date)
    except ValueError:
        return _buupass_inventory_unavailable(
            result, "The requested travel date could not be verified."
        )
    date_label = (
        f"{requested_date.strftime('%a, %d %b')}"
        .replace(" 0", " ")
        .casefold()
    )
    if date_label not in page_text.casefold():
        return _buupass_inventory_unavailable(
            result,
            f"BuuPass did not show the requested date ({date}).",
        )

    offers = _buupass_offers(page_data.get("cards", []), origin, destination)
    if not offers:
        return ToolResult.ok(
            (
                f"No current BuuPass listings matching {origin} to {destination} "
                f"on {date} could be verified. Check the official provider page: "
                f"{booking_url}"
            ),
            {
                **result,
                "status": "live_inventory_empty",
                "offers": [],
                "source_url": page_url,
            },
        )

    lines = [
        f"Current BuuPass options for {origin} to {destination} on {date}:"
    ]
    for index, offer in enumerate(offers, start=1):
        lines.append(
            f"{index}. {offer['operator']} - {offer['departure']} "
            f"{origin} to {offer['arrival']} {destination}; "
            f"{offer['duration']}; {offer['seats_left']} seats left; "
            f"from KSH {offer['starting_fare']} per seat."
        )
    lines.append(
        f"Source: {booking_url}. Reply with the option number you want to "
        "continue with. No seat was selected and no booking or payment was made."
    )
    return ToolResult.ok(
        "\n".join(lines),
        {
            **result,
            "status": "live_inventory",
            "offers": offers,
            "source_url": page_url,
            "notice": "Live provider inventory only; no booking or payment was made.",
        },
    )


def _is_buupass_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "buupass.com",
        "www.buupass.com",
    }


def _route_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _buupass_offers(
    cards: object, origin: str, destination: str
) -> list[dict[str, object]]:
    if not isinstance(cards, list):
        return []
    expected_route = _route_key(f"Route {origin} {destination}")
    offers: list[dict[str, object]] = []
    for card_index, raw_card in enumerate(cards):
        if not isinstance(raw_card, str):
            continue
        lines = [line.strip() for line in raw_card.splitlines() if line.strip()]
        route = next(
            (line for line in lines if line.casefold().startswith("route ")), ""
        )
        if _route_key(route) != expected_route:
            continue
        times = re.findall(
            r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s*[AP]M\b", raw_card, re.IGNORECASE
        )
        seats = re.search(r"\b(\d+)\s+seats?\s+left\b", raw_card, re.IGNORECASE)
        fare = re.search(
            r"\bFrom\s*KSH\s*([\d,]+)", raw_card, re.IGNORECASE
        )
        duration = re.search(r"\b\d+\s*h\s*\d+\s*m\b", raw_card, re.IGNORECASE)
        if len(lines) < 2 or len(times) < 2 or not seats or not fare or not duration:
            continue
        offers.append({
            "operator": lines[0],
            "departure": times[0],
            "arrival": times[1],
            "duration": re.sub(r"\s+", " ", duration.group(0)),
            "seats_left": int(seats.group(1)),
            "starting_fare": fare.group(1),
            "provider_card_index": card_index,
        })
    return offers


def _buupass_inventory_unavailable(
    result: dict, reason: str
) -> ToolResult:
    message = (
        f"Could not verify live BuuPass inventory: {reason} "
        f"Use the official route page: {result['booking_url']}"
    )
    return ToolResult.fail(
        "LIVE_INVENTORY_UNVERIFIED",
        reason,
        message=message,
        data={
            "provider": "buupass",
            "status": "provider_handoff",
            "origin": result["origin"],
            "destination": result["destination"],
            "date": result["date"],
            "offers": [],
            "booking_url": result["booking_url"],
        },
    )


registry.register(search_flights, permission=PermissionLevel.AUTO,
                  category="travel", requires_internet=True,
                  tags=["travel", "flights", "external"])
registry.register(book_flight, permission=PermissionLevel.CONFIRM,
                  category="travel", requires_internet=True,
                  tags=["travel", "flights", "booking", "external"])
registry.register(book_reservation, permission=PermissionLevel.CONFIRM,
                  category="travel", requires_internet=True,
                  tags=["travel", "reservation", "booking", "external"])
registry.register(search_buses, permission=PermissionLevel.AUTO,
                  category="travel", requires_internet=True,
                  tags=["travel", "buses", "external"])
