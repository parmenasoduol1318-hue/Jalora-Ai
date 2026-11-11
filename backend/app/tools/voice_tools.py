"""Voice tools (§31-33, §57): a full-conversation outbound call.

`make_call` is the Level-3 (CONFIRM) tool behind "call this number, ask about
X, and if approved send me the details on WhatsApp": it builds a structured
script from the purpose (and optional agent questions), pauses for approval
before any dialing happens (via the generic graph approval machinery), then
conducts the call — synchronously deterministic when the mock provider is
active — and returns a transcript plus a §32 summary of the form:

    Person's response: ...
    Jalora summary: ...
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import comms, voice
from app.tools.base import PermissionLevel, registry


async def make_call(phone_number: str, purpose: str,
                    questions: list[str] | None = None) -> ToolResult:
    """Make a real phone call to accomplish a goal. Builds a goal-driven script, pauses for your approval before dialing, conducts the conversation (spoken questions, capturing yes/no and free-form answers), and returns a full transcript plus a summary starting with 'Person's response:' and 'Jalora summary:'. Provide the E.164 phone number (e.g. +254712345678) and a short, specific purpose; optionally list follow-up questions to ask when the person approves."""
    ctx = get_tool_context()
    try:
        view = await voice.place_call(
            user_id=ctx.user_id,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            conversation_id=ctx.conversation_id,
            to_phone=phone_number,
            purpose=purpose,
            questions=questions,
        )
    except comms.CommsError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(_format_summary(view), view)


def _format_summary(view: dict) -> str:
    status = view.get("status", "")
    to = view.get("to_phone", "")
    summary = view.get("summary") or ""
    if status == "completed" and summary:
        return f"Call to {to} completed.\n{summary}"
    if status == "in_progress":
        return (
            f"Call placed to {to} and is in progress ({view.get('provider')}). "
            f"Jalora will update this task with the transcript and summary when it ends."
        )
    return f"Call to {to} ended with status '{status}'. Jalora summary: {summary or 'none yet'}"


registry.register(make_call, permission=PermissionLevel.CONFIRM, category="comms",
                  requires_internet=True,
                  tags=["comms", "voice", "external", "application", "purpose"],
                  default_for_actions=["call"])