"""Structured user-input prompts for workflows that cannot safely continue."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry


class UserInputArguments(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=1000)
    input_type: Literal["text", "textarea", "email", "number", "choice"] = "text"
    options: list[str] | None = None
    required: bool = True
    answer: str | None = None

    @model_validator(mode="after")
    def validate_answer(self) -> "UserInputArguments":
        if self.input_type == "choice" and not self.options:
            raise ValueError("Choice prompts require at least one option.")
        if self.answer is not None:
            self.answer = self.answer.strip()
            if self.required and not self.answer:
                raise ValueError("An answer is required.")
            if (
                self.input_type == "choice"
                and self.answer
                and self.answer not in (self.options or [])
            ):
                raise ValueError("Choose one of the listed options.")
        return self


async def request_user_input(
    title: str,
    question: str,
    input_type: Literal["text", "textarea", "email", "number", "choice"] = "text",
    options: list[str] | None = None,
    required: bool = True,
    answer: str | None = None,
) -> ToolResult:
    """Show a structured form prompt and pause this task for the user's answer.

    Call only when required information is genuinely missing from the request,
    conversation, and available sources. Choose a suitable input_type; use
    options for a choice prompt. The task resumes with the submitted answer.
    """
    try:
        prompt = UserInputArguments(
            title=title,
            question=question,
            input_type=input_type,
            options=options,
            required=required,
            answer=answer,
        )
    except ValueError as exc:
        return ToolResult.fail("INVALID_USER_INPUT", str(exc))

    if prompt.answer is None:
        return ToolResult.fail(
            "USER_INPUT_REQUIRED",
            "This prompt must be answered through Jalora's structured input form.",
        )
    return ToolResult.ok(
        "User input received.",
        {"answer": prompt.answer},
    )


registry.register(
    request_user_input,
    permission=PermissionLevel.AUTO,
    category="interaction",
    tags=["user_input", "clarification", "prompt"],
    side_effect="read_only",
    always_available=True,
)
