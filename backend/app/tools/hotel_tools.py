"""Booking.com hotel search and user-completed booking handoff."""
from __future__ import annotations

from app.errors import ToolResult
from app.services import travel
from app.tools.base import PermissionLevel, registry

_REQUIRED_DETAILS = (
    ("location", "Destination (city, area, or property name and city)"),
    ("check_in", "Check-in date (YYYY-MM-DD)"),
    ("check_out", "Check-out date (YYYY-MM-DD)"),
    ("adults", "Number of adults"),
    ("rooms", "Number of rooms"),
)


def _missing_details(
    *,
    location: str,
    check_in: str,
    check_out: str,
    adults: int | None,
    rooms: int | None,
) -> list[str]:
    values = {
        "location": bool((location or "").strip()),
        "check_in": bool((check_in or "").strip()),
        "check_out": bool((check_out or "").strip()),
        "adults": adults is not None,
        "rooms": rooms is not None,
    }
    return [label for key, label in _REQUIRED_DETAILS if not values[key]]


def _needs_details(missing: list[str], children_ages: list[int] | None) -> ToolResult:
    required = list(missing)
    if children_ages is None:
        required.append("If children are travelling, each child's age")
    return ToolResult.ok(
        "I need a few details before I can prepare your Booking.com hotel search: "
        + "; ".join(missing)
        + ". If children are travelling, include each child's age.",
        {
            "status": "needs_details",
            "missing_details": missing,
            "required_details": required,
            "children_ages_optional": True,
            "provider": "booking.com",
        },
    )


async def search_hotels(
    location: str = "",
    check_in: str = "",
    check_out: str = "",
    adults: int | None = None,
    rooms: int | None = None,
    children_ages: list[int] | None = None,
) -> ToolResult:
    """Prepare a Booking.com hotel search.

    Required details: destination, check-in and check-out dates in YYYY-MM-DD
    format, adult count, and room count. If children are travelling, provide
    their ages. When details are missing, returns a clear checklist to ask the
    user. The result is a Booking.com link; it does not invent hotel listings
    or reserve a room.
    """
    missing = _missing_details(
        location=location,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        rooms=rooms,
    )
    if missing:
        return _needs_details(missing, children_ages)
    try:
        result = await travel.search_hotels(
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            rooms=rooms,
            children_ages=children_ages,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Your Booking.com hotel search for {result['location']} is ready. "
        "Open the link to see live availability and prices.",
        result,
    )


async def book_hotel(
    location: str = "",
    check_in: str = "",
    check_out: str = "",
    adults: int | None = None,
    rooms: int | None = None,
    children_ages: list[int] | None = None,
) -> ToolResult:
    """Prepare a Booking.com handoff for a hotel after collecting trip details.

    Pass the full hotel name and destination together in location. Required trip
    details are check_in, check_out, adults, and rooms; children_ages is optional
    unless children are travelling. Missing details are returned as a checklist.
    This does not make or confirm a reservation. The user selects a property,
    reviews its current terms, and completes the booking on Booking.com.
    """
    missing = _missing_details(
        location=location,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        rooms=rooms,
    )
    if missing:
        return _needs_details(missing, children_ages)
    try:
        result = await travel.book_hotel(
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            rooms=rooms,
            children_ages=children_ages,
        )
    except travel.TravelError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        "No reservation has been made in Jalora. Open Booking.com to choose a "
        "property, check its current price and policies, and complete the booking.",
        result,
    )


registry.register(
    search_hotels,
    permission=PermissionLevel.AUTO,
    category="travel",
    requires_internet=True,
    tags=["travel", "hotels", "booking.com", "external"],
    side_effect="read_only",
    default_for_actions=["search"],
)
registry.register(
    book_hotel,
    permission=PermissionLevel.AUTO,
    category="travel",
    requires_internet=True,
    tags=["travel", "hotels", "booking", "booking.com", "external"],
    side_effect="read_only",
    default_for_actions=["book"],
)
