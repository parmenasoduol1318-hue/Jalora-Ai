"""General task state machine and agent registry (spec §27, §28, §49).

Provides a reusable, provider-agnostic execution platform for all Jalora
action tasks — email sends, calendar events, reminders, document operations,
browser automation, and any future action type.

The task lifecycle state machine:

    PENDING ──► RUNNING ──► PAUSED_APPROVAL
       │         │             │
       │         │        ┌────► REJECTED ──► CANCELLED
       │         │        └────► APPROVED ──► RUNNING ──► COMPLETED
       │         │                    │
       │         └────────► FAILED
       └────────────────► CANCELLED

State mapping to the ``Task`` model (app/models/task.py):
    PENDING            → "queued"
    RUNNING            → "running"
    PAUSED_APPROVAL    → "waiting_for_approval"
    APPROVED           → "running"  (transient — immediately resumes)
    COMPLETED          → "completed"
    FAILED             → "failed"
    STOPPED            → "stopped"
    CANCELLED          → "cancelled"

Agents are dispatched by task/action type via :class:`AgentRegistry`.
Each registered agent implements :class:`AgentHandler` — a small interface
that knows how to propose steps, execute them, and resume after approval.

Note on imports: ``app.tools.base`` and ``app.services.policy`` are imported
lazily inside functions because ``app.tools.__init__`` transitively imports
``chat_service`` which imports ``policy``, creating a cycle when loaded in
isolation. The conftest / main module sets these up in the correct order for
tests and production; lazy imports keep this module safe to import standalone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import ToolResult
from app.models.agent import AgentRun, Approval
from app.models.task import Task, TaskStep
from app.services.run_registry import (
    is_paused,
    is_stopped,
    register_active,
    unregister_active,
)

logger = logging.getLogger("jalora.task_engine")

__all__ = [
    "TaskStatus",
    "TaskStateError",
    "TaskStateMachine",
    "AgentSpec",
    "AgentHandler",
    "AgentRegistry",
    "TaskEngine",
    "TaskResult",
    "JobAgentHandler",
    "registry",
    "engine",
]


# --------------------------------------------------------------------------- #
# 1. State machine (§49)                                                      #
# --------------------------------------------------------------------------- #

class TaskStatus(str, Enum):
    """Canonical task lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED_APPROVAL = "paused_approval"
    APPROVED = "approved"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"

    # Terminal states — once entered, no further transitions are allowed.
    TERMINAL = frozenset({
        "completed", "failed", "stopped", "cancelled",
    })

    def is_terminal(self) -> bool:
        return self.value in TaskStatus.TERMINAL


