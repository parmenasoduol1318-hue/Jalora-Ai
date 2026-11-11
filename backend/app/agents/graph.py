"""Jalora supervisor graph (spec Â§3): UNDERSTAND -> PLAN -> SELECT TOOLS ->
EXECUTE -> OBSERVE -> CONTINUE/RETRY/ASK APPROVAL -> VERIFY -> RESPOND.

Implemented as a durable LangGraph state machine:

    START -> agent -> (tool calls?) -> tools -> agent  ... loop
                          |               |
                          v               +-- paused (approval) -> END
                       respond <-------------+-- stopped/max steps
                          |
                          v
                         END

Key guarantees:
  * Tool selection is made by the model through bound tools â€” never keyword matching.
  * Level 3/4 tools pause the run and persist state for human approval (spec Â§24).
  * Every tool execution is audited with honest success/failure (spec Â§25, Â§45).
  * A STOP request terminates tool execution promptly.
  * Step budgets prevent infinite agent loops.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Annotated, Any, TypedDict
from urllib.parse import urlparse

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
    messages_from_dict,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from app.agents.intent import (
    classify_intent,
    explicitly_requests_email_send,
    has_multiple_outreach_recipients,
    needs_outreach_style_clarification,
)
from app.agents.prompts import MAX_STEPS_NUDGE, STOP_NUDGE
from app.errors import ModelEmptyResponseError, ToolResult
from app.events import emit
from app.services.model_router import JaloraChatModel
from app.services.tool_execution import decode_tool_result
from app.services.tool_preflight import validate_tool_arguments
from app.tools.base import PermissionLevel, ToolRegistry, ToolSpec

logger = logging.getLogger("jalora.agent")
_MODEL_OUTAGE_MESSAGE = (
    "The model became unavailable after some steps completed. "
    "Completed results were preserved; the remaining steps were not completed."
)
_MODEL_OUTAGE_NO_ACTION_MESSAGE = (
    "The model became unavailable before any requested task action completed. "
    "No task action is confirmed as completed."
)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    steps: int
    sources: list[dict[str, Any]]
    pending_approval: dict[str, Any] | None
    approved_call_ids: list[str]
    rejected_call_ids: list[str]
    skipped_call_ids: list[str]
    status: str  # running | completed | partial | paused_approval | stopped | error
    run_id: str
    user_id: str
    force_final: bool
    # P3 agent-core fields
    policy: dict[str, Any]  # {"autonomy": int, "tool_policies": {name: policy}}
    plan: list[dict[str, Any]] | None
    task_id: str
    tool_calls: int
    plan_attempts: int
    plan_continuation_attempts: int
    plan_recovery: bool
    recovery_dispatch: bool
    # Classified intent is retained for response behavior and task state.
    intent: str
    request: str
    direct_action: dict[str, str] | None
    provider_name: str
    provider_locked: bool
    model_override: str | None
    tool_context_project_id: str | None
    # Phase C browser state and artifacts (spec Â§16)
    browser_session_state: dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    recoverable: bool
    workflow_context: dict[str, Any] | None


def _validate_tool_arguments(
    spec: ToolSpec,
    args: dict[str, Any],
) -> tuple[dict[str, Any], ToolResult | None]:
    """Validate and normalize arguments against the capability's input schema."""
    if "_raw" in args:
        return args, ToolResult.fail(
            "INVALID_TOOL_ARGUMENTS",
            "The model emitted malformed tool-call JSON.",
            message=(
                f"The '{spec.name}' call contained malformed arguments. "
                "Retry it as one well-formed JSON object matching the tool schema. "
                "Never continue with omitted or defaulted arguments."
            ),
            data={"missing_fields": [], "invalid_fields": ["_raw"]},
        )
    normalized, validation_errors = validate_tool_arguments(spec, args)
    if validation_errors is None:
        return normalized, None
    missing_fields = sorted({
        str(error["loc"][0])
        for error in validation_errors
        if error["type"] == "missing" and error.get("loc")
    })
    invalid_fields = sorted({
        str(error["loc"][0])
        for error in validation_errors
        if error["type"] != "missing" and error.get("loc")
    })
    return args, ToolResult.fail(
        "INVALID_TOOL_ARGUMENTS",
        "The arguments did not satisfy the capability's input schema.",
        message=(
            f"The '{spec.name}' call is missing or has invalid arguments. "
            f"Missing fields: {', '.join(missing_fields) or 'none'}. "
            f"Invalid fields: {', '.join(invalid_fields) or 'none'}. "
            "Retry this same capability with corrected arguments, using "
            "values already present in the request or earlier tool results. "
            "Ask the user only if a required value is genuinely unavailable; "
            "do not switch to an unrelated tool."
        ),
        data={
            "missing_fields": missing_fields,
            "invalid_fields": invalid_fields,
        },
    )


def _public_contact_plan_error(
    request: str,
    plan: list[dict[str, Any]],
) -> str | None:
    if "public_contact_search" not in classify_intent(request).signals:
        return None

    tools = [str(step.get("tool") or "") for step in plan]
    contact_searches = tools.count("search_public_contacts")
    if contact_searches != 1 or any(
        name in {"search_companies", "search_email"} for name in tools
    ):
        return (
            "Public organization contact lookups require exactly one "
            "search_public_contacts step for all requested organizations. "
            "Do not use search_companies or search_email."
        )
    if (
        explicitly_requests_email_send(request)
        and has_multiple_outreach_recipients(request)
        and (
            "send_email" not in tools
            or "send_email_batch" in tools
        )
    ):
        return (
            "For public-organization outreach with an explicit send request, "
            "include send_email steps and never use send_email_batch. Each "
            "send_email action must be presented for approval before the next "
            "organization's message is sent."
        )
    return None


