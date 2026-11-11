"""Planner tool: validates model-published plans against registered capabilities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.errors import ToolResult

if TYPE_CHECKING:
    from app.tools.base import ToolRegistry

VALID_STATUSES = ("pending", "active", "done", "failed", "skipped")
MAX_PLAN_STEPS = 50
logger = logging.getLogger("jalora.plan")


class PlanValidationError(ValueError):
    """A planner-supplied step graph failed structural or capability checks."""


def normalize_steps(
    steps: list[Any],
    *,
    capability_registry: ToolRegistry | None = None,
    allow_argument_hints: bool = False,
) -> list[dict[str, Any]] | None:
    if not isinstance(steps, list) or not steps:
        return None
    if len(steps) > MAX_PLAN_STEPS:
        raise PlanValidationError(
            f"A plan cannot contain more than {MAX_PLAN_STEPS} steps."
        )
    if allow_argument_hints:
        retained_steps = []
        for item in steps:
            if isinstance(item, dict) and set(item) in ({"arguments"}, {"args"}):
                logger.warning("Omitting a standalone argument fragment from the plan")
                continue
            retained_steps.append(item)
        steps = retained_steps
        if not steps:
            return None

    plan: list[dict[str, Any]] = []
    raw_dependencies: list[list[Any]] = []
    identifiers: dict[str, int] = {}
    for index, item in enumerate(steps):
        if isinstance(item, str):
            description = item.strip()[:500]
            identifier = f"step_{index + 1}"
            status = "pending"
            dependencies: list[Any] = []
            metadata: dict[str, Any] = {}
        elif isinstance(item, dict):
            description = str(
                item.get("description")
                or item.get("task")
                or item.get("expected_output")
                or item.get("expectedOutput")
                or ""
            ).strip()[:500]
            identifier = str(item.get("id") or f"step_{index + 1}").strip()
            status = str(item.get("status", "pending"))
            if status not in VALID_STATUSES:
                raise PlanValidationError(
                    f"Step {index + 1} has unsupported status '{status}'."
                )
            raw = item.get(
                "dependencies",
                item.get("depends_on", item.get("depends", [])),
            )
            if raw is None:
                raw = []
            if not isinstance(raw, (list, tuple, set)):
                raise PlanValidationError(
                    f"Step {index + 1} dependencies must be a list."
                )
            dependencies = list(raw)
            metadata = {}
            kind = item.get("kind")
            if kind is not None:
                if kind not in {"response", "reasoning", "tool"}:
                    raise PlanValidationError(
                        f"Step {index + 1} kind must be 'tool', 'response', or 'reasoning'."
                    )
                if kind in {"response", "reasoning"}:
                    metadata["kind"] = kind
            tool_name = item.get(
                "tool", item.get("name", item.get("capability"))
            )
            if isinstance(tool_name, str) and tool_name.strip().lower() in {
                "", "none", "null", "no_tool", "no capability", "response",
            }:
                if kind is None:
                    metadata["kind"] = (
                        "response"
                        if tool_name.strip().lower() == "response"
                        else "reasoning"
                    )
                tool_name = None
            if kind == "response" and tool_name is not None:
                raise PlanValidationError(
                    f"Response step {index + 1} cannot reference a capability."
                )
            if tool_name is not None:
                tool_name = str(tool_name).strip()
                if not tool_name:
                    raise PlanValidationError(
                        f"Step {index + 1} has an empty capability name."
                    )
                metadata["tool"] = tool_name[:128]
                arguments = item.get("arguments", item.get("args"))
                has_arguments = "arguments" in item or "args" in item
                if has_arguments and not isinstance(arguments, dict):
                    if not allow_argument_hints:
                        raise PlanValidationError(
                            f"Step {index + 1} arguments must be an object."
                        )
                    metadata["argument_hints_omitted"] = True
                    logger.warning(
                        "Omitting non-object argument hints for plan step %s "
                        "using capability %s",
                        identifier, tool_name,
                    )
                tool_spec = None
                if capability_registry is not None:
                    tool_spec = capability_registry.get(tool_name)
                    if tool_spec is None:
                        raise PlanValidationError(
                            f"Step {index + 1} references unavailable capability "
                            f"'{tool_name}'."
                        )
                if has_arguments and dependencies and allow_argument_hints:
                    metadata["argument_hints_omitted"] = True
                    logger.warning(
                        "Omitting argument hints for dependency-linked plan step %s",
                        identifier,
                    )
                elif has_arguments and tool_spec is not None:
                    args_schema = tool_spec.tool.args_schema
                    if args_schema is None:
                        if arguments:
                            if allow_argument_hints:
                                metadata["argument_hints_omitted"] = True
                                logger.warning(
                                    "Omitting invalid argument hints for plan step %s "
                                    "using capability %s",
                                    identifier, tool_name,
                                )
                            else:
                                raise PlanValidationError(
                                    f"Capability '{tool_name}' does not accept arguments."
                                )
                    else:
                        try:
                            args_schema.model_validate(arguments)
                        except ValidationError as exc:
                            if not allow_argument_hints:
                                raise PlanValidationError(
                                    f"Step {index + 1} arguments do not match "
                                    f"'{tool_name}' input schema."
                                ) from exc
                            metadata["argument_hints_omitted"] = True
                            logger.warning(
                                "Omitting invalid argument hints for plan step %s "
                                "using capability %s",
                                identifier, tool_name,
                            )
                if has_arguments and not metadata.get("argument_hints_omitted"):
                    metadata["arguments"] = arguments
            elif ("arguments" in item or "args" in item) and item.get(
                "arguments", item.get("args")
            ) != {}:
                if not allow_argument_hints:
                    raise PlanValidationError(
                        f"Step {index + 1} cannot declare arguments without a capability."
                    )
                metadata["argument_hints_omitted"] = True
                logger.warning(
                    "Omitting argument hints without a capability for plan step %s",
                    identifier,
                )
            elif kind == "tool":
                raise PlanValidationError(
                    f"Tool step {index + 1} must name a registered capability."
                )

            for key, alias in (
                ("expected_output", "expectedOutput"),
                ("completion_criteria", "completionCriteria"),
            ):
                value = item.get(key, item.get(alias))
                if value is not None:
                    if not isinstance(value, str) or not value.strip():
                        raise PlanValidationError(
                            f"Step {index + 1} {key} must be non-empty text."
                        )
                    metadata[key] = value.strip()[:500]
        else:
            raise PlanValidationError(f"Step {index + 1} must be text or an object.")

        if not description:
            raise PlanValidationError(f"Step {index + 1} needs a description.")
        if not identifier:
            raise PlanValidationError(f"Step {index + 1} needs a non-empty id.")
        identifier = identifier[:128]
        if identifier in identifiers:
            raise PlanValidationError(f"Duplicate step id '{identifier}'.")
        identifiers[identifier] = index
        plan.append({
            "id": identifier[:128],
            "description": description,
            "status": status,
            **metadata,
        })
        raw_dependencies.append(dependencies)

    for index, dependencies in enumerate(raw_dependencies):
        normalized: list[int] = []
        for dependency in dependencies:
            if isinstance(dependency, bool):
                raise PlanValidationError(
                    f"Step {index + 1} contains an invalid dependency reference."
                )
            if isinstance(dependency, int):
                dependency_index = dependency
            elif isinstance(dependency, str):
                reference = dependency.strip()
                if reference in identifiers:
                    dependency_index = identifiers[reference]
                elif reference.isdecimal():
                    dependency_index = int(reference)
                else:
                    raise PlanValidationError(
                        f"Step {index + 1} depends on unknown step '{reference}'."
                    )
            else:
                raise PlanValidationError(
                    f"Step {index + 1} contains an invalid dependency reference."
                )
            if dependency_index < 0 or dependency_index >= len(plan):
                raise PlanValidationError(
                    f"Step {index + 1} depends on a step outside this plan."
                )
            if dependency_index == index:
                raise PlanValidationError(
                    f"Step {index + 1} cannot depend on itself."
                )
            if dependency_index not in normalized:
                normalized.append(dependency_index)
        plan[index]["dependencies"] = normalized

    _validate_acyclic(plan)
    return plan


def _validate_acyclic(plan: list[dict[str, Any]]) -> None:
    remaining = [0] * len(plan)
    dependents: list[list[int]] = [[] for _ in plan]
    for index, step in enumerate(plan):
        dependencies = step.get("dependencies", [])
        remaining[index] = len(dependencies)
        for dependency in dependencies:
            dependents[dependency].append(index)

    ready = [index for index, count in enumerate(remaining) if count == 0]
    visited = 0
    while ready:
        completed = ready.pop()
        visited += 1
        for dependent in dependents[completed]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
    if visited != len(plan):
        raise PlanValidationError("The plan contains a dependency cycle.")


def update_plan(steps: list) -> ToolResult:
    """Publish or revise a plan of steps using registered capabilities.

    Each object may include an id, capability name and arguments, dependency
    ids/indices, expected output, and completion criteria. A final response
    step may use ``kind="response"`` and is completed only after responding.
    """
    from app.tools.base import registry

    try:
        plan = normalize_steps(steps, capability_registry=registry)
    except PlanValidationError as exc:
        return ToolResult.fail(
            "INVALID_PLAN", str(exc),
            message="The plan contains invalid steps or capability inputs.",
        )
    if plan is None:
        return ToolResult.fail(
            "INVALID_PLAN", "steps must be a non-empty list.",
            message="Provide a non-empty list of plan steps.",
        )
    return ToolResult.ok("Plan updated.", {"steps": plan})