# Valid state transitions: source → set of allowed targets.
_VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({
        TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED,
    }),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.PAUSED_APPROVAL, TaskStatus.STOPPED,
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.PAUSED_APPROVAL: frozenset({
        TaskStatus.APPROVED, TaskStatus.CANCELLED, TaskStatus.STOPPED,
    }),
    TaskStatus.APPROVED: frozenset({TaskStatus.RUNNING, TaskStatus.STOPPED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.STOPPED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

# Map Task model statuses (as used by the DB / API) → TaskStatus.
_DB_STATUS_MAP: dict[str, TaskStatus] = {
    "queued": TaskStatus.PENDING,
    "running": TaskStatus.RUNNING,
    "waiting_for_approval": TaskStatus.PAUSED_APPROVAL,
    "paused": TaskStatus.PAUSED_APPROVAL,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "stopped": TaskStatus.STOPPED,
    "cancelled": TaskStatus.CANCELLED,
}

# Reverse map: TaskStatus → Task model status string.
_DB_STATUS_REVERSE: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "queued",
    TaskStatus.RUNNING: "running",
    TaskStatus.PAUSED_APPROVAL: "waiting_for_approval",
    TaskStatus.APPROVED: "running",
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED: "failed",
    TaskStatus.STOPPED: "stopped",
    TaskStatus.CANCELLED: "cancelled",
}


class TaskStateError(Exception):
    """Raised when a state transition is invalid."""


def _coerce_status(value: TaskStatus | str) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError:
        mapped = _DB_STATUS_MAP.get(value)
        if mapped is not None:
            return mapped
        raise TaskStateError(f"Unknown task status: {value!r}")


class TaskStateMachine:
    """Validates and tracks task state transitions.

    Usage::

        sm = TaskStateMachine(TaskStatus.PENDING)
        sm.can_transition_to(TaskStatus.RUNNING)   # True
        sm.transition(TaskStatus.RUNNING)           # advances state

    The machine is *reusable*: after an approval resolves it can transition
    PAUSED_APPROVAL → APPROVED → RUNNING without creating a new machine.
    """

    __slots__ = ("_state",)

    def __init__(self, initial: TaskStatus | str = TaskStatus.PENDING) -> None:
        self._state: TaskStatus = _coerce_status(initial)

    @property
    def state(self) -> TaskStatus:
        return self._state

    def can_transition_to(self, target: TaskStatus | str) -> bool:
        """Return True if ``target`` is a valid next state from current."""
        t = _coerce_status(target)
        if self._state in TaskStatus.TERMINAL:
            return False
        return t in _VALID_TRANSITIONS.get(self._state, frozenset())

    def transition(self, target: TaskStatus | str) -> TaskStatus:
        """Advance to ``target`` if valid; returns the new state.

        Raises :class:`TaskStateError` on invalid transitions.
        """
        t = _coerce_status(target)
        if not self.can_transition_to(t):
            allowed = [s.value for s in _VALID_TRANSITIONS.get(self._state, frozenset())]
            raise TaskStateError(
                f"Invalid transition {self._state.value} → {t.value}. "
                f"Allowed: {allowed}"
            )
        self._state = t
        return self._state

    def to_db_status(self) -> str:
        """Convert to the Task model status string used in the database."""
        return _DB_STATUS_REVERSE.get(self._state, self._state.value)

    @classmethod
    def from_db_status(cls, db_status: str) -> "TaskStateMachine":
        """Build a machine from a persisted Task.status string."""
        return cls(_DB_STATUS_MAP.get(db_status, TaskStatus.PENDING))


# --------------------------------------------------------------------------- #
# 2. Permission level (lazy import to avoid circular dependency)              #
# --------------------------------------------------------------------------- #

_PERMISSION_LABELS = {1: "AUTO", 2: "NOTIFY", 3: "CONFIRM", 4: "STRICT"}


def _get_permission_level() -> Any:
    """Lazily import PermissionLevel from app.tools.base."""
    from app.tools.base import PermissionLevel
    return PermissionLevel


CONFIRM_LEVEL = 3  # PermissionLevel.CONFIRM — value, not the enum, for lazy access


# --------------------------------------------------------------------------- #
# 3. Agent registry (§23, §24)                                                #
# --------------------------------------------------------------------------- #

class AgentHandler(Protocol):
    """Interface every registered action-agent must satisfy.

    A handler is a thin adapter that knows how to:

    * **propose** a plan (list of ``{description, tool, arguments}`` dicts)
      for a given natural-language request, so the UI can show live progress;
    * **execute** a single step, returning a :class:`ToolResult`;
    * **resume** after an approval is granted (the approval payload is passed
      back so the handler can re-issue the gated call).

    Handlers may be synchronous or async.
    """

    def propose(self, *, user_id: str, request: str, **kwargs: Any) -> list[dict[str, Any]]:
        ...

    async def execute_step(
        self, *, user_id: str, step: dict[str, Any], **kwargs: Any
    ) -> ToolResult:
        ...

    async def resume(
        self, *, user_id: str, step: dict[str, Any], approval_result: dict[str, Any],
    ) -> ToolResult:
        ...


@dataclass
class AgentSpec:
    """Registry entry for a single action-agent."""

    name: str
    action_type: str
    description: str = ""
    permission: int = 1  # AUTO by default; matches PermissionLevel.AUTO
    requires_internet: bool = False
    handler: AgentHandler | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def requires_approval(self) -> bool:
        """True when the policy engine will gate this agent's action tools."""
        return self.permission >= CONFIRM_LEVEL


class AgentRegistry:
    """Dispatches agents by task/action type.

    Registration is keyed by ``action_type`` (e.g. ``"email"``, ``"calendar"``,
    ``"reminders"``). :meth:`resolve` returns the best-matching spec for a
    given request — preferring exact action-type matches, then signal-based
    fallbacks.
    """

    def __init__(self) -> None:
        self._by_type: dict[str, AgentSpec] = {}
        self._by_signal: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        """Register an agent. Later registrations for the same action_type win."""
        self._by_type[spec.action_type] = spec
        for tag in spec.tags:
            self._by_signal.setdefault(tag, spec)

    def get(self, action_type: str) -> AgentSpec | None:
        return self._by_type.get(action_type)

    def all(self) -> list[AgentSpec]:
        return list(self._by_type.values())

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "action_type": s.action_type,
                "permission": s.permission,
                "permission_label": _PERMISSION_LABELS.get(s.permission, str(s.permission)),
                "description": s.description,
                "requires_internet": s.requires_internet,
                "requires_approval": s.requires_approval,
                "tags": s.tags,
            }
            for s in sorted(self._by_type.values(), key=lambda s: s.action_type)
        ]

    def resolve(self, *, intent: str, signals: tuple[str, ...]) -> AgentSpec | None:
        """Find the best agent for an intent/signals pair.

        ``intent`` comes from :func:`app.agents.intent.classify_intent`;
        ``signals`` are the matched keywords. Returns the spec or ``None``
        for informational/chat intents that have no dedicated agent.
        """
        intent_map: dict[str, str] = {
            "scheduling": "calendar",
            "task": "email",
            "research": "jobs",
        }
        action_type = intent_map.get(intent)

        # Signal-based overrides take priority.
        for sig in reversed(signals):
            sig_lower = sig.lower()
            if "reminder" in sig_lower:
                action_type = "reminders"
            elif "browser" in sig_lower or "web" in sig_lower:
                action_type = "browser"
            elif "document" in sig_lower:
                action_type = "documents"
            elif any(k in sig_lower for k in ("job", "cv", "resume", "vacancy")):
                action_type = "jobs"

        # Check signal-based registry first for finer control.
        for sig in reversed(signals):
            if sig.lower() in self._by_signal:
                return self._by_signal[sig.lower()]

        if action_type is not None:
            return self._by_type.get(action_type)

        return None


