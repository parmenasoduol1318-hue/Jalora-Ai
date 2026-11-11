"""Registry-driven discovery of task-relevant capabilities."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.intent import (
    IntentResult,
    classify_intent,
    explicitly_requests_email_send,
    has_multiple_outreach_recipients,
)
from app.tools.base import PermissionLevel, registry, task_action_terms


_LOCAL_APP_OPEN_COMMAND = re.compile(
    r"^\s*(?:(?:hey\s+)?jalora[, ]+)?(?:please\s+)?"
    r"(?:open|launch|start)\s+(?:the\s+)?"
    r"(?P<app>[\w][\w .&'()-]{0,118}?)"
    r"(?:\s+app(?:lication)?)?[.!?]?\s*$",
    re.I,
)
_LOCAL_APP_CLOSE_COMMAND = re.compile(
    r"^\s*(?:(?:hey\s+)?jalora[, ]+)?(?:please\s+)?"
    r"(?:close|quit|exit)\s+(?:the\s+)?"
    r"(?P<app>[\w][\w .&'()-]{0,118}?)"
    r"(?:\s+app(?:lication)?)?[.!?]?\s*$",
    re.I,
)
_GENERIC_LOCAL_APP_COMMAND = re.compile(
    r"^\s*(?:please\s+)?(?:open|launch|start)\s+"
    r"(?:(?:any|all|an?|the)\s+)?apps?(?:lication)?[.!?]?\s*$",
    re.I,
)
_LOCAL_APP_ACTION = re.compile(
    r"\b(?:inspect|read|look at|check|click|type|press|close|launch|open)\b"
    r".{0,100}\b(?:app|application|window|screen)\b"
    r"|\b(?:app|application|window|screen)\b.{0,100}"
    r"\b(?:inspect|read|look at|check|click|type|press|close|launch|open)\b",
    re.I,
)
_REMOTE_DEVICE_REQUEST = re.compile(
    r"\b(?:on|to|using|through)\s+"
    r"(?:(?:my|the|another|paired|remote)\s+){0,2}"
    r"(?:phone|tablet|device|android|iphone|computer|laptop)\b"
    r".{0,180}\b(?:youtube|song|video|play|search|open|visit|browse|go to)\b"
    r"|"
    r"\b(?:open|visit|browse|go to|play|search)\b.{0,120}"
    r"\b(?:youtube|song|video|link|website|site|url)\b"
    r".{0,100}\b(?:on|to|using)\s+"
    r"(?:(?:my|the|another|paired|remote)\s+){0,2}"
    r"(?:phone|tablet|device|android|iphone|computer|laptop)\b",
    re.I,
)
_NON_APP_TARGETS = frozenset({
    "all apps", "an app", "any", "any app", "any application",
    "app", "application",
})
_FOLLOW_UP_ACTION = re.compile(
    r"\b(?:and|then)\s+"
    r"(?:browse|call|check|close|click|find|launch|open|play|read|reply|"
    r"save|search|send|start|translate|type|use|watch|delete|empty|follow|mark|"
    r"summarize|summarise)\b",
    re.I,
)


def local_app_open_target(text: str) -> str | None:
    """Return the app name for an exact, single-app launch request."""
    command = local_app_command(text)
    if command is None or command[0] != "open":
        return None
    return command[1]


def local_app_command(text: str) -> tuple[str, str] | None:
    """Return one exact launch or graceful-close request for a named app."""
    value = text or ""
    match = _LOCAL_APP_OPEN_COMMAND.fullmatch(value)
    action = "open"
    if match is None:
        match = _LOCAL_APP_CLOSE_COMMAND.fullmatch(value)
        action = "close"
    if match is None:
        return None
    app_name = re.sub(r"\s+", " ", match.group("app")).strip()
    if (
        not app_name
        or app_name.casefold() in _NON_APP_TARGETS
        or _FOLLOW_UP_ACTION.search(app_name)
    ):
        return None
    return action, app_name


def tool_names_for_intent(intent: str, request: str = "") -> list[str]:
    """Discover capabilities from request text, with intent as supporting context."""
    task = request or (
        "" if intent in {
            "chat", "research", "coding", "math", "science", "web_search",
            "email", "scheduling", "task",
        } else intent
    )
    signals = set(classify_intent(task).signals) if task else set()
    return registry.select_for_task(
        task, context=intent if task else "", signals=signals
    )


def voice_tool_names() -> list[str]:
    """Tool names bound during a live voice call."""
    return sorted({
        spec.name for spec in registry.all()
        if spec.always_available or "voice" in spec.tags
    })


def resolve_intent_hint(text: str, hint: str | None) -> str | None:
    """Use a shortcut intent only when the message lacks a clear specific intent."""
    if hint is None:
        return None
    result = classify_intent(text or "")
    if result.intent not in {"chat", "task"} or "image_generation" in result.signals:
        return None
    return hint


@dataclass(frozen=True)
class IntentRouting:
    """Intent metadata plus the discovered, bounded capability set."""

    intent: str
    signals: tuple[str, ...]
    tool_names: list[str] | None


def route_request(
    text: str,
    *,
    intent_override: str | None = None,
    capability_hint: str | None = None,
    extra_signals: tuple[str, ...] = (),
    context_text: str = "",
) -> IntentRouting:
    """Classify the request and retrieve relevant registered capabilities.

    The intent classifier supplies response/prompt metadata only. Capability
    binding uses the full request and live registry metadata, so multi-step
    requests can combine tools without falling back to every registered tool
    or maintaining a separate domain-to-tool allowlist.
    """
    request_text = text or ""
    result: IntentResult = classify_intent(request_text)
    app_command = local_app_command(request_text)
    if app_command is not None:
        tool_names = {
            spec.name for spec in registry.all()
            if spec.always_available and spec.permission == PermissionLevel.AUTO
        }
        action, app_name = app_command
        tool_name = "close_local_app" if action == "close" else (
            "open_youtube"
            if app_name.casefold() in {"youtube", "youtube.com"}
            else "open_local_app"
        )
        if registry.get(tool_name) is not None:
            tool_names.add(tool_name)
        return IntentRouting(
            intent="task",
            signals=tuple(dict.fromkeys(
                result.signals + extra_signals + ("local_app_control",)
            )),
            tool_names=sorted(tool_names),
        )
    context_result = classify_intent(context_text) if context_text else None
    active_signals = set(result.signals)
    if context_result:
        active_signals.update(context_result.signals)
    if _LOCAL_APP_ACTION.search(request_text):
        active_signals.add("local_app_control")
    if _REMOTE_DEVICE_REQUEST.search(request_text):
        active_signals.add("remote_device_control")
    selected_intent = intent_override or (
        "task"
        if "remote_device_control" in active_signals and result.intent == "chat"
        else context_result.intent
        if result.intent == "chat" and context_result
        else result.intent
    )
    if "public_contact_search" in active_signals:
        selected_intent = "research"
    discovery_request = "\n\n".join(filter(None, (
        request_text,
        context_text,
    )))
    multi_step = is_multi_step_request(discovery_request)
    if request_text.strip() and selected_intent in {"research", "web_search"}:
        discovery_request += "\nSearch the web"
    tool_names = registry.select_for_task(
        discovery_request,
        context=selected_intent if request_text.strip() else "",
        signals=active_signals,
    )
    if "remote_device_control" in active_signals:
        selected = {
            spec.name for spec in registry.all()
            if spec.always_available and spec.permission == PermissionLevel.AUTO
        }
        if registry.get("request_remote_url") is not None:
            selected.add("request_remote_url")
        tool_names = sorted(selected)
    if _GENERIC_LOCAL_APP_COMMAND.fullmatch(request_text):
        tool_names = [
            name for name in tool_names if name != "open_local_app"
        ]
    if "live_data_lookup" in active_signals:
        tool_names = sorted(set(tool_names) | {
            name for name in ("search_web", "open_url")
            if registry.get(name) is not None
        })
    if "regional_transit" in active_signals:
        tool_names = sorted(set(tool_names) | {
            name for name in ("search_web", "open_url")
            if registry.get(name) is not None
        })
    if "kenyan_curriculum" in active_signals:
        tool_names = sorted(set(tool_names) | {
            name for name in ("search_web", "open_url")
            if registry.get(name) is not None
        })
    if "bus_booking" in active_signals:
        selected = set(tool_names)
        selected.discard("search_web")
        selected.discard("open_url")
        selected.update(
            name for name in (
                "search_buses",
                "browser_open",
                "browser_wait",
                "browser_extract",
                "browser_click",
                "browser_select",
                "browser_fill",
            )
            if registry.get(name) is not None
        )
        tool_names = sorted(selected)
    if "biomedical_review" in result.signals:
        direct_request_tools = registry.select_for_task(
            request_text,
            context=selected_intent,
            signals=set(result.signals),
        )
        tool_names = sorted({
            name for name in tool_names
            if (spec := registry.get(name)) is not None
            and (
                (spec.always_available and spec.permission == PermissionLevel.AUTO)
                or (
                    spec.category == "research"
                    and {"evidence", "source", "browser"}.intersection(spec.tags)
                )
            )
        } | {
            name for name in direct_request_tools
            if (spec := registry.get(name)) is not None
            and spec.category != "research"
            and not (
                spec.always_available
                and spec.permission == PermissionLevel.AUTO
            )
        } | {
            name for name in ("search_web", "open_url")
            if registry.get(name) is not None
        })
    if "public_contact_search" in active_signals:
        selected = set(tool_names)
        selected.discard("search_email")
        selected.discard("read_email")
        selected.discard("search_companies")
        selected.discard("make_call")
        selected.discard("send_email_batch")
        selected.discard("compose_email_batch")
        selected.update(
            name for name in (
                "search_web",
                "search_public_contacts",
                "open_url",
                "compose_email",
            )
            if registry.get(name) is not None
        )
        if explicitly_requests_email_send(discovery_request):
            selected.add("send_email")
        tool_names = sorted(selected)
    if (
        has_multiple_outreach_recipients(request_text)
        and "public_contact_search" not in active_signals
        and registry.get("compose_email_batch") is not None
    ):
        tool_names = sorted(set(tool_names) | {"compose_email_batch"})
    if (
        has_multiple_outreach_recipients(discovery_request)
        and explicitly_requests_email_send(discovery_request)
        and "public_contact_search" not in active_signals
        and registry.get("send_email_batch") is not None
    ):
        tool_names = sorted(set(tool_names) | {"send_email_batch"})
    if capability_hint:
        category_tools = {
            spec.name for spec in registry.all()
            if spec.category == capability_hint
        }
        if not category_tools:
            raise ValueError(f"Unknown capability category: {capability_hint}")
        tool_names = sorted(set(tool_names or []) | category_tools)
    if "attached_document" in extra_signals and registry.get("read_document"):
        tool_names = sorted(set(tool_names) | {"read_document"})
    return IntentRouting(
        intent=selected_intent,
        signals=tuple(dict.fromkeys(
            result.signals
            + (context_result.signals if context_result else ())
            + extra_signals
            + tuple(sorted(active_signals))
            + (("multi_step",) if multi_step else ())
        )),
        tool_names=tool_names,
    )


_MULTI_STEP_SEPARATORS = re.compile(
    r"\b(?:and then|then|after that|afterwards|finally|and also|and)\b"
    r"|[;\n]",
    re.I,
)


def is_multi_step_request(text: str) -> bool:
    """Detect multiple task-capability clauses, not compound wording alone."""
    signals = set(classify_intent(text or "").signals)
    if (
        "biomedical_review" in signals
        and not re.search(
            r"\b(?:step\s+\d+|step[- ]by[- ]step|and then|after that|"
            r"afterwards|finally|create|generate|write|draft|prepare|"
            r"schedule|email|send)\b",
            text or "",
            re.I,
        )
    ):
        return False
    clauses = (clause.strip() for clause in _MULTI_STEP_SEPARATORS.split(text or ""))
    action_terms = task_action_terms()
    actionable_clauses: list[set[str]] = []
    clause_action_counts: list[int] = []
    for clause in clauses:
        if not clause:
            continue
        action_text = re.sub(
            r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", " ", clause, flags=re.I
        )
        verbs = set(re.findall(r"[a-z]+", action_text.lower())) & action_terms
        action_tools = {
            name
            for name in registry.select_for_task(clause)
            if (spec := registry.get(name)) is not None
            and name != "update_plan"
            and not (
                spec.always_available
                and spec.permission == PermissionLevel.AUTO
            )
        }
        if verbs or action_tools:
            actionable_clauses.append(action_tools)
            clause_action_counts.append(len(verbs))

    return (
        len(actionable_clauses) > 1
        or any(action_count > 1 for action_count in clause_action_counts)
    )
