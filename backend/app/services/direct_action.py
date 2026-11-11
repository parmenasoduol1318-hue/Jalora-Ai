from __future__ import annotations

from typing import Any

from app.database.session import SessionLocal
from app.errors import BadRequestError, ForbiddenError, NotFoundError
from app.models.user import User
from app.services.model_router import ModelRouter
from app.services.policy import decide_for_user
from app.tools import registry


async def execute_direct_action(
    *,
    user_id: str,
    model_router: ModelRouter,
    tool_name: str,
    arguments: dict[str, Any],
    request_message: str,
    description: str,
    success_message: str,
    require_approval: bool = True,
    conversation_id: str | None = None,
    project: str | None = None,
    workspace_project_id: str | None = None,
):
    """Submit a server-initiated capability action to the canonical executor."""
    from app.context import bind_tool_model_router
    from app.events import ListEmitter
    from app.services.chat_service import execute_chat

    spec = registry.get(tool_name)
    if spec is None:
        raise BadRequestError(
            "The requested capability is unavailable.", "CAPABILITY_UNAVAILABLE"
        )
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        decision = decide_for_user(
            permission=spec.permission,
            preferences=user.prefs or {},
            tool_name=tool_name,
            tags=spec.tags,
        )
    if not decision.allowed:
        raise ForbiddenError(
            decision.detail or f"The {tool_name} capability is denied by user policy.",
            decision.code or "TOOL_DENIED",
        )

    with bind_tool_model_router(model_router):
        outcome = await execute_chat(
            user_id=user_id,
            request_message=request_message,
            conversation_id=conversation_id,
            router=model_router,
            emitter=ListEmitter(),
            mode="agent",
            project=project,
            direct_tool_call={
                "tool_name": tool_name,
                "arguments": arguments,
                "description": description,
                "success_message": success_message,
                "failure_message": f"Could not complete {description.rstrip('.')}.",
                "require_approval": require_approval,
                **(
                    {"workspace_project_id": workspace_project_id}
                    if workspace_project_id is not None
                    else {}
                ),
            },
        )
    if outcome.approval is not None:
        outcome.approval = {
            **outcome.approval,
            "task_id": outcome.task_id,
            "run_id": outcome.run_id,
            "conversation_id": outcome.conversation_id,
            "standalone": False,
        }
    return outcome