# --------------------------------------------------------------------------- #
# 4. Email agent handler — concrete adapter for email tasks                    #
# --------------------------------------------------------------------------- #

class EmailAgentHandler:
    """Adapter that bridges :class:`AgentRegistry` to the email service/tools.

    Delegates to :mod:`app.services.email` for draft generation and sending,
    and reuses the existing, tested email code. This keeps the state machine
    generic while reusing proven email logic.
    """

    action_type = "email"

    def propose(self, *, user_id: str, request: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Build a two-step plan: generate draft, then send (approval-gated)."""
        from app.agents.intent import extract_email_address
        from app.services import email as email_service

        recipient = kwargs.get("recipient") or extract_email_address(request) or ""
        draft = email_service.generate_email_draft(recipient, request)
        return [
            {
                "description": "Generate email draft",
                "tool": "compose_email",
                "arguments": {
                    "to": draft["to"], "subject": draft["subject"], "body": draft["body"],
                    "regards": draft["regards"],
                },
            },
            {
                "description": "Send email (requires approval)",
                "tool": "send_email",
                "arguments": {
                    "to": draft["to"], "subject": draft["subject"], "body": draft["body"],
                    "regards": draft["regards"],
                },
            },
        ]

    async def execute_step(
        self, *, user_id: str, step: dict[str, Any], **kwargs: Any
    ) -> ToolResult:
        from app.services import email as email_service

        tool_name = step.get("tool", "")
        args = step.get("arguments", {})

        if tool_name == "compose_email":
            try:
                draft = await email_service.compose_draft(
                    user_id=user_id,
                    recipient=args.get("to", ""),
                    subject=args.get("subject", ""),
                    body=email_service.append_regards(
                        args.get("body", " "), args.get("regards", "")
                    ),
                )
                return ToolResult.ok(
                    f"Draft saved for {args.get('to')}.",
                    {"draft_id": draft.get("draft_id"), **draft},
                )
            except email_service.EmailError as exc:
                return ToolResult.fail(exc.code, exc.detail, message=exc.detail)

        if tool_name == "send_email":
            try:
                receipt = await email_service.send_email(
                    user_id=user_id,
                    recipient=args.get("to", ""),
                    subject=args.get("subject", ""),
                    body=email_service.append_regards(
                        args.get("body", " "), args.get("regards", "")
                    ),
                )
                return ToolResult.ok(
                    f"Email sent to {args.get('to')}.",
                    receipt.as_dict(),
                )
            except email_service.EmailError as exc:
                return ToolResult.fail(exc.code, exc.detail, message=exc.detail)

        return ToolResult.fail(
            "UNKNOWN_TOOL", f"Unknown email step tool: {tool_name}",
            message=f"Cannot execute step: unknown tool '{tool_name}'.",
        )

    async def resume(
        self, *, user_id: str, step: dict[str, Any], approval_result: dict[str, Any],
    ) -> ToolResult:
        """After approval, execute the send_email step and return the receipt."""
        return await self.execute_step(user_id=user_id, step=step)


# --------------------------------------------------------------------------- #
# 4b. Job agent handler — concrete adapter for job-search tasks                 #
# --------------------------------------------------------------------------- #

class JobAgentHandler:
    """Adapter that bridges :class:`AgentRegistry` to the job search tools.

    Delegates to :mod:`app.tools.job_tools` for search, CV generation,
    application drafting, and approval-gated sending.  This keeps the state
    machine generic while reusing proven job logic.
    """

    action_type = "jobs"

    def propose(self, *, user_id: str, request: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Build a plan: search jobs, generate CV, draft application, send (approval-gated)."""
        keywords = kwargs.get("keywords") or request
        location = kwargs.get("location", "")

        plan: list[dict[str, Any]] = [
            {
                "description": "Search for matching job postings",
                "tool": "search_jobs",
                "arguments": {
                    "keywords": keywords,
                    "location": location,
                    "max_results": 10,
                },
            },
        ]
        # After search, generate a tailored CV for the top result (approval-gated)
        plan.append({
            "description": "Generate tailored CV (requires approval to send)",
            "tool": "generate_cv_for_job",
            "arguments": {"job_id": "{search_result_job_id}", "include_cover_letter": True},
        })
        # Send application (approval-gated — level 3)
        plan.append({
            "description": "Send application email (requires approval)",
            "tool": "send_application",
            "arguments": {"draft_id": "{draft_id}", "recipient_email": "{contact_email}"},
        })
        return plan

    async def execute_step(
        self, *, user_id: str, step: dict[str, Any], **kwargs: Any
    ) -> ToolResult:
        from app.tools import job_tools

        tool_name = step.get("tool", "")
        args = step.get("arguments", {})

        if tool_name == "search_jobs":
            return await job_tools.search_jobs(
                keywords=args.get("keywords", ""),
                location=args.get("location", ""),
                remote_only=args.get("remote_only", False),
                max_results=args.get("max_results", 10),
            )

        if tool_name == "generate_cv_for_job":
            return await job_tools.generate_cv_for_job(
                job_id=args.get("job_id", ""),
                template_id=args.get("template_id"),
                include_cover_letter=args.get("include_cover_letter", False),
            )

        if tool_name == "save_job_posting":
            return await job_tools.save_job_posting(
                job_id=args.get("job_id", ""),
                title=args.get("title", ""),
                company=args.get("company", ""),
                url=args.get("url", ""),
                location=args.get("location", ""),
                is_remote=args.get("is_remote", False),
            )

        if tool_name == "create_application_draft":
            return await job_tools.create_application_draft(**args)

        if tool_name == "send_application":
            return await job_tools.send_application(
                draft_id=args.get("draft_id", ""),
                recipient_email=args.get("recipient_email", ""),
            )

        return ToolResult.fail(
            "UNKNOWN_TOOL", f"Unknown job step tool: {tool_name}",
            message=f"Cannot execute step: unknown tool '{tool_name}'.",
        )

    async def resume(
        self, *, user_id: str, step: dict[str, Any], approval_result: dict[str, Any],
    ) -> ToolResult:
        """After approval, execute the step (send_application or generate_cv)."""
        return await self.execute_step(user_id=user_id, step=step)

class TaskEngine:
    """Central task execution orchestrator.

    Binds the state machine, agent registry, and approval flow together so
    that any action type (email, calendar, etc.) follows the same lifecycle:

        create_task → PENDING→RUNNING → propose_plan
        → execute steps → (approval needed?) → PAUSED_APPROVAL
        → resolve(approved) → APPROVED → RUNNING → COMPLETED

    The engine persists state to ``Task``, ``AgentRun``, ``Approval``,
    and ``TaskStep`` rows so the UI can show live progress and so a paused
    run can be resumed later.
    """

    def __init__(
        self,
        db: Session,
        *,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.db = db
        self.registry = registry or _default_registry
        self._machines: dict[str, TaskStateMachine] = {}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _policy_decide(
        *, permission: int, autonomy: int, policy: str, tags: list[str] | None,
    ) -> Any:
        """Lazy wrapper around :func:`app.services.policy.decide`."""
        from app.services.policy import decide
        return decide(
            permission=_resolve_permission(permission),
            autonomy=autonomy,
            policy=policy,
            tags=tags,
        )

    @staticmethod
    def _default_autonomy() -> int:
        from app.services.policy import DEFAULT_AUTONOMY
        return DEFAULT_AUTONOMY

    # -- task creation -------------------------------------------------------

    def create_task(
        self, *,
        user_id: str,
        goal: str,
        action_type: str | None = None,
        conversation_id: str | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new Task in PENDING state with an AgentRun.

        If ``action_type`` is None the engine resolves one from the goal text
        via the agent registry's intent-based resolver.
        """
        from app.agents.intent import classify_intent

        intent = classify_intent(goal)
        if action_type is None:
            spec = self.registry.resolve(intent=intent.intent, signals=intent.signals)
            if spec is not None:
                action_type = spec.action_type

        prefs = preferences or {}
        autonomy = _safe_int(prefs.get("autonomy"), self._default_autonomy())
        policy = str(prefs.get("tool_policies", {}).get(action_type or "", "automatic"))

        permission = 1  # PermissionLevel.AUTO
        spec = self.registry.get(action_type) if action_type else None
        if spec is not None:
            permission = spec.permission

        decision = self._policy_decide(
            permission=permission,
            autonomy=autonomy,
            policy=policy,
            tags=spec.tags if spec else [],
        )

        run = AgentRun(
            user_id=user_id,
            conversation_id=conversation_id,
            status="running",
            model="task_engine",
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        task = Task(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run.id,
            goal=goal[:2000],
            status="queued",
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        # Seed a TaskStep row for the initial "prepare" action so the UI
        # has something to render immediately.
        step = TaskStep(
            task_id=task.id,
            idx=0,
            description=f"Prepare {action_type or 'action'} task",
            status="pending",
            tool_id=action_type or None,
        )
        self.db.add(step)
        self.db.commit()

        machine = TaskStateMachine(TaskStatus.PENDING)
        self._machines[task.id] = machine

        # Persist the approval-need decision on the task row for the UI.
        needs_review = decision.require_approval or (
            action_type == "email" and decision.allowed
        )
        task.result = "approval_required=true" if needs_review else "approval_required=false"
        self.db.commit()

        return task

    # -- state helpers -------------------------------------------------------

    def get_machine(self, task_id: str) -> TaskStateMachine:
        """Get or rebuild the state machine for a persisted task."""
        machine = self._machines.get(task_id)
        if machine is None:
            task = self.db.get(Task, task_id)
            if task is not None:
                machine = TaskStateMachine.from_db_status(task.status)
            else:
                machine = TaskStateMachine(TaskStatus.PENDING)
            self._machines[task_id] = machine
        return machine

    def _sync_task_status(self, task_id: str, machine: TaskStateMachine) -> None:
        """Persist the machine's current state back to the Task row."""
        task = self.db.get(Task, task_id)
        if task is not None:
            task.status = machine.to_db_status()
            task.updated_at = _utcnow()
            self.db.commit()

    # -- plan + step execution ----------------------------------------------

    def propose_plan(self, task_id: str) -> list[dict[str, Any]]:
        """Ask the dispatched agent to propose steps for this task."""
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskStateError(f"Task {task_id} not found")

        spec = self._resolve_spec(task)
        if spec is None or spec.handler is None:
            return []

        machine = self.get_machine(task_id)
        machine.transition(TaskStatus.RUNNING)
        self._sync_task_status(task_id, machine)

        plan = spec.handler.propose(
            user_id=task.user_id, request=task.goal,
        )
        self._sync_steps(task_id, plan)
        return plan

    async def execute_step(
        self,
        task_id: str,
        step_idx: int,
        *,
        approval_result: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a single plan step.

        If ``approval_result`` is provided the step is being resumed after
        an approval was granted — the handler's ``resume`` path is used.
        """
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskStateError(f"Task {task_id} not found")

        spec = self._resolve_spec(task)
        if spec is None or spec.handler is None:
            return ToolResult.fail(
                "NO_AGENT", f"No agent registered for task {task_id}",
                message="No agent is registered for this task type.",
            )

        steps = self._load_steps(task_id)
        step = steps[step_idx] if 0 <= step_idx < len(steps) else None
        if step is None:
            return ToolResult.fail(
                "INVALID_STEP", f"Step {step_idx} not found for task {task_id}",
                message=f"Step {step_idx} does not exist for this task.",
            )

        machine = self.get_machine(task_id)
        if machine.state not in (TaskStatus.RUNNING, TaskStatus.APPROVED):
            raise TaskStateError(
                f"Task {task_id} is in state {machine.state.value}, cannot execute step"
            )

        step.status = "active"
        self.db.commit()

        try:
            if approval_result is not None:
                result = await spec.handler.resume(
                    user_id=task.user_id, step=step.arguments or {},
                    approval_result=approval_result,
                )
            else:
                result = await spec.handler.execute_step(
                    user_id=task.user_id, step=step.arguments or {},
                )
        except Exception as exc:
            logger.exception("step execution failed for task %s", task_id)
            result = ToolResult.fail(
                "STEP_EXCEPTION", f"{type(exc).__name__}: {exc}",
                message="The step failed unexpectedly.",
            )

        if result.success:
            step.status = "done"
        else:
            step.status = "failed"
            step.detail = result.error.code if result.error else ""
        step.updated_at = _utcnow()
        self.db.commit()
        return result

    # -- approval flow -------------------------------------------------------

    def request_approval(
        self,
        *,
        task_id: str,
        step_idx: int,
        tool_name: str,
        arguments: dict[str, Any],
        level: int,
        kind: str = "tool",
    ) -> Approval:
        """Pause the task for user approval and persist an Approval row."""
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskStateError(f"Task {task_id} not found")

        approval = Approval(
            user_id=task.user_id,
            run_id=task.run_id or "",
            tool_name=tool_name,
            arguments={"step_idx": step_idx, **arguments},
            level=level,
            kind=kind,
            status="pending",
        )
        self.db.add(approval)

        machine = self.get_machine(task_id)
        machine.transition(TaskStatus.PAUSED_APPROVAL)
        task.status = machine.to_db_status()
        task.updated_at = _utcnow()
        self.db.commit()
        self.db.refresh(approval)
        from app.services.notifications import notify

        notify(
            self.db,
            user_id=task.user_id,
            text=f"Approval required for {tool_name}. Review it in Tasks.",
            kind="approval_required",
            link=task.conversation_id,
            run_id=task.run_id,
            task_id=task.id,
        )
        self.db.commit()

        # Mark the step as pending-approval.
        steps = self._load_steps(task_id)
        if 0 <= step_idx < len(steps):
            steps[step_idx].status = "pending"
            steps[step_idx].updated_at = _utcnow()
            self.db.commit()

        return approval

    async def resolve_approval(self, *, approval_id: str, approve: bool) -> TaskResult:
        """Resolve a pending approval: resume the run or cancel it."""
        approval = self.db.get(Approval, approval_id)
        if approval is None:
            raise TaskStateError(f"Approval {approval_id} not found")
        target = "sending" if approve else "rejected"
        claimed = self.db.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == "pending")
            .values(status=target, decided_at=_utcnow())
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            raise TaskStateError(f"Approval already {approval.status}")
        approval.decided_at = _utcnow()
        self.db.commit()

        task = self.db.scalar(
            select(Task).where(Task.run_id == approval.run_id)
        )
        result = TaskResult(approval=approval, steps_completed=[])
        if task is None:
            return result

        machine = self.get_machine(task.id)
        if approve:
            machine.transition(TaskStatus.APPROVED)
            self._sync_task_status(task.id, machine)

            step_idx = int(approval.arguments.get("step_idx", 0))
            step_result = await self.execute_step(
                task.id, step_idx,
                approval_result=approval.arguments,
            )
            result.steps_completed.append(step_result)
            if step_result.success:
                approval.status = "approved"
                machine.transition(TaskStatus.RUNNING)
                self._sync_task_status(task.id, machine)
            else:
                error_code = step_result.error.code if step_result.error else ""
                approval.status = (
                    "outcome_unknown"
                    if error_code in {"PROVIDER_ERROR", "TIMEOUT", "NETWORK_ERROR", "STEP_EXCEPTION"}
                    else "failed"
                )
                self.db.commit()
                machine.transition(TaskStatus.FAILED)
                self._sync_task_status(task.id, machine)
                result.status = TaskStatus.FAILED
        else:
            machine.transition(TaskStatus.CANCELLED)
            self._sync_task_status(task.id, machine)
            result.status = TaskStatus.CANCELLED

        result.machine = machine
        return result

    # -- full execution ------------------------------------------------------

    async def execute(
        self,
        *,
        user_id: str,
        goal: str,
        preferences: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> TaskResult:
        """End-to-end: create task, propose plan, execute steps, handle approvals.

        If the user's autonomy level allows auto-execution (autonomy ≥ 3 with
        an "automatic" policy), eligible steps are sent directly. Email sends
        always pause for explicit review, regardless of autonomy.
        """
        task = self.create_task(
            user_id=user_id,
            goal=goal,
            conversation_id=conversation_id,
            preferences=preferences,
        )

        machine = self.get_machine(task.id)
        plan = self.propose_plan(task.id)

        result = TaskResult(task=task, plan=plan, steps_completed=[])
        spec = self._resolve_spec(task)
        if spec is None or spec.handler is None:
            machine.transition(TaskStatus.FAILED)
            self._sync_task_status(task.id, machine)
            result.error = "No agent registered for this task type."
            result.status = TaskStatus.FAILED
            return result

        prefs = preferences or {}
        autonomy = _safe_int(prefs.get("autonomy"), self._default_autonomy())
        auto_policy = (
            spec.requires_approval
            and autonomy >= 3
            and str(prefs.get("tool_policies", {}).get(
                spec.action_type, "automatic")) == "automatic"
            and spec.action_type != "email"
        )

        register_active(task.user_id, task.run_id)
        try:
            for idx, step in enumerate(plan):
                if is_stopped(task.user_id, task.run_id):
                    machine.transition(TaskStatus.STOPPED)
                    self._sync_task_status(task.id, machine)
                    result.status = TaskStatus.STOPPED
                    return result

                if is_paused(task.user_id, task.run_id):
                    machine.transition(TaskStatus.STOPPED)
                    self._sync_task_status(task.id, machine)
                    result.status = TaskStatus.STOPPED
                    return result

                # Approval-gated step?
                step_decision = self._step_policy_decision(spec, step, prefs)
                if step_decision is not None and not step_decision.allowed:
                    step_res = ToolResult.fail(
                        step_decision.code or "TOOL_DENIED",
                        step_decision.detail or "This action is denied by policy.",
                        message=step_decision.detail or "This action is denied by policy.",
                    )
                    result.steps_completed.append(step_res)
                    machine.transition(TaskStatus.FAILED)
                    self._sync_task_status(task.id, machine)
                    result.status = TaskStatus.FAILED
                    result.error = step_res.error.details if step_res.error else ""
                    return result
                requires_approve = step_decision is not None and (
                    step_decision.require_approval
                    or step.get("tool") == "send_email"
                )
                if requires_approve and (
                    step.get("tool") == "send_email" or not auto_policy
                ):
                    approval = self.request_approval(
                        task_id=task.id, step_idx=idx,
                        tool_name=step.get("tool", ""),
                        arguments=step.get("arguments", {}),
                        level=spec.permission,
                    )
                    machine.transition(TaskStatus.PAUSED_APPROVAL)
                    self._sync_task_status(task.id, machine)
                    result.status = TaskStatus.PAUSED_APPROVAL
                    result.approval = approval
                    result.step_idx = idx
                    return result

                step_res = await self.execute_step(task.id, idx)
                result.steps_completed.append(step_res)
                if not step_res.success and step_res.error:
                    machine.transition(TaskStatus.FAILED)
                    self._sync_task_status(task.id, machine)
                    result.status = TaskStatus.FAILED
                    result.error = step_res.error.details
                    return result
        finally:
            unregister_active(task.user_id, task.run_id)

        machine.transition(TaskStatus.COMPLETED)
        self._sync_task_status(task.id, machine)
        task.result = "completed"
        self.db.commit()
        result.status = TaskStatus.COMPLETED
        return result

    # -- internals -----------------------------------------------------------

    def _resolve_spec(self, task: Task) -> AgentSpec | None:
        """Find the agent spec for a task.

        Tries the task's first step ``tool_id`` first, then falls back to
        intent-based resolution from the goal text.
        """
        first_step = self.db.scalars(
            select(TaskStep).where(TaskStep.task_id == task.id)
            .order_by(TaskStep.idx).limit(1)
        ).first()
        if first_step is not None and first_step.tool_id:
            spec = self.registry.get(first_step.tool_id)
            if spec is not None:
                return spec
        from app.agents.intent import classify_intent
        intent = classify_intent(task.goal)
        return self.registry.resolve(intent=intent.intent, signals=intent.signals)

    def _load_steps(self, task_id: str) -> list[TaskStep]:
        return list(self.db.scalars(
            select(TaskStep).where(TaskStep.task_id == task_id)
            .order_by(TaskStep.idx)
        ).all())

    def _sync_steps(self, task_id: str, plan: list[dict[str, Any]]) -> None:
        """Persist plan steps as TaskStep rows."""
        existing = {
            s.idx: s for s in self.db.scalars(
                select(TaskStep).where(TaskStep.task_id == task_id)
                .order_by(TaskStep.idx)
            ).all()
        }
        for idx, item in enumerate(plan):
            row = existing.get(idx)
            if row is None:
                row = TaskStep(
                    task_id=task_id, idx=idx,
                    description=item.get("description", ""),
                    status="pending",
                    tool_id=item.get("tool"),
                    arguments=item.get("arguments", {}),
                )
                self.db.add(row)
            else:
                row.description = item.get("description", row.description)
                row.status = item.get("status", row.status)
                row.tool_id = item.get("tool", row.tool_id) or row.tool_id
                row.arguments = item.get("arguments", {}) or row.arguments
        for idx, row in existing.items():
            if idx >= len(plan):
                row.status = "skipped"
                row.updated_at = _utcnow()
        self.db.commit()

    def _step_requires_approval(
        self, spec: AgentSpec, step: dict[str, Any], prefs: dict[str, Any],
    ) -> bool:
        """Whether a given step needs user approval based on the policy engine."""
        decision = self._step_policy_decision(spec, step, prefs)
        return decision is not None and decision.allowed and (
            decision.require_approval or step.get("tool") == "send_email"
        )

    def _step_policy_decision(
        self, spec: AgentSpec, step: dict[str, Any], prefs: dict[str, Any],
    ) -> Any | None:
        """Return the permission-policy decision for a gated step."""
        tool_name = step.get("tool", "")
        if not tool_name:
            return None
        gated = {
            "send_email", "send_sms", "send_whatsapp", "place_voice_call",
            "make_call", "send_payment", "create_event", "schedule_task",
            "create_reminder", "create_automation",
        }
        if tool_name not in gated:
            return None
        autonomy = _safe_int(prefs.get("autonomy"), self._default_autonomy())
        policy = str(prefs.get("tool_policies", {}).get(tool_name, "automatic"))
        decision = self._policy_decide(
            permission=CONFIRM_LEVEL,
            autonomy=autonomy,
            policy=policy,
            tags=spec.tags,
        )
        return decision


@dataclass
class TaskResult:
    """Outcome of a task execution run."""

    task: Task | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    steps_completed: list[ToolResult] = field(default_factory=list)
    approval: Approval | None = None
    machine: TaskStateMachine | None = None
    status: TaskStatus = TaskStatus.PENDING
    step_idx: int = 0
    error: str | None = None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_permission(value: int) -> Any:
    """Convert an int permission level to a PermissionLevel enum for policy.decide."""
    PL = _get_permission_level()
    try:
        return PL(value)
    except ValueError:
        return PL.AUTO


# --------------------------------------------------------------------------- #
# 6. Default registry + engine singletons                                    #
# --------------------------------------------------------------------------- #

_default_registry = AgentRegistry()


def _register_builtin_agents() -> None:
    """Eagerly register the built-in action agents."""
    _default_registry.register(AgentSpec(
        name="email",
        action_type="email",
        description="Send, draft, search, and manage email",
        permission=CONFIRM_LEVEL,
        requires_internet=True,
        handler=EmailAgentHandler(),
        tags=["email", "external"],
    ))
    _default_registry.register(AgentSpec(
        name="calendar",
        action_type="calendar",
        description="Create and manage calendar events",
        permission=CONFIRM_LEVEL,
        tags=["calendar"],
    ))
    _default_registry.register(AgentSpec(
        name="reminders",
        action_type="reminders",
        description="Set reminders and schedule tasks",
        permission=CONFIRM_LEVEL,
        tags=["scheduling"],
    ))
    _default_registry.register(AgentSpec(
        name="documents",
        action_type="documents",
        description="Read, search, and create documents",
        permission=1,  # AUTO
        tags=["documents"],
    ))
    _default_registry.register(AgentSpec(
        name="browser",
        action_type="browser",
        description="Web browsing and automation",
        permission=CONFIRM_LEVEL,
        tags=["browser"],
    ))
    _default_registry.register(AgentSpec(
        name="jobs",
        action_type="jobs",
        description="Job search, CV generation, and application submission",
        permission=CONFIRM_LEVEL,
        requires_internet=True,
        handler=JobAgentHandler(),
        tags=["jobs", "research", "external"],
    ))


_register_builtin_agents()


def get_engine(db: Session) -> TaskEngine:
    """Factory that returns a :class:`TaskEngine` bound to ``db``."""
    return TaskEngine(db)


# Module-level convenience: callers can do ``from app.services.task_engine import engine; engine(db)``.
engine = TaskEngine

# Convenience alias for the default registry.
registry = _default_registry
