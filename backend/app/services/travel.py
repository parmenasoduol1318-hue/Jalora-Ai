"""Travel adapters using approved providers for live results.

Flights use the official Amadeus API. Bus search is restricted to public search
and booking flows on the official BuuPass site. The browser-backed tool reads
visible provider listings; this service prepares the route URL and never
supplies synthetic fares, operators, seats, or booking confirmations.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date as date_type
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.config import get_settings

logger = logging.getLogger("jalora.travel")
BUUPASS_BOOKING_URL = "https://buupass.com/"

# Amadeus for Developers base URL. Production uses api.amadeus.com; the sandbox
# (test.api.amadeus.com) is the default so prod is opt-in via JALORA_AMADEUS_BASE_URL.
AMADEUS_DEFAULT_BASE = "https://test.api.amadeus.com"
_APPROVED_AMADEUS_HOSTS = frozenset({
    "test.api.amadeus.com",
    "api.amadeus.com",
})


def _amadeus_base() -> str:
    base = (get_settings().amadeus_base_url or AMADEUS_DEFAULT_BASE).rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_AMADEUS_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise TravelError(
            "UNAPPROVED_FLIGHT_SOURCE",
            "Flight searches and bookings are restricted to the official Amadeus API.",
        )
    return base


class TravelError(Exception):
    """Structured failure for travel operations (never carries secrets)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fingerprint(*parts) -> str:
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Deterministic offline fixtures (mock)
# ---------------------------------------------------------------------------
# A tiny, stable roster of routes the mock can always "find".  Keys are normalised
# to upper-case 3-letter codes / city names so lookups are deterministic.
_MOCK_ROUTES: dict[tuple[str, str], list[dict]] = {
    ("JFK", "LHR"): [
        {"carrier": "BA",   "dep": "10:30", "arr": "22:45", "price": 720.00, "dur": 405},
        {"carrier": "DL",   "dep": "14:15", "arr": "02:30+1", "price": 780.00, "dur": 375},
    ],
    ("SFO", "NRT"): [
        {"carrier": "JL",   "dep": "11:55", "arr": "17:20+1", "price": 1150.00, "dur": 625},
        {"carrier": "UA",   "dep": "13:40", "arr": "20:10+1", "price": 1290.00, "dur": 635},
    ],
    ("NBO", "MBA"): [  # Nairobi -> Mombasa (domestic)
        {"carrier": "KQ", "dep": "07:00", "arr": "07:50", "price": 85.00, "dur": 50},
        {"carrier": "KS", "dep": "12:15", "arr": "13:05", "price": 85.00, "dur": 50},
    ],
}
_MOCK_DEFAULT = [
    {"carrier": "XZ", "dep": "09:00", "arr": "11:30", "price": 340.00, "dur": 150},
]


def _normalise_code(place: str) -> str:
    p = (place or "").strip().upper()
    # Common city aliases -> IATA-ish codes.
    aliases = {"nairobi": "NBO", "mombasa": "MBA", "jkia": "NBO", "london": "LHR",
               "jfk": "JFK", "san francisco": "SFO", "tokyo": "NRT"}
    return aliases.get(p.lower() if p.lower() in aliases else p, p)


def _mock_offers(origin: str, destination: str, date: str,
                 return_date: str, adults: int, max_results: int) -> list[dict]:
    o = _normalise_code(origin)
    d = _normalise_code(destination)
    template = _MOCK_ROUTES.get((o, d), _MOCK_ROUTES.get((d, o), _MOCK_DEFAULT))
    offers: list[dict] = []
    for item in template:
        if len(offers) >= max(1, max_results):
            break
        oid = "flt_" + _fingerprint("mock", o, d, date, return_date, adults,
                                    item["carrier"], item["dep"])
        offers.append({
            "offer_id": oid,
            "provider": "mock",
            "origin": o,
            "destination": d,
            "departure": {"date": date, "time": item["dep"]},
            "arrival": {"date": date if "+" not in item["arr"] else "", "time": item["arr"]},
            "duration_min": item["dur"] * adults,
            "carrier": item["carrier"],
            "price": round(item["price"] * adults, 2),
            "currency": "usd",
            "passengers": adults,
        })
    return offers


