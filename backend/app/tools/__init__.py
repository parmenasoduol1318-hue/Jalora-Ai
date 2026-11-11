"""Registers every built-in tool with the shared registry."""
from app.tools import web as _web  # noqa: F401
from app.tools import browse as _browse  # noqa: F401
from app.tools import browser as _browser  # noqa: F401
from app.tools import device_tools as _device_tools  # noqa: F401
from app.tools import workspace_tools as _workspace_tools  # noqa: F401
from app.tools import comms as _comms  # noqa: F401
from app.tools import memory_tools as _memory_tools  # noqa: F401
from app.tools import document_tools as _document_tools  # noqa: F401
from app.tools import media_tools as _media_tools  # noqa: F401
from app.tools import generation_tools as _generation_tools
from app.tools import creative_tools as _creative_tools  # noqa: F401
from app.tools import plan as _plan  # noqa: F401
from app.tools import email_tools as _email_tools  # noqa: F401
from app.tools import voice_tools as _voice_tools  # noqa: F401
from app.tools import schedule_tools as _schedule_tools  # noqa: F401
from app.tools import calendar_tools as _calendar_tools  # noqa: F401
from app.tools import drive_tools as _drive_tools  # noqa: F401
from app.tools import contact_tools as _contact_tools  # noqa: F401
from app.tools import user_input as _user_input  # noqa: F401
from app.tools import maps_tools as _maps_tools  # noqa: F401
from app.tools import weather_tools as _weather_tools  # noqa: F401
from app.tools import translate_tools as _translate_tools  # noqa: F401
from app.tools import coding_tools as _coding_tools  # noqa: F401
from app.tools import external_agent as _external_agent  # noqa: F401
from app.tools import math_tools as _math_tools  # noqa: F401
from app.tools import plot_tool as _plot_tool  # noqa: F401
from app.tools import payment_tools as _payment_tools  # noqa: F401
from app.tools import travel_tools as _travel_tools  # noqa: F401
from app.tools import hotel_tools as _hotel_tools  # noqa: F401
from app.tools import job_tools as _job_tools  # noqa: F401
from app.tools.base import PermissionLevel, ToolResult, registry

registry.register(
    _plan.update_plan,
    permission=PermissionLevel.AUTO,
    category="internal",
    tags=["internal"],
    always_available=True,
)

__all__ = ["registry", "PermissionLevel", "ToolResult"]
