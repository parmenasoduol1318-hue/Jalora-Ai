"""Cross-cutting event emitter used by the agent graph, tools and model layer
to push structured events (tokens, tool activity, sources, approvals) to the
API layer without tight coupling."""
from __future__ import annotations

import contextvars
from typing import Any, Protocol


class Emitter(Protocol):
    def emit(self, event_type: str, data: dict[str, Any]) -> None: ...


class NullEmitter:
    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        return


class ListEmitter:
    """Collects events in memory (used for non-streaming responses & tests)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **data})

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


_current_emitter: contextvars.ContextVar[Emitter | None] = contextvars.ContextVar(
    "jalora_emitter", default=None
)


def set_emitter(emitter: Emitter | None) -> None:
    _current_emitter.set(emitter)


def emit(event_type: str, data: dict[str, Any]) -> None:
    emitter = _current_emitter.get()
    if emitter is not None:
        emitter.emit(event_type, data)
