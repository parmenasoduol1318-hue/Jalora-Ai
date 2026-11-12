from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_router
from app.config import get_settings
from app.errors import BadRequestError, ForbiddenError, NotFoundError
from app.models.calendar import CalendarEvent
from app.models.user import User
from app.schemas import HealthResponse
from app.services import calendar as calendar_service
from app.services.direct_action import execute_direct_action
from app.services.model_router import ModelRouter
from app.services.policy import decide_for_user
from app.services.provider_state import normalize_disabled, overview
from app.tools import registry

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        database = f"error: {type(exc).__name__}"
        response.status_code = 503
    from app.startup import startup_warnings

    capabilities = {
        # Phase E (§34-38): external-service readiness, booleans only.
        "gmail_oauth": bool(settings.gmail_client_id and settings.gmail_client_secret),
        # The exact callback Google must have registered (public, not a secret).
        "gmail_redirect_uri": settings.gmail_callback_url,
        # Google Calendar and Drive share the same OAuth client as Gmail.
        "google_integrations_oauth": bool(
            settings.gmail_client_id and settings.gmail_client_secret
        ),
        "google_integrations_redirect_uri": settings.oauth_callback_url,
        "email_provider": settings.email_provider or "auto",
        "twilio": bool(settings.twilio_account_sid and settings.twilio_auth_token),
        "telnyx": bool(settings.telnyx_api_key and settings.telnyx_account_sid),
        # Phase F (§31-33): voice is enabled when a telephony provider is
        # configured, otherwise calls run against the deterministic offline mock.
        "voice": bool(settings.phone_comms_configured),
        # Phase 1 live voice: in-app two-way call (server STT/TTS), no telephony.
        "voice_live": bool(getattr(settings, "voice_enabled", True)),
        "voice_stt": settings.voice_stt_provider or "auto",
        "voice_tts": settings.voice_tts_provider or "auto",
        # Phase 2/3 phone transport: Media Streams <-> VoiceSession (Telnyx/Twilio).
        "voice_phone": bool(
            getattr(settings, "voice_enabled", True)
            and settings.phone_comms_configured
        ),
        "voice_inbound": bool(getattr(settings, "voice_inbound_enabled", False)),
        "search_provider": settings.search_provider or "none",
        "browser": (settings.browser_driver or "auto") in ("real", "auto"),
        # Phase I (§63-68): personal-assistant integrations (all mock-first).
        "calendar_provider": settings.calendar_provider or "auto",
        "drive_provider": settings.drive_provider or "auto",
        "contacts": True,
        "maps_provider": settings.maps_provider or "auto",
        "weather_provider": settings.weather_provider or "auto",
        "translate_provider": settings.translate_provider or "auto",
    }

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=settings.version,
        env=settings.env,
        database=database,
        providers=settings.provider_status(),
        warnings=startup_warnings,
        timestamp=datetime.now(timezone.utc),
        capabilities=capabilities,
    )


@router.get("/providers")
def providers(user: User = Depends(get_current_user)):
    """Per-user provider status — booleans only, never secrets.

    `usable` reflects both configuration and the user's own disconnect
    preference, which is what routing actually honours.
    """
    settings = get_settings()
    disabled = normalize_disabled((user.prefs or {}).get("disabled_providers"))
    provider_status = overview(settings, disabled)
    stored_keys = (user.prefs or {}).get("provider_keys") or {}
    for name, label in (("copilot", "GitHub Copilot SDK"), ("codex", "OpenAI Codex SDK")):
        configured = bool(stored_keys.get(name))
        is_disabled = name in disabled
        provider_status[name] = {
            "model": label,
            "configured": configured,
            "keyless": False,
            "disabled": is_disabled,
            "usable": configured and not is_disabled,
            "logo": name,
        }
    return {
        "providers": provider_status,
        "disabled_providers": disabled,
    }


@router.get("/providers/availability")
async def provider_availability(model_router: ModelRouter = Depends(get_router)):
    """Live reachability checks (e.g. is Ollama actually running?)."""
    out = {}
    for name in model_router.specs:
        out[name] = {
            "configured": model_router.is_configured(name),
            **(await model_router.healthcheck(name) if model_router.is_configured(name) else {}),
        }
    return {"providers": out}


@router.get("/ollama/memory")
def ollama_memory_state(user: User = Depends(get_current_user)):
    """Live state of the memory-aware local model loader (free RAM, thresholds,
    loaded/installed Olama models, recent unload events)."""
    from app.services.ollama_memory import get_monitor
    return get_monitor().snapshot()


@router.get("/tools")
def tools_manifest():
    """The tool system manifest: what Jalora can do and what needs approval."""
    return {"tools": registry.manifest()}


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    """Report supported capability scope without implying provider readiness."""
    from app.services.capability_catalog import capability_catalog

    return {
        "execution": "Registered capability tools use the shared registry and policy.",
        "outcome_notice": (
            "Registration and local tests do not establish live provider availability, "
            "delivery, booking, or verification. Check each action's task evidence."
        ),
        "capabilities": capability_catalog(),
    }


