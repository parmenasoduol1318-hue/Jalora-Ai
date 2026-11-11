"""Tool registry with permission-gated, request-aware capability discovery."""
from __future__ import annotations

import functools
import inspect
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Literal

from langchain_core.tools import BaseTool, StructuredTool

from app.errors import ToolResult

logger = logging.getLogger("jalora.tools")

SIDE_EFFECT_LEVELS = {
    "read_only", "reversible", "external", "irreversible", "unknown",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SHORT_TOKENS = frozenset({
    "ai", "cv", "hr", "js", "py", "qa", "sms", "ui", "ux",
})
_GENERIC_ACTION_TERMS = frozenset({
    "add", "cancel", "create", "delete", "edit", "find", "get", "generate",
    "list", "make", "open", "prepare", "read", "research", "run", "save", "schedule", "search",
    "send", "update", "upload", "write", "draft", "summarize", "inspect",
    "analyze", "compare", "review", "store", "export", "import", "book",
    "summarise",
    "call", "notify", "translate", "convert", "calculate", "verify",
    "select", "navigate", "scroll", "click", "back", "close", "screenshot",
    "download", "fill", "extract", "package", "browse", "plot", "forward",
    "compose", "type",
})
_ACTION_ALIASES = {
    "add": frozenset({"add", "create", "save"}),
    "browse": frozenset({"browse", "open", "render", "view"}),
    "calculate": frozenset({"calculate", "compute", "solve"}),
    "call": frozenset({"call", "dial", "phone"}),
    "compose": frozenset({"compose", "draft", "write"}),
    "create": frozenset({
        "create", "make", "write", "generate", "draft", "schedule", "remind",
        "package", "convert", "turn",
    }),
    "delete": frozenset({"delete", "remove", "forget", "erase"}),
    "forward": frozenset({"forward", "send"}),
    "generate": frozenset({"generate", "create", "make", "write", "prepare"}),
    "get": frozenset({"get", "find", "check", "what"}),
    "search": frozenset({"search", "find", "look", "research"}),
    "find": frozenset({"find", "search", "look", "check"}),
    "edit": frozenset({"edit", "change", "modify", "update", "write"}),
    "fill": frozenset({"fill", "enter", "type", "login", "log", "sign"}),
    "navigate": frozenset({
        "navigate", "open", "visit", "go", "load", "login", "log", "sign",
    }),
    "schedule": frozenset({"schedule", "create", "book", "remind"}),
    "remind": frozenset({"remind", "schedule", "notify"}),
    "read": frozenset({
        "read", "open", "review", "check", "summarize", "summarise",
    }),
    "open": frozenset({
        "open", "read", "visit", "navigate", "login", "log", "sign", "show",
        "display",
    }),
    "send": frozenset({"send", "email", "text", "message"}),
    "store": frozenset({"store", "remember", "save"}),
    "package": frozenset({"package", "create", "generate", "export"}),
    "summarize": frozenset({"summarize", "summarise", "summary"}),
    "inspect": frozenset({"inspect", "check", "analyze"}),
    "plot": frozenset({
        "plot", "graph", "visualize", "show", "shown", "display", "present",
        "illustrate",
    }),
    "screenshot": frozenset({
        "screenshot", "capture", "snapshot", "show", "shown", "display", "view",
    }),
    "type": frozenset({"type", "fill", "enter"}),
}
_CLAUSE_ACTION_TERMS = frozenset(
    _GENERIC_ACTION_TERMS
    | {
        alias
        for aliases in _ACTION_ALIASES.values()
        for alias in aliases
    }
)
_NEGATED_ACTION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|no\s+need\s+to|not\s+to|without)\b"
    r"(?:\s+\w+){0,3}\s+(?P<action>[a-z][a-z0-9_-]*)\b",
    re.I,
)
_GENERIC_TERMS = frozenset({
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "before", "between", "by", "can", "could", "do", "does",
    "for", "from", "give", "how", "i", "in", "into", "is", "it",
    "its", "me", "my", "of", "on", "or", "please", "should", "that", "the",
    "their", "them", "then", "there", "these", "this", "those", "through",
    "to", "up", "use", "using", "want", "we", "what", "when", "where",
    "which", "who", "will", "with", "would", "you", "your",
})


def _capability_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _GENERIC_TERMS or token.isdecimal():
            continue
        if token == "reminder":
            token = "remind"
        if len(token) > 5 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 5 and token.endswith(("ing", "ed")):
            token = token[:-3]
        elif len(token) > 6 and token.endswith("ment"):
            token = token[:-4]
        elif (
            len(token) > 2
            and token.endswith("s")
            and token not in _SHORT_TOKENS
            and not token.endswith("ss")
        ):
            token = token[:-1]
        if len(token) > 2 or token in _SHORT_TOKENS:
            tokens.append(token)
    return tokens


