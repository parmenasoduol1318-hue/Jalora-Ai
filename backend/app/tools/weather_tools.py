"""Weather tool (§67): keyless Open-Meteo by default."""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import weather as weather_service
from app.tools.base import PermissionLevel, registry


async def get_weather(location: str = "") -> ToolResult:
    """Get current weather and a short forecast. `location` is optional; when omitted the user's saved location is used."""
    try:
        data = await weather_service.get_weather(
            user_id=get_tool_context().user_id, location=location)
    except weather_service.WeatherError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    current = data["current"]
    return ToolResult.ok(
        f"{data['location']}: {current['temp_c']}°C, {current['condition']}.",
        data)


registry.register(get_weather, permission=PermissionLevel.AUTO, category="weather",
                  requires_internet=True, tags=["weather", "voice", "external"])
