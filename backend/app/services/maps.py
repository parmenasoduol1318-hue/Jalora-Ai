"""Maps & places service (§66).

Keyless by default: OpenStreetMap Nominatim for geocoding, OSRM for routing.
A deterministic offline mock keeps the whole assistant (and the test suite)
working without network access. Distances use the haversine formula.
"""
from __future__ import annotations

import hashlib
import math

import httpx

from app.config import get_settings

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_OSRM = "https://router.project-osrm.org/route/v1"

# Small offline gazetteer so mock results are meaningful for common queries.
_GAZETTEER: dict[str, tuple[float, float, str]] = {
    "nairobi": (-1.286389, 36.817223, "Nairobi, Kenya"),
    "mombasa": (-4.043477, 39.668206, "Mombasa, Kenya"),
    "kisumu": (-0.091702, 34.767956, "Kisumu, Kenya"),
    "nakuru": (-0.303099, 36.080025, "Nakuru, Kenya"),
    "london": (51.507351, -0.127758, "London, United Kingdom"),
    "paris": (48.856613, 2.352222, "Paris, France"),
    "new york": (40.712776, -74.005974, "New York, USA"),
    "lagos": (6.524379, 3.379206, "Lagos, Nigeria"),
    "kampala": (0.347596, 32.582520, "Kampala, Uganda"),
    "dar es salaam": (-6.792354, 39.208328, "Dar es Salaam, Tanzania"),
}


class MapsError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return round(2 * r * math.asin(math.sqrt(a)), 3)


def resolve_provider() -> str:
    choice = (get_settings().maps_provider or "auto").strip().lower()
    if choice == "auto":
        return "osm"
    if choice not in ("mock", "osm", "google"):
        raise MapsError("PROVIDER_NOT_CONFIGURED",
                        f"Unknown maps provider '{choice}' (auto|osm|mock).")
    return choice


def _coords(text: str) -> tuple[float, float] | None:
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None
    return None


def _mock_place(query: str) -> dict:
    key = (query or "").strip().lower()
    if key in _GAZETTEER:
        lat, lon, display = _GAZETTEER[key]
    else:
        seed = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
        lat = round(-1.5 + (seed % 700) / 1000.0, 6)
        lon = round(36.0 + (seed % 900) / 1000.0, 6)
        display = (query or "").strip().title() or "Unknown place"
    return {"name": (query or "").strip(), "display_name": display,
            "lat": lat, "lon": lon, "provider": "mock"}


async def _nominatim_place(query: str) -> dict:
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": "JaloraAI/0.1 (personal assistant)"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(_NOMINATIM, params=params, headers=headers)
    if resp.status_code >= 400:
        raise MapsError("PROVIDER_ERROR",
                        f"Nominatim HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if not data:
        raise MapsError("PLACE_NOT_FOUND", f"Could not find a place named '{query}'.")
    top = data[0]
    return {"name": query, "display_name": top.get("display_name", query),
            "lat": float(top["lat"]), "lon": float(top["lon"]), "provider": "osm"}


async def _place(query: str) -> dict:
    raw = (query or "").strip()
    if not raw:
        raise MapsError("INVALID_PLACE", "A place or 'lat,lon' is required.")
    coords = _coords(raw)
    if coords is not None:
        return {"name": raw, "display_name": raw, "lat": coords[0],
                "lon": coords[1], "provider": "coords"}
    if resolve_provider() == "mock":
        return _mock_place(raw)
    return await _nominatim_place(raw)


async def find_place(*, query: str) -> dict:
    return await _place(query)


async def _osrm_route(a: dict, b: dict, mode: str) -> dict:
    profile = {"driving": "driving", "walking": "foot", "cycling": "bike"}.get(mode, "driving")
    url = f"{_OSRM}/{profile}/{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params={"overview": "false"},
                                headers={"User-Agent": "JaloraAI/0.1"})
    if resp.status_code >= 400:
        raise MapsError("PROVIDER_ERROR", f"OSRM HTTP {resp.status_code}.")
    routes = resp.json().get("routes") or []
    if not routes:
        raise MapsError("NO_ROUTE", "No route found between the two places.")
    route = routes[0]
    return {"provider": "osm", "origin": a["display_name"],
            "destination": b["display_name"],
            "distance_km": round(route.get("distance", 0) / 1000.0, 3),
            "duration_min": round(route.get("duration", 0) / 60.0, 1),
            "mode": mode}


async def get_directions(*, origin: str, destination: str, mode: str = "driving") -> dict:
    provider = resolve_provider()
    a = await _place(origin)
    b = await _place(destination)
    if provider == "osm" and a["provider"] == "osm" and b["provider"] == "osm":
        return await _osrm_route(a, b, mode)
    distance = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    speed = max(get_settings().maps_mock_speed_kmh, 1.0)
    return {"provider": provider, "origin": a["display_name"],
            "destination": b["display_name"], "distance_km": distance,
            "duration_min": round(distance / speed * 60, 1), "mode": mode}


async def calculate_distance(*, origin: str, destination: str) -> dict:
    a = await _place(origin)
    b = await _place(destination)
    return {"origin": a["display_name"], "destination": b["display_name"],
            "distance_km": haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])}


async def find_nearby(*, location: str, query: str = "", radius_km: float = 5.0,
                      limit: int = 10) -> dict:
    provider = resolve_provider()
    center = await _place(location)
    limit = max(1, min(int(limit or 10), 50))
    if provider == "osm" and center["provider"] == "osm" and query:
        return await _nominatim_nearby(center, query, radius_km, limit)
    label = (query or "Place").strip().title()
    results = []
    for i in range(min(limit, 5)):
        lat = center["lat"] + ((i % 3) - 1) * 0.001 * (i + 1)
        lon = center["lon"] + ((i % 2) * 2 - 1) * 0.001 * (i + 1)
        results.append({
            "name": f"{label} {i + 1}",
            "distance_km": haversine_km(center["lat"], center["lon"], lat, lon),
            "lat": round(lat, 6), "lon": round(lon, 6),
        })
    return {"provider": provider, "center": center["display_name"], "results": results}


async def _nominatim_nearby(center: dict, query: str, radius_km: float, limit: int) -> dict:
    d = max(radius_km, 1.0) / 111.0
    viewbox = (f"{center['lon'] - d},{center['lat'] + d},"
               f"{center['lon'] + d},{center['lat'] - d}")
    params = {"q": query, "format": "json", "limit": limit, "viewbox": viewbox,
              "bounded": 1}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_NOMINATIM, params=params,
                                headers={"User-Agent": "JaloraAI/0.1"})
    results = []
    if resp.status_code < 400:
        for item in resp.json():
            lat, lon = float(item.get("lat", 0)), float(item.get("lon", 0))
            results.append({
                "name": item.get("display_name", ""),
                "distance_km": haversine_km(center["lat"], center["lon"], lat, lon),
                "lat": lat, "lon": lon,
            })
    return {"provider": "osm", "center": center["display_name"], "results": results}