def _safe_always_available(spec: "ToolSpec") -> bool:
    return spec.always_available and spec.permission == PermissionLevel.AUTO


class PermissionLevel(IntEnum):
    AUTO = 1        # run without asking (search, read, summarize)
    NOTIFY = 2      # run, but surface in activity feed (store memory, reminders)
    CONFIRM = 3     # require explicit user approval (send email/SMS/call/book)
    STRICT = 4      # require approval + extra scrutiny (payments, deletion)


@dataclass
class ToolSpec:
    name: str
    tool: BaseTool
    permission: PermissionLevel
    category: str
    description: str = ""
    requires_internet: bool = False
    tags: list[str] = field(default_factory=list)
    side_effect: Literal[
        "read_only", "reversible", "external", "irreversible", "unknown"
    ] = "unknown"
    preconditions: list[str] = field(default_factory=list)
    idempotent: bool | None = None
    retryable: bool | None = None
    verification_tool: str | None = None
    compensation_tool: str | None = None
    required_signals: list[str] = field(default_factory=list)
    default_for_actions: list[str] = field(default_factory=list)
    always_available: bool = False


def _tool_operation(spec: ToolSpec) -> str | None:
    return next(
        (term for term in reversed(
            _capability_tokens(spec.name.replace("_", " "))
        ) if term in _GENERIC_ACTION_TERMS),
        None,
    )


def _tool_scope_terms(spec: ToolSpec) -> set[str]:
    return (
        set(_capability_tokens(spec.name.replace("_", " ")))
        | set(_capability_tokens(" ".join(spec.tags)))
    ) - _GENERIC_ACTION_TERMS


def _tool_matches_clause(
    spec: ToolSpec,
    clause: Counter[str],
    context_terms: set[str],
    raw_clause: str = "",
) -> bool:
    operation = _tool_operation(spec)
    capability_terms = _tool_scope_terms(spec)
    if operation is None:
        return bool(clause.keys() & capability_terms)
    requested_actions = _ACTION_ALIASES.get(operation, frozenset({operation}))
    if raw_clause:
        negated_actions = {
            match.group("action").lower()
            for match in _NEGATED_ACTION.finditer(raw_clause)
        }
        if requested_actions.intersection(negated_actions):
            return False
    matched_actions = clause.keys() & requested_actions
    if operation == "send" and "send" not in clause and matched_actions & {
        "email", "text", "message",
    }:
        # These words also commonly name content or channels. Treat them as
        # send actions only when phrased as a command, not as nouns ("find emails").
        imperative = re.search(
            r"(?:^\s*|[,;]\s*|\band\s+)"
            r"(?:please\s+|could you\s+|can you\s+|would you\s+)?"
            r"(?:email|text|message)\b",
            raw_clause,
            re.I,
        )
        if not imperative:
            matched_actions = matched_actions - {"email", "text", "message"}
    return bool(
        matched_actions
        and (
            not capability_terms
            or (clause.keys() | context_terms) & capability_terms
            or (
                spec.category == "coding"
                and "file" in clause
                and operation == "edit"
            )
            or operation in {"calculate", "verify"}
        )
    )


def task_action_terms() -> frozenset[str]:
    """Generic action vocabulary used for multi-step detection."""
    return frozenset(_GENERIC_ACTION_TERMS - {"back"})


