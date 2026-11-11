"""Maps & places tools (§66): find_place, get_directions, calculate_distance,
find_nearby. Keyless OSM/OSRM by default.
"""
from __future__ import annotations

from app.errors import ToolResult
from app.services import maps as maps_service
from app.tools.base import PermissionLevel, registry


async def find_place(query: str) -> ToolResult:
    """Geocode a place name to coordinates (lat/lon). Use for locating a city, address or landmark."""
    try:
        place = await maps_service.find_place(query=query)
    except maps_service.MapsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Located {place['display_name']}.", place)


async def get_directions(origin: str, destination: str, mode: str = "driving") -> ToolResult:
    """Get directions (distance + duration) between two places. `mode` is driving|walking|cycling."""
    try:
        route = await maps_service.get_directions(
            origin=origin, destination=destination, mode=mode)
    except maps_service.MapsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"{route['distance_km']} km, about {route['duration_min']} min "
        f"from {route['origin']} to {route['destination']}.", route)


async def calculate_distance(origin: str, destination: str) -> ToolResult:
    """Straight-line distance (km) between two places or 'lat,lon' points."""
    try:
        result = await maps_service.calculate_distance(
            origin=origin, destination=destination)
    except maps_service.MapsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"{result['distance_km']} km between {result['origin']} and "
        f"{result['destination']}.", result)


async def find_nearby(location: str, query: str = "", radius_km: float = 5.0,
                      limit: int = 10) -> ToolResult:
    """Find places near a location, optionally filtered by keyword (e.g. "hotel")."""
    try:
        result = await maps_service.find_nearby(
            location=location, query=query, radius_km=radius_km, limit=limit)
    except maps_service.MapsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Found {len(result['results'])} place(s) near {result['center']}.", result)


registry.register(find_place, permission=PermissionLevel.AUTO, category="maps",
                  requires_internet=True, tags=["maps", "external"])
registry.register(get_directions, permission=PermissionLevel.AUTO, category="maps",
                  requires_internet=True, tags=["maps", "external"])
registry.register(calculate_distance, permission=PermissionLevel.AUTO, category="maps")
registry.register(find_nearby, permission=PermissionLevel.AUTO, category="maps",
                  requires_internet=True, tags=["maps", "external"])
