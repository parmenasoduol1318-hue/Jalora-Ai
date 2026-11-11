"""Structured errors and responses.

Every API error and every tool result uses a structured envelope so raw
Python exceptions never leak into user-facing answers (spec §45).
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    details: str


class ApiError(BaseModel):
    success: bool = False
    message: str
    error: ErrorDetail


class ToolResult(BaseModel):
    """Uniform result contract for every tool in the system (spec §45)."""

    success: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorDetail | None = None

    @classmethod
    def ok(cls, message: str, data: dict[str, Any] | None = None) -> "ToolResult":
        return cls(success=True, message=message, data=data or {})

    @classmethod
    def fail(cls, code: str, details: str, message: str | None = None,
             data: dict[str, Any] | None = None) -> "ToolResult":
        return cls(
            success=False,
            message=message or details,
            error=ErrorDetail(code=code, details=details),
            data=data or {},
        )


class JaloraError(Exception):
    """Application error carrying an HTTP status and machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ServiceUnavailableError(JaloraError):
    def __init__(self, details: str, code: str = "SERVICE_UNAVAILABLE"):
        super().__init__(503, code, details)


class NotFoundError(JaloraError):
    def __init__(self, message: str = "Resource not found", code: str = "NOT_FOUND"):
        super().__init__(404, code, message)


class AuthError(JaloraError):
    def __init__(self, message: str = "Not authenticated", code: str = "UNAUTHORIZED"):
        super().__init__(401, code, message)


class ForbiddenError(JaloraError):
    def __init__(self, message: str = "Forbidden", code: str = "FORBIDDEN"):
        super().__init__(403, code, message)


class ConflictError(JaloraError):
    def __init__(self, message: str = "Conflict", code: str = "CONFLICT"):
        super().__init__(409, code, message)


class BadRequestError(JaloraError):
    def __init__(self, message: str = "Bad request", code: str = "BAD_REQUEST"):
        super().__init__(400, code, message)


class TooManyRequestsError(JaloraError):
    def __init__(self, message: str = "Rate limit exceeded", code: str = "RATE_LIMITED"):
        super().__init__(429, code, message)


class ModelNotConfiguredError(JaloraError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "MODEL_NOT_CONFIGURED",
            "No AI model is configured. Add a provider API key (e.g. JALORA_OPENAI_API_KEY) "
            "or start Ollama, then retry.",
        )


class ModelEmptyResponseError(RuntimeError):
    """Raised when a model stream completes without yielding any content.

    Carries a message that is already safe to show to the user; callers map it
    to a friendly notification (never a raw ValueError/stack trace).
    """

    def __init__(self, provider: str = "") -> None:
        self.provider = provider
        super().__init__(
            "The AI model didn't return a response. Please try again."
        )


async def api_error_handler(request: Request, exc: JaloraError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiError(success=False, message=exc.message,
                         error=ErrorDetail(code=exc.code, details=exc.message)).model_dump(),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.config import get_settings
    import logging

    logging.getLogger("jalora").exception("unhandled error on %s", request.url.path)
    detail = str(exc) if get_settings().is_production else f"{type(exc).__name__}: {exc}"
    return JSONResponse(
        status_code=500,
        content=ApiError(
            success=False,
            message="Internal server error",
            error=ErrorDetail(code="INTERNAL_ERROR", details=detail),
        ).model_dump(),
    )