def _serialize_result(result: Any) -> str:
    if isinstance(result, ToolResult):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return ToolResult.ok("ok", {"value": result}).model_dump_json()
    return ToolResult.ok(str(result)).model_dump_json()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        func: Callable[..., Any] | Callable[..., Awaitable[Any]],
        *,
        permission: PermissionLevel = PermissionLevel.AUTO,
        category: str = "general",
        requires_internet: bool = False,
        tags: list[str] | None = None,
        side_effect: Literal[
            "read_only", "reversible", "external", "irreversible", "unknown"
        ] = "unknown",
        preconditions: list[str] | None = None,
        idempotent: bool | None = None,
        retryable: bool | None = None,
        verification_tool: str | None = None,
        compensation_tool: str | None = None,
        required_signals: list[str] | None = None,
        default_for_actions: list[str] | None = None,
        always_available: bool = False,
    ) -> BaseTool:
        name = func.__name__
        if side_effect not in SIDE_EFFECT_LEVELS:
            raise ValueError(f"Unsupported side-effect classification: {side_effect}")
        declared_preconditions = list(preconditions or [])
        if any(not isinstance(value, str) or not value.strip()
               for value in declared_preconditions):
            raise ValueError("Capability preconditions must be non-empty strings.")
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def _async_wrapper(**kwargs: Any) -> str:
            try:
                result = await func(**kwargs)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - tools must fail structurally
                logger.exception("tool %s crashed", name)
                result = ToolResult.fail(
                    "TOOL_EXCEPTION", f"{type(exc).__name__}: {exc}",
                    message=f"Tool {name} failed unexpectedly.",
                )
            return _serialize_result(result)

        @functools.wraps(func)
        def _sync_wrapper(**kwargs: Any) -> str:
            try:
                result = func(**kwargs)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001
                logger.exception("tool %s crashed", name)
                result = ToolResult.fail(
                    "TOOL_EXCEPTION", f"{type(exc).__name__}: {exc}",
                    message=f"Tool {name} failed unexpectedly.",
                )
            return _serialize_result(result)

        if is_async:
            structured = StructuredTool.from_function(
                coroutine=_async_wrapper, name=name, description=(func.__doc__ or "").strip()
            )
        else:
            structured = StructuredTool.from_function(
                func=_sync_wrapper, name=name, description=(func.__doc__ or "").strip()
            )

        spec = ToolSpec(
            name=name,
            tool=structured,
            permission=permission,
            category=category,
            description=(func.__doc__ or "").strip(),
            requires_internet=requires_internet,
            tags=list(tags or []),
            side_effect=side_effect,
            preconditions=declared_preconditions,
            idempotent=idempotent,
            retryable=retryable,
            verification_tool=verification_tool,
            compensation_tool=compensation_tool,
            required_signals=list(required_signals or []),
            default_for_actions=list(default_for_actions or []),
            always_available=always_available,
        )
        self._tools[name] = spec
        return structured

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def langchain_tools(self) -> list[BaseTool]:
        return [spec.tool for spec in self._tools.values()]

    def select_for_task(
        self,
        task: str,
        *,
        context: str = "",
        max_tools: int = 16,
        minimum_relevance: float = 0.08,
        signals: set[str] | None = None,
    ) -> list[str]:
        """Rank registered capabilities by their declared metadata and schemas.

        This is a generic lexical retrieval boundary, not a domain-to-tool
        mapping. Capability descriptions, tags, and argument schemas are the
        searchable source of truth; always-available infrastructure tools and
        source readers related to selected web-discovery tools are included
        independently of the task query.
        """
        active_signals = signals or set()
        context_terms = set(_capability_tokens(context))
        candidates = [
            spec for spec in self._tools.values()
            if not _safe_always_available(spec)
            and set(spec.required_signals) <= active_signals
        ]
        searchable_task = re.sub(r"""(["'“”‘’]).*?\1""", " ", task or "")
        searchable_task = re.sub(
            r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", " ", searchable_task, flags=re.I
        )
        searchable_task = re.sub(r"https?://\S+", " ", searchable_task, flags=re.I)
        action_pattern = "|".join(
            re.escape(term)
            for term in sorted(
                _CLAUSE_ACTION_TERMS, key=lambda term: (-len(term), term)
            )
        )
        clause_texts = [
            clause for clause in re.split(
                r"[.!?;:\n]+|\b(?:and then|then|after that|finally|and also)\b"
                rf"|\band(?=\s+(?:(?:if|when|once)\s+\w+\s+)?"
                rf"(?:{action_pattern})\b)",
                searchable_task,
                flags=re.I,
            ) if clause.strip()
        ]
        clauses: list[tuple[Counter[str], str]] = []
        for clause in clause_texts:
            terms = Counter(_capability_tokens(clause))
            if (
                re.search(r"\b[\w-]+\.[a-z0-9]{1,10}\b", clause, re.I)
                and "@" not in clause
                and "://" not in clause
            ):
                terms["file"] += 1
            clauses.append((terms, clause))
        query = Counter(_capability_tokens(searchable_task))
        query.update({
            token: count * 0.25
            for token, count in Counter(_capability_tokens(context)).items()
        })
        if not candidates or not query:
            return sorted(
                spec.name for spec in self._tools.values() if _safe_always_available(spec)
            )

        documents: dict[str, tuple[Counter[str], Counter[str]]] = {}
        metadata_frequency: Counter[str] = Counter()
        detail_frequency: Counter[str] = Counter()
        for spec in candidates:
            schema = spec.tool.args_schema
            schema_text = ""
            if schema is not None:
                schema_text = json.dumps(schema.model_json_schema(), sort_keys=True)
            metadata = " ".join((
                spec.name.replace("_", " "),
                spec.category,
                " ".join(spec.tags),
                spec.permission.name,
                spec.side_effect,
            ))
            details = " ".join((
                spec.description,
                " ".join(spec.preconditions),
                "internet required" if spec.requires_internet else "",
                schema_text,
            ))
            metadata_terms = Counter(_capability_tokens(metadata))
            detail_terms = Counter(_capability_tokens(details))
            documents[spec.name] = (metadata_terms, detail_terms)
            metadata_frequency.update(metadata_terms.keys())
            detail_frequency.update(detail_terms.keys())

        total = len(candidates)
        average_metadata_length = max(
            1.0,
            sum(
                sum(metadata.values())
                for metadata, _ in documents.values()
            ) / total,
        )
        average_detail_length = max(
            1.0,
            sum(
                sum(details.values())
                for _, details in documents.values()
            ) / total,
        )
        ranked: list[tuple[float, str]] = []
        for spec in candidates:
            operation = _tool_operation(spec)
            if operation:
                requested_actions = _ACTION_ALIASES.get(operation, frozenset({operation}))
                scoped_clauses = [
                    clause for clause, raw_clause in clauses
                    if _tool_matches_clause(
                        spec, clause, context_terms, raw_clause
                    )
                ]
                default_action_clauses = [
                    clause for clause, _raw_clause in clauses
                    if clause.keys() & requested_actions
                    and set(spec.default_for_actions) & requested_actions
                ]
                has_specific_match = any(
                    other.name != spec.name
                    and not other.default_for_actions
                    and any(
                        _tool_matches_clause(other, clause, context_terms)
                        for clause in default_action_clauses
                    )
                    for other in candidates
                )
                if not scoped_clauses and not (
                    default_action_clauses and not has_specific_match
                ):
                    continue
            elif not any(
                _tool_matches_clause(spec, clause, context_terms, raw_clause)
                for clause, raw_clause in clauses
            ):
                continue
            metadata_terms, detail_terms = documents[spec.name]
            metadata_length = sum(metadata_terms.values())
            detail_length = sum(detail_terms.values())
            score = 0.0
            for token, query_count in query.items():
                query_weight = min(query_count, 2)
                for terms, frequency_map, length, average_length, weight in (
                    (metadata_terms, metadata_frequency, metadata_length,
                     average_metadata_length, 1.0),
                    (detail_terms, detail_frequency, detail_length,
                     average_detail_length, 0.25),
                ):
                    frequency = terms.get(token, 0)
                    if not frequency:
                        continue
                    inverse_frequency = math.log(
                        1 + (total - frequency_map[token] + 0.5)
                        / (frequency_map[token] + 0.5)
                    )
                    saturation = frequency * 2.2 / (
                        frequency + 1.2 * (0.25 + 0.75 * length / average_length)
                    )
                    score += weight * inverse_frequency * saturation * query_weight
            if score >= minimum_relevance:
                ranked.append((score, spec.name))

        selected = {
            name for _, name in sorted(ranked, key=lambda item: (-item[0], item[1]))[:max_tools]
        }
        pending = list(selected)
        while pending:
            spec = self._tools[pending.pop()]
            verifier = spec.verification_tool
            if verifier and verifier in self._tools and verifier not in selected:
                selected.add(verifier)
                pending.append(verifier)
        selected_web_discovery = any(
            self._tools[name].category == "research"
            and "web" in self._tools[name].tags
            and not {"source", "browser"}.intersection(self._tools[name].tags)
            for name in selected
        )
        if selected_web_discovery:
            selected.update(
                spec.name for spec in candidates
                if spec.category == "research"
                and "web" in spec.tags
                and {"source", "browser"}.intersection(spec.tags)
            )
        selected.update(
            spec.name for spec in candidates
            if spec.required_signals
        )
        selected.update(
            spec.name for spec in self._tools.values() if _safe_always_available(spec)
        )
        return sorted(selected)

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "category": spec.category,
                "permission": int(spec.permission),
                "description": spec.description,
                "requires_internet": spec.requires_internet,
                "tags": list(spec.tags),
                "input_schema": (
                    spec.tool.args_schema.model_json_schema()
                    if spec.tool.args_schema is not None
                    else {}
                ),
                "output_schema": ToolResult.model_json_schema(),
                "side_effect": spec.side_effect,
                "preconditions": list(spec.preconditions),
                "idempotent": spec.idempotent,
                "retryable": spec.retryable,
                "verification_tool": spec.verification_tool,
                "compensation_tool": spec.compensation_tool,
                "required_signals": list(spec.required_signals),
                "default_for_actions": list(spec.default_for_actions),
                "always_available": spec.always_available,
            }
            for spec in sorted(self._tools.values(), key=lambda s: s.name)
        ]


registry = ToolRegistry()
