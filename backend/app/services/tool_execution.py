"""Shared result decoding for capability execution surfaces."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.errors import ToolResult
from app.tools.base import ToolSpec

logger = logging.getLogger("jalora.tool_execution")


def decode_tool_result(raw: Any) -> ToolResult | None:
    """Decode supported structured tool outputs; return None when malformed."""
    if isinstance(raw, ToolResult):
        return raw
    try:
        if isinstance(raw, Mapping):
            return ToolResult.model_validate(raw)
        if isinstance(raw, (str, bytes, bytearray)):
            return ToolResult.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError):
        return None
    return None


async def invoke_registered_tool(
    spec: ToolSpec,
    args: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
    label: str = "Capability",
) -> ToolResult:
    """Invoke one pre-authorized tool and normalize its result or failure."""
    try:
        invocation = spec.tool.ainvoke(args)
        if timeout_seconds is None:
            raw = await invocation
        else:
            raw = await asyncio.wait_for(invocation, timeout=timeout_seconds)
    except TimeoutError:
        return ToolResult.fail(
            "TIMEOUT",
            f"{label} '{spec.name}' exceeded {timeout_seconds}s.",
            message=f"{label} '{spec.name}' timed out.",
        )
    except Exception as exc:
        logger.exception("%s '%s' failed during execution", label, spec.name)
        return ToolResult.fail(
            "TOOL_EXCEPTION",
            f"{label} '{spec.name}' failed during execution ({type(exc).__name__}).",
            message=f"{label} '{spec.name}' failed.",
        )

    result = decode_tool_result(raw)
    if result is not None:
        return result
    logger.error("%s '%s' returned malformed output", label, spec.name)
    return ToolResult.fail(
        "MALFORMED_TOOL_RESULT",
        f"{label} '{spec.name}' returned invalid structured output.",
        message=f"{label} '{spec.name}' returned an invalid result.",
    )