def _add_public_contact_verification_steps(
    plan: list[dict[str, Any]],
    search_step_index: int | None,
    result_data: dict[str, Any] | None,
    *,
    send_requested: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Add verified recipient actions and serialize any explicitly requested sends."""
    results_by_organization = (
        result_data.get("results_by_organization")
        if isinstance(result_data, dict)
        else None
    )
    if not isinstance(results_by_organization, dict) or not results_by_organization:
        return plan, False

    revised = [dict(step) for step in plan]
    search_index = search_step_index
    if search_index is None or not 0 <= search_index < len(revised):
        search_index = next(
            (
                index for index, step in enumerate(revised)
                if step.get("tool") == "search_public_contacts"
                and step.get("status") == "done"
            ),
            None,
        )
    if search_index is None:
        return plan, False

    used_ids = {str(step.get("id") or "") for step in revised}
    compose_candidates = [
        index for index, step in enumerate(revised)
        if step.get("tool") == "compose_email"
        and step.get("status") in {"pending", "active", "done"}
    ]
    send_candidates = [
        index for index, step in enumerate(revised)
        if step.get("tool") == "send_email"
        and step.get("status") in {"pending", "active", "done"}
    ]
    assigned_compose_indexes: set[int] = set()
    assigned_send_indexes: set[int] = set()
    organization_steps: list[tuple[str, int, int | None, int | None]] = []
    for organization, results in results_by_organization.items():
        if not isinstance(organization, str) or not isinstance(results, list) or not results:
            continue
        urls = [
            str(item.get("url") or "").strip()
            for item in results
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]
        if not urls:
            continue
        candidate_urls = "; ".join(urls[:3])
        description = (
                f"Open the official contact page for {organization}. "
                f"Candidate URLs: {candidate_urls}"
            )[:500]
        existing_index = next(
            (
                index for index, step in enumerate(revised)
                if step.get("tool") == "open_url"
                and organization.casefold()
                in str(step.get("description") or "").casefold()
                and step.get("status") in {"pending", "active", "done"}
            ),
            None,
        )
        if existing_index is None:
            step_id = f"verify_contact_{len(revised) + 1}"
            while step_id in used_ids:
                step_id = f"verify_contact_{len(revised) + 2}"
            revised.append({
                "id": step_id,
                "description": description,
                "status": "pending",
                "tool": "open_url",
                "arguments": {"url": urls[0]},
                "dependencies": [search_index],
            })
            used_ids.add(step_id)
            verification_index = len(revised) - 1
        else:
            step = revised[existing_index]
            step["description"] = description
            step["arguments"] = {"url": urls[0]}
            verification_index = existing_index

        organization_text = organization.casefold()
        compose_index = next(
            (
                index for index, step in enumerate(revised)
                if step.get("tool") == "compose_email"
                and step.get("status") in {"pending", "active", "done"}
                and index not in assigned_compose_indexes
                and organization_text
                in str(step.get("description") or "").casefold()
            ),
            None,
        )
        if compose_index is None:
            compose_index = next(
                (
                    index for index in compose_candidates
                    if index not in assigned_compose_indexes
                ),
                None,
            )
        send_index = next(
            (
                index for index, step in enumerate(revised)
                if step.get("tool") == "send_email"
                and step.get("status") in {"pending", "active", "done"}
                and index not in assigned_send_indexes
                and organization_text
                in str(step.get("description") or "").casefold()
            ),
            None,
        )
        if send_index is None:
            send_index = next(
                (
                    index for index in send_candidates
                    if index not in assigned_send_indexes
                ),
                None,
            )
        if compose_index is not None:
            assigned_compose_indexes.add(compose_index)
        if send_index is not None:
            assigned_send_indexes.add(send_index)
        if send_requested and compose_index is not None:
            revised[compose_index]["status"] = "skipped"
        organization_steps.append(
            (organization, verification_index, compose_index, send_index)
        )

    if not organization_steps:
        return plan, False

    for _organization, verification_index, compose_index, send_index in organization_steps:
        revised[verification_index]["dependencies"] = [search_index]
        if compose_index is not None:
            revised[compose_index]["dependencies"] = [verification_index]
        if send_index is not None:
            revised[send_index]["dependencies"] = [
                verification_index
                if send_requested or compose_index is None
                else compose_index
            ]

    return revised, True


def _ready_plan_tools(plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    steps = plan or []
    return [
        step for index, step in enumerate(steps)
        if step.get("tool")
        and step.get("status") in {"pending", "active"}
        and all(
            isinstance(dependency, int)
            and 0 <= dependency < len(steps)
            and steps[dependency].get("status") in {"done", "skipped"}
            for dependency in step.get("dependencies", [])
        )
    ]


def _select_public_contact_tool_calls(
    plan: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]],
    *,
    answered_ids: set[str] | None = None,
    read_only_tool_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select ready contact reads together, or queue ready emails in plan order."""
    if not plan or any(
        step.get("status") == "failed" for step in plan
    ) or not tool_calls:
        return tool_calls, []
    if any(call.get("name") == "update_plan" for call in tool_calls):
        return tool_calls, []
    answered = answered_ids or set()
    available_calls = [
        call for call in tool_calls
        if str(call.get("id") or "") not in answered
    ]

    ready_steps = _ready_plan_tools(plan)
    if not ready_steps:
        return [], tool_calls

    next_tool = str(ready_steps[0].get("tool") or "")
    if next_tool in (read_only_tool_names or set()):
        selected_step_indexes = {
            index for index, step in enumerate(plan)
            if step in ready_steps and step.get("tool") == next_tool
        }
    elif next_tool not in {"open_url", "send_email"}:
        selected_step_indexes = {
            next(
                index for index, step in enumerate(plan)
                if step is ready_steps[0]
            )
        }
    elif next_tool == "open_url":
        selected_step_indexes = {
            index for index, step in enumerate(plan)
            if step in ready_steps and step.get("tool") == "open_url"
        }
    else:
        selected_step_indexes = {
            next(
                index for index, step in enumerate(plan)
                if step is ready_steps[0]
            )
        }

    selected: list[dict[str, Any]] = []
    matched_step_indexes: set[int] = set()
    for call in available_calls:
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        match = _matching_plan_step(
            plan,
            name,
            args,
            permit_read_only_argument_correction=(name == "open_url"),
        )
        if match in matched_step_indexes:
            match = next(
                (
                    index for index in sorted(selected_step_indexes)
                    if index not in matched_step_indexes
                    and plan[index].get("tool") == name
                ),
                None,
            )
        if match in selected_step_indexes and match not in matched_step_indexes:
            selected.append(call)
            matched_step_indexes.add(match)

    selected_ids = {id(call) for call in selected}
    return selected, [
        call for call in available_calls if id(call) not in selected_ids
    ]


def _effective_tool_call_budget(
    plan: list[dict[str, Any]] | None,
    configured_budget: int,
) -> int:
    """Reserve enough calls to finish a validated public-contact workflow."""
    saved_workflow_steps = sum(
        1 for step in (plan or [])
        if str(step.get("id") or "").startswith("workflow:")
        and isinstance(step.get("tool"), str)
    )
    if saved_workflow_steps:
        return max(configured_budget, saved_workflow_steps + 1)
    if not plan or not any(
        step.get("tool") == "search_public_contacts" for step in plan
    ):
        return configured_budget
    planned_calls = sum(
        1 for step in plan
        if isinstance(step.get("tool"), str) and step.get("tool")
    )
    return max(configured_budget, planned_calls)


def _expand_contact_tool_bundle(
    tool_names: list[str] | None,
    registry: ToolRegistry,
) -> frozenset[str]:
    selected = (
        {spec.name for spec in registry.all()}
        if tool_names is None
        else set(tool_names)
    )
    if "search_public_contacts" in selected:
        selected.update(
            name for name in (
                "search_web",
                "open_url",
                "compose_email",
            )
            if registry.get(name) is not None
        )
    return frozenset(selected)


def _build_planning_messages(request: str, instruction: str) -> list[BaseMessage]:
    """Keep plan generation focused on the current task, not the full chat history."""
    return [
        SystemMessage(content=instruction),
        HumanMessage(content=request),
    ]


def _normalize_email_action_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Convert instruction-style email text into a polished, reviewable draft."""
    if name not in {"send_email", "compose_email"}:
        return args
    if not isinstance(args, dict):
        return args

    body = str(args.get("body") or "").strip()
    to = str(args.get("to") or args.get("recipient") or "").strip()
    subject = str(args.get("subject") or "").strip()
    if not body or not to:
        return args

    lower = body.lower()
    if not re.search(
        r"\b(?:send\s+(?:an\s+)?email|email\s+to|write\s+to|ask\s+(?:him|her|them)|tell\s+(?:him|her|them)|how\s+(?:he|she|they)\s+is\s+doing)\b",
        lower,
    ):
        return args

    from app.services import email as email_service

    draft = email_service.generate_email_draft(to, body)
    if not draft.get("to") or not draft.get("body"):
        return args

    args = dict(args)
    args["to"] = draft["to"]
    args["subject"] = draft.get("subject") or subject or "Follow Up"
    args["body"] = draft["body"]
    if not args.get("regards") and draft.get("regards"):
        args["regards"] = draft["regards"]
    return args


TERMINAL_STATUSES = {"paused_approval", "paused", "stopped", "partial", "error"}


def serialize_state(state: AgentState) -> dict[str, Any]:
    return {
        "messages": [message_to_dict(m) for m in state.get("messages", [])],
        "steps": state.get("steps", 0),
        "sources": state.get("sources", []),
        "status": state.get("status", "running"),
        "run_id": state.get("run_id", ""),
        "user_id": state.get("user_id", ""),
        "approved_call_ids": state.get("approved_call_ids", []),
        "rejected_call_ids": state.get("rejected_call_ids", []),
        "skipped_call_ids": state.get("skipped_call_ids", []),
        "force_final": state.get("force_final", False),
        "pending_approval": state.get("pending_approval"),
        "policy": state.get("policy") or {},
        "plan": state.get("plan"),
        "task_id": state.get("task_id", ""),
        "tool_calls": state.get("tool_calls", 0),
        "plan_attempts": state.get("plan_attempts", 0),
        "plan_continuation_attempts": state.get("plan_continuation_attempts", 0),
        "plan_recovery": state.get("plan_recovery", False),
        "recovery_dispatch": state.get("recovery_dispatch", False),
        "intent": state.get("intent", ""),
        "request": state.get("request", ""),
        "direct_action": state.get("direct_action"),
        "tool_context_project_id": state.get("tool_context_project_id"),
        "browser_session_state": state.get("browser_session_state"),
        "artifacts": state.get("artifacts", []),
        "recoverable": state.get("recoverable", False),
        "workflow_context": state.get("workflow_context"),
    }


def deserialize_state(data: dict[str, Any]) -> AgentState:
    messages = messages_from_dict(data.get("messages", []))
    return {
        "messages": messages,
        "steps": data.get("steps", 0),
        "sources": data.get("sources", []),
        "status": data.get("status", "running"),
        "run_id": data.get("run_id", ""),
        "user_id": data.get("user_id", ""),
        "approved_call_ids": data.get("approved_call_ids", []),
        "rejected_call_ids": data.get("rejected_call_ids", []),
        "skipped_call_ids": data.get("skipped_call_ids", []),
        "force_final": data.get("force_final", False),
        "pending_approval": data.get("pending_approval"),
        "policy": data.get("policy") or {},
        "plan": data.get("plan"),
        "task_id": data.get("task_id", ""),
        "tool_calls": data.get("tool_calls", 0),
        "plan_attempts": data.get("plan_attempts", 0),
        "plan_continuation_attempts": data.get("plan_continuation_attempts", 0),
        "plan_recovery": data.get("plan_recovery", False),
        "recovery_dispatch": data.get("recovery_dispatch", False),
        "intent": data.get("intent", ""),
        "request": data.get("request", ""),
        "direct_action": data.get("direct_action"),
        "tool_context_project_id": data.get("tool_context_project_id"),
        "browser_session_state": data.get("browser_session_state"),
        "artifacts": data.get("artifacts", []),
        "recoverable": data.get("recoverable", False),
        "workflow_context": data.get("workflow_context"),
    }


def _unanswered_tool_call_ids(messages: list[BaseMessage]) -> list[str]:
    """Tool call ids on the last AI message that have no ToolMessage yet."""
    last_ai = _last_ai(messages)
    if last_ai is None or not last_ai.tool_calls:
        return []
    answered = {
        m.tool_call_id for m in messages if isinstance(m, ToolMessage)
    }
    return [tc["id"] for tc in last_ai.tool_calls if tc["id"] not in answered]


def _has_current_turn_tool_result(messages: list[BaseMessage]) -> bool:
    """Whether a tool result belongs to the latest user request, not old history."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, ToolMessage):
            return True
    return False


def _current_turn_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Exclude older turns from tool-failure and completion decisions."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _response_calls_tool(response: AIMessage, tool_name: str) -> bool:
    return any(
        str(call.get("name") or "") == tool_name
        for call in response.tool_calls or []
    )


def _current_turn_has_tool_attempt(
    messages: list[BaseMessage],
    tool_name: str,
) -> bool:
    current_messages = _current_turn_messages(messages)
    calls_by_id = {
        str(call.get("id") or ""): str(call.get("name") or "")
        for message in current_messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls or []
    }
    return any(
        isinstance(message, ToolMessage)
        and calls_by_id.get(message.tool_call_id) == tool_name
        for message in current_messages
    )


def _last_ai(messages: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _has_rejected_plan_update(messages: list[BaseMessage]) -> bool:
    plan_call_ids = {
        call.get("id")
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("name") == "update_plan"
    }
    return any(
        isinstance(message, ToolMessage)
        and message.tool_call_id in plan_call_ids
        and not _parse_tool_result(message.content).success
        for message in messages
    )


def _chunk_text_pieces(chunk: Any) -> list[str]:
    """Extract displayable text deltas from a streamed message chunk."""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        pieces: list[str] = []
        for block in content:
            if isinstance(block, str) and block:
                pieces.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                pieces.append(str(block["text"]))
        return pieces
    return []


_INTERNAL_MODEL_BLOCK = re.compile(
    r"<(?P<tag>thinking|analysis|reasoning|syntax_call)\b[^>]*>"
    r".*?(?:</(?P=tag)\s*>|$)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_internal_model_markup(text: str) -> str:
    """Keep private reasoning and provider-specific pseudo-tool syntax out of replies."""
    if not isinstance(text, str) or not text:
        return ""
    return _INTERNAL_MODEL_BLOCK.sub("", text).strip()


def _parse_text_tool_calls(text: str, registry: ToolRegistry) -> tuple[str, list[dict[str, Any]]]:
    """Extract tool calls from model *text* output when native ``tool_calls``
    are absent (e.g. Ollama/Qwen emitting JSON inside the content field).

    Unlike the previous ``_sanitize_tool_call_json`` which *stripped* every JSON
    pattern from the response (causing tool calls to be silently lost and the
    raw JSON to leak to the user), this function **extracts** each tool-call JSON
    block, **validates** the tool name against the registered tool registry,
    parses the arguments strictly, and returns a clean (text-stripped,
    validated) tuple.

    Only tools that exist in the registry are accepted — unknown tool names are
    silently dropped (the model is told it invented a tool it cannot use).
    Malformed JSON or missing required fields produce a no-op ``ToolResult`` that
    is fed back to the model so it can self-correct.

    Returns:
        (cleaned_text, list_of_parsed_tool_calls)
        Each call dict has keys: name, args (dict), id, type="tool_call".
    """
    if not isinstance(text, str) or not text.strip():
        return text or "", []

    # Match {"name": "tool_name", "arguments": {...}} with balanced braces.
    # We use a manual brace-matching scan rather than a regex so that nested
    # objects in the arguments (e.g. email body with JSON) are captured fully.
    calls: list[dict[str, Any]] = []
    removed_spans: list[tuple[int, int]] = []  # (start, end) offsets to excise

    pattern = re.compile(
        r'\{"name"\s*:\s*"([^"]+)"\s*,\s*"(?:arguments|parameters|args)"\s*:\s*(\{)'
    )
    for m in pattern.finditer(text):
        name = m.group(1)
        args_start = m.start(2)  # position of the opening '{' of arguments
        # Manually match balanced braces to handle nested objects
        depth = 0
        in_string = False
        escaped = False
        i = args_start
        end = -1
        while i < len(text):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    args_end = i + 1
                    end = args_end
                    while end < len(text) and text[end].isspace():
                        end += 1
                    if end < len(text) and text[end] == '}':
                        end += 1
                    break
            i += 1
        if end == -1:
            # Unbalanced — skip this match
            continue

        removed_spans.append((m.start(), end))

        args_str = text[args_start:args_end]
        try:
            args = json.loads(args_str)
            if not isinstance(args, dict):
                args = {}
        except (json.JSONDecodeError, ValueError):
            args = {"_raw": args_str[:500]}

        # Validate the tool name against the registry
        spec = registry.get(name)
        if spec is None:
            logger.warning(
                "Model emitted tool call for unregistered tool '%s'; dropping.", name
            )
            continue

        call_id = f"txtcall_{len(calls) + 1}"
        calls.append({
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        })

    # Strip every matched JSON block (valid or invalid) from the visible text
    # so the user never sees serialized tool-call JSON as chat output.
    cleaned_parts: list[str] = []
    pos = 0
    for start, end in removed_spans:
        cleaned_parts.append(text[pos:start])
        pos = end
    cleaned_parts.append(text[pos:])
    cleaned = "".join(cleaned_parts).strip()
    return cleaned, calls


def build_graph(
    *,
    model: JaloraChatModel,
    fallback_models: list[JaloraChatModel] | None = None,
    registry: ToolRegistry,
    max_steps: int = 24,
    tool_timeout: float = 60.0,
    max_tool_calls: int = 24,
    max_retries: int = 2,
    entry: str = "agent",
    system_prompt: str = "",
    tool_names: list[str] | None = None,
):
    """Compile the supervisor graph. `entry='tools'` resumes a paused run.

    `tool_names` optionally binds only the task-discovered registry subset.
    `None` (default) binds every registered tool.
    """
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun, Approval
    from app.services import run_registry
    from app.services.audit import log_activity, timer
    from app.models.user import new_id

    bound_tool_names = _expand_contact_tool_bundle(tool_names, registry)
    available_tools = [
        spec.tool for spec in registry.all() if spec.name in bound_tool_names
    ]
    tools_bound = model.bind_tools(available_tools)
    bound_fallback_models = [
        fallback.bind_tools(available_tools) for fallback in (fallback_models or [])
    ]
    tools_free = model.model_copy(update={"bound_tools": []})
    planner_spec = registry.get("update_plan")
    _EMPTY_STREAM_ATTEMPTS = 2
    failed_providers: set[str] = set()

    def _validate_output(text: str, intent: str | None = None) -> str:
        """Post-generation output validation and normalization guard.

        - Strips non-whitespace control characters.
        - Balances unterminated code fences.
        - Detects truncation (trailing ellipsis) and appends a marker.
        - Applies intent-specific post-generation rules.

        Tool-call JSON that leaks into model text output is handled separately
        by ``_parse_text_tool_calls`` in ``_stream_model`` — it is extracted,
        validated against the registry, and attached as native tool_calls rather
        than being stripped (which silently dropped tool calls on models that
        emit JSON in the content field).
        """
        if not isinstance(text, str):
            return str(text or "")
        # Strip non-whitespace control characters
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
        # Balance unterminated code fences
        fence_count = text.count("```")
        if fence_count % 2 == 1:
            text = text.rstrip() + "\n```"
        # Detect truncation (trailing ellipsis indicates incomplete output)
        if text.rstrip().endswith("..."):
            text = text.rstrip()[:-3].rstrip() + " [response truncated]"
        return text

    async def _stream_model(
        m,
        messages: list[BaseMessage],
        *,
        alternates: list[Any] | None = None,
    ) -> AIMessage:
        """Buffer candidate output until generation succeeds; fail over per turn."""
        candidates = [m, *(alternates or [])]
        last_error: Exception | None = None
        for candidate_index, candidate in enumerate(candidates):
            provider = getattr(getattr(candidate, "bound", candidate), "spec", None)
            provider_name = getattr(provider, "name", "")
            model_name = getattr(getattr(candidate, "bound", candidate), "model_name", "")
            if provider_name and provider_name in failed_providers:
                continue
            if candidate_index:
                emit("status", {
                    "message": f"Falling back to {provider_name}/{model_name}",
                    "provider": provider_name,
                })
            for attempt in range(1, _EMPTY_STREAM_ATTEMPTS + 1):
                chunks: list[Any] = []
                full = None
                started = time.perf_counter()
                first_token_ms: int | None = None
                try:
                    async for chunk in candidate.astream(messages):
                        if first_token_ms is None and _chunk_text_pieces(chunk):
                            first_token_ms = int((time.perf_counter() - started) * 1000)
                        chunks.append(chunk)
                        full = chunk if full is None else full + chunk
                except ValueError as exc:
                    if str(exc) == "No generation chunks were returned":
                        logger.warning(
                            "empty model stream from %s (attempt %d/%d)",
                            provider_name or "model", attempt, _EMPTY_STREAM_ATTEMPTS,
                        )
                        continue
                    last_error = exc
                    logger.warning(
                        "model stream failed for %s/%s; trying an eligible fallback",
                        provider_name or "model",
                        model_name,
                        exc_info=True,
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - try only configured candidates
                    last_error = exc
                    logger.warning(
                        "model stream failed for %s/%s; trying an eligible fallback",
                        provider_name or "model",
                        model_name,
                        exc_info=True,
                    )
                    break
                if full is None:
                    logger.warning(
                        "empty model stream from %s (attempt %d/%d): no content received",
                        provider_name or "model", attempt, _EMPTY_STREAM_ATTEMPTS,
                    )
                    continue

                emit("run_metrics", {
                    "provider": provider_name,
                    "model": model_name,
                    "first_token_ms": first_token_ms,
                    "stream_ms": int((time.perf_counter() - started) * 1000),
                    "attempt": attempt,
                })
                raw_content = _strip_internal_model_markup(
                    _validate_output(full.content)
                )
                parsed_calls = _parse_text_tool_calls(raw_content, registry)
                if parsed_calls[0]:
                    emit("token", {"text": parsed_calls[0]})
                metadata = dict(full.response_metadata or {})
                metadata.update({"provider": provider_name, "model": model_name})
                return AIMessage(
                    content=parsed_calls[0],
                    tool_calls=(full.tool_calls or []) + parsed_calls[1],
                    additional_kwargs=full.additional_kwargs or {},
                    id=full.id or new_id(),
                    response_metadata=metadata,
                )
            if provider_name:
                failed_providers.add(provider_name)

        if last_error is not None:
            raise last_error
        raise ModelEmptyResponseError(
            getattr(getattr(m, "bound", m), "spec", None).name
            if getattr(getattr(m, "bound", m), "spec", None) else ""
        )

    async def _stream_until_usable(
        candidates: list[Any],
        messages: list[BaseMessage],
        accept,
        *,
        purpose: str,
        allow_unaccepted_response: bool = True,
    ) -> AIMessage:
        last_message: AIMessage | None = None
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            provider = getattr(getattr(candidate, "bound", candidate), "spec", None)
            provider_name = getattr(provider, "name", "")
            model_name = getattr(getattr(candidate, "bound", candidate), "model_name", "")
            if provider_name and provider_name in failed_providers:
                continue
            if index:
                emit("status", {
                    "message": (
                        f"Trying {provider_name}/{model_name} after the previous "
                        f"model did not produce a usable {purpose}."
                    ),
                    "provider": provider_name,
                })
            try:
                response = await _stream_model(candidate, messages)
            except Exception as exc:  # noqa: BLE001 - try only configured candidates
                last_error = exc
                logger.warning(
                    "%s generation failed for %s/%s",
                    purpose, provider_name or "model", model_name,
                    exc_info=True,
                )
                continue
            if accept(response):
                return response
            last_message = response

        if last_message is not None and allow_unaccepted_response:
            return last_message
        if last_message is not None:
            raise ModelEmptyResponseError("")
        if last_error is not None:
            raise last_error
        raise ModelEmptyResponseError("")

    def _pending_plan_tools(plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [
            step for step in (plan or [])
            if step.get("tool") and step.get("status") in {"pending", "active"}
        ]

    def _task_request(state: AgentState, messages: list[BaseMessage]) -> str:
        request = state.get("request")
        if request:
            return request
        return next(
            (
                str(message.content)
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
            ),
            "",
        )

    async def recover_without_plan(
        state: AgentState,
        messages: list[BaseMessage],
        reason: str,
    ) -> dict[str, Any]:
        latest_request = _task_request(state, messages)
        capabilities = [
            f"- {spec.name}: {spec.description[:180]}"
            for spec in registry.all()
            if spec.name in bound_tool_names and spec.name != "update_plan"
        ]
        recovery_instruction = (
            "The plan formatter did not produce a usable plan. Do not stop or "
            "tell the user that planning failed. Re-read the complete request, "
            "work out the actions needed, and continue using only the registered "
            "capabilities listed below. Propose the next safe tool action(s) "
            "with valid arguments; the runtime will validate and publish those "
            "actions as a recovery plan before executing them. Keep dependent "
            "actions for later turns when their inputs require earlier results. "
            "Ask the user only for genuinely missing information. Do not claim "
            "that an action is complete without its tool result.\n\n"
            "Available capabilities:\n"
            + ("\n".join(capabilities) or "- No task capabilities are available.")
        )
        if "public_contact_search" in classify_intent(latest_request).signals:
            recovery_instruction += (
                "\nFor public organization contacts, use exactly one "
                "search_public_contacts call containing all requested "
                "organizations. Do not tell the user to provide the email "
                "addresses or claim that public lookup is unavailable before "
                "that tool has been attempted. Verify discovered addresses "
                "against the official contact pages before drafting."
            )
        if explicitly_requests_email_send(latest_request):
            recovery_instruction += (
                "\nEmail delivery remains subject to the normal explicit "
                "approval workflow. Do not claim delivery before approval."
            )
        try:
            is_public_contact_lookup = (
                "public_contact_search"
                in classify_intent(latest_request).signals
                and "search_public_contacts" in bound_tool_names
            )
            response = await _stream_until_usable(
                [tools_bound, *bound_fallback_models],
                _with_system(messages, recovery_instruction),
                lambda candidate: (
                    _response_calls_tool(candidate, "search_public_contacts")
                    if is_public_contact_lookup
                    else bool(candidate.tool_calls)
                    or bool(str(candidate.content or "").strip())
                ),
                purpose="task execution recovery",
                allow_unaccepted_response=not is_public_contact_lookup,
            )
        except Exception as exc:
            logger.warning(
                "Task execution recovery failed after plan issue (%s)",
                reason,
                exc_info=True,
            )
            return {
                "messages": [AIMessage(
                    content=_model_outage_message(messages, exc),
                    id=new_id(),
                )],
                "status": "partial",
                "plan_recovery": True,
                "force_final": False,
            }
        return {
            "messages": [response],
            "status": "running",
            "plan_recovery": True,
            "force_final": False,
        }

    async def _constrained_final(messages: list[BaseMessage], candidate: AIMessage) -> AIMessage:
        """Repair explicit measurable format violations once, when requested."""
        from app.services.response_validation import correction_instruction, validate_response

        user_prompt = next(
            (str(message.content) for message in reversed(messages)
             if message.type == "human"),
            "",
        )
        issues = validate_response(user_prompt, str(candidate.content or ""))
        if not issues:
            return candidate
        try:
            corrected = await _stream_model(
                tools_free,
                _with_system(
                    messages + [candidate],
                    correction_instruction(user_prompt, str(candidate.content or ""), issues),
                ),
            )
            corrected.content = _validate_output(corrected.content)
            if not validate_response(user_prompt, str(corrected.content or "")):
                return corrected
        except Exception:  # noqa: BLE001 - preserve the original answer honestly
            logger.warning("response constraint repair failed", exc_info=True)
        return candidate

    async def agent_node(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        if state.get("force_final") or state.get("status") == "stopped":
            return await respond_node(state)

        plan = state.get("plan") or []
        batch_send_steps = [
            step for step in plan if step.get("tool") == "send_email_batch"
        ]
        send_steps = [
            step for step in plan if step.get("tool") == "send_email"
        ]
        tool_steps = [step for step in plan if step.get("tool")]
        if len(batch_send_steps) == 1:
            current_messages = _current_turn_messages(messages)
            calls_by_id = {
                str(call.get("id") or ""): call
                for message in current_messages
                if isinstance(message, AIMessage)
                for call in message.tool_calls or []
            }
            batch_result = next(
                (
                    _parse_tool_result(str(message.content or ""))
                    for message in current_messages
                    if isinstance(message, ToolMessage)
                    and calls_by_id.get(message.tool_call_id, {}).get("name")
                    == "send_email_batch"
                    and _parse_tool_result(str(message.content or "")).success
                ),
                None,
            )
            pending_non_batch_steps = [
                step for step in tool_steps
                if step.get("tool") != "send_email_batch"
                and step.get("status") in {"pending", "active"}
            ]
            if batch_result is not None and not pending_non_batch_steps:
                sent = (batch_result.data or {}).get("sent", [])
                providers = {
                    str(item.get("provider") or "")
                    for item in sent if isinstance(item, dict)
                }
                if providers == {"email_mock"}:
                    summary = (
                        f"Recorded {len(sent)} approved emails in the offline "
                        "email mock; no messages were delivered to recipients."
                    )
                else:
                    summary = f"Sent {len(sent)} approved emails."
                return {
                    "messages": [AIMessage(content=summary, id=new_id())],
                    "status": "running",
                }
        if (
            len(send_steps) > 1
            and all(step.get("status") in {"done", "skipped"} for step in tool_steps)
        ):
            planned_recipients: set[str] = set()
            for step in send_steps:
                arguments = step.get("arguments")
                recipient = (
                    str(arguments.get("to") or "").strip().casefold()
                    if isinstance(arguments, dict)
                    else ""
                )
                if not recipient:
                    match = re.search(
                        r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w-]+(?:\.[\w-]+)+",
                        str(step.get("description") or ""),
                    )
                    recipient = match.group(0).strip().casefold() if match else ""
                if recipient:
                    planned_recipients.add(recipient)

            current_messages = _current_turn_messages(messages)
            calls_by_id = {
                str(call.get("id") or ""): call
                for message in current_messages
                if isinstance(message, AIMessage)
                for call in message.tool_calls or []
            }
            confirmed_recipients: set[str] = set()
            for message in current_messages:
                if not isinstance(message, ToolMessage):
                    continue
                result = _parse_tool_result(str(message.content or ""))
                call = calls_by_id.get(message.tool_call_id) or {}
                if not result.success or call.get("name") != "send_email":
                    continue
                args = call.get("args")
                recipient = (
                    str(args.get("to") or "").strip().casefold()
                    if isinstance(args, dict)
                    else ""
                )
                if recipient:
                    confirmed_recipients.add(recipient)
            if (
                len(planned_recipients) == len(send_steps)
                and confirmed_recipients == planned_recipients
            ):
                return {
                    "messages": [AIMessage(
                        content=(
                            f"All {len(send_steps)} planned outreach emails were "
                            "sent successfully after approval."
                        ),
                        id=new_id(),
                    )],
                    "status": "running",
                }

        latest_request = _task_request(state, messages)
        is_public_contact_lookup = (
            "public_contact_search"
            in classify_intent(latest_request).signals
            and "search_public_contacts" in bound_tool_names
        )
        previous_user_request = "\n\n".join(
            str(message.content)
            for message in [
                prior for prior in messages[:-1]
                if isinstance(prior, HumanMessage)
                and str(prior.content).strip() != latest_request.strip()
            ][-3:]
        )
        outreach_context = "\n".join(
            value for value in (previous_user_request, latest_request) if value
        )
        if (
            not state.get("plan")
            and needs_outreach_style_clarification(outreach_context)
            and "public_contact_search"
            not in classify_intent(outreach_context).signals
        ):
            return {
                "messages": [AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "request_user_input",
                        "args": {
                            "title": "Outreach message style",
                            "question": (
                                "Should I prepare one shared email for all "
                                "recipients, or a separately tailored email for "
                                "each recipient?"
                            ),
                            "input_type": "choice",
                            "options": [
                                "One shared email for all recipients",
                                "A separately tailored email for each recipient",
                            ],
                            "required": True,
                        },
                        "id": new_id(),
                    }],
                    id=new_id(),
                )],
                "status": "running",
            }
        hotel_details_request = (
            _hotel_booking_details_request(latest_request)
            if state.get("intent") not in {"coding", "research", "web_search"}
            else None
        )
        if hotel_details_request:
            return {
                "messages": [AIMessage(
                    content=hotel_details_request,
                    id=new_id(),
                )],
                "status": "completed",
            }

        from app.tools.routing import is_multi_step_request

        requires_plan = is_multi_step_request(latest_request)
        if requires_plan and not state.get("plan") and not state.get("plan_recovery"):
            planning_attempts = state.get("plan_attempts", 0)
            if planner_spec is None or "update_plan" not in bound_tool_names:
                return await recover_without_plan(
                    state,
                    messages,
                    "the planning capability was not selected"
                )
            if planning_attempts >= 3:
                return await recover_without_plan(
                    state,
                    messages,
                    "the planner exceeded its retry limit"
                )

            capabilities = [
                f"- {spec.name}: {spec.description[:180]}"
                for spec in registry.all()
                if spec.name in bound_tool_names and spec.name != "update_plan"
            ]
            planning_instruction = (
                "Planning pass: do not execute any task capability yet. First call "
                "update_plan with a dependency-ordered plan for the user's complete "
                "request. Every tool-backed step must name one of the registered "
                "capabilities listed below. Use only update_plan in this pass; task "
                "execution begins only after the plan validates.\n\n"
                "Available capabilities:\n"
                + "\n".join(capabilities)
            )
            if "public_contact_search" in classify_intent(
                latest_request
            ).signals:
                planning_instruction += (
                    "\n\nThis is a public organization-contact lookup, not a "
                    "commercial company search or a search of the user's mailbox. "
                    "Plan one search_public_contacts step containing every "
                    "requested organization in its organizations list. Do not use "
                    "one search step per organization. Do not use search_companies "
                    "or search_email for these contacts. Keep "
                    "the search step pending any later verification or drafting "
                    "steps; do not mark it complete in the plan. The user has "
                    "already requested a public lookup: do not ask them to "
                    "provide organization email addresses or claim that lookup "
                    "is unavailable instead of scheduling this search."
                )
                if (
                    explicitly_requests_email_send(latest_request)
                    and has_multiple_outreach_recipients(latest_request)
                ):
                    planning_instruction += (
                        "\nThe user explicitly requests sending to multiple "
                        "organizations. Include send_email steps in the "
                        "requested order after each address is verified. Never "
                        "use send_email_batch. Each send_email action must pause "
                        "for approval before the next organization's message "
                        "is sent."
                    )
            web_discovery_tools = [
                spec.name for spec in registry.all()
                if spec.name in bound_tool_names
                and spec.category == "research"
                and "web" in spec.tags
                and not {"source", "browser"}.intersection(spec.tags)
            ]
            page_read_tools = [
                spec.name for spec in registry.all()
                if spec.name in bound_tool_names
                and spec.category == "research"
                and {"source", "browser"}.intersection(spec.tags)
            ]
            report_tools = [
                spec.name for spec in registry.all()
                if spec.name in bound_tool_names
                and spec.category == "files"
                and "report" in spec.tags
            ]
            if web_discovery_tools and page_read_tools and report_tools:
                planning_instruction += (
                    "\n\nFor any plan that combines web research with a report or "
                    "document, include a page-reading step using one of "
                    f"{', '.join(page_read_tools)}. Make it depend on web discovery, "
                    "and make every report step depend on that page-reading step. "
                    "Use exact earlier step IDs in dependencies; use an empty "
                    "dependency list only for genuinely independent work. Do not "
                    "put placeholder or guessed content in artifact arguments."
                )
            planning_instruction += (
                "\n\nPlan format: every step must have a non-empty id and "
                "description. Tool-backed steps use the exact registered name in "
                "the 'tool' field and an optional 'dependencies' list of step IDs. "
                "Only include 'arguments' when they are a JSON object; omit "
                "arguments that depend on future tool results. Do not create "
                "standalone argument-only steps. Example: "
                '{"id":"market","description":"Search for current market data",'
                '"tool":"search_web","dependencies":[]}. Use snake_case field '
                "names such as 'expected_output'."
            )
            planning_instruction += (
                "\nIf update_plan rejects a previous plan, read the exact validation "
                "message and correct that issue in the revised plan. Preserve the "
                "user's requested tasks; do not ask the user to repair internal "
                "plan IDs or change the request."
            )
            if re.search(
                r"\b(?:pause|stop|wait)\b.{0,80}\b(?:confirmation|confirm|approval)\b"
                r"|\bonly\s+after\s+(?:i|the\s+user)\s+(?:respond|reply|confirm)",
                latest_request,
                re.I,
            ):
                planning_instruction += (
                    "\n\nThe user requested an explicit confirmation checkpoint. "
                    "Treat it as a hard execution boundary: include only actions "
                    "that must happen before the checkpoint. Do not execute or "
                    "include later actions in this plan; after completing the "
                    "checkpoint work, present the result and ask for confirmation."
                )
            planner_model = model.bind_tools([planner_spec.tool])
            planner_fallbacks = [
                fallback.bind_tools([planner_spec.tool])
                for fallback in (fallback_models or [])
            ]
            try:
                ai = await _stream_until_usable(
                    [planner_model, *planner_fallbacks],
                    _build_planning_messages(latest_request, planning_instruction),
                    lambda response: bool(response.tool_calls)
                    and all(call.get("name") == "update_plan"
                            for call in response.tool_calls),
                    purpose="validated plan",
                    allow_unaccepted_response=not _has_rejected_plan_update(messages),
                )
            except Exception as exc:  # noqa: BLE001 - try normal task execution next
                return await recover_without_plan(
                    state,
                    messages,
                    f"planning model failed ({type(exc).__name__})"
                )
            if not ai.tool_calls:
                return await recover_without_plan(
                    state,
                    messages,
                    "the planner returned no validated plan"
                )
            return {
                "messages": [ai],
                "status": "running",
                "plan_attempts": planning_attempts + 1,
            }

        run_id = state.get("run_id", "")
        user_id = state.get("user_id", "")
        if run_id and run_registry.is_stopped(user_id, run_id):
            return {"status": "stopped"}
        if run_id and run_registry.is_paused(user_id, run_id):
            # Generic pause (spec Â§24): defer the next model call to a resume.
            # Any dangling tool calls get PAUSED fillers so the persisted
            # state is valid for the resumed model call.
            pending = _unanswered_tool_call_ids(messages)
            if pending:
                fillers = [
                    ToolMessage(
                        content=ToolResult.fail(
                            "PAUSED", "The run was paused; this tool was not executed.",
                            message="Paused; re-issue this tool after resuming.",
                        ).model_dump_json(),
                        tool_call_id=cid,
                    )
                    for cid in pending
                ]
                return {"messages": fillers, "status": "paused"}
            return {"status": "paused"}

        try:
            pending_plan_tools = _pending_plan_tools(state.get("plan"))
            if pending_plan_tools:
                plan = state.get("plan") or []
                is_public_contact_workflow = (
                    "public_contact_search"
                    in classify_intent(latest_request).signals
                )
                ready_plan_tools = (
                    _ready_plan_tools(plan)
                    if is_public_contact_workflow
                    else pending_plan_tools
                )
                ready_send_steps = [
                    step for step in ready_plan_tools
                    if step.get("tool") == "send_email"
                ]
                failed_steps = [
                    step for step in plan if step.get("status") == "failed"
                ]
                if failed_steps:
                    plan_instruction = (
                        "A planned tool step failed. First revise the remaining "
                        "work with update_plan, using the failure details in the "
                        "conversation. Preserve completed work, keep independent "
                        "pending work, and replace a failed approach with a viable "
                        "alternative when possible. Give a replacement step a new "
                        "ID or changed inputs. Do not retry the same failed action "
                        "unchanged, and do not execute "
                        "steps that depend on the failure until the revised plan "
                        "validates. Then execute pending steps in dependency order "
                        "using only their named registered capabilities. Do not "
                        "claim a step is complete without its tool result.\n\n"
                    )
                else:
                    plan_instruction = (
                        "A validated plan is active. Execute its pending tool-backed "
                        "steps in dependency order, using only the named registered "
                        "capabilities. Do not claim a step is complete without its "
                        "tool result.\n\n"
                    )
                plan_instruction += (
                    "Current plan:\n"
                    + "\n".join(
                        f"- {step.get('id')}: {step.get('description')} "
                        f"[{step.get('tool')}; {step.get('status')}]"
                        for step in plan
                    )
                )
                if _is_research_artifact_workflow(state.get("plan") or [], registry):
                    plan_instruction += (
                        "\nThis is a source-backed artifact workflow. Read the "
                        "opened page content, extract every requested value, and "
                        "cross-check key facts before creating the artifact. Search "
                        "snippets and page titles are not facts. Cite the opened "
                        "source URLs in the artifact; do not use placeholders, "
                        "guesses, or claims that the source does not support."
                    )
                contact_search_is_pending = any(
                    step.get("tool") == "search_public_contacts"
                    for step in pending_plan_tools
                )
                pending_tool_names = {
                    str(ready_plan_tools[0].get("tool") or "")
                } if ready_plan_tools else set()
                if not is_public_contact_workflow:
                    pending_tool_names = {
                        str(step.get("tool") or "")
                        for step in pending_plan_tools
                        if step.get("tool")
                    }
                plan_needs_repair = any(
                    step.get("status") == "failed"
                    for step in plan
                )
                if contact_search_is_pending:
                    plan_instruction += (
                        "\nThe pending public-contact lookup is mandatory. "
                        "Call search_public_contacts with every requested "
                        "organization; do not answer that you cannot look up "
                        "emails or ask the user to supply them."
                    )
                pending_outreach_sends = [
                    step for step in pending_plan_tools
                    if step.get("tool") == "send_email"
                ]
                pending_contact_verifications = [
                    step for step in ready_plan_tools
                    if step.get("tool") == "open_url"
                    and "official contact page for"
                    in str(step.get("description") or "").lower()
                ]
                if pending_contact_verifications:
                    plan_instruction += (
                        "\nThe public-contact search has produced candidate "
                        "pages that must be checked before drafting or sending. "
                        "Open every ready candidate page in this response, using "
                        "only candidate URLs in the plan. Use only email addresses "
                        "actually shown on the verified pages."
                    )
                if is_public_contact_workflow and ready_plan_tools:
                    if ready_send_steps and explicitly_requests_email_send(
                        latest_request
                    ):
                        plan_instruction += (
                            "\nAll contact pages have been checked. Create one "
                            "complete, recipient-specific send_email draft for "
                            "each ready organization in this response. Do not call "
                            "compose_email separately: each send_email call will "
                            "be shown as an editable approval before it can send. "
                            "The app will present those approvals one at a time; "
                            "do not batch them into send_email_batch."
                        )
                    elif all(
                        step.get("tool") == "open_url"
                        for step in ready_plan_tools
                    ):
                        plan_instruction += (
                            "\nRead every ready official contact page in this "
                            "response. Do not draft or send until verification "
                            "steps are complete."
                        )
                    else:
                        plan_instruction += (
                            "\nExecute only the first ready action in plan order "
                            "and emit exactly one tool call in this response."
                        )
                if (
                    pending_outreach_sends
                    and "public_contact_search"
                    in classify_intent(latest_request).signals
                ):
                    plan_instruction += (
                        "\nThis is sequential multi-organization outreach. "
                        "Continue with the next pending send_email step after "
                        "each approval or rejection. Do not finish the run or "
                        "claim completion while any organization send step is "
                        "pending. Submit only the next recipient's complete "
                        "message for approval; approval of one message does not "
                        "approve any other recipient."
                    )

                def has_required_action(response: AIMessage) -> bool:
                    if plan_needs_repair:
                        # Let the dispatcher reject any premature action with
                        # PLAN_REVISION_REQUIRED so the model can repair its plan.
                        return bool(response.tool_calls)
                    if contact_search_is_pending:
                        return _response_calls_tool(
                            response, "search_public_contacts"
                        )
                    if pending_contact_verifications:
                        return _response_calls_tool(response, "open_url")
                    if (
                        is_public_contact_workflow
                        and explicitly_requests_email_send(latest_request)
                        and ready_send_steps
                    ):
                        ready_send_indexes = {
                            index for index, step in enumerate(plan)
                            if step in ready_send_steps
                        }
                        matched_send_indexes = {
                            matched_index
                            for call in response.tool_calls or []
                            if call.get("name") == "send_email"
                            and (
                                matched_index := _matching_plan_step(
                                    plan,
                                    "send_email",
                                    call.get("args")
                                    if isinstance(call.get("args"), dict)
                                    else {},
                                )
                            ) in ready_send_indexes
                        }
                        return matched_send_indexes == ready_send_indexes
                    if pending_tool_names:
                        return any(
                            str(call.get("name") or "") in pending_tool_names
                            for call in response.tool_calls or []
                        )
                    return bool(response.tool_calls)

                ai = await _stream_until_usable(
                    [tools_bound, *bound_fallback_models],
                    _with_system(messages, plan_instruction),
                    has_required_action,
                    purpose="planned action",
                )
                continuation_attempts = state.get(
                    "plan_continuation_attempts", 0
                )
                for _ in range(max_retries):
                    if has_required_action(ai) or not _pending_plan_tools(
                        state.get("plan")
                    ):
                        break
                    continuation_attempts += 1
                    continuation_instruction = (
                        plan_instruction
                        + "\nYour previous response did not execute a planned "
                        "capability. Continue the existing plan now: call the "
                        "registered tools for ready pending steps. Do not give a "
                        "final answer while planned tool steps remain."
                    )
                    if contact_search_is_pending:
                        continuation_instruction += (
                            " The required next action is specifically "
                            "search_public_contacts; do not substitute a "
                            "text response or ask the user for the addresses."
                        )
                    if pending_contact_verifications:
                        continuation_instruction += (
                            " Open only the candidate page for the next ready "
                            "organization; do not batch other page reads."
                        )
                    if pending_outreach_sends:
                        continuation_instruction += (
                            " At least one recipient still needs an individual "
                            "send_email approval. Do not stop after the prior "
                            "recipient; call send_email for the next pending "
                            "organization now."
                        )
                    ai = await _stream_until_usable(
                        [tools_bound, *bound_fallback_models],
                        _with_system(messages + [ai], continuation_instruction),
                        has_required_action,
                        purpose="planned action continuation",
                    )
                if contact_search_is_pending and not has_required_action(ai):
                    raise ModelEmptyResponseError(
                        "The model did not invoke the required public-contact search."
                    )
                if pending_plan_tools and not has_required_action(ai):
                    raise ModelEmptyResponseError(
                        "The model did not continue the next pending plan action."
                    )
                return {
                    "messages": [ai],
                    "status": "running",
                    "plan_continuation_attempts": (
                        0 if ai.tool_calls else continuation_attempts
                    ),
                }
            elif (
                is_public_contact_lookup
                and not _current_turn_has_tool_attempt(
                    messages, "search_public_contacts"
                )
            ):
                ai = await _stream_until_usable(
                    [tools_bound, *bound_fallback_models],
                    _with_system(
                        messages,
                        "This request names public organizations and asks for "
                        "their email addresses. First call search_public_contacts "
                        "with all named organizations. Do not tell the user to "
                        "provide the addresses or claim that lookup is unavailable.",
                    ),
                    lambda response: _response_calls_tool(
                        response, "search_public_contacts"
                    ),
                    purpose="required public-contact lookup",
                    allow_unaccepted_response=False,
                )
            else:
                ai = await _stream_model(
                    tools_bound,
                    _with_system(messages, None),
                    alternates=bound_fallback_models,
                )
            return {"messages": [ai], "status": "running"}
        except Exception as exc:  # noqa: BLE001 - preserve completed tool work
            # If tools already ran, preserve their outputs and report a partial
            # workflow instead of discarding state or implying completion.
            # Initial model failures still propagate to service-level fallback.
            if _has_current_turn_tool_result(messages):
                logger.warning(
                    "model failed after tool execution; returning partial state: %s",
                    type(exc).__name__,
                )
                return {
                    "messages": [AIMessage(
                        content=_model_outage_message(
                            _current_turn_messages(messages),
                            exc,
                            plan=state.get("plan") or [],
                        ),
                        id=new_id(),
                    )],
                    "status": "partial",
                    "force_final": True,
                    "recoverable": True,
                }
            raise

    async def tools_node(state: AgentState) -> dict[str, Any]:
        from app.services.policy import decide, normalize_autonomy, normalize_tool_policies
        from app.tools.plan import PlanValidationError, normalize_steps

        messages = list(state.get("messages", []))
        ai = _last_ai(messages)
        run_id = state.get("run_id", "")
        user_id = state.get("user_id", "")
        task_id = state.get("task_id", "")
        _task_intent = state.get("intent", "")
        steps = state.get("steps", 0)
        tool_calls = state.get("tool_calls", 0)
        latest_request = _task_request(state, messages)
        from app.tools.routing import is_multi_step_request

        requires_plan = is_multi_step_request(latest_request)
        sources = list(state.get("sources", []))
        artifacts = list(state.get("artifacts", []))
        browser_session_state = state.get("browser_session_state") or {}
        plan = state.get("plan")
        workflow_context = state.get("workflow_context")
        policy = state.get("policy") or {}
        autonomy = normalize_autonomy(policy.get("autonomy"))
        tool_policies = normalize_tool_policies(policy.get("tool_policies"))
        results: list[BaseMessage] = []
        plan_steps_by_call: dict[str, int] = {}
        pending_approval: dict[str, Any] | None = None
        status = "running"
        answered = {
            m.tool_call_id for m in messages if isinstance(m, ToolMessage)
        }
        stopped = run_registry.is_stopped(user_id, run_id)
        tool_calls_to_execute = list(ai.tool_calls or []) if ai is not None else []
        if "public_contact_search" in classify_intent(latest_request).signals:
            tool_calls_to_execute, deferred_calls = (
                _select_public_contact_tool_calls(
                    plan,
                    tool_calls_to_execute,
                    answered_ids={
                        str(call.get("id") or "")
                        for call in tool_calls_to_execute
                        if str(call.get("id") or "") in answered
                    },
                    read_only_tool_names={
                        spec.name for spec in registry.all()
                        if spec.side_effect == "read_only"
                    },
                )
            )
            ready_send_recipients: set[str] = set()
            for step in _ready_plan_tools(plan):
                if step.get("tool") != "send_email":
                    continue
                arguments = step.get("arguments")
                recipient = (
                    str(arguments.get("to") or "").strip()
                    if isinstance(arguments, dict)
                    else ""
                )
                if not recipient:
                    match = re.search(
                        r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w-]+(?:\.[\w-]+)+",
                        str(step.get("description") or ""),
                    )
                    recipient = match.group(0) if match else ""
                if recipient:
                    ready_send_recipients.add(recipient.casefold())
            for deferred_call in deferred_calls:
                deferred_name = str(deferred_call.get("name") or "")
                deferred_args = (
                    deferred_call.get("args")
                    if isinstance(deferred_call.get("args"), dict)
                    else {}
                )
                if (
                    deferred_name == "send_email"
                    and str(deferred_args.get("to") or "").casefold()
                    in ready_send_recipients
                ):
                    continue
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "SKIPPED",
                        "Public-contact outreach actions run one organization at a time.",
                        message=(
                            "This action was deferred. Complete the next "
                            "dependency-ready organization-specific step first."
                        ),
                    ).model_dump_json(),
                    tool_call_id=str(deferred_call.get("id") or ""),
                ))

        if (
            ai is not None
            and explicitly_requests_email_send(latest_request)
            and "public_contact_search"
            not in classify_intent(latest_request).signals
            and "send_email_batch" in bound_tool_names
        ):
            calls = list(tool_calls_to_execute)
            send_calls = [
                call for call in calls
                if call.get("name") == "send_email"
            ]
            batch_calls = [
                call for call in calls
                if call.get("name") == "send_email_batch"
            ]
            all_unanswered = all(
                str(call.get("id") or "") not in answered
                for call in send_calls + batch_calls
            )
            if all_unanswered and not batch_calls and len(send_calls) > 1:
                batch_arguments = {
                    "emails": [dict(call.get("args") or {}) for call in send_calls]
                }
                first_send_id = str(send_calls[0].get("id") or "")
                rewritten_calls = []
                replaced = False
                for call in calls:
                    if call.get("name") != "send_email":
                        rewritten_calls.append(call)
                    elif not replaced:
                        rewritten_calls.append({
                            "name": "send_email_batch",
                            "args": batch_arguments,
                            "id": first_send_id,
                            "type": "tool_call",
                        })
                        replaced = True
                ai.tool_calls = rewritten_calls
                tool_calls_to_execute = rewritten_calls
            elif all_unanswered and len(batch_calls) == 1 and not send_calls:
                batch_arguments = dict(batch_calls[0].get("args") or {})
                first_send_id = str(batch_calls[0].get("id") or "")
            else:
                batch_arguments = {}
                first_send_id = ""

            if batch_arguments and plan:
                send_plan_indexes = [
                    index for index, step in enumerate(plan)
                    if step.get("tool") == "send_email"
                ]
                emails = batch_arguments.get("emails")
                if (
                    len(send_plan_indexes) >= 1
                    and isinstance(emails, list)
                    and (
                        len(send_plan_indexes) == 1
                        or len(emails) == len(send_plan_indexes)
                    )
                    and all(
                        plan[index].get("status") in {"pending", "active"}
                        for index in send_plan_indexes
                    )
                ):
                    keeper_index = send_plan_indexes[0]
                    send_plan_index_set = set(send_plan_indexes)
                    external_dependencies = list(dict.fromkeys(
                        dependency
                        for index in send_plan_indexes
                        for dependency in plan[index].get("dependencies", [])
                        if dependency not in send_plan_index_set
                    ))
                    combined_step = {
                        **plan[keeper_index],
                        "description": (
                            f"Send all {len(emails)} reviewed outreach emails "
                            "after one batch approval"
                        ),
                        "tool": "send_email_batch",
                        "arguments": batch_arguments,
                        "dependencies": [],
                        "status": "pending",
                    }
                    merged_plan: list[dict[str, Any]] = []
                    old_to_new: dict[int, int] = {}
                    for index, step in enumerate(plan):
                        if index == keeper_index:
                            old_to_new[index] = len(merged_plan)
                            merged_plan.append(combined_step)
                        elif index in send_plan_indexes:
                            continue
                        else:
                            old_to_new[index] = len(merged_plan)
                            merged_plan.append(dict(step))
                    for index in send_plan_indexes:
                        old_to_new[index] = old_to_new[keeper_index]
                    merged_plan[old_to_new[keeper_index]]["dependencies"] = list(dict.fromkeys(
                        old_to_new[dependency]
                        for dependency in external_dependencies
                    ))
                    for index, step in enumerate(plan):
                        if index in send_plan_index_set:
                            continue
                        merged_plan[old_to_new[index]]["dependencies"] = list(dict.fromkeys(
                            old_to_new[dependency]
                            for dependency in step.get("dependencies", [])
                        ))
                    plan = merged_plan
                    progress, current = _plan_progress(plan)
                    await _sync_plan(task_id, plan, progress, current)
                    emit("plan", {
                        "task_id": task_id,
                        "steps": plan,
                        "progress": progress,
                        "current_step": current,
                    })

        if (
            ai is not None
            and requires_plan
            and state.get("plan_recovery")
        ):
            recovery_steps = []
            for index, call in enumerate(ai.tool_calls or [], start=1):
                name = str(call.get("name") or "")
                spec = registry.get(name)
                arguments = call.get("args")
                if not isinstance(arguments, dict):
                    arguments = {}
                if (
                    not name
                    or name == "update_plan"
                    or spec is None
                    or name not in bound_tool_names
                ):
                    continue
                if any(
                    step.get("tool") == name
                    and step.get("arguments", {}) == arguments
                    for step in (plan or [])
                ):
                    continue
                recovery_steps.append({
                    "id": f"recovery_{len(plan or []) + index}",
                    "description": (
                        f"Use {name}: {spec.description}"
                    )[:500],
                    "tool": name,
                    "arguments": arguments,
                    "dependencies": [],
                })
            if recovery_steps:
                try:
                    recovered_plan = normalize_steps(
                        [*(plan or []), *recovery_steps],
                        capability_registry=registry,
                        allow_argument_hints=True,
                    )
                except PlanValidationError as exc:
                    logger.warning(
                        "Could not normalize recovery plan: %s", exc,
                        exc_info=True,
                    )
                    recovered_plan = None
                if recovered_plan is not None:
                    recovered_plan = _repair_research_artifact_plan(
                        recovered_plan, registry, set(bound_tool_names)
                    )
                    recovered_plan = _preserve_completed_plan_steps(
                        plan or [], recovered_plan
                    )
                    contact_error = _public_contact_plan_error(
                        latest_request, recovered_plan
                    )
                    artifact_error = _research_artifact_plan_error(
                        recovered_plan, registry
                    )
                    if contact_error or artifact_error:
                        reason = contact_error or artifact_error
                        logger.warning("Rejected recovery plan: %s", reason)
                        recovery_ids = {
                            str(call.get("id") or "")
                            for call in (ai.tool_calls or [])
                            if str(call.get("name") or "") != "update_plan"
                        }
                        results.extend(
                            ToolMessage(
                                content=ToolResult.fail(
                                    "RECOVERY_PLAN_REJECTED",
                                    reason,
                                    message=(
                                        "Revise the proposed actions to follow the "
                                        "task's required workflow, then continue."
                                    ),
                                ).model_dump_json(),
                                tool_call_id=call_id,
                            )
                            for call_id in recovery_ids
                        )
                    else:
                        plan = recovered_plan
                        progress, current = _plan_progress(plan)
                        await _sync_plan(task_id, plan, progress, current)
                        emit("plan", {
                            "task_id": task_id,
                            "steps": plan,
                            "progress": progress,
                            "current_step": current,
                            "recovered": True,
                        })

        # Collect independent tool calls for parallel execution (spec §11).
        # Tools that require user approval, are update_plan, or already have
        # answers are handled individually in the loop below. Only AUTO-permission
        # tools that pass every guard are batched via asyncio.gather so multi-
        # tool web-research turns (e.g. several open_url calls) run concurrently
        # instead of one after another.
        parallel_calls: list[tuple[str, str, dict[str, Any], Any]] = []

        async def _flush_parallel() -> None:
            """Execute all collected parallelizable tool calls concurrently."""
            nonlocal tool_calls, steps, sources, artifacts
            nonlocal browser_session_state, plan, status, pending_approval

            if not parallel_calls:
                return

            batch = parallel_calls[:]
            parallel_calls.clear()
            count = len(batch)
            tool_calls += count
            steps += count

            async def _run_one(
                cid: str, tname: str, targs: dict[str, Any], tspec: Any,
            ):
                from app.services.execution_contract import ActionOutcome
                from app.services.execution_lifecycle import (
                    AttemptHandle,
                    begin_tool_attempt,
                    finish_tool_attempt,
                )

                tt = timer()
                emit("tool_start", {
                    "name": tname,
                    "args": targs,
                    "permission": int(tspec.permission),
                    "call_id": cid,
                })
                attempts = 0
                attempt_handle: AttemptHandle | None = None
                while True:
                    attempts += 1
                    dispatch_uncertain = False
                    if task_id and run_id:
                        attempt_handle = begin_tool_attempt(
                            task_id=task_id,
                            run_id=run_id,
                            tool_call_id=cid,
                            tool_name=tname,
                            arguments=targs,
                            plan_step_index=plan_steps_by_call.get(cid),
                            side_effect=str(tspec.side_effect),
                        )
                        if attempt_handle.cached_raw is not None:
                            raw = attempt_handle.cached_raw
                            parsed = _parse_tool_result(raw)
                            break
                        if attempt_handle.blocked_code:
                            raw = ToolResult.fail(
                                attempt_handle.blocked_code,
                                "The prior action attempt has no safely replayable outcome.",
                                message=(
                                    "This action was not repeated because its previous "
                                    "outcome is uncertain. Reconcile the provider outcome "
                                    "before deciding whether to retry."
                                ),
                            ).model_dump_json()
                            parsed = _parse_tool_result(raw)
                            break
                        if run_registry.is_stopped(user_id, run_id):
                            raw = ToolResult.fail(
                                "STOPPED",
                                "User requested STOP before tool dispatch.",
                                message="Stopped by user.",
                            ).model_dump_json()
                            parsed = _parse_tool_result(raw)
                            finish_tool_attempt(
                                handle=attempt_handle,
                                task_id=task_id,
                                run_id=run_id,
                                raw=raw,
                                tool_name=tname,
                                success=False,
                                error_code="STOPPED",
                                outcome=ActionOutcome.CANCELLED,
                            )
                            break
                    try:
                        from dataclasses import replace

                        from app.context import (
                            ToolContext,
                            bind_tool_execution,
                            get_tool_context,
                        )
                        from app.services.tool_execution import invoke_registered_tool

                        try:
                            parent_context = get_tool_context()
                        except RuntimeError:
                            parent_context = ToolContext(
                                user_id=user_id,
                                run_id=run_id or None,
                                task_id=task_id or None,
                            )
                        execution_context = replace(
                            parent_context,
                            step_id=attempt_handle.step_id if attempt_handle else None,
                            action_id=attempt_handle.action_id if attempt_handle else None,
                            tool_call_id=cid,
                            attempt_number=(
                                attempt_handle.attempt_number if attempt_handle else attempts
                            ),
                            idempotency_key=(
                                attempt_handle.idempotency_key if attempt_handle else None
                            ),
                        )
                        with bind_tool_execution(execution_context):
                            invoked = await invoke_registered_tool(
                                tspec,
                                targs,
                                timeout_seconds=tool_timeout,
                                label="Tool",
                            )
                        raw = invoked.model_dump_json()
                        if (
                            not invoked.success
                            and invoked.error is not None
                            and invoked.error.code in {"TIMEOUT", "TOOL_EXCEPTION"}
                        ):
                            dispatch_uncertain = True
                    except asyncio.CancelledError:
                        if attempt_handle is not None:
                            cancelled_raw = ToolResult.fail(
                                "OUTCOME_UNKNOWN",
                                "Execution was cancelled while the tool was in flight.",
                                message="The action may have reached its target; do not retry automatically.",
                            ).model_dump_json()
                            finish_tool_attempt(
                                handle=attempt_handle,
                                task_id=task_id,
                                run_id=run_id,
                                raw=cancelled_raw,
                                tool_name=tname,
                                success=False,
                                error_code="OUTCOME_UNKNOWN",
                                outcome=ActionOutcome.OUTCOME_UNKNOWN,
                            )
                        raise

                    parsed = _parse_tool_result(raw)
                    if (
                        attempt_handle is not None
                        and not parsed.success
                        and tspec.side_effect != "read_only"
                        and (
                            dispatch_uncertain
                            or (
                                parsed.error is not None
                                and parsed.error.code in {"TIMEOUT", "OUTCOME_UNKNOWN"}
                            )
                        )
                    ):
                        finish_tool_attempt(
                            handle=attempt_handle,
                            task_id=task_id,
                            run_id=run_id,
                            raw=raw,
                            tool_name=tname,
                            success=False,
                            error_code=(
                                parsed.error.code if parsed.error else "OUTCOME_UNKNOWN"
                            ),
                            outcome=ActionOutcome.OUTCOME_UNKNOWN,
                        )
                        break
                    if parsed.success or attempts > max_retries or not _is_retryable(parsed, tspec):
                        if attempt_handle is not None:
                            finish_tool_attempt(
                                handle=attempt_handle,
                                task_id=task_id,
                                run_id=run_id,
                                raw=raw,
                                tool_name=tname,
                                success=parsed.success,
                                error_code=(
                                    parsed.error.code if parsed.error else None
                                ),
                            )
                        break
                    if attempt_handle is not None:
                        finish_tool_attempt(
                            handle=attempt_handle,
                            task_id=task_id,
                            run_id=run_id,
                            raw=raw,
                            tool_name=tname,
                            success=False,
                            error_code=parsed.error.code if parsed.error else None,
                        )
                    logger.info("retrying tool %s (attempt %d): %s",
                                tname, attempts, parsed.error.code if parsed.error else "?")
                    emit("tool_retry", {
                        "name": tname, "attempt": attempts,
                        "code": parsed.error.code if parsed.error else "",
                        "call_id": cid,
                    })
                    await asyncio.sleep(0.4 * attempts)

                emit("tool_end", {
                    "name": tname,
                    "success": parsed.success,
                    "message": parsed.message,
                    "duration_ms": tt.elapsed_ms,
                    "attempts": attempts,
                    "call_id": cid,
                    "detail": _live_tool_detail(tname, parsed),
                })
                return (
                    cid, tname, targs, parsed, raw, tt.elapsed_ms, attempts,
                    attempt_handle,
                )

            batch_results = await asyncio.gather(
                *[_run_one(cid, tn, ta, ts) for cid, tn, ta, ts in batch]
            )

            # Process results in original order (deterministic state updates)
            for (
                cid, tname, targs, parsed, raw, duration_ms, _attempts, _attempt_handle
            ) in batch_results:
                # §18: a credential fill that finds no stored login pauses the run
                # for a dedicated ``credentials`` approval WITHOUT appending a
                # ToolMessage, so the fill re-runs (now with the stored secret)
                # on resume.
                if (not parsed.success and parsed.error is not None
                        and parsed.error.code == "CREDENTIALS_NEEDED"):
                    data = parsed.data or {}
                    dom = str(data.get("domain") or "")
                    fld = str(data.get("field") or "")
                    spec_obj = registry.get(tname)
                    level = (int(spec_obj.permission) if spec_obj is not None
                             else int(PermissionLevel.CONFIRM))
                    cred_approval = await _create_approval(
                        user_id=user_id, run_id=run_id, call_id=cid,
                        tool_name=tname, arguments={**targs, "domain": dom},
                        level=level, kind="credentials",
                        task_id=task_id,
                        task_step_index=plan_steps_by_call.get(cid),
                    )
                    pending_approval = {
                        "id": cred_approval.id, "run_id": run_id, "tool_name": tname,
                        "arguments": targs, "level": level, "call_id": cid,
                        "kind": "credentials",
                        "credential": {"domain": dom},
                        "field": fld,
                    }
                    status = "paused_approval"
                    emit("approval", {
                        "approval_id": cred_approval.id,
                        "run_id": run_id,
                        "tool_name": tname,
                        "arguments": targs,
                        "level": level,
                        "kind": "credentials",
                        "credential": {"domain": dom},
                        "field": fld,
                        "message": "Enter your login credentials to continue.",
                    })
                    await _audit_tool(
                        user_id=user_id, run_id=run_id, call_id=cid,
                        tool=tname, args=targs,
                        result=parsed, duration_ms=duration_ms,
                        side_effect=str(
                            registry.get(tname).side_effect
                            if registry.get(tname) is not None
                            else "unknown"
                        ),
                        task_id=task_id,
                        task_step_id=(
                            _attempt_handle.step_id if _attempt_handle else None
                        ),
                        action_id=(
                            _attempt_handle.action_id if _attempt_handle else None
                        ),
                        attempt_id=(
                            _attempt_handle.attempt_id if _attempt_handle else None
                        ),
                        attempt_number=(
                            _attempt_handle.attempt_number if _attempt_handle else None
                        ),
                    )
                    continue
                message_content = raw
                if (
                    tname == "open_url"
                    and "public_contact_search"
                    in classify_intent(latest_request).signals
                ):
                    message_content = _compact_public_contact_page_result(raw)
                results.append(ToolMessage(content=message_content, tool_call_id=cid))
                stopped_now = run_registry.is_stopped(user_id, run_id)
                if status != "stopped" and stopped_now:
                    status = "stopped"

                if parsed.success:
                    if plan:
                        revised, changed = _complete_plan_step(
                            plan, tname, targs, plan_steps_by_call.get(cid)
                        )
                        if tname == "search_public_contacts":
                            revised, verification_added = (
                                _add_public_contact_verification_steps(
                                    revised,
                                    plan_steps_by_call.get(cid),
                                    parsed.data,
                                    send_requested=(
                                        explicitly_requests_email_send(
                                            latest_request
                                        )
                                    ),
                                )
                            )
                            changed = changed or verification_added
                        if changed:
                            plan = revised
                            progress, current = _plan_progress(revised)
                            await _sync_plan(task_id, revised, progress, current)
                            emit("plan", {
                                "task_id": task_id,
                                "steps": revised,
                                "progress": progress,
                                "current_step": current,
                            })
                    if tname in ("search_web", "open_url", "browse_page"):
                        sources = _dedupe_sources(
                            sources + _extract_sources(tname, parsed.data)
                        )
                    elif tname in ("browser_open", "browser_navigate", "browser_extract"):
                        sources = _dedupe_sources(
                            sources + _extract_sources(tname, parsed.data)
                        )
                    # Phase D (§44, §55): any tool can attach an artifact
                    if tname.startswith("browser_"):
                        bstatus = (parsed.data or {}).get("browser")
                        if isinstance(bstatus, dict):
                            browser_session_state = bstatus
                            emit("browser_status", {
                                "run_id": run_id, "browser": bstatus,
                            })
                    artifact = (parsed.data or {}).get("artifact")
                    seen_urls = set()
                    if isinstance(artifact, dict) and artifact.get("url"):
                        artifacts = artifacts + [artifact]
                        seen_urls.add(artifact["url"])
                        emit("artifact", {
                            "run_id": run_id, "artifact": artifact,
                            "tool": tname,
                        })
                    # A tool may return several related artifacts (e.g. a plot's
                    # PNG + SVG + PDF exports) as a list under "artifacts".
                    extra_artifacts = (parsed.data or {}).get("artifacts")
                    if isinstance(extra_artifacts, list):
                        for extra in extra_artifacts:
                            if not isinstance(extra, dict):
                                continue
                            url = extra.get("url")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                artifacts = artifacts + [extra]
                                emit("artifact", {
                                    "run_id": run_id, "artifact": extra,
                                    "tool": tname,
                                })
                    # Priority 3 (§31-33): emit call_result after a phone call
                    # completes so the frontend can show contact, duration, and
                    # call summary.  (Spec: phone calls show contact, verified
                    # phone number, purpose, opening script, and call result.)
                    if tname == "make_call":
                        call_data = parsed.data or {}
                        emit("call_result", {
                            "call_id": cid,
                            "run_id": run_id,
                            "to_phone": call_data.get("to_phone", ""),
                            "from_phone": call_data.get("from_phone", ""),
                            "purpose": targs.get("purpose", ""),
                            "status": call_data.get("status", ""),
                            "duration_seconds": call_data.get("duration_seconds"),
                            "summary": call_data.get("summary", ""),
                            "transcript": call_data.get("transcript", ""),
                        })

                # Replanner (spec §9-10): a real tool failure invalidates the
                # active plan step -- mark it failed and re-emit the plan.
                if not parsed.success and plan and _causes_replan(parsed):
                    revised, changed = _fail_active_step(
                        plan, tname, targs, plan_steps_by_call.get(cid)
                    )
                    if changed:
                        plan = revised
                        progress, current = _plan_progress(revised)
                        await _sync_plan(task_id, revised, progress, current)
                        emit("plan", {
                            "task_id": task_id,
                            "steps": revised,
                            "progress": progress,
                            "current_step": current,
                            "reason": f"{tname} failed"
                                      + (f" ({parsed.error.code})" if parsed.error else ""),
                        })

                await _audit_tool(
                    user_id=user_id, run_id=run_id, call_id=cid,
                    tool=tname, args=targs,
                    result=parsed, duration_ms=duration_ms,
                    side_effect=str(
                        registry.get(tname).side_effect
                        if registry.get(tname) is not None
                        else "unknown"
                    ),
                    task_id=task_id,
                    task_step_id=(
                        _attempt_handle.step_id if _attempt_handle else None
                    ),
                    action_id=(
                        _attempt_handle.action_id if _attempt_handle else None
                    ),
                    attempt_id=(
                        _attempt_handle.attempt_id if _attempt_handle else None
                    ),
                    attempt_number=(
                        _attempt_handle.attempt_number if _attempt_handle else None
                    ),
                )

        for tool_call in tool_calls_to_execute:
            call_id = tool_call.get("id", "")
            name = tool_call.get("name", "")
            args = tool_call.get("args") or {}

            if call_id in answered:
                continue  # already executed before a pause/resume

            workflow_step_call = _workflow_step_for_call(
                workflow_context, str(call_id)
            )
            if workflow_step_call is not None:
                try:
                    args = _resolve_workflow_step_arguments(
                        workflow_context or {},
                        workflow_step_call,
                        messages + results,
                    )
                except ValueError as exc:
                    invalid_input = ToolResult.fail(
                        "INVALID_WORKFLOW_INPUT",
                        str(exc),
                        message=(
                            "A saved workflow step could not resolve its required "
                            "input or prior-step output."
                        ),
                    )
                    results.append(ToolMessage(
                        content=invalid_input.model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    workflow_step_index = workflow_step_call.get("plan_index")
                    if (
                        isinstance(workflow_step_index, int)
                        and plan is not None
                        and 0 <= workflow_step_index < len(plan)
                    ):
                        plan[workflow_step_index]["status"] = "failed"
                        progress, current = _plan_progress(plan)
                        await _sync_plan(task_id, plan, progress, current)
                    continue
                workflow_step_index = workflow_step_call.get("plan_index")
                if (
                    isinstance(workflow_step_index, int)
                    and plan is not None
                    and 0 <= workflow_step_index < len(plan)
                ):
                    plan[workflow_step_index]["arguments"] = args
                    progress, current = _plan_progress(plan)
                    await _sync_plan(task_id, plan, progress, current)

            if stopped:
                results.append(ToolMessage(
                    content=ToolResult.fail("STOPPED", "User requested STOP; tool not executed.",
                                            message="Stopped by user.").model_dump_json(),
                    tool_call_id=call_id,
                ))
                status = "stopped"
                continue

            if run_registry.is_paused(user_id, run_id):
                # Generic pause: this call is deferred, not lost -- resume
                # re-enters the agent node and the model re-issues it.
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "PAUSED", "The run was paused; this tool was not executed.",
                        message="Paused before this tool ran; re-issue it after resuming.",
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                status = "paused"
                continue

            step_budget = max_steps
            if isinstance(workflow_context, dict):
                step_budget += len(workflow_context.get("step_calls", []))
            if steps >= step_budget:
                results.append(ToolMessage(
                    content=ToolResult.fail("MAX_STEPS", "Step budget exhausted.",
                                            message="Step budget exhausted before this tool ran.").model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            spec = registry.get(name)
            if spec is None:
                results.append(ToolMessage(
                    content=ToolResult.fail("UNKNOWN_TOOL", f"'{name}' is not available.",
                                            message=f"Tool '{name}' does not exist.").model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            if (
                name not in bound_tool_names
                and not (
                    workflow_step_call is not None
                    and workflow_step_call.get("tool") == name
                )
            ):
                logger.warning(
                    "Tool '%s' rejected because it was not selected for this task.",
                    name,
                )
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "CAPABILITY_NOT_SELECTED",
                        "The requested capability was outside the task's discovered scope.",
                        message=(
                            "This capability was not selected for the user's request. "
                            "Continue with a selected capability that advances the original "
                            "goal, or explain the actual limitation to the user."
                        ),
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            args, argument_error = _validate_tool_arguments(spec, args)
            if argument_error is not None:
                results.append(ToolMessage(
                    content=argument_error.model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            # --- internal planner tool: persist + stream the plan --------
            if name == "update_plan":
                # Force any pending parallel batch to complete first so the
                # plan reflects the latest tool results before we update it.
                await _flush_parallel()
                try:
                    new_plan = normalize_steps(
                        args.get("steps"),
                        capability_registry=registry,
                        allow_argument_hints=True,
                    )
                except PlanValidationError as exc:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "INVALID_PLAN", str(exc),
                            message="The plan contains invalid steps or capability inputs.",
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                if new_plan is None:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "INVALID_PLAN", "steps must be a non-empty list.",
                            message="Provide a non-empty list of plan steps.",
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                new_plan = _repair_research_artifact_plan(
                    new_plan, registry, set(bound_tool_names)
                )
                contact_plan_error = _public_contact_plan_error(
                    latest_request, new_plan
                )
                if contact_plan_error:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "PUBLIC_CONTACT_PLAN_REQUIRED",
                            contact_plan_error,
                            message=contact_plan_error,
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                plan_error = _research_artifact_plan_error(new_plan, registry)
                if plan_error:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "RESEARCH_PLAN_INCOMPLETE",
                            plan_error,
                            message=(
                                "Revise the plan so source pages are read before "
                                "creating research artifacts."
                            ),
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                if _has_unchanged_failed_step(plan, new_plan):
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "FAILED_STEP_UNCHANGED",
                            "The revised plan repeats an unchanged failed step.",
                            message=(
                                "Choose an alternative action or input for the "
                                "remaining work; do not resubmit the failed step "
                                "unchanged."
                            ),
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                new_plan = _preserve_completed_plan_steps(plan, new_plan)
                plan = new_plan
                steps += 1
                progress, current = _plan_progress(new_plan)
                await _sync_plan(task_id, new_plan, progress, current)
                emit("plan", {
                    "task_id": task_id,
                    "steps": new_plan,
                    "progress": progress,
                    "current_step": current,
                })
                results.append(ToolMessage(
                    content=ToolResult.ok(
                        "Plan updated. Argument hints for dependent or invalid inputs "
                        "were omitted; execution calls will be validated against each "
                        "capability's schema.",
                        {"steps": new_plan},
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            if plan and any(step.get("status") == "failed" for step in plan):
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "PLAN_REVISION_REQUIRED",
                        "A planned step failed and the remaining plan has not been revised.",
                        message=(
                            "Revise the plan with update_plan before executing more "
                            "task actions. Preserve completed work and do not retry "
                            "the unchanged failed action."
                        ),
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            if requires_plan and not plan:
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "PLAN_REQUIRED",
                        "This multi-step request has no validated plan.",
                        message=(
                            "Do not execute any task actions yet. First publish a "
                            "validated plan with update_plan, including the registered "
                            "capability for each tool-backed step."
                        ),
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue

            # --- user action policy (autonomy + per-tool policies) --------
            decision = decide(
                permission=spec.permission,
                autonomy=autonomy,
                policy=tool_policies.get(name, ""),
                tags=spec.tags,
            )
            if not decision.allowed:
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        decision.code, decision.detail, message=decision.detail,
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                continue
            needs_approval = decision.require_approval or name in {
                "send_email",
                "request_user_input",
            }

            # Research artifacts must be grounded in sources collected during
            # this run. A report-generation call without tracked evidence is
            # incomplete, even if the model supplies fluent prose.
            if (
                _requires_research_evidence(_task_intent, sources)
                and name in {"create_pdf", "create_docx"}
            ):
                from app.services.content_validation import validate_research_report

                report_errors = validate_research_report(args.get("content"), sources)
                if report_errors:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "RESEARCH_EVIDENCE_REQUIRED",
                            report_errors[0],
                            message="The report was not created because its evidence could not be verified.",
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue

            # Validate generated email content before it can become an
            # approval card.  Approval is a review of a deliverable draft,
            # not a way to launder an incomplete model response into a send.
            if name in {"send_email", "compose_email"}:
                args = _normalize_email_action_args(name, args)
                if (
                    name == "send_email"
                    and "public_contact_search"
                    in classify_intent(latest_request).signals
                ):
                    evidence_error = _public_contact_email_evidence_error(
                        messages,
                        str(args.get("to") or ""),
                        plan=plan,
                        arguments=args,
                    )
                    if evidence_error:
                        results.append(ToolMessage(
                            content=ToolResult.fail(
                                "PUBLIC_CONTACT_EMAIL_UNVERIFIED",
                                evidence_error,
                                message=(
                                    "Do not send this message. Verify the "
                                    "recipient on an organization contact page "
                                    "or ask the user how to proceed."
                                ),
                            ).model_dump_json(),
                            tool_call_id=call_id,
                        ))
                        continue
                from app.services.content_validation import validate_generated_text

                draft_errors = (
                    validate_generated_text(args.get("subject"), field="subject")
                    + validate_generated_text(args.get("body"), field="body")
                    + (
                        validate_generated_text(
                            args.get("regards"), field="regards"
                        )
                        if args.get("regards")
                        else []
                    )
                )
                if draft_errors:
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "INVALID_DRAFT",
                            "Email draft is incomplete or contains unresolved placeholders.",
                            message="Repair the email draft before requesting approval.",
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue

            plan_step = None
            if plan and name != "update_plan":
                revised, plan_step, plan_error = _activate_plan_step(
                    plan,
                    name,
                    args,
                    permit_read_only_argument_correction=(
                        spec.permission == PermissionLevel.AUTO
                        and spec.side_effect == "read_only"
                    ),
                )
                if plan_error is not None:
                    results.append(ToolMessage(
                        content=plan_error.model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                if requires_plan and plan_step is None and name != "request_user_input":
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "UNPLANNED_ACTION",
                            f"Capability '{name}' is not represented by a pending plan step.",
                            message=(
                                "Revise the plan with update_plan so this action is "
                                "explicitly covered, then continue in dependency order."
                            ),
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                if plan_step is not None:
                    plan_steps_by_call[call_id] = plan_step
                    if revised != plan:
                        plan = revised
                        progress, current = _plan_progress(revised)
                        await _sync_plan(task_id, revised, progress, current)
                        emit("plan", {
                            "task_id": task_id,
                            "steps": revised,
                            "progress": progress,
                            "current_step": current,
                        })

            if needs_approval and call_id not in state.get("approved_call_ids", []):
                if call_id in state.get("skipped_call_ids", []):
                    await _flush_parallel()
                    if plan is not None and plan_step is not None:
                        plan = [dict(step) for step in plan]
                        plan[plan_step]["status"] = "skipped"
                        progress, current = _plan_progress(plan)
                        await _sync_plan(task_id, plan, progress, current)
                        emit("plan", {
                            "task_id": task_id,
                            "steps": plan,
                            "progress": progress,
                            "current_step": current,
                        })
                    results.append(ToolMessage(
                        content=ToolResult.ok(
                            "The user skipped this action; continue with the remaining task if possible.",
                            {"skipped": True},
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                if call_id in state.get("rejected_call_ids", []):
                    await _flush_parallel()
                    results.append(ToolMessage(
                        content=ToolResult.fail(
                            "USER_REJECTED", "The user rejected this action.",
                            message="The user rejected this action; do not retry it.",
                        ).model_dump_json(),
                        tool_call_id=call_id,
                    ))
                    continue
                # Flush any pending parallel calls before the approval pause
                await _flush_parallel()
                kind = "tool"
                approval_extra: dict[str, Any] = {}
                approval_arguments = args
                if name == "browser_fill":
                    marker = _credential_marker(args)
                    if marker:
                        # §18: a credential fill shows the credential sub-form in
                        # the approval modal (domain comes from the session state)
                        # and is persisted so approve() can store the credentials.
                        domain = _credential_domain_hint(state, user_id)
                        approval_extra = {
                            "credential": {"domain": domain},
                            "field": marker,
                        }
                        if domain:
                            approval_arguments = {**args, "domain": domain}
                approval = await _create_approval(
                    user_id=user_id, run_id=run_id, call_id=call_id,
                    tool_name=name, arguments=approval_arguments,
                    level=int(spec.permission), kind=kind,
                    task_id=task_id,
                    task_step_index=plan_step,
                    plan_step_id=(
                        str(plan[plan_step].get("id") or "")
                        if plan is not None and plan_step is not None
                        else None
                    ),
                )
                pending_approval = {
                    "id": approval.id,
                    "run_id": run_id,
                    "tool_name": name,
                    "arguments": args,
                    "level": int(spec.permission),
                    "call_id": call_id,
                    "kind": kind,
                }
                if approval_extra:
                    pending_approval.update(approval_extra)
                status = "paused_approval"
                emit("approval", {
                    "approval_id": approval.id,
                    "run_id": run_id,
                    "tool_name": name,
                    "arguments": args,
                    "level": int(spec.permission),
                    "kind": kind,
                    "message": (
                        "Jalora needs your answer before this task can continue."
                        if name == "request_user_input"
                        else f"'{name}' needs your approval before it can run."
                    ),
                    **approval_extra,
                })
                break  # remaining calls stay pending until this one is decided

            # --- tool-call budget ----------------------------------------
            # Account for tools already queued in the parallel batch so we
            # never exceed max_tool_calls when the batch is flushed.
            tool_budget = _effective_tool_call_budget(plan, max_tool_calls)
            if tool_calls + len(parallel_calls) + 1 > tool_budget:
                await _flush_parallel()
                results.append(ToolMessage(
                    content=ToolResult.fail(
                        "TOOL_LIMIT",
                        f"Tool budget of {tool_budget} calls exhausted.",
                        message="Tool budget exhausted; give the final answer now.",
                    ).model_dump_json(),
                    tool_call_id=call_id,
                ))
                break  # remaining calls get SKIPPED fillers + a wrap-up answer

            # --- collect for parallel execution (spec §11) -------------------
            # Tools that reach here are AUTO-permission, no approval needed,
            # within step and rate budgets, and not update_plan. They are
            # independent by construction (the model selected them for the
            # same context) and can safely run concurrently.
            parallel_calls.append((call_id, name, args, spec))

        # Execute any remaining parallel tool calls
        await _flush_parallel()

        workflow_expansions = []
        for message in results:
            if not isinstance(message, ToolMessage):
                continue
            parsed = _parse_tool_result(str(message.content or ""))
            execution_id = (parsed.data or {}).get(
                "canonical_workflow_execution_id"
            )
            if parsed.success and isinstance(execution_id, str):
                workflow_expansions.append((message.tool_call_id, execution_id))

        for parent_call_id, execution_id in workflow_expansions:
            try:
                from app.services.workflow_automation import WorkflowEngine

                workflow = WorkflowEngine().get_canonical_execution(
                    execution_id, user_id
                )
                if (
                    workflow["task_id"] != task_id
                    or workflow["run_id"] != run_id
                ):
                    raise ValueError(
                        "Workflow execution does not belong to this task and run."
                    )
                plan = [dict(step) for step in (plan or [])]
                parent_step_index = plan_steps_by_call.get(parent_call_id)
                step_calls = []
                prior_step_index = (
                    parent_step_index
                    if isinstance(parent_step_index, int)
                    and 0 <= parent_step_index < len(plan)
                    else None
                )
                for index, step in enumerate(workflow["steps"]):
                    call_id = f"workflow-{execution_id}-{index + 1}"
                    step_id = f"workflow:{execution_id}:{step['order']}"
                    plan_index = len(plan)
                    dependencies = (
                        [prior_step_index]
                        if prior_step_index is not None
                        else []
                    )
                    plan.append({
                        "id": step_id,
                        "description": (
                            f"{workflow['pattern_name']}: {step['name']}"
                        )[:500],
                        "tool": step["tool"],
                        "arguments": step["args"],
                        "dependencies": dependencies,
                        "status": "pending",
                        "kind": "tool",
                    })
                    step_calls.append({
                        "call_id": call_id,
                        "plan_index": plan_index,
                        "order": step["order"],
                        "name": step["name"],
                        "tool": step["tool"],
                        "arguments": step["args"],
                    })
                    prior_step_index = plan_index

                workflow_context = {
                    "execution_id": execution_id,
                    "input_data": workflow["input_data"],
                    "step_calls": step_calls,
                    "next_step": 0,
                }
                progress, current = _plan_progress(plan)
                await _sync_plan(task_id, plan, progress, current)
                emit("plan", {
                    "task_id": task_id,
                    "steps": plan,
                    "progress": progress,
                    "current_step": current,
                    "workflow_execution_id": execution_id,
                })
                first_step = step_calls[0]
                expanded_parent_result = ToolMessage(
                    content=ToolResult.ok(
                        "Saved workflow loaded. Its steps are executing through "
                        "the central task lifecycle.",
                        {
                            "execution_id": execution_id,
                            "status": "running",
                            "task_id": task_id,
                            "run_id": run_id,
                        },
                    ).model_dump_json(),
                    tool_call_id=parent_call_id,
                )
                parent_result_index = next(
                    (
                        index for index, result in enumerate(results)
                        if isinstance(result, ToolMessage)
                        and result.tool_call_id == parent_call_id
                    ),
                    None,
                )
                if parent_result_index is None:
                    raise ValueError(
                        "Workflow request result was not recorded by the central executor."
                    )
                results[parent_result_index] = expanded_parent_result
                results.append(AIMessage(
                    content="",
                    tool_calls=[{
                        "name": first_step["tool"],
                        "args": first_step["arguments"],
                        "id": first_step["call_id"],
                        "type": "tool_call",
                    }],
                    id=new_id(),
                ))
            except Exception as exc:
                logger.exception(
                    "Could not expand saved workflow execution %s", execution_id
                )
                from app.services.workflow_automation import WorkflowEngine

                WorkflowEngine().set_canonical_execution_status(
                    execution_id, "failed", error=str(exc)
                )
                status = "error"
                for result_index, message in enumerate(results):
                    if (
                        isinstance(message, ToolMessage)
                        and message.tool_call_id == parent_call_id
                    ):
                        results[result_index] = ToolMessage(
                            content=ToolResult.fail(
                                "WORKFLOW_EXPANSION_FAILED",
                                "The saved workflow could not be expanded into canonical steps.",
                                message="The workflow was not executed.",
                            ).model_dump_json(),
                            tool_call_id=parent_call_id,
                        )
                        break

        if isinstance(workflow_context, dict):
            step_calls_by_id = {
                str(item.get("call_id")): item
                for item in workflow_context.get("step_calls", [])
                if isinstance(item, dict) and item.get("call_id")
            }
            for message in results:
                if not isinstance(message, ToolMessage):
                    continue
                workflow_step_call = step_calls_by_id.get(
                    str(message.tool_call_id)
                )
                if workflow_step_call is None:
                    continue
                parsed = _parse_tool_result(str(message.content or ""))
                plan_index = workflow_step_call.get("plan_index")
                canonical_step_id = (
                    plan[plan_index].get("canonical_step_id")
                    if isinstance(plan_index, int)
                    and plan is not None
                    and 0 <= plan_index < len(plan)
                    else None
                )
                from app.services.workflow_automation import WorkflowEngine

                workflow_step_call["resolved_arguments"] = (
                    plan[plan_index].get("arguments")
                    if isinstance(plan_index, int) and plan is not None
                    else workflow_step_call.get("arguments")
                )
                WorkflowEngine().record_canonical_step(
                    execution_id=workflow_context["execution_id"],
                    step_order=int(workflow_step_call["order"]),
                    step_name=str(workflow_step_call["name"]),
                    task_step_id=canonical_step_id,
                    tool_name=str(workflow_step_call["tool"]),
                    tool_args=(
                        workflow_step_call.get("resolved_arguments")
                        if isinstance(
                            workflow_step_call.get("resolved_arguments"), dict
                        )
                        else workflow_step_call.get("arguments") or {}
                    ),
                    result=parsed,
                    duration_seconds=0.0,
                    is_last_step=(
                        workflow_step_call["order"]
                        == workflow_context["step_calls"][-1]["order"]
                    ),
                )
                if not parsed.success:
                    if (
                        isinstance(plan_index, int)
                        and plan is not None
                        and 0 <= plan_index < len(plan)
                    ):
                        plan[plan_index]["status"] = "failed"
                        progress, current = _plan_progress(plan)
                        await _sync_plan(task_id, plan, progress, current)
                    completed_before_failure = any(
                        item.get("status") in {"done", "skipped"}
                        for item in (plan or [])[:plan_index]
                    ) if isinstance(plan_index, int) else False
                    status = "partial" if completed_before_failure else "error"
                    continue

                current_index = int(workflow_context.get("next_step", 0))
                step_index = workflow_context["step_calls"].index(
                    workflow_step_call
                )
                if step_index < current_index:
                    continue
                next_index = step_index + 1
                workflow_context["next_step"] = next_index
                if next_index < len(workflow_context["step_calls"]):
                    next_step = workflow_context["step_calls"][next_index]
                    results.append(AIMessage(
                        content="",
                        tool_calls=[{
                            "name": next_step["tool"],
                            "args": next_step["arguments"],
                            "id": next_step["call_id"],
                            "type": "tool_call",
                        }],
                        id=new_id(),
                    ))

        if pending_approval and isinstance(workflow_context, dict):
            from app.services.workflow_automation import WorkflowEngine

            WorkflowEngine().set_canonical_execution_status(
                workflow_context["execution_id"], "approval_required"
            )

        direct_action = state.get("direct_action")
        if direct_action and not pending_approval:
            direct_call_id = direct_action.get("call_id", "")
            direct_result = next(
                (
                    message for message in results
                    if isinstance(message, ToolMessage)
                    and message.tool_call_id == direct_call_id
                ),
                None,
            )
            if direct_result is not None:
                parsed = _parse_tool_result(str(direct_result.content or ""))
                if parsed.success:
                    status = "completed"
                    if direct_action["tool_name"] == "open_youtube":
                        final_message = "Opened YouTube in the default browser."
                    elif direct_action["tool_name"] == "run_external_coding_agent":
                        preview = parsed.data if isinstance(parsed.data, dict) else {}
                        preview_parts = [
                            "Prepared a coding-agent proposal. No project files were changed."
                        ]
                        changes = preview.get("file_changes")
                        if isinstance(changes, list) and changes:
                            preview_parts.append("Proposed file changes:")
                            remaining = 24_000
                            for change in changes:
                                if not isinstance(change, dict):
                                    continue
                                path = str(change.get("path") or "unknown file")
                                state = str(change.get("status") or "change")
                                diff = str(change.get("diff") or "")
                                section = f"{state}: {path}"
                                if diff and remaining > 0:
                                    section += f"\n{diff[:remaining]}"
                                    remaining -= min(len(diff), remaining)
                                if change.get("diff_truncated"):
                                    section += "\n[This file's diff is truncated.]"
                                preview_parts.append(section)
                                if remaining <= 0:
                                    preview_parts.append("[Preview truncated; see task tool details.]")
                                    break
                        reply = str(preview.get("reply") or "")
                        if reply and not changes:
                            preview_parts.append(reply[:12_000])
                        final_message = "\n\n".join(preview_parts)
                    elif direct_action.get("success_message"):
                        final_message = direct_action["success_message"]
                    elif direct_action.get("action") == "close":
                        if parsed.data.get("status") in {"closed", "already_exited"}:
                            final_message = f"Closed {direct_action['display_name']}."
                        else:
                            final_message = (
                                f"Asked {direct_action['display_name']} to close "
                                "gracefully, but could not verify that it exited."
                            )
                            status = "partial"
                    else:
                        final_message = f"Started {direct_action['display_name']}."
                else:
                    if direct_action.get("failure_message"):
                        final_message = (
                            f"{direct_action['failure_message']} {parsed.message}"
                        )
                    else:
                        action_verb = (
                            "close" if direct_action.get("action") == "close" else "open"
                        )
                        final_message = (
                            f"Could not {action_verb} {direct_action['display_name']}: "
                            f"{parsed.message}"
                        )
                    status = "error"
                results.append(AIMessage(content=final_message, id=new_id()))

        batch_calls_by_id = {
            str(call.get("id") or ""): call
            for call in tool_calls_to_execute
            if call.get("name") == "send_email_batch"
        }
        successful_batch = next(
            (
                _parse_tool_result(str(message.content or ""))
                for message in results
                if isinstance(message, ToolMessage)
                and batch_calls_by_id.get(message.tool_call_id) is not None
                and _parse_tool_result(str(message.content or "")).success
            ),
            None,
        )
        pending_non_batch_steps = [
            step for step in (plan or [])
            if step.get("tool") != "send_email_batch"
            and step.get("tool")
            and step.get("status") in {"pending", "active"}
        ]
        if successful_batch is not None and not pending_non_batch_steps:
            for step in plan or []:
                if (
                    step.get("tool") == "send_email_batch"
                    and step.get("status") in {"pending", "active"}
                ):
                    step["status"] = "done"
            sent = (successful_batch.data or {}).get("sent", [])
            providers = {
                str(item.get("provider") or "")
                for item in sent if isinstance(item, dict)
            }
            summary = (
                f"Recorded {len(sent)} approved emails in the offline email mock; "
                "no messages were delivered to recipients."
                if providers == {"email_mock"}
                else f"Sent {len(sent)} approved emails."
            )
            results.append(AIMessage(content=summary, id=new_id()))
            status = "completed"

        update: dict[str, Any] = {
            "messages": results,
            "sources": sources,
            "status": status,
            "pending_approval": pending_approval,
            "tool_calls": tool_calls,
            "artifacts": artifacts,
            "browser_session_state": browser_session_state,
            "recovery_dispatch": False,
        }
        if plan is not None:
            update["plan"] = plan
        if workflow_context is not None:
            update["workflow_context"] = workflow_context
        if pending_approval:
            update["force_final"] = False
        return update

    async def respond_node(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        status = state.get("status", "running")
        from app.tools.routing import is_multi_step_request

        pending_ids = _unanswered_tool_call_ids(messages)
        current_turn_messages = _current_turn_messages(messages)
        partial = _has_failed_tool_result(current_turn_messages)
        plan = state.get("plan") or []
        reconciled_plan, plan_changed = _reconcile_successful_plan_steps(
            plan, current_turn_messages
        )
        if plan_changed:
            state = {**state, "plan": reconciled_plan}
            progress, current = _plan_progress(reconciled_plan)
            await _sync_plan(
                state.get("task_id", ""), reconciled_plan, progress, current
            )
            emit("plan", {
                "task_id": state.get("task_id", ""),
                "steps": reconciled_plan,
                "progress": progress,
                "current_step": current,
            })

        async def completed_response_plan_update(final_status: str) -> dict[str, Any]:
            if final_status != "completed":
                return {}
            plan = state.get("plan") or []
            revised, changed = _complete_response_plan_steps(plan)
            if not changed:
                return {}
            progress, current = _plan_progress(revised)
            await _sync_plan(state.get("task_id", ""), revised, progress, current)
            emit("plan", {
                "task_id": state.get("task_id", ""),
                "steps": revised,
                "progress": progress,
                "current_step": current,
            })
            return {"plan": revised}

        if status == "running" and run_registry.is_paused(
            state.get("user_id", ""), state.get("run_id", "")
        ):
            status = "paused"  # pause landed after the last tool executed

        if status == "paused_approval":
            return {"status": "paused_approval"}

        if status == "paused":
            # Generic pause: answer any dangling tool calls with PAUSED
            # fillers (no model call â€” the resume continues the turn).
            if pending_ids:
                filler = [
                    ToolMessage(
                        content=ToolResult.fail(
                            "PAUSED", "The run was paused; this tool was not executed.",
                            message="Paused; re-issue this tool after resuming.",
                        ).model_dump_json(),
                        tool_call_id=cid,
                    )
                    for cid in pending_ids
                ]
                return {"messages": filler, "status": "paused"}
            return {"status": "paused"}

        if status == "stopped":
            if pending_ids:
                filler = [
                    ToolMessage(
                        content=ToolResult.fail(
                            "STOPPED", "User pressed STOP; this tool was not executed.",
                            message="Stopped by user.",
                        ).model_dump_json(),
                        tool_call_id=cid,
                    )
                    for cid in pending_ids
                ]
                final = await _stream_model(
                    tools_free,
                    _with_system(messages + filler, STOP_NUDGE),
                )
                final.content = _validate_output(final.content)
                return {"messages": filler + [final], "status": "stopped"}
            return {"status": "stopped"}

        bus_search_reply = _buupass_search_reply(current_turn_messages)
        if bus_search_reply is not None:
            message, succeeded = bus_search_reply
            final_status = "completed" if succeeded else "partial"
            return {
                "messages": [AIMessage(content=message, id=new_id())],
                "status": final_status,
                "recoverable": not succeeded,
                **await completed_response_plan_update(final_status),
            }

        if status == "partial" and state.get("recoverable") and not partial:
            cbr_reply = _official_cbr_fallback(
                _task_request(state, messages),
                current_turn_messages,
            )
            if cbr_reply and not _pending_plan_tools(plan):
                logger.info(
                    "Completed an exact CBR answer from retrieved official CBK evidence"
                )
                return {
                    "messages": [AIMessage(content=cbr_reply, id=new_id())],
                    "status": "completed",
                    "recoverable": False,
                    **await completed_response_plan_update("completed"),
                }

        if status == "partial" and state.get("recoverable"):
            last_assistant = next(
                (
                    message for message in reversed(current_turn_messages)
                    if isinstance(message, AIMessage)
                    and not message.tool_calls
                    and str(message.content or "").strip()
                ),
                None,
            )
            if last_assistant is not None:
                return {"status": "partial", "recoverable": True}

        if (
            not state.get("plan")
            and not state.get("plan_recovery")
            and is_multi_step_request(_task_request(state, messages))
        ):
            recovery = await recover_without_plan(
                state,
                messages,
                "the response path reached a multi-step task without a plan",
            )
            response = _last_ai(recovery.get("messages", []))
            if (
                recovery.get("status") == "running"
                and response is not None
                and response.tool_calls
            ):
                recovery["recovery_dispatch"] = True
            elif recovery.get("status") == "running":
                recovery["status"] = "completed"
            return recovery

        if not pending_ids and _pending_plan_tools(state.get("plan")):
            pending = ", ".join(
                str(step.get("description") or step.get("tool"))
                for step in _pending_plan_tools(state.get("plan"))
            )
            unfinished_message = (
                "I could not complete all planned task actions. These "
                f"steps remain unfinished: {pending}. Completed work is "
                "preserved; resume this task from the Tasks panel to continue."
            )
            unfinished_message = (
                _hotel_booking_recovery(_task_request(state, messages))
                or unfinished_message
            )
            return {
                "messages": [AIMessage(
                    content=unfinished_message,
                    id=new_id(),
                )],
                "status": "partial",
                "recoverable": True,
            }

        if (
            _has_execution_guard_failure(current_turn_messages)
            and not _has_successful_task_action(current_turn_messages)
        ):
            final = AIMessage(
                content=(
                    "Some requested actions remain unfinished. No task actions "
                    "were run because a tool call did not meet the task's "
                    "capability, argument, or plan requirements."
                ),
                id=new_id(),
            )
            return {"messages": [final], "status": "partial"}

        if pending_ids:
            # Step budget exhausted (or forced wrap-up): answer outstanding tool
            # calls honestly, then force a final answer without tools.
            filler = [
                ToolMessage(
                    content=ToolResult.fail(
                        "SKIPPED", "Continuing without this tool (step budget).",
                        message="Tool skipped; proceed to the final answer.",
                    ).model_dump_json(),
                    tool_call_id=cid,
                )
                for cid in pending_ids
            ]
            final = await _stream_model(
                tools_free, _with_system(messages + filler, MAX_STEPS_NUDGE)
            )
            final.content = _validate_output(final.content)
            final = await _constrained_final(messages + filler, final)
            final_status = "partial" if status == "partial" or partial else "completed"
            return {
                "messages": filler + [final],
                "status": final_status,
                **await completed_response_plan_update(final_status),
            }

        last = _last_ai(messages)
        if last is not None and last.tool_calls and not str(last.content or "").strip():
            # Tool budget exhausted with every call answered: the model never
            # got to write prose â€” force one last tool-free answer (spec Â§63).
            final = await _stream_model(tools_free, _with_system(messages, MAX_STEPS_NUDGE))
            final.content = _validate_output(final.content)
            final = await _constrained_final(messages, final)
            final_status = "partial" if status == "partial" or partial else "completed"
            return {
                "messages": [final],
                "status": final_status,
                **await completed_response_plan_update(final_status),
            }

        if _last_ai(messages) is None:
            final = await _stream_model(tools_free, _with_system(messages, None))
            final.content = _validate_output(final.content)
            final = await _constrained_final(messages, final)
            final_status = "partial" if status == "partial" or partial else "completed"
            return {
                "messages": [final],
                "status": final_status,
                **await completed_response_plan_update(final_status),
            }

        if last is not None:
            corrected = await _constrained_final(messages, last)
            if corrected is not last:
                final_status = "partial" if status == "partial" or partial else "completed"
                return {
                    "messages": [corrected],
                    "status": final_status,
                    **await completed_response_plan_update(final_status),
                }
        final_status = "partial" if status == "partial" or partial else "completed"
        return {
            "status": final_status,
            **await completed_response_plan_update(final_status),
        }

    def _with_system(messages: list[BaseMessage], extra: str | None) -> list[BaseMessage]:
        head: list[BaseMessage] = []
        if system_prompt:
            text = system_prompt if extra is None else f"{system_prompt}\n\n{extra}"
            head.append(SystemMessage(content=text))
        elif extra:
            head.append(SystemMessage(content=extra))
        return head + messages

    def route_after_agent(state: AgentState) -> str:
        if state.get("status") in ("stopped", "partial", "error"):
            return "respond"
        if state.get("status") == "paused":
            return END  # nothing left to stream â€” persist and wait for resume
        ai = _last_ai(state.get("messages", []))
        if ai and ai.tool_calls:
            return "tools"
        return "respond"

    def route_after_tools(state: AgentState) -> str:
        if state.get("status") in (
            "paused_approval", "paused", "stopped", "partial", "error"
        ):
            return END
        if state.get("status") == "completed":
            return "respond"
        if state.get("pending_approval"):
            return END
        if _buupass_search_reply(state.get("messages", [])) is not None:
            return "respond"
        if state.get("steps", 0) >= max_steps:
            return "respond"
        if state.get("tool_calls", 0) >= _effective_tool_call_budget(
            state.get("plan"), max_tool_calls
        ):
            return "respond"
        if run_registry.is_stopped(state.get("user_id", ""), state.get("run_id", "")):
            return "respond"
        if run_registry.is_paused(state.get("user_id", ""), state.get("run_id", "")):
            return "respond"  # respond_node normalises the status to "paused"
        if _unanswered_tool_call_ids(state.get("messages", [])):
            return "tools"
        return "agent"

    def route_after_respond(state: AgentState) -> str:
        if not state.get("recovery_dispatch"):
            return END
        if state.get("status") in ("paused_approval", "paused", "stopped", "error"):
            return END
        if _unanswered_tool_call_ids(state.get("messages", [])):
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("respond", respond_node)
    graph.add_edge(START, entry)
    graph.add_conditional_edges(
        "agent", route_after_agent,
        {"tools": "tools", "respond": "respond", END: END},
    )
    graph.add_conditional_edges(
        "tools", route_after_tools,
        {"agent": "agent", "tools": "tools", "respond": "respond", END: END},
    )
    graph.add_conditional_edges(
        "respond", route_after_respond,
        {"tools": "tools", END: END},
    )
    return graph.compile()


def _parse_tool_result(raw: Any) -> ToolResult:
    parsed = decode_tool_result(raw)
    if parsed is not None:
        return parsed
    return ToolResult.fail(
        "MALFORMED_TOOL_RESULT",
        "The tool returned invalid structured output.",
        message="The tool result could not be validated; the step was not completed.",
    )


def _workflow_step_for_call(
    workflow_context: dict[str, Any] | None,
    call_id: str,
) -> dict[str, Any] | None:
    if not isinstance(workflow_context, dict):
        return None
    step_calls = workflow_context.get("step_calls")
    if not isinstance(step_calls, list):
        return None
    return next(
        (
            item for item in step_calls
            if isinstance(item, dict) and item.get("call_id") == call_id
        ),
        None,
    )


def _resolve_workflow_step_arguments(
    workflow_context: dict[str, Any],
    step_call: dict[str, Any],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    from app.services.workflow_automation import WorkflowEngine

    step_results: dict[str, dict[str, Any]] = {}
    calls_by_id = {
        str(item.get("call_id")): item
        for item in workflow_context.get("step_calls", [])
        if isinstance(item, dict) and item.get("call_id")
    }
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        prior_step = calls_by_id.get(str(message.tool_call_id))
        if prior_step is None:
            continue
        parsed = _parse_tool_result(str(message.content or ""))
        step_results[str(prior_step["name"])] = {
            "success": parsed.success,
            "message": parsed.message,
            "data": parsed.data or {},
            "error": parsed.error.model_dump() if parsed.error else None,
        }
    resolved = WorkflowEngine._resolve_templates(
        step_call.get("arguments") or {},
        {
            "input": workflow_context.get("input_data") or {},
            "steps": step_results,
        },
    )
    if not isinstance(resolved, dict):
        raise ValueError("Workflow step arguments must resolve to an object.")
    return resolved


def _buupass_search_reply(
    messages: list[BaseMessage],
) -> tuple[str, bool] | None:
    """Use verified BuuPass search output directly instead of another model call."""
    last_ai = _last_ai(messages)
    if last_ai is None or not last_ai.tool_calls:
        return None
    calls = [
        call for call in last_ai.tool_calls
        if str(call.get("name") or "") == "search_buses"
    ]
    if not calls:
        return None
    if any(
        str(call.get("name") or "") not in {"search_buses", "update_plan"}
        for call in last_ai.tool_calls
    ):
        return None
    tool_messages = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }
    results = [
        _parse_tool_result(str(tool_messages[call.get("id")].content or ""))
        for call in calls
        if call.get("id") in tool_messages
    ]
    if len(results) != len(calls):
        return None
    return "\n\n".join(result.message for result in results), all(
        result.success for result in results
    )


def _hotel_booking_recovery(request: str) -> str | None:
    """Replace stale hotel-ID plans with the supported Booking.com handoff."""
    if not re.search(r"\b(?:hotel|accommodation|lodging)\b", request, re.I):
        return None
    if not re.search(r"\b(?:book|booking|reserve|reservation)\b", request, re.I):
        return None
    return (
        "I couldn't complete that hotel request, and no reservation was made. "
        "Jalora can prepare a live Booking.com search, but it cannot reserve or "
        "confirm a room in the app. To create the search for your chosen hotel, "
        "please provide the check-in date and check-out date (YYYY-MM-DD), the "
        "number of adults, and the number of rooms. If children are travelling, "
        "include each child's age. I will then give you a Booking.com link where "
        "you can review availability, prices, and terms and complete the booking."
    )


def _hotel_booking_details_request(request: str) -> str | None:
    """Ask for booking inputs before the model can invent hotel/booking IDs."""
    is_hotel_booking = (
        re.search(r"\b(?:hotel|accommodation|lodging)\b", request, re.I)
        and re.search(r"\b(?:book|booking|reserve|reservation)\b", request, re.I)
    )
    if not is_hotel_booking:
        return None

    missing: list[str] = []
    if len(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", request)) < 2:
        missing.append("check-in and check-out dates (YYYY-MM-DD)")
    if not re.search(r"\b\d+\s+(?:adults?|people|guests?)\b", request, re.I):
        missing.append("the number of adults")
    if not re.search(r"\b\d+\s+rooms?\b", request, re.I):
        missing.append("the number of rooms")
    if not missing:
        return None

    return (
        "I can prepare a Booking.com search for that hotel, but I need "
        + ", ".join(missing)
        + ". If children are travelling, please include each child's age. "
        "I can't confirm a reservation in Jalora; after I prepare the search, "
        "you'll review availability, prices, and terms and complete checkout "
        "on Booking.com."
    )


def _has_failed_tool_result(messages: list[BaseMessage]) -> bool:
    """Return whether any executed tool produced an unambiguous failure.

    A model response is not proof that a failed dependency was recovered.  The
    graph therefore carries a partial status through finalization whenever a
    tool result failed, while still preserving successful independent outputs.
    """
    benign_control_codes = {
        "REJECTED", "USER_REJECTED", "PAUSED", "STOPPED", "SKIPPED",
        "PLAN_REVISION_REQUIRED", "FAILED_STEP_UNCHANGED",
        # Older persisted turns may contain the pre-discovery intent guard's
        # rejection code; keep those historical control results non-fatal.
        "INTENT_MISMATCH",
    }
    tool_names_by_call = {
        str(call.get("id") or ""): str(call.get("name") or "")
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    tool_results = [
        (message, _parse_tool_result(str(message.content or "")))
        for message in messages
        if isinstance(message, ToolMessage)
    ]
    successful_tools = {
        tool_names_by_call.get(message.tool_call_id, "")
        for message, result in tool_results
        if result.success
    }
    has_successful_tool = any(result.success for _, result in tool_results)
    for message, result in tool_results:
        if result.success or not result.error:
            continue
        if result.error.code in benign_control_codes:
            continue
        if result.error.code == "CAPABILITY_NOT_SELECTED" and has_successful_tool:
            continue
        if (
            result.error.code in {"DEPENDENCY_NOT_READY", "INVALID_TOOL_ARGUMENTS"}
            and tool_names_by_call.get(message.tool_call_id, "") in successful_tools
        ):
            continue
        return True
    return False


def _has_execution_guard_failure(messages: list[BaseMessage]) -> bool:
    guard_codes = {
        "PLAN_REQUIRED",
        "UNPLANNED_ACTION",
        "CAPABILITY_NOT_SELECTED",
        "INVALID_TOOL_ARGUMENTS",
        "UNKNOWN_TOOL",
        "DEPENDENCY_NOT_READY",
    }
    return any(
        result.error is not None and result.error.code in guard_codes
        for message in messages
        if isinstance(message, ToolMessage)
        for result in [_parse_tool_result(str(message.content or ""))]
    )


def _has_successful_task_action(messages: list[BaseMessage]) -> bool:
    tool_names_by_call = {
        str(call.get("id") or ""): str(call.get("name") or "")
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    return any(
        result.success and tool_names_by_call.get(message.tool_call_id) != "update_plan"
        for message in messages
        if isinstance(message, ToolMessage)
        for result in [_parse_tool_result(str(message.content or ""))]
    )


def _public_contact_email_evidence_error(
    messages: list[BaseMessage],
    recipient: str,
    *,
    plan: list[dict[str, Any]] | None = None,
    arguments: dict[str, Any] | None = None,
) -> str | None:
    normalized_recipient = recipient.strip().casefold()
    if not normalized_recipient:
        return "The outreach email has no recipient address."
    if plan is None:
        return "The public-contact task has no validated organization-specific plan."

    verification_urls: set[str] = set()
    matching_arguments = dict(arguments or {})
    for step in plan:
        expected = step.get("arguments")
        planned_recipient = (
            str(expected.get("to") or "").strip()
            if isinstance(expected, dict)
            else ""
        )
        if (
            step.get("tool") == "send_email"
            and planned_recipient.casefold() == normalized_recipient
        ):
            matching_arguments["to"] = planned_recipient
            break
    send_step_index = _matching_plan_step(
        plan,
        "send_email",
        matching_arguments or {"to": recipient},
    )
    if send_step_index is None:
        return "The outreach recipient is not linked to a planned organization."
    for dependency in plan[send_step_index].get("dependencies", []):
        try:
            verification_index = int(dependency)
        except (TypeError, ValueError):
            verification_index = next(
                (
                    index for index, step in enumerate(plan)
                    if str(step.get("id") or "") == str(dependency)
                ),
                -1,
            )
        if not 0 <= verification_index < len(plan):
            continue
        verification_step = plan[verification_index]
        if verification_step.get("tool") != "open_url":
            continue
        verification_arguments = verification_step.get("arguments")
        page_url = (
            str(verification_arguments.get("url") or "").strip()
            if isinstance(verification_arguments, dict)
            else ""
        )
        if page_url:
            verification_urls.add(page_url)
        description = str(verification_step.get("description") or "")
        candidate_urls = re.search(
            r"Candidate URLs:\s*(.+)$",
            description,
            flags=re.IGNORECASE,
        )
        if candidate_urls is not None:
            verification_urls.update(
                url.strip()
                for url in candidate_urls.group(1).split(";")
                if url.strip()
            )
    if not verification_urls:
        return (
            "The outreach recipient has no dependency on a planned "
            "organization contact page."
        )

    organization = ""
    for dependency in plan[send_step_index].get("dependencies", []):
        try:
            verification_index = int(dependency)
        except (TypeError, ValueError):
            verification_index = next(
                (
                    index for index, step in enumerate(plan)
                    if str(step.get("id") or "") == str(dependency)
                ),
                -1,
            )
        if not 0 <= verification_index < len(plan):
            continue
        verification_step = plan[verification_index]
        if verification_step.get("tool") != "open_url":
            continue
        organization_match = re.search(
            r"official contact page for\s+(.+?)(?:\.|$)",
            str(verification_step.get("description") or ""),
            flags=re.IGNORECASE,
        )
        if organization_match:
            organization = organization_match.group(1).strip()
            break
    if not organization:
        return "The planned contact page is not linked to an organization name."

    current_messages = _current_turn_messages(messages)
    calls_by_id = {
        str(call.get("id") or ""): call
        for message in current_messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls or []
    }
    email_found = False
    for message in current_messages:
        if not isinstance(message, ToolMessage):
            continue
        call = calls_by_id.get(message.tool_call_id)
        if not isinstance(call, dict) or call.get("name") != "open_url":
            continue
        call_arguments = call.get("args")
        requested_url = (
            str(call_arguments.get("url") or "").strip()
            if isinstance(call_arguments, dict)
            else ""
        )
        result = _parse_tool_result(str(message.content or ""))
        if not result.success or not isinstance(result.data, dict):
            continue
        page_url = str(result.data.get("url") or "").strip()
        if (
            not page_url.startswith(("https://", "http://"))
            or page_url != requested_url
            or page_url not in verification_urls
        ):
            continue
        public_emails = result.data.get("public_emails")
        if isinstance(public_emails, list) and any(
            isinstance(address, str)
            and address.strip().casefold() == normalized_recipient
            for address in public_emails
        ):
            email_found = True
            if _public_contact_page_matches_organization(
                organization, result.data
            ):
                return None

    if email_found:
        return (
            "The recipient appears on the opened page, but the page content "
            f"does not sufficiently match {organization}. The address remains "
            "unverified; ask the user how to proceed."
        )

    return (
        "The recipient address was not extracted from a contact page opened "
        "during this task. The address remains unverified."
    )


def _public_contact_page_matches_organization(
    organization: str,
    page_data: dict[str, Any],
) -> bool:
    expected_tokens = {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9]+", organization)
        if token.casefold() not in {
            "a", "an", "and", "for", "in", "of", "the", "to",
        }
    }
    if not expected_tokens:
        return False

    source_text = " ".join(
        str(page_data.get(field) or "")
        for field in ("title", "text", "contact_evidence", "url")
    )
    source_text = re.sub(
        r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
        " ",
        source_text,
    )
    source_tokens = {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9]+", source_text)
    }
    required_matches = min(2, len(expected_tokens))
    return len(expected_tokens & source_tokens) >= required_matches


def _compact_public_contact_page_result(raw: str) -> str:
    """Keep verified contact evidence without carrying full pages between turns."""
    result = _parse_tool_result(raw)
    if not result.success or not isinstance(result.data, dict):
        return raw

    text = str(result.data.get("text") or "")
    evidence: list[str] = []
    for match in re.finditer(
        r"\b(?:e-?mail|contact|support|inquir|@)\b",
        text,
        re.IGNORECASE,
    ):
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 260)
        snippet = " ".join(text[start:end].split())
        if snippet and snippet not in evidence:
            evidence.append(snippet)
        if len(evidence) == 6:
            break
    if not evidence and text:
        evidence.append(" ".join(text[:400].split()))

    compact = {
        "url": result.data.get("url"),
        "title": result.data.get("title"),
        "public_emails": result.data.get("public_emails") or [],
        "contact_evidence": " ... ".join(evidence)[:1800],
        "total_chars": result.data.get("total_chars"),
        "untrusted": result.data.get("untrusted", True),
    }
    return ToolResult.ok(
        f"Opened the organization contact page; extracted "
        f"{len(compact['public_emails'])} public email address(es).",
        compact,
    ).model_dump_json()


def _model_outage_message(
    messages: list[BaseMessage],
    error: Exception | None = None,
    plan: list[dict[str, Any]] | None = None,
) -> str:
    detail = str(error or "").casefold()
    if any(marker in detail for marker in (
        "no credits remaining",
        "credit balance is too low",
        "insufficient_balance",
        "insufficient balance",
    )):
        message = (
            "The configured AI providers rejected further requests because "
            "their usage credits are exhausted. Completed results were "
            "preserved; remaining steps were not executed. Add provider "
            "credits or select a funded provider, then resume this task."
        )
    elif any(marker in detail for marker in (
        "in_flight_budget_exhausted",
        "retry-after",
        "rate_limit_exceeded",
        "rate limit",
    )):
        message = (
            "The configured AI providers temporarily rate-limited this run. "
            "Completed results were preserved; remaining steps were not "
            "executed. Wait for the provider limit to reset, then resume this task."
        )
    elif "timeout" in detail or "timed out" in detail:
        message = (
            "The AI provider timed out while continuing this run. Completed "
            "results were preserved; remaining steps were not executed. Resume "
            "this task to continue without repeating verified completed steps."
        )
    elif any(marker in detail for marker in (
        "prompt tokens limit exceeded",
        "context length",
        "maximum context",
        "too many tokens",
    )):
        message = (
            "The selected AI provider rejected the next step because the "
            "conversation exceeded its prompt/context limit. Completed results "
            "were preserved; remaining steps were not executed. Resume with a "
            "model that supports a larger context window or reduce the amount "
            "of retrieved material."
        )
    else:
        message = (
            _MODEL_OUTAGE_MESSAGE
            if _has_successful_task_action(messages)
            else _MODEL_OUTAGE_NO_ACTION_MESSAGE
        )

    pending = [
        str(step.get("description") or "").strip()
        for step in plan or []
        if step.get("status") == "pending" and str(step.get("description") or "").strip()
    ]
    if pending:
        message += " The remaining work is unfinished."
        message += " Still pending: " + "; ".join(pending[:5]) + "."
        if len(pending) > 5:
            message += f" And {len(pending) - 5} more step(s)."
    return message


def _official_cbr_fallback(
    request: str,
    messages: list[BaseMessage],
) -> str | None:
    """Answer an exact CBR-rate request from a dated official CBK result."""
    if not (
        re.search(r"\b(?:CBR|Central Bank Rate)\b", request, re.I)
        and re.search(r"\b(?:current|latest|today|exact|announced)\b", request, re.I)
        and re.search(r"\b(?:rate|percent(?:age)?)\b", request, re.I)
        and re.search(r"\b(?:MPC|Monetary Policy Committee)\b", request, re.I)
    ):
        return None

    tool_names = {
        str(call.get("id") or ""): str(call.get("name") or "")
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls or []
    }
    candidates: list[tuple[str, str, str, str]] = []
    rate_pattern = re.compile(
        r"(?:\bCBR\b|\bCentral Bank Rate\b).{0,120}?"
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\b"
        r"|(\d+(?:\.\d+)?)\s*(?:%|percent)\b.{0,120}?"
        r"(?:\bCBR\b|\bCentral Bank Rate\b)",
        re.I,
    )
    date_pattern = re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/")

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_name = tool_names.get(message.tool_call_id) or str(
            getattr(message, "name", "") or ""
        )
        if tool_name not in {"search_web", "open_url", "browse_page"}:
            continue
        result = _parse_tool_result(str(message.content or ""))
        if not result.success:
            continue
        data = result.data or {}
        items = (
            data.get("results", [])
            if tool_name == "search_web"
            else [data]
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if urlparse(url).hostname not in {
                "centralbank.go.ke",
                "www.centralbank.go.ke",
            }:
                continue
            title = str(item.get("title") or "").strip()
            evidence = " ".join(
                value for value in (
                    title,
                    str(item.get("snippet") or ""),
                    str(item.get("text") or ""),
                ) if value
            )
            if not re.search(r"\b(?:MPC|Monetary Policy Committee)\b", evidence, re.I):
                continue
            match = rate_pattern.search(evidence)
            if not match:
                continue
            rate = match.group(1) or match.group(2)
            date_match = date_pattern.search(url)
            if not date_match:
                continue
            published = datetime.strptime(
                "-".join(date_match.groups()), "%Y-%m-%d"
            ).date()
            age_days = (date.today() - published).days
            if not 0 <= age_days <= 90:
                continue
            candidates.append((
                published.isoformat(),
                rate,
                title or "CBK Monetary Policy release",
                url,
            ))

    if not candidates:
        return None
    _, rate, title, url = max(candidates, key=lambda candidate: candidate[0])
    return (
        f"The latest dated official CBK MPC result retrieved reports the "
        f"Central Bank Rate at **{rate}%**. Source: [{title}]({url})."
    )


_RETRYABLE_CODES = {"TIMEOUT", "NETWORK_ERROR", "TOOL_EXCEPTION"}


def _is_retryable(parsed: ToolResult, spec) -> bool:
    """Spec Â§61: only safe read-only failures are retried automatically;
    CONFIRM+ tools are never auto-retried (duplicate-send risk)."""
    if (
        spec.permission >= PermissionLevel.CONFIRM
        or spec.side_effect != "read_only"
    ):
        return False
    return bool(parsed.error) and parsed.error.code in _RETRYABLE_CODES


_LIVE_OUTPUT_TOOLS = {
    "run_command", "run_tests", "run_linter",
    "edit_code", "edit_code_multi", "create_project", "list_files",
    "run_external_coding_agent",
}


def _live_tool_detail(name: str, parsed: ToolResult) -> dict[str, Any] | None:
    """A compact, secret-free payload for the UI's live coding console.

    Only the coding/exec tools emit detail: their results carry the command
    output, file paths, or unified diffs the user needs to watch the process
    like an IDE. Everything else returns None (chips only)."""
    if name not in _LIVE_OUTPUT_TOOLS:
        return None
    d = parsed.data if isinstance(parsed.data, dict) else None
    if not d:
        return None
    if name in ("run_command", "run_tests"):
        return {
            "command": str(d.get("command", "")),
            "stdout": str(d.get("stdout") or "")[-6000:],
            "stderr": str(d.get("stderr") or "")[-6000:],
            "exit_code": d.get("exit_code"),
            "ok": bool(d.get("ok")),
            "timed_out": bool(d.get("timed_out")),
        }
    if name == "run_linter":
        return {"diagnostics": d.get("diagnostics") or [],
                "checked": d.get("checked", 0)}
    if name in ("edit_code",):
        return {"path": d.get("path"), "created": d.get("created"),
                "diff": str(d.get("diff") or "")[:6000]}
    if name == "edit_code_multi":
        return {"applied": [
            {"path": a.get("path"), "created": a.get("created"),
             "diff": str(a.get("diff") or "")[:4000]}
            for a in (d.get("applied") or [])
        ], "failures": d.get("failures") or []}
    if name == "create_project":
        return {"name": d.get("name"), "template": d.get("template"),
                "files": d.get("files") or []}
    if name == "run_external_coding_agent":
        return {
            "provider": d.get("provider"),
            "preview_only": bool(d.get("preview_only")),
            "file_changes": [
                {
                    "path": str(change.get("path") or ""),
                    "status": str(change.get("status") or ""),
                    "diff": str(change.get("diff") or "")[:12_000],
                    "diff_truncated": bool(change.get("diff_truncated")),
                }
                for change in (d.get("file_changes") or [])
                if isinstance(change, dict)
            ],
            "reply": str(d.get("reply") or "")[:20_000],
            "reply_truncated": bool(d.get("reply_truncated")),
        }
    if name == "list_files":
        return {"files": [str(f.get("path")) for f in (d.get("files") or [])][:300]}
    return None


def _plan_progress(plan: list[dict[str, Any]]) -> tuple[int, int]:
    """(progress 0-100, current_step index) for a plan list."""
    total = len(plan)
    if not total:
        return 0, 0
    done = sum(1 for s in plan if s.get("status") in ("done", "skipped"))
    progress = int(round(100 * done / total))
    current = next(
        (i for i, s in enumerate(plan) if s.get("status") == "active"),
        next((i for i, s in enumerate(plan) if s.get("status") == "pending"), 0),
    )
    return progress, current


def _is_web_discovery_step(step: dict[str, Any], registry: ToolRegistry) -> bool:
    spec = registry.get(step.get("tool") or "")
    return bool(
        spec
        and spec.category == "research"
        and "web" in spec.tags
        and not {"source", "browser"}.intersection(spec.tags)
    )


def _is_page_read_step(step: dict[str, Any], registry: ToolRegistry) -> bool:
    spec = registry.get(step.get("tool") or "")
    return bool(
        spec
        and spec.category == "research"
        and {"source", "browser"}.intersection(spec.tags)
    )


def _is_report_step(step: dict[str, Any], registry: ToolRegistry) -> bool:
    spec = registry.get(step.get("tool") or "")
    return bool(
        spec
        and spec.category == "files"
        and "report" in spec.tags
    )


def _is_research_artifact_workflow(
    plan: list[dict[str, Any]], registry: ToolRegistry,
) -> bool:
    return (
        any(_is_web_discovery_step(step, registry) for step in plan)
        and any(_is_report_step(step, registry) for step in plan)
    )


def _research_artifact_plan_error(
    plan: list[dict[str, Any]], registry: ToolRegistry,
) -> str | None:
    """Require current source pages between web discovery and reports."""
    if not _is_research_artifact_workflow(plan, registry):
        return None

    search_indices = {
        index for index, step in enumerate(plan)
        if _is_web_discovery_step(step, registry)
    }
    page_indices = {
        index for index, step in enumerate(plan)
        if _is_page_read_step(step, registry)
    }
    report_indices = {
        index for index, step in enumerate(plan)
        if _is_report_step(step, registry)
    }
    if not page_indices:
        return (
            "A plan that combines web research and document creation must include "
            "a page-reading step before the report."
        )

    def ancestors(index: int) -> set[int]:
        found: set[int] = set()
        pending = list(plan[index].get("dependencies") or [])
        while pending:
            dependency = pending.pop()
            if dependency in found:
                continue
            found.add(dependency)
            pending.extend(plan[dependency].get("dependencies") or [])
        return found

    researched_pages = {
        page_index for page_index in page_indices
        if search_indices.intersection(ancestors(page_index))
    }
    if not researched_pages:
        return (
            "At least one page-reading step must depend on a web-discovery step "
            "so it reads current search results."
        )
    if any(
        not researched_pages.intersection(ancestors(report_index))
        for report_index in report_indices
    ):
        return (
            "Every report step must depend on a page-reading step that follows "
            "web discovery."
        )
    return None


def _repair_research_artifact_plan(
    plan: list[dict[str, Any]],
    registry: ToolRegistry,
    bound_tool_names: set[str],
) -> list[dict[str, Any]]:
    """Wire selected source readers into a research-to-report plan."""
    if not _is_research_artifact_workflow(plan, registry):
        return plan

    repaired = [dict(step) for step in plan]
    search_indices = {
        index for index, step in enumerate(repaired)
        if _is_web_discovery_step(step, registry)
    }
    report_indices = [
        index for index, step in enumerate(repaired)
        if _is_report_step(step, registry)
    ]
    page_indices = [
        index for index, step in enumerate(repaired)
        if _is_page_read_step(step, registry)
    ]

    if not page_indices:
        page_tool = next(
            (
                spec.name for spec in registry.all()
                if spec.name in bound_tool_names
                and _is_page_read_step({"tool": spec.name}, registry)
            ),
            None,
        )
        if page_tool is not None and report_indices:
            insert_at = min(report_indices)
            prior_searches = [
                index for index in search_indices if index < insert_at
            ]
            if prior_searches:
                for step in repaired:
                    step["dependencies"] = [
                        dependency + 1 if dependency >= insert_at else dependency
                        for dependency in step.get("dependencies", [])
                    ]
                source_id = "read_research_sources"
                used_ids = {step.get("id") for step in repaired}
                suffix = 2
                while source_id in used_ids:
                    source_id = f"read_research_sources_{suffix}"
                    suffix += 1
                source_step = {
                    "id": source_id,
                    "description": "Open and read source pages from the web search results.",
                    "status": "pending",
                    "tool": page_tool,
                    "dependencies": [
                        index + 1 if index >= insert_at else index
                        for index in prior_searches
                    ],
                }
                repaired.insert(insert_at, source_step)
                page_indices = [insert_at]
                report_indices = [
                    index + 1 if index >= insert_at else index
                    for index in report_indices
                ]
                logger.warning(
                    "Added required source-reading step %s to the research report plan",
                    source_id,
                )

    def ancestors(index: int) -> set[int]:
        found: set[int] = set()
        pending = list(repaired[index].get("dependencies") or [])
        while pending:
            dependency = pending.pop()
            if dependency in found:
                continue
            found.add(dependency)
            pending.extend(repaired[dependency].get("dependencies") or [])
        return found

    search_indices = {
        index for index, step in enumerate(repaired)
        if _is_web_discovery_step(step, registry)
    }
    page_indices = [
        index for index, step in enumerate(repaired)
        if _is_page_read_step(step, registry)
    ]
    report_indices = [
        index for index, step in enumerate(repaired)
        if _is_report_step(step, registry)
    ]

    for page_index in page_indices:
        if search_indices.intersection(ancestors(page_index)):
            continue
        prior_searches = [
            index for index in search_indices
            if index < page_index and page_index not in ancestors(index)
        ]
        if prior_searches:
            dependencies = repaired[page_index].setdefault("dependencies", [])
            dependencies.extend(
                index for index in prior_searches if index not in dependencies
            )
            logger.warning(
                "Connected source-reading plan step %s to web search",
                repaired[page_index].get("id"),
            )

    researched_pages = {
        index for index in page_indices
        if search_indices.intersection(ancestors(index))
    }
    if researched_pages:
        for report_index in report_indices:
            if researched_pages.intersection(ancestors(report_index)):
                continue
            eligible_pages = [
                index for index in researched_pages
                if index < report_index and report_index not in ancestors(index)
            ]
            if eligible_pages:
                page_index = max(eligible_pages)
                dependencies = repaired[report_index].setdefault("dependencies", [])
                if page_index not in dependencies:
                    dependencies.append(page_index)
                    logger.warning(
                        "Connected report plan step %s to source-reading step %s",
                        repaired[report_index].get("id"),
                        repaired[page_index].get("id"),
                    )

    return repaired


def _requires_research_evidence(
    intent: str, sources: list[dict[str, Any]],
) -> bool:
    return intent == "research" or any(
        source.get("source_type") in {"search_result", "page"}
        for source in sources
    )


def _preserve_completed_plan_steps(
    previous: list[dict[str, Any]] | None,
    revised: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Only retain completion state established by successful tool results."""
    previous_by_id = {
        step.get("id"): step for step in (previous or [])
        if step.get("id")
    }
    authoritative = []
    for step in revised:
        updated = dict(step)
        updated["status"] = "pending"
        prior = previous_by_id.get(step.get("id"))
        if prior:
            same_step = all(
                prior.get(key) == step.get(key)
                for key in (
                    "description", "tool", "arguments", "dependencies", "kind"
                )
            )
            if prior.get("status") == "done" and same_step:
                updated["status"] = "done"
        authoritative.append(updated)
    return authoritative


def _has_unchanged_failed_step(
    previous: list[dict[str, Any]] | None,
    revised: list[dict[str, Any]],
) -> bool:
    """Prevent a plan revision from silently resetting an identical failed step."""
    failed_by_id = {
        step.get("id"): step
        for step in (previous or [])
        if step.get("id") and step.get("status") == "failed"
    }
    return any(
        prior.get("tool") == step.get("tool")
        and prior.get("arguments") == step.get("arguments")
        for step in revised
        if (prior := failed_by_id.get(step.get("id"))) is not None
    )


def _complete_response_plan_steps(
    plan: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Complete eligible prose-only steps after the final answer exists."""
    revised = [dict(step) for step in plan]
    changed = False
    for step in revised:
        if (
            step.get("kind") == "response"
            and step.get("status") in {"pending", "active"}
            and all(
                revised[index].get("status") in {"done", "skipped"}
                for index in step.get("dependencies", [])
            )
        ):
            step["status"] = "done"
            changed = True
    return revised, changed


def _reconcile_successful_plan_steps(
    plan: list[dict[str, Any]],
    messages: list[BaseMessage],
) -> tuple[list[dict[str, Any]], bool]:
    """Reconcile persisted plan progress with successful tool results.

    A model can fail immediately after a side-effecting tool succeeds. The
    result is authoritative even when the model cannot make a follow-up
    planning call to update the visible task plan.
    """
    calls = {
        str(call.get("id") or ""): call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    revised = [dict(step) for step in plan]
    changed = False
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        call = calls.get(message.tool_call_id)
        if not call or call.get("name") == "update_plan":
            continue
        result = _parse_tool_result(str(message.content or ""))
        if not result.success:
            continue
        step_index = _matching_plan_step(
            revised,
            str(call.get("name") or ""),
            call.get("args") if isinstance(call.get("args"), dict) else {},
        )
        if step_index is not None and revised[step_index].get("status") != "done":
            revised[step_index]["status"] = "done"
            changed = True
    return revised, changed


def _matching_plan_step(
    plan: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    permit_read_only_argument_correction: bool = False,
) -> int | None:
    candidates = [
        (index, step)
        for index, step in enumerate(plan)
        if step.get("tool") == tool_name
        and step.get("status") in {"pending", "active"}
    ]
    for index, step in candidates:
        expected = step.get("arguments") or {}
        if (
            (expected or tool_name != "send_email")
            and all(
                arguments.get(key) == value for key, value in expected.items()
            )
        ):
            return index
        description = str(step.get("description") or "")
        planned_recipient = re.search(
            r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w-]+(?:\.[\w-]+)+",
            description,
        )
        if (
            tool_name == "send_email"
            and planned_recipient is not None
            and str(arguments.get("to") or "").casefold()
            == planned_recipient.group(0).casefold()
        ):
            return index
        candidate_urls = re.search(
            r"Candidate URLs:\s*(.+)$",
            description,
            flags=re.IGNORECASE,
        )
        if (
            permit_read_only_argument_correction
            and tool_name == "open_url"
            and "official contact page for" in description.casefold()
            and candidate_urls is not None
            and arguments.get("url") in {
                url.strip()
                for url in candidate_urls.group(1).split(";")
                if url.strip()
            }
        ):
            return index
    if (
        permit_read_only_argument_correction
        and len(candidates) == 1
        and _is_minor_argument_correction(
            candidates[0][1].get("arguments") or {},
            arguments,
        )
    ):
        return candidates[0][0]
    return None


def _is_minor_argument_correction(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> bool:
    differences = [
        (expected_value, actual.get(key))
        for key, expected_value in expected.items()
        if actual.get(key) != expected_value
    ]
    return (
        len(differences) == 1
        and isinstance(differences[0][0], str)
        and isinstance(differences[0][1], str)
        and SequenceMatcher(
            None,
            differences[0][0].casefold(),
            differences[0][1].casefold(),
        ).ratio() >= 0.9
    )


def _activate_plan_step(
    plan: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    permit_read_only_argument_correction: bool = False,
) -> tuple[list[dict[str, Any]], int | None, ToolResult | None]:
    """Activate a tool-backed plan step only after all dependencies finish."""
    target = _matching_plan_step(
        plan,
        tool_name,
        arguments,
        permit_read_only_argument_correction=(
            permit_read_only_argument_correction
        ),
    )
    if target is None:
        return plan, None, None
    revised = [dict(item) for item in plan]
    changed = True
    while changed:
        changed = False
        for step in revised:
            if (
                step.get("kind") == "reasoning"
                and step.get("status") in {"pending", "active"}
                and all(
                    revised[dependency].get("status") in {"done", "skipped"}
                    for dependency in step.get("dependencies", [])
                )
            ):
                step["status"] = "done"
                changed = True
    step = revised[target]
    unresolved = [
        dependency for dependency in step.get("dependencies", [])
        if revised[dependency].get("status") not in {"done", "skipped"}
    ]
    if unresolved:
        dependency_ids = [revised[index]["id"] for index in unresolved]
        return plan, None, ToolResult.fail(
            "DEPENDENCY_NOT_READY",
            "The plan step depends on unfinished prerequisites.",
            message=(
                "Do not run this step yet. Complete its listed prerequisites "
                "first, then retry it."
            ),
            data={"dependency_ids": dependency_ids},
        )
    if step.get("status") == "active":
        return revised, target, None
    revised[target]["status"] = "active"
    return revised, target, None


def _complete_plan_step(
    plan: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    step_index: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    target = step_index
    if target is None or target >= len(plan):
        target = _matching_plan_step(plan, tool_name, arguments)
    if target is None or plan[target].get("status") != "active":
        return plan, False
    revised = [dict(item) for item in plan]
    revised[target]["status"] = "done"
    return revised, True


_BENIGN_FAILURES = {
    "USER_REJECTED", "STOPPED", "MAX_STEPS", "TOOL_LIMIT", "SKIPPED",
    "PAUSED", "CREDENTIALS_NEEDED", "PLAN_REVISION_REQUIRED",
    "FAILED_STEP_UNCHANGED",
}


def _causes_replan(parsed: ToolResult) -> bool:
    """True when a tool failure should mark the active plan step failed."""
    return not parsed.success and (
        parsed.error is None or parsed.error.code not in _BENIGN_FAILURES
    )


def _fail_active_step(
    plan: list[dict[str, Any]],
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    step_index: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Mark the matching active (or first active) step as failed."""
    target = step_index
    if target is None and tool_name:
        target = _matching_plan_step(plan, tool_name, arguments or {})
    if target is None:
        target = next(
            (i for i, s in enumerate(plan) if s.get("status") == "active"),
            next((i for i, s in enumerate(plan) if s.get("status") == "pending"), -1),
        )
    if target < 0 or plan[target].get("status") == "failed":
        return plan, False
    revised = [dict(s) for s in plan]
    revised[target]["status"] = "failed"
    return revised, True


def _dedupe_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate citations by URL (research quality, spec Â§20â€“21)."""
    positions: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or "").strip()
        if not key or key not in positions:
            if key:
                positions[key] = len(out)
            out.append(item)
            continue
        existing = out[positions[key]]
        if item.get("source_type") == "page":
            out[positions[key]] = {**existing, **item}
        elif existing.get("source_type") != "page":
            out[positions[key]] = {
                **existing,
                **{name: value for name, value in item.items() if value},
            }
    return out


async def _sync_plan(task_id: str, plan: list[dict[str, Any]],
                     progress: int, current_step: int) -> None:
    """Mirror the model's plan into Task/TaskStep rows (spec Â§27/Â§28)."""
    if not task_id:
        return
    import hashlib
    import json

    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.task import Task, TaskStep
    from app.services.audit import redact

    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            rows = db.scalars(
                select(TaskStep).where(TaskStep.task_id == task_id)
            ).all()
            existing = {row.idx: row for row in rows}
            old_plan = task.plan if isinstance(task.plan, list) else []
            for idx, old_item in enumerate(old_plan):
                row = existing.get(idx)
                if (
                    row is not None
                    and row.plan_step_id is None
                    and isinstance(old_item, dict)
                ):
                    row.plan_step_id = str(old_item.get("id") or f"step_{idx + 1}")

            by_plan_id = {
                row.plan_step_id: row for row in rows if row.plan_step_id
            }
            selected: list[TaskStep] = []
            selected_ids: set[str] = set()
            for idx, item in enumerate(plan):
                model_step_id = str(item.get("id") or f"step_{idx + 1}")
                tool_id = item.get("tool") or item.get("name")
                arguments = (
                    redact(item.get("arguments"))
                    if item.get("arguments") is not None
                    else None
                )
                row = by_plan_id.get(model_step_id)
                if (
                    row is not None
                    and row.status in {"done", "failed", "skipped"}
                    and (
                        row.tool_id != tool_id
                        or row.arguments != arguments
                    )
                ):
                    payload = json.dumps(
                        {"tool": tool_id, "arguments": arguments},
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    )
                    model_step_id = (
                        f"{model_step_id}:{hashlib.sha256(payload.encode()).hexdigest()[:12]}"
                    )
                    row = by_plan_id.get(model_step_id)
                if row is None:
                    old_row = existing.get(idx)
                    if (
                        old_row is not None
                        and old_row.plan_step_id is None
                        and old_row.id not in selected_ids
                    ):
                        row = old_row
                        row.plan_step_id = model_step_id
                if row is None:
                    row = TaskStep(
                        task_id=task_id,
                        idx=max((item.idx for item in rows), default=-1) + idx + 1,
                        plan_step_id=model_step_id,
                        description=item.get("description", ""),
                        status=item.get("status", "pending"),
                        dependencies=item.get("dependencies", []),
                        tool_id=tool_id,
                        arguments=arguments,
                    )
                    db.add(row)
                    db.flush()
                    rows.append(row)
                    by_plan_id[model_step_id] = row
                else:
                    unchanged = (
                        row.tool_id == tool_id and row.arguments == arguments
                    )
                    row.description = item.get("description", row.description)
                    row.tool_id = tool_id
                    if arguments is not None:
                        row.arguments = arguments
                    row.dependencies = item.get("dependencies", [])
                    if row.status in {"done", "failed", "skipped"} and unchanged:
                        item["status"] = row.status
                    else:
                        row.status = item.get("status", "pending")
                        row.outcome_status = "planned"
                        row.error_code = None
                        row.completed_at = None
                selected.append(row)
                selected_ids.add(row.id)
                item["canonical_step_id"] = row.id

            offset = max((row.idx for row in rows), default=-1) + len(plan) + 1
            for row in rows:
                row.idx += offset
            db.flush()
            for idx, row in enumerate(selected):
                row.idx = idx

            historical = [row for row in rows if row.id not in selected_ids]
            for index, row in enumerate(historical, start=len(selected)):
                row.idx = index
                if row.status not in {"done", "failed", "skipped"}:
                    row.status = "skipped"
                    row.outcome_status = "skipped"

            for idx, (item, row) in enumerate(zip(plan, selected)):
                dependency_ids = [
                    selected[dependency].id
                    for dependency in item.get("dependencies", [])
                    if isinstance(dependency, int)
                    and 0 <= dependency < len(selected)
                ]
                item["canonical_step_id"] = row.id
                item["dependency_step_ids"] = dependency_ids
                row.dependency_step_ids = dependency_ids
                row.dependencies = item.get("dependencies", [])

            task.plan = plan
            task.progress = progress
            task.current_step = current_step
            db.commit()
    except Exception as exc:
        logger.exception("failed to persist task plan for task %s", task_id)
        raise RuntimeError(
            f"Could not persist the canonical execution plan for task {task_id}."
        ) from exc


def _extract_sources(tool_name: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if tool_name == "search_web":
        for r in data.get("results", []):
            out.append({"title": r.get("title", ""), "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                        "source_type": "search_result"})
    elif tool_name in ("open_url", "browse_page"):
        out.append({"title": data.get("title", ""), "url": data.get("url", ""),
                    "snippet": (data.get("text", "") or "")[:300],
                    "source_type": "page"})
    elif tool_name in ("browser_open", "browser_navigate", "browser_extract"):
        bstatus = data.get("browser") or {}
        out.append({
            "title": data.get("title", "") or bstatus.get("title", ""),
            "url": data.get("url", "") or bstatus.get("url", ""),
            "snippet": (data.get("text", "") or "")[:300],
            "source_type": "page",
        })
    return out


def _valid_research_sources(sources: list[dict[str, Any]]) -> bool:
    """Return whether the run has at least one usable HTTP(S) source."""
    for source in sources or []:
        url = str(source.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return True
    return False


def _credential_marker(args: dict[str, Any]) -> str:
    text = str(args.get("text") or "").strip().lower()
    if text in ("@credential:username", "@credential:email"):
        return "username"
    if text in ("@credential:password", "@credential:pass", "@credential:secret"):
        return "password"
    return ""


def _hostname(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _credential_domain_hint(state: AgentState, user_id: str) -> str:
    """Best-effort page domain for a credential fill approval: from the live
    browser session state in the graph, falling back to the session manager."""
    from app.services import browser_session

    url = (state.get("browser_session_state") or {}).get("url") or ""
    host = _hostname(url)
    if not host:
        session = browser_session.get_session(user_id)
        history = session.history if session is not None else None
        if history and session.index >= 0:  # type: ignore[union-attr]
            host = _hostname(str(history[session.index]))
    if host.startswith("www."):
        host = host[4:]
    return host


async def _create_approval(*, user_id: str, run_id: str, call_id: str,
                           tool_name: str, arguments: dict, level: int,
                           kind: str = "tool",
                           plan_step_id: str | None = None,
                           task_id: str | None = None,
                           task_step_index: int | None = None):
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.agent import AgentRun, Approval
    from app.models.task import Task, TaskStep
    from app.models.user import new_id
    from app.services.approval_lifecycle import initialize_approval
    from app.services.audit import log_activity

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        canonical_task_id = task_id or (run.task_id if run else None)
        task_step = None
        if canonical_task_id and task_step_index is not None:
            task_step = db.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == canonical_task_id,
                    TaskStep.idx == task_step_index,
                )
            )
        elif canonical_task_id:
            task_step = next(
                (
                    candidate
                    for candidate in db.scalars(
                        select(TaskStep)
                        .where(
                            TaskStep.task_id == canonical_task_id,
                            TaskStep.plan_step_id.is_(None),
                            TaskStep.tool_id == tool_name,
                        )
                        .order_by(TaskStep.idx.desc())
                    ).all()
                    if candidate.arguments == arguments
                ),
                None,
            )
        if canonical_task_id and task_step is None:
            last_index = db.scalar(
                select(TaskStep.idx)
                .where(TaskStep.task_id == canonical_task_id)
                .order_by(TaskStep.idx.desc())
                .limit(1)
            )
            task_step = TaskStep(
                task_id=canonical_task_id,
                idx=(last_index + 1) if last_index is not None else 0,
                description=f"Execute {tool_name}",
                status="active",
                tool_id=tool_name,
                capability=tool_name,
                arguments=arguments,
                action_id=new_id(),
                outcome_status="awaiting_approval",
            )
            db.add(task_step)
            db.flush()
        approval = Approval(
            id=new_id(),
            user_id=user_id, run_id=run_id, tool_name=tool_name,
            arguments={"call_id": call_id, **arguments}, level=level,
            status="pending", kind=kind, plan_step_id=plan_step_id,
            task_id=canonical_task_id,
            task_step_id=task_step.id if task_step else None,
        )
        if task_step is not None:
            task_step.outcome_status = "awaiting_approval"
            task_step.action_id = task_step.action_id or new_id()
            task_step.approval_id = approval.id
        initialize_approval(approval)
        db.add(approval)
        if run:
            run.status = "paused_approval"
        task = db.get(Task, canonical_task_id) if canonical_task_id else None
        if task:
            task.status = "waiting_for_approval"
        db.commit()
        db.refresh(approval)
        log_activity(
            db, user_id=user_id, run_id=run_id, agent="supervisor", tool=tool_name,
            arguments=arguments, result_summary="Approval requested", success=True,
            approval_status="pending",
            task_id=canonical_task_id,
            task_step_id=task_step.id if task_step else None,
            action_id=task_step.action_id if task_step else None,
            outcome_status="awaiting_approval",
        )
        db.expunge(approval)
        return approval


async def _audit_tool(*, user_id: str, run_id: str, call_id: str,
                      tool: str, args: dict,
                      result: ToolResult, duration_ms: int,
                      side_effect: str = "unknown",
                      task_id: str | None = None,
                      task_step_id: str | None = None,
                      action_id: str | None = None,
                      attempt_id: str | None = None,
                      attempt_number: int | None = None) -> None:
    from app.database.session import SessionLocal
    from app.services.audit import log_activity

    try:
        with SessionLocal() as db:
            meta: dict[str, Any] = (
                {"code": result.error.code} if result.error else {}
            )
            meta["call_id"] = call_id
            provider_name: str | None = None
            if (
                tool == "send_email"
                and result.success
                and isinstance(result.data, dict)
            ):
                receipt_fields = {
                    key: result.data[key]
                    for key in ("provider", "message_id", "thread_id", "status")
                    if isinstance(result.data.get(key), str)
                }
                if receipt_fields:
                    meta["provider_receipt"] = receipt_fields
                    provider_name = str(receipt_fields.get("provider") or "") or None
            log_activity(
                db, user_id=user_id, run_id=run_id, agent="supervisor", tool=tool,
                arguments=args, result_summary=result.message, success=result.success,
                duration_ms=duration_ms,
                meta=meta or None,
                provider=provider_name,
                error_code=result.error.code if result.error else None,
                task_id=task_id,
                task_step_id=task_step_id,
                action_id=action_id,
                attempt_id=attempt_id,
                outcome_status=_canonical_action_outcome(result, side_effect),
                attempt_number=attempt_number,
            )
    except Exception:  # noqa: BLE001 - audit must never break execution
        logger.exception("failed to audit tool %s", tool)


def _canonical_action_outcome(result: ToolResult, side_effect: str) -> str:
    if not result.success:
        if (
            side_effect != "read_only"
            and result.error is not None
            and result.error.code in {"TIMEOUT", "TOOL_EXCEPTION", "OUTCOME_UNKNOWN"}
        ):
            return "outcome_unknown"
        return "failed"
    data = result.data if isinstance(result.data, dict) else {}
    status_value = str(data.get("status") or "").lower()
    if status_value == "delivered":
        return "delivered"
    if status_value == "verified":
        return "verified"
    if data.get("provider") and status_value in {
        "accepted", "queued", "sent", "submitted",
    }:
        return "provider_accepted"
    return "succeeded"
