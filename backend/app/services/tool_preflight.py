"""Shared validation for capability arguments before execution."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.tools.base import ToolSpec


def validate_tool_arguments(
    spec: ToolSpec,
    args: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Return normalized arguments, or validation errors for an invalid call."""
    if spec.tool.args_schema is None:
        return args, None
    try:
        normalized = spec.tool.args_schema.model_validate(args).model_dump(
            exclude_unset=True
        )
    except ValidationError as exc:
        return args, exc.errors()
    return normalized, None