@router.get("/metrics")
def metrics_endpoint(user: User = Depends(get_current_user)):
    """Request metrics snapshot (Phase L, sec 58).

    Returns total requests, total errors, average response time, and
    per-endpoint breakdown. Counters are in-memory per worker and the
    operational data is available only to authenticated users.
    """
    from app.services.metrics import get_snapshot

    settings = get_settings()
    if not settings.metrics_enabled:
        return {"enabled": False}
    data = get_snapshot()
    data["enabled"] = True
    return data


@router.get("/calendar/today")
def calendar_today(
    user: User = Depends(get_current_user),
):
    """Today's calendar events for the dashboard's daily-activity card."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    events = calendar_service.list_calendar(
        user_id=user.id,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    return {"events": events}


class CalendarEventCreate(BaseModel):
    title: str
    start: str
    end: str | None = None
    location: str = ""
    description: str = ""
    attendees: list[dict] | None = None
    timezone: str = "Africa/Nairobi"


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None
    attendees: list[dict] | None = None
    timezone: str | None = None
    force: bool = False


@router.get("/calendar")
def calendar_range(
    start: str = "",
    end: str = "",
    limit: int = 200,
    user: User = Depends(get_current_user),
):
    """Calendar events in an ISO-8601 window (Google when connected, else local)."""
    events = calendar_service.list_calendar(
        user_id=user.id, start=start or None, end=end or None, limit=limit)
    return {"events": events}


@router.post("/calendar/events", status_code=202)
async def create_calendar_event(
    body: CalendarEventCreate,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """Prepare a centrally approved calendar event creation."""
    _ensure_calendar_action_allowed(user, "create_event")
    try:
        if not body.title.strip():
            raise calendar_service.CalendarError(
                "INVALID_EVENT", "An event title is required."
            )
        start = calendar_service.parse_dt(body.start)
        end = calendar_service.parse_dt(body.end)
        if start is None:
            raise calendar_service.CalendarError(
                "INVALID_EVENT", "An event start time is required."
            )
        if end is not None and end <= start:
            raise calendar_service.CalendarError(
                "INVALID_EVENT", "Event end must be after its start."
            )
        timezone_name = calendar_service.validate_timezone(body.timezone)
    except calendar_service.CalendarError as exc:
        raise BadRequestError(exc.detail, exc.code)
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="create_event",
        arguments={
            "title": body.title,
            "start": body.start,
            "end": body.end or "",
            "location": body.location,
            "description": body.description,
            "attendees": body.attendees or [],
            "timezone": timezone_name,
        },
        request_message=f"Create the calendar event '{body.title}'.",
        description=f"Create calendar event '{body.title}'.",
        success_message=f"Created calendar event '{body.title}'.",
    )
    return _calendar_action_response(outcome)


@router.patch("/calendar/events/{event_id}", status_code=202)
async def update_calendar_event(
    event_id: str,
    body: CalendarEventUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _ensure_calendar_action_allowed(user, "update_event")
    event = db.scalar(select(CalendarEvent).where(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == user.id,
    ))
    if event is None:
        raise NotFoundError("Calendar event not found.")
    fields = body.model_dump(exclude_unset=True, exclude={"force"})
    try:
        if "timezone" in fields and fields["timezone"] is not None:
            fields["timezone"] = calendar_service.validate_timezone(fields["timezone"])
        start = calendar_service.parse_dt(fields.get("start")) or event.start_at
        end = calendar_service.parse_dt(fields.get("end")) or event.end_at
        if start is not None and end is not None and end <= start:
            raise calendar_service.CalendarError(
                "INVALID_EVENT", "Event end must be after its start."
            )
    except calendar_service.CalendarError as exc:
        raise BadRequestError(exc.detail, exc.code)
    arguments = {"event_id": event_id, **fields, "force": body.force}
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="update_event",
        arguments=arguments,
        request_message=f"Update calendar event {event_id}.",
        description=f"Update calendar event {event_id}.",
        success_message=f"Updated calendar event {event_id}.",
    )
    return _calendar_action_response(outcome)


@router.delete("/calendar/events/{event_id}", status_code=202)
async def delete_calendar_event(
    event_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    _ensure_calendar_action_allowed(user, "delete_event")
    event = db.scalar(select(CalendarEvent).where(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == user.id,
    ))
    if event is None:
        raise NotFoundError("Calendar event not found.")
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name="delete_event",
        arguments={"event_id": event_id},
        request_message=f"Delete calendar event {event_id}.",
        description=f"Delete calendar event {event_id}.",
        success_message=f"Deleted calendar event {event_id}.",
    )
    return _calendar_action_response(outcome)


def _calendar_action_response(outcome):
    if outcome.status == "error" and outcome.error:
        from app.errors import JaloraError

        raise JaloraError(
            502,
            outcome.error.get("code", "CALENDAR_ACTION_FAILED"),
            outcome.error.get("details", "The calendar action could not be prepared."),
        )
    return outcome.as_response()


def _ensure_calendar_action_allowed(user: User, tool_name: str) -> None:
    spec = registry.get(tool_name)
    if spec is None:
        raise BadRequestError(
            "The requested calendar capability is unavailable.",
            "CAPABILITY_UNAVAILABLE",
        )
    decision = decide_for_user(
        permission=spec.permission,
        preferences=user.prefs or {},
        tool_name=tool_name,
        tags=spec.tags,
    )
    if not decision.allowed:
        raise ForbiddenError(
            decision.detail or "This calendar action is denied by user policy.",
            decision.code or "TOOL_DENIED",
        )