def resolve_provider() -> str:
    """Resolve the approved flight provider without live mock fallbacks."""
    settings = get_settings()
    choice = (settings.travel_provider or "auto").strip().lower()
    if choice == "mock":
        if settings.env.lower() == "test":
            return "mock"
        raise TravelError(
            "LIVE_PROVIDER_REQUIRED",
            "Synthetic travel offers are disabled outside the test environment.",
        )
    if choice == "amadeus":
        if not settings.amadeus_client_id or not settings.amadeus_client_secret:
            raise TravelError(
                "PROVIDER_NOT_CONFIGURED",
                "Configure Amadeus API credentials to search or book real flights.",
            )
        _amadeus_base()
        return "amadeus"
    if choice != "auto":
        raise TravelError(
            "PROVIDER_NOT_CONFIGURED",
            f"Unsupported travel provider '{choice}'. Approved flight provider: amadeus.",
        )
    if settings.amadeus_client_id and settings.amadeus_client_secret:
        _amadeus_base()
        return "amadeus"
    if settings.env.lower() == "test":
        return "mock"
    raise TravelError(
        "PROVIDER_NOT_CONFIGURED",
        "Configure Amadeus API credentials to search or book real flights.",
    )


async def _amadeus_token() -> str:
    settings = get_settings()
    base = _amadeus_base()
    data = {
        "grant_type": "client_credentials",
        "client_id": settings.amadeus_client_id or "",
        "client_secret": settings.amadeus_client_secret or "",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base}/v1/security/oauth2/token", data=data,
            headers={"User-Agent": "JaloraAI/0.1", "Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        raise TravelError(
            "PROVIDER_ERROR",
            f"Amadeus auth HTTP {resp.status_code}: {resp.text[:300]}",
        )
    body = resp.json() if resp.content else {}
    token = body.get("access_token")
    if not token:
        raise TravelError("PROVIDER_ERROR", "Amadeus did not return an access token.")
    return token


async def _amadeus_search_flights(*, origin, destination, date, return_date,
                                  adults, max_results) -> list[dict]:
    base = _amadeus_base()
    token = await _amadeus_token()
    params = {
        "originLocationCode": _normalise_code(origin),
        "destinationLocationCode": _normalise_code(destination),
        "departureDate": date,
        "adults": str(adults),
        "max": str(max(1, max_results)),
    }
    if return_date:
        params["returnDate"] = return_date
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{base}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "JaloraAI/0.1"},
        )
    if resp.status_code >= 400:
        raise TravelError(
            "PROVIDER_ERROR",
            f"Amadeus flights HTTP {resp.status_code}: {resp.text[:300]}",
        )
    body = resp.json() if resp.content else {}
    items = body.get("data", [])
    offers = []
    for it in items:
        for seg in (it.get("itineraries") or [{}])[0].get("segments") or []:
            dep = seg.get("departure", {})
            arr = seg.get("arrival", {})
            price = (((it.get("price") or {}).get("total") or 0))
            offers.append({
                "offer_id": str(it.get("id")),
                "provider": "amadeus",
                "origin": dep.get("iataCode", origin),
                "destination": arr.get("iataCode", destination),
                "departure": {"date": dep.get("at", "")[:10], "time": dep.get("at", "")[11:]},
                "arrival": {"date": arr.get("at", "")[:10], "time": arr.get("at", "")[11:]},
                "duration_min": 0,
                "carrier": (seg.get("carrierCode") or "??"),
                "price": float(price) if price else 0.0,
                "currency": ((it.get("price") or {}).get("currency") or "usd"),
                "passengers": adults,
            })
    return offers


async def search_flights(*, origin: str, destination: str, date: str,
                         return_date: str = "", adults: int = 1,
                         max_results: int = 10) -> dict:
    """Search flights only through the approved Amadeus API."""
    if not origin or not destination or not date:
        raise TravelError("INVALID_ARGS",
                          "origin, destination and date are required.")
    provider = resolve_provider()
    if provider == "amadeus":
        offers = await _amadeus_search_flights(
            origin=origin, destination=destination, date=date,
            return_date=return_date, adults=adults, max_results=max_results)
    else:
        offers = _mock_offers(origin, destination, date, return_date,
                              adults, max_results)
    return {
        "provider": provider,
        "origin": _normalise_code(origin),
        "destination": _normalise_code(destination),
        "date": date,
        "return_date": return_date,
        "adults": adults,
        "offers": offers,
    }


