"""Weather service (§67).

Keyless default: Open-Meteo (no API key). A deterministic offline mock keeps
the assistant working without network access. The location can come from the
request, the user profile (`prefs["location"]`), a matching contact, or the
configured default.
"""
from __future__ import annotations

import hashlib

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.user import User
from app.services import contacts as contacts_service
from app.services import maps as maps_service

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> human condition.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog", 51: "light drizzle",
    53: "moderate drizzle", 55: "dense drizzle", 61: "slight rain",
    63: "moderate rain", 65: "heavy rain", 71: "slight snow",
    73: "moderate snow", 75: "heavy snow", 80: "rain showers",
    81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
}


class WeatherError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def resolve_provider() -> str:
    choice = (get_settings().weather_provider or "auto").strip().lower()
    if choice == "auto":
        return "openmeteo"
    if choice not in ("mock", "openmeteo", "open-meteo"):
        raise WeatherError("PROVIDER_NOT_CONFIGURED",
                           f"Unknown weather provider '{choice}' (auto|openmeteo|mock).")
    return "openmeteo" if choice == "open-meteo" else choice


def _user_location(db: Session, user_id: str) -> str:
    user = db.get(User, user_id)
    if user and isinstance(user.prefs, dict):
        return str(user.prefs.get("location") or "")
    return ""


def resolve_location(user_id: str, location: str = "") -> str:
    """Best-effort location string: request -> contact -> profile -> default."""
    explicit = (location or "").strip()
    if explicit:
        db = SessionLocal()
        try:
            contact = contacts_service.resolve_contact(db, user_id=user_id, query=explicit)
        finally:
            db.close()
        if contact is not None and (contact.company or contact.notes):
            return (contact.company or contact.notes).strip()
        return explicit
    db = SessionLocal()
    try:
        profile = _user_location(db, user_id)
    finally:
        db.close()
    return profile or (get_settings().default_location or "").strip()


def _mock_weather(location: str) -> dict:
    seed = int(hashlib.sha256(location.encode("utf-8")).hexdigest(), 16)
    temp = round(-5 + (seed % 400) / 10.0, 1)  # -5 .. 35 C
    codes = [0, 1, 2, 3, 61, 63, 80, 95]
    code = codes[seed % len(codes)]
    return {
        "provider": "mock",
        "location": location or "Unknown",
        "current": {
            "temp_c": temp,
            "condition": _WMO.get(code, "unknown"),
            "weather_code": code,
            "wind_kph": round((seed % 300) / 10.0, 1),
            "humidity": 40 + seed % 55,
        },
        "daily": [
            {"date": f"day+{i}", "temp_max_c": temp + 4 - i,
             "temp_min_c": temp - 5 - i, "condition": _WMO.get(code, "unknown")}
            for i in range(3)
        ],
    }


async def _openmeteo_weather(location: str) -> dict:
    place = await maps_service._place(location)
    params = {
        "latitude": place["lat"], "longitude": place["lon"],
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "timezone": "auto", "forecast_days": 3,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(_OPEN_METEO, params=params,
                                headers={"User-Agent": "JaloraAI/0.1"})
    if resp.status_code >= 400:
        raise WeatherError("PROVIDER_ERROR",
                           f"Open-Meteo HTTP {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    cur = payload.get("current", {})
    code = int(cur.get("weather_code", 0))
    daily_raw = payload.get("daily", {})
    dates = daily_raw.get("time", [])
    daily = []
    for i, day in enumerate(dates):
        dcode = int((daily_raw.get("weather_code") or [0])[i])
        daily.append({
            "date": day,
            "temp_max_c": (daily_raw.get("temperature_2m_max") or [None])[i],
            "temp_min_c": (daily_raw.get("temperature_2m_min") or [None])[i],
            "condition": _WMO.get(dcode, "unknown"),
        })
    return {
        "provider": "openmeteo",
        "location": place["display_name"],
        "current": {
            "temp_c": cur.get("temperature_2m"),
            "condition": _WMO.get(code, "unknown"),
            "weather_code": code,
            "wind_kph": cur.get("wind_speed_10m"),
            "humidity": cur.get("relative_humidity_2m"),
        },
        "daily": daily,
    }


async def get_weather(*, user_id: str, location: str = "") -> dict:
    resolved = resolve_location(user_id, location)
    if not resolved:
        raise WeatherError(
            "NO_LOCATION",
            "No location given and none on file. Provide a city or set "
            "prefs['location'].",
        )
    if resolve_provider() == "mock":
        return _mock_weather(resolved)
    return await _openmeteo_weather(resolved)
