"""Calendar tools (§63): find/create/update/delete events.

Writes require approval (CONFIRM). `create_event` performs conflict detection
and, on a clash, returns the conflict plus a suggested free slot so the agent
can propose an alternative before the approval-gated write.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import calendar as calendar_service
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


async def find_events(start: str = "", end: str = "", limit: int = 50) -> ToolResult:
    """List the user's calendar events between optional ISO-8601 `start` and `end` (newest window first). Use for "what's on my calendar", "am I free tomorrow"."""
    events = calendar_service.list_calendar(
        user_id=_user_id(), start=start or None, end=end or None, limit=limit)
    if not events:
        return ToolResult.ok("No calendar events found in that window.", {"events": []})
    return ToolResult.ok(f"Found {len(events)} event(s).", {"events": events})


async def create_event(title: str, start: str, end: str = "", location: str = "",
                       description: str = "", attendees: list | None = None,
                       timezone: str = "Africa/Nairobi") -> ToolResult:
    """Create a calendar event. `start`/`end` are ISO-8601 datetimes. Detects conflicts and proposes a free alternative; always requires user approval before writing."""
    try:
        result = calendar_service.add_calendar_event(
            user_id=_user_id(), title=title, start=start, end=end or None,
            location=location, description=description, attendees=attendees or [],
            timezone_name=timezone)
    except calendar_service.CalendarError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    if result.get("status") == "conflict":
        suggested = result.get("suggested", {}).get("start")
        msg = "That time conflicts with an existing event."
        if suggested:
            msg += f" The next free slot is {suggested}."
        return ToolResult.ok(msg, result)
    return ToolResult.ok(
        f"Event '{result['event']['title']}' created at {result['event']['start']}.",
        result)


async def update_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
    attendees: list[dict] | None = None,
    timezone: str | None = None,
    force: bool = False,
) -> ToolResult:
    """Update an existing calendar event by id. Requires user approval."""
    fields = {k: v for k, v in {
        "title": title, "start": start, "end": end,
        "location": location, "description": description,
        "attendees": attendees, "timezone": timezone,
        "force": force,
    }.items() if v is not None}
    try:
        result = calendar_service.edit_calendar_event(
            user_id=_user_id(), event_id=event_id, **fields)
    except calendar_service.CalendarError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    if result.get("status") == "conflict":
        return ToolResult.ok("The new time conflicts with an existing event.", result)
    return ToolResult.ok(f"Event {event_id} updated.", result)


async def delete_event(event_id: str) -> ToolResult:
    """Delete a calendar event by id. Requires user approval."""
    try:
        result = calendar_service.remove_calendar_event(
            user_id=_user_id(), event_id=event_id)
    except calendar_service.CalendarError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Event {event_id} deleted.", result)


registry.register(find_events, permission=PermissionLevel.AUTO, category="calendar",
                  tags=["voice"])
registry.register(create_event, permission=PermissionLevel.CONFIRM, category="calendar",
                  tags=["calendar", "event", "meeting", "invite", "voice", "external"])
registry.register(update_event, permission=PermissionLevel.CONFIRM, category="calendar",
                  tags=["calendar", "external"])
registry.register(delete_event, permission=PermissionLevel.CONFIRM, category="calendar",
                  tags=["calendar", "external"])
