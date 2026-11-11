from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, get_router
from app.errors import JaloraError
from app.models.user import User
from app.schemas import ChatRequest, ChatResponse
from app.services.chat_service import execute_chat, stream_chat
from app.services.model_router import ModelRouter

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    if payload.stream:
        return StreamingResponse(
            stream_chat(
                user_id=user.id,
                request_message=payload.message,
                conversation_id=payload.conversation_id,
                router=model_router,
                provider_override=payload.model_provider,
                model_override=payload.model_name,
                media_ids=payload.media_ids,
                document_ids=payload.document_ids,
                mode=payload.mode or "chat",
                intent_hint=payload.intent_hint,
                capability_hint=payload.capability_hint,
                math_level=payload.math_level,
                show_working=payload.show_working,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    from app.events import ListEmitter

    emitter = ListEmitter()
    outcome = await execute_chat(
        user_id=user.id,
        request_message=payload.message,
        conversation_id=payload.conversation_id,
        router=model_router,
        emitter=emitter,
        provider_override=payload.model_provider,
        model_override=payload.model_name,
        media_ids=payload.media_ids,
        document_ids=payload.document_ids,
        mode=payload.mode or "chat",
        intent_hint=payload.intent_hint,
        capability_hint=payload.capability_hint,
        math_level=payload.math_level,
        show_working=payload.show_working,
    )
    data = outcome.as_response()
    if outcome.status == "error" and outcome.error:
        status_code = {
            "MODEL_NOT_CONFIGURED": 503,
            "NOT_FOUND": 404,
            "DOCUMENT_NOT_READY": 400,
        }.get(outcome.error.get("code", ""), 502)
        raise JaloraError(status_code, outcome.error["code"], outcome.error["details"])
    return ChatResponse.from_outcome(outcome)
