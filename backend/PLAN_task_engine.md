# Plan: Task State Machine + Agent Registry

## Part 1: Fix EmailDraftOut (DONE — 22/22 tests pass)
- Made `EmailDraftOut.id` optional (`id: str = ""`) so `compose_draft()`'s `draft_id` dict works with `EmailDraftOut(**draft)`.

## Part 2: Implement task_engine.py (file is 0 bytes, needs full implementation)
### A. Task state machine
- `TaskStatus` enum: PENDING, RUNNING, PAUSED_APPROVAL, APPROVED, COMPLETED, FAILED, STOPPED, CANCELLED
- `VALID_TRANSITIONS` table mapping state → set of allowed next states
- `TaskStateMachine` class: validates transitions, tracks current state
- Maps between graph states and Task.status strings

### B. Agent registry
- `AgentSpec` dataclass: name, action_type, description, permission, executor
- `AgentRegistry` class: register/dispatch by action_type

### C. TaskEngine
- create_task, propose_plan, execute_step, request_approval, resolve_approval, execute

## Part 3: Verify
- Re-run test_phase_e_email.py (22/22 must pass)
- Run full test suite for regressions
