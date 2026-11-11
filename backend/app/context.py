"""Request-scoped execution context shared with tools (user identity)."""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from app.services.model_router import ModelRouter


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    run_id: str | None = None
    task_id: str | None = None
    conversation_id: str | None = None
    # Coding workspace scope: the conversation id of a ``project="workspace"``
    # conversation. Empty/None falls back to the legacy per-user workspace.
    project_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None
    tool_call_id: str | None = None
    attempt_number: int | None = None
    idempotency_key: str | None = None


_current: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar(
    "jalora_tool_context", default=None
)
_tool_model_router: contextvars.ContextVar[ModelRouter | None] = contextvars.ContextVar(
    "jalora_tool_model_router", default=None
)


def set_tool_context(ctx: ToolContext | None) -> None:
    _current.set(ctx)


def get_tool_context() -> ToolContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("No tool context bound; tools may only run inside an agent run.")
    return ctx


@contextmanager
def bind_tool_model_router(router: ModelRouter) -> Iterator[None]:
    token = _tool_model_router.set(router)
    try:
        yield
    finally:
        _tool_model_router.reset(token)


def get_tool_model_router() -> ModelRouter:
    router = _tool_model_router.get()
    if router is None:
        raise RuntimeError("No model router is bound to this tool execution.")
    return router


@contextmanager
def bind_tool_execution(ctx: ToolContext) -> Iterator[None]:
    token = _current.set(ctx)
    try:
        yield
    finally:
        _current.reset(token)