async def book_flight(*, flight_offer_id: str, passenger_name: str,
                      contact: str = "") -> dict:
    """Book an Amadeus flight offer; synthetic confirmations are test-only."""
    if not flight_offer_id:
        raise TravelError("INVALID_ARGS", "flight_offer_id is required.")
    if not passenger_name:
        raise TravelError("INVALID_ARGS", "passenger_name is required.")
    provider = resolve_provider()
    if provider == "amadeus":
        base = _amadeus_base()
        token = await _amadeus_token()
        payload = {
            "data": {
                "type": "flight-order",
                "flights": [{"offerId": flight_offer_id,
                             "travelers": [{"id": "1", "name": {"firstName": "Traveller"}}]}],
            }
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}/booking/flight-orders",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "User-Agent": "JaloraAI/0.1"},
            )
        if resp.status_code >= 400:
            raise TravelError(
                "PROVIDER_ERROR",
                f"Amadeus booking HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json() if resp.content else {}
        booking_id = str(body.get("data", {}).get("id") or _fingerprint("amadeus", flight_offer_id))
    else:
        if get_settings().env.lower() != "test":
            raise TravelError(
                "LIVE_PROVIDER_REQUIRED",
                "A live flight-booking provider is required to confirm a booking.",
            )
        booking_id = "res_" + _fingerprint("mock", provider, flight_offer_id, passenger_name)
    return {
        "provider": provider,
        "booking_id": booking_id,
        "status": "confirmed",
        "offer_id": flight_offer_id,
        "passenger": passenger_name,
        "contact": contact,
    }


async def book_reservation(*, venue: str, date: str, party_size: int = 1,
                           contact: str = "") -> dict:
    """Return test fixtures only; Amadeus does not book generic reservations."""
    if not venue:
        raise TravelError("INVALID_ARGS", "venue is required.")
    if not date:
        raise TravelError("INVALID_ARGS", "date is required.")
    try:
        party_size = int(party_size)
    except (TypeError, ValueError):
        party_size = 1
    provider = resolve_provider()
    if provider == "amadeus":
        raise TravelError(
            "PROVIDER_NOT_SUPPORTED",
            "The configured flight provider cannot book generic reservations.",
        )
    if get_settings().env.lower() != "test":
        raise TravelError(
            "LIVE_PROVIDER_REQUIRED",
            "A live reservation provider is required to confirm a booking.",
        )
    booking_id = "res_" + _fingerprint("mock", "reservation", venue, date, party_size)
    return {
        "provider": provider,
        "booking_id": booking_id,
        "status": "confirmed",
        "venue": venue,
        "date": date,
        "party_size": max(1, party_size),
        "contact": contact,
    }


# ---------------------------------------------------------------------------
# BuuPass bus search
# ---------------------------------------------------------------------------
async def search_buses(*, origin: str, destination: str, date: str) -> dict:
    """Validate a request and prepare its official BuuPass route URL.

    The route page is based on BuuPass's public route URLs. Live listings are
    retrieved separately by the browser-backed tool, not from search snippets
    or local fixtures.
    """
    if (
        not isinstance(origin, str)
        or not origin.strip()
        or not isinstance(destination, str)
        or not destination.strip()
    ):
        raise TravelError("INVALID_ARGS", "origin, destination and date are required.")
    if not isinstance(date, str):
        raise TravelError(
            "INVALID_DATE",
            "Use a travel date in YYYY-MM-DD format.",
        )
    try:
        travel_date = date_type.fromisoformat(date)
    except ValueError as exc:
        raise TravelError(
            "INVALID_DATE",
            "Use a travel date in YYYY-MM-DD format.",
        ) from exc
    if travel_date.isoformat() != date:
        raise TravelError(
            "INVALID_DATE",
            "Use a travel date in YYYY-MM-DD format.",
        )
    origin = " ".join(origin.split())
    destination = " ".join(destination.split())
    route = (
        f"{quote(origin.title(), safe='')}-to-{quote(destination.title(), safe='')}"
    )
    booking_url = f"{BUUPASS_BOOKING_URL.rstrip('/')}/{route}"
    return {
        "provider": "buupass",
        "status": "provider_handoff",
        "origin": origin,
        "destination": destination,
        "date": date,
        "offers": [],
        "booking_url": booking_url,
        "notice": (
            "Jalora did not retrieve live bus options or make a booking. Open "
            "the official BuuPass route page, select the requested travel date "
            "and an available bus, then follow BuuPass's prompts for seats, "
            "passenger details, and payment."
        ),
    }


# ---------------------------------------------------------------------------
# Booking.com hotel search and booking handoff
# ---------------------------------------------------------------------------
_BOOKING_COM_SEARCH_URL = "https://www.booking.com/searchresults.html"


def _booking_com_search_url(
    *,
    location: str,
    check_in: str,
    check_out: str,
    adults: int,
    rooms: int,
    children_ages: list[int],
) -> str:
    params = [
        ("ss", location),
        ("checkin", check_in),
        ("checkout", check_out),
        ("group_adults", str(adults)),
        ("no_rooms", str(rooms)),
        ("group_children", str(len(children_ages))),
    ]
    if children_ages:
        params.append(("age", ",".join(str(age) for age in children_ages)))
    return f"{_BOOKING_COM_SEARCH_URL}?{urlencode(params)}"


def _validate_hotel_search(
    *,
    location: str,
    check_in: str,
    check_out: str,
    adults: int,
    rooms: int,
    children_ages: list[int],
) -> None:
    if not isinstance(location, str) or not location.strip():
        raise TravelError("INVALID_ARGS", "A destination is required.")
    if len(location) > 200:
        raise TravelError("INVALID_ARGS", "The destination must be 200 characters or fewer.")
    if not isinstance(check_in, str) or not isinstance(check_out, str):
        raise TravelError(
            "INVALID_DATE",
            "Use valid check-in and check-out dates in YYYY-MM-DD format.",
        )
    try:
        arrival = date_type.fromisoformat(check_in)
        departure = date_type.fromisoformat(check_out)
    except ValueError as exc:
        raise TravelError(
            "INVALID_DATE",
            "Use valid check-in and check-out dates in YYYY-MM-DD format.",
        ) from exc
    if arrival.isoformat() != check_in or departure.isoformat() != check_out:
        raise TravelError(
            "INVALID_DATE",
            "Use check-in and check-out dates in YYYY-MM-DD format.",
        )
    if departure <= arrival:
        raise TravelError("INVALID_DATE", "Check-out must be later than check-in.")
    if not isinstance(adults, int) or isinstance(adults, bool) or not 1 <= adults <= 30:
        raise TravelError("INVALID_GUESTS", "Adults must be between 1 and 30.")
    if not isinstance(rooms, int) or isinstance(rooms, bool) or not 1 <= rooms <= 30:
        raise TravelError("INVALID_ROOMS", "Rooms must be between 1 and 30.")
    if (
        not isinstance(children_ages, list)
        or len(children_ages) > 30
        or any(
            not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 17
            for age in children_ages
        )
    ):
        raise TravelError(
            "INVALID_CHILD_AGES",
            "Provide an age from 0 to 17 for each child.",
        )


async def search_hotels(
    *,
    location: str,
    check_in: str,
    check_out: str,
    adults: int,
    rooms: int,
    children_ages: list[int] | None = None,
) -> dict:
    """Prepare a live hotel search on Booking.com using validated trip details."""
    location = " ".join(location.split()) if isinstance(location, str) else ""
    if children_ages is None:
        children_ages = []
    _validate_hotel_search(
        location=location,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        rooms=rooms,
        children_ages=children_ages,
    )
    return {
        "provider": "booking.com",
        "status": "search_ready",
        "location": location,
        "check_in": check_in,
        "check_out": check_out,
        "adults": adults,
        "children_ages": children_ages,
        "rooms": rooms,
        "booking_url": _booking_com_search_url(
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            rooms=rooms,
            children_ages=children_ages,
        ),
        "notice": (
            "Availability, current prices, property details, and booking are "
            "provided by Booking.com. No reservation has been made."
        ),
    }


async def book_hotel(
    *,
    location: str,
    check_in: str,
    check_out: str,
    adults: int,
    rooms: int,
    children_ages: list[int] | None = None,
) -> dict:
    """Create a Booking.com handoff; the user completes and confirms there."""
    result = await search_hotels(
        location=location,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        rooms=rooms,
        children_ages=children_ages,
    )
    result["status"] = "booking_handoff"
    result["notice"] = (
        "No reservation has been made in Jalora. Open Booking.com to choose a "
        "property, review its current price and policies, enter traveler details, "
        "and complete the booking there."
    )
    return result
