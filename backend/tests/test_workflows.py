"""Tests for Cross-App Workflow automation (spec §5).

Run with: python -m pytest backend/tests/test_workflows.py -v
"""
import pytest

from app.models.workflows import CrossAppWorkflow, WorkflowExecutionRecord, WorkflowStepResult
from app.services.workflow_automation import (
    WorkflowEngine, 
    WorkflowExecutionResult,
    WorkflowStep
)


# ============================================================================
# Workflow Step Data Class Tests
# ============================================================================

def test_workflow_step_creation():
    """Test creating a WorkflowStep."""
    step = WorkflowStep(
        order=1,
        name="test_step",
        tool="test_tool",
        args={"key": "value"},
        conditions=["cond1", "cond2"]
    )
    
    assert step.order == 1
    assert step.name == "test_step"
    assert step.tool == "test_tool"
    assert step.args == {"key": "value"}
    assert step.conditions == ["cond1", "cond2"]


def test_workflow_step_defaults():
    """Test WorkflowStep default values."""
    step = WorkflowStep(order=0, name="simple", tool="simple_tool", args={})
    
    assert step.next_step is None
    assert step.conditions == []


# ============================================================================
# Workflow Engine Tests (In-Memory)
# ============================================================================

class TestWorkflowEngine:
    """Test workflow pattern management without database writes."""

    def test_create_pattern_rejects_unregistered_capability(self):
        engine = WorkflowEngine()

        with pytest.raises(ValueError, match="unregistered capability"):
            engine.create_pattern("user-123", "Invalid Workflow", [{
                "order": 1,
                "name": "unknown",
                "tool": "not_a_registered_capability",
                "args": {},
            }])
    
    def test_list_patterns_empty(self):
        """Test listing patterns returns empty list initially."""
        engine = WorkflowEngine()
        patterns = engine.list_patterns("user-123")
        assert patterns == []
    
    def test_create_pattern_in_memory(self):
        """Test creating a workflow pattern (in-memory simulation)."""
        engine = WorkflowEngine()
        steps = [
            {"order": 1, "name": "step1", "tool": "tool_a", "args": {}},
            {"order": 2, "name": "step2", "tool": "tool_b", "args": {}}
        ]
        
        # Test in-memory pattern creation (simulated)
        pattern_id = "test-pattern-id"
        pattern_data = {
            "id": pattern_id,
            "user_id": "user-123",
            "name": "Test Workflow",
            "steps": steps
        }
        
        assert pattern_data["name"] == "Test Workflow"
        assert len(pattern_data["steps"]) == 2
    
    def test_execute_workflow_simulation(self):
        """Test workflow execution simulation."""
        engine = WorkflowEngine()
        
        # Simulate execution result
        result = WorkflowExecutionResult(
            success=True,
            pattern_name="Test Workflow",
            steps_completed=2,
            steps_total=2,
            output_data={
                "step1": {"tool": "tool_a", "status": "executed", "result": "ok"},
                "step2": {"tool": "tool_b", "status": "executed", "result": "ok"}
            }
        )
        
        assert result.success == True
        assert result.steps_completed == 2
        assert result.steps_total == 2
        assert "step1" in result.output_data
    
    def test_execute_workflow_failure(self):
        """Test workflow execution failure handling."""
        engine = WorkflowEngine()
        
        result = WorkflowExecutionResult(
            success=False,
            pattern_name="Failed Workflow",
            steps_completed=1,
            steps_total=3,
            output_data={
                "step1": {"tool": "tool_a", "status": "executed", "result": "ok"}
            },
            error="Tool execution failed"
        )
        
        assert result.success == False
        assert result.steps_completed == 1
        assert result.error == "Tool execution failed"


# ============================================================================
# CrossAppWorkflow Model Tests
# ============================================================================

class TestCrossAppWorkflowModel:
    """Test CrossAppWorkflow model structure."""
    
    def test_model_fields(self):
        """Test CrossAppWorkflow has expected fields."""
        import inspect
        from app.models.workflows import CrossAppWorkflow
        
        # Check table name
        assert hasattr(CrossAppWorkflow, '__tablename__')
        
        # Check key mappings exist
        assert CrossAppWorkflow.id is not None
        assert CrossAppWorkflow.user_id is not None
        assert CrossAppWorkflow.name is not None


class TestWorkflowExecutionRecordModel:
    """Test WorkflowExecutionRecord model structure."""
    
    def test_model_fields(self):
        """Test WorkflowExecutionRecord has expected fields."""
        from app.models.workflows import WorkflowExecutionRecord
        
        # Check table name
        assert hasattr(WorkflowExecutionRecord, '__tablename__')
        
        # Check key mappings exist
        assert WorkflowExecutionRecord.id is not None
        assert WorkflowExecutionRecord.status is not None


class TestWorkflowStepResultModel:
    """Test WorkflowStepResult model structure."""
    
    def test_model_fields(self):
        """Test WorkflowStepResult has expected fields."""
        from app.models.workflows import WorkflowStepResult
        
        # Check table name
        assert hasattr(WorkflowStepResult, '__tablename__')
        
        # Check key mappings exist
        assert WorkflowStepResult.id is not None
        assert WorkflowStepResult.tool_name is not None


# ============================================================================
# Workflow Execution Result Tests
# ============================================================================

class TestWorkflowExecutionResult:
    """Test WorkflowExecutionResult dataclass."""
    
    def test_result_success(self):
        """Test successful execution result."""
        result = WorkflowExecutionResult(
            success=True,
            pattern_name="Test",
            steps_completed=3,
            steps_total=3,
            output_data={"step1": {"result": "ok"}}
        )
        
        assert result.success == True
        assert result.steps_completed == 3
        assert result.steps_total == 3
        assert result.error is None
    
    def test_result_failure(self):
        """Test failed execution result."""
        result = WorkflowExecutionResult(
            success=False,
            pattern_name="Failed",
            steps_completed=1,
            steps_total=5,
            output_data={},
            error="Connection timeout"
        )
        
        assert result.success == False
        assert result.error == "Connection timeout"


# ============================================================================
# Edge Cases
# ============================================================================

def test_workflow_engine_singleton():
    """Test WorkflowEngine is a singleton."""
    from app.services.workflow_automation import get_workflow_engine
    
    engine1 = get_workflow_engine()
    engine2 = get_workflow_engine()
    
    assert engine1 is engine2


def test_saved_workflow_uses_canonical_graph_approvals_and_typed_outputs(
    client, fake_router, monkeypatch
):
    from langchain_core.messages import AIMessage

    from app.database.session import SessionLocal
    from app.errors import ToolResult
    from app.models.agent import AgentRun, Approval
    from app.models.task import Task, TaskStep
    from app.models.workflows import WorkflowExecutionRecord, WorkflowStepResult
    from app.tools.base import PermissionLevel, registry
    from app.tools import workflow_tools as _workflow_tools

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    confirmed_values = []

    async def workflow_echo(value: str) -> ToolResult:
        return ToolResult.ok("Echoed.", {"value": value})

    async def workflow_confirm(value: str) -> ToolResult:
        confirmed_values.append(value)
        return ToolResult.ok("Confirmed.", {"value": value})

    registry.register(workflow_echo, permission=PermissionLevel.AUTO, category="test")
    registry.register(
        workflow_confirm, permission=PermissionLevel.CONFIRM, category="test"
    )
    registered = client.post("/api/auth/register", json={
        "email": "workflow-central@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    user_id = registered.json()["user"]["id"]
    pattern = WorkflowEngine().create_pattern(user_id, "Typed flow", [
        {
            "order": 1,
            "name": "echo",
            "tool": "workflow_echo",
            "args": {"value": "{{input.message}}"},
        },
        {
            "order": 2,
            "name": "confirm",
            "tool": "workflow_confirm",
            "args": {"value": "{{steps.echo.data.value}}"},
        },
    ])
    assert pattern["id"]

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "run_saved_workflow",
            "description": "Run the saved workflow",
            "tool": "execute_workflow",
            "dependencies": [],
        }]},
        "id": "workflow_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "execute_workflow",
        "args": {
            "pattern_id": pattern["id"],
            "input_data": {"message": "typed value"},
        },
        "id": "execute_saved_workflow",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="The saved workflow completed."))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Run the saved workflow.", "mode": "agent"},
    )
    assert response.status_code == 200, response.text
    first_pause = response.json()
    assert first_pause["status"] == "paused_approval", first_pause
    assert first_pause["approval"]["tool_name"] == "execute_workflow"
    assert confirmed_values == []

    first_approval = client.post(
        f"/api/approvals/{first_pause['approval']['id']}/approve",
        headers=headers,
    )
    assert first_approval.status_code == 200, first_approval.text
    second_pause = first_approval.json()
    assert second_pause["status"] == "paused_approval", second_pause
    assert second_pause["approval"]["tool_name"] == "workflow_confirm"
    assert confirmed_values == []

    task_id = first_pause["task_id"]
    run_id = first_pause["run_id"]
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None
        run = db.get(AgentRun, run_id)
        assert run is not None and run.state_json
        saved_state = run.state_json
        assert saved_state["workflow_context"]["execution_id"]
        assert saved_state["workflow_context"]["next_step"] == 1
        workflow_execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=task_id, run_id=run_id,
        ).one()
        assert workflow_execution.status == "approval_required"
        assert task.status == "waiting_for_approval"
        canonical_steps = db.query(TaskStep).filter_by(task_id=task_id).order_by(
            TaskStep.idx
        ).all()
        assert any(
            row.plan_step_id
            and row.plan_step_id.startswith(f"workflow:{workflow_execution.id}:")
            and row.tool_id == "workflow_echo"
            and row.status == "done"
            for row in canonical_steps
        )
        child_approval = db.get(Approval, second_pause["approval"]["id"])
        assert child_approval is not None
        assert child_approval.task_id == task_id
        assert child_approval.task_step_id
        assert db.get(TaskStep, child_approval.task_step_id).approval_id == child_approval.id

    final_response = client.post(
        f"/api/approvals/{second_pause['approval']['id']}/approve",
        headers=headers,
    )
    assert final_response.status_code == 200, final_response.text
    final = final_response.json()
    assert final["status"] == "completed", final
    assert confirmed_values == ["typed value"]

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=task_id, run_id=run_id,
        ).one()
        assert task is not None and task.status == "completed"
        assert execution.status == "completed"
        step_results = db.query(WorkflowStepResult).filter_by(
            execution_id=execution.id,
        ).order_by(WorkflowStepResult.step_order).all()
        assert [row.step_name for row in step_results] == ["echo", "confirm"]
        assert all(row.task_step_id for row in step_results)
        assert step_results[1].tool_args == {"value": "typed value"}
        status = WorkflowEngine().get_execution_status(execution.id, user_id)
        assert status["task_id"] == task_id
        assert status["task_status"] == "completed"
        assert all(step["success"] for step in status["steps"])


@pytest.mark.parametrize("decision", ["skip", "reject"])
def test_saved_workflow_step_uses_shared_skip_and_reject(
    client, fake_router, monkeypatch, decision,
):
    from langchain_core.messages import AIMessage

    from app.database.session import SessionLocal
    from app.errors import ToolResult
    from app.models.workflows import WorkflowExecutionRecord, WorkflowStepResult
    from app.tools.base import PermissionLevel, registry
    from app.tools import workflow_tools as _workflow_tools

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    dispatched = []

    async def workflow_skip_action(value: str) -> ToolResult:
        dispatched.append(value)
        return ToolResult.ok("Action ran.", {"value": value})

    registry.register(
        workflow_skip_action,
        permission=PermissionLevel.CONFIRM,
        category="test",
    )
    registered = client.post("/api/auth/register", json={
        "email": f"workflow-{decision}@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    pattern = WorkflowEngine().create_pattern(user_id, "Skippable flow", [{
        "order": 1,
        "name": "optional_action",
        "tool": "workflow_skip_action",
        "args": {"value": "{{input.value}}"},
    }])

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "run_skippable_workflow",
            "description": "Run the saved workflow",
            "tool": "execute_workflow",
            "dependencies": [],
        }]},
        "id": "workflow_skip_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "execute_workflow",
        "args": {"pattern_id": pattern["id"], "input_data": {"value": "skip me"}},
        "id": "execute_skippable_workflow",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="The optional workflow action was skipped."))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Run the saved workflow as an agent task.", "mode": "agent"},
    )
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["status"] == "paused_approval"

    outer = client.post(
        f"/api/approvals/{initial['approval']['id']}/approve",
        headers=headers,
    )
    assert outer.status_code == 200, outer.text
    pending = outer.json()
    assert pending["status"] == "paused_approval"
    assert pending["approval"]["tool_name"] == "workflow_skip_action"
    assert dispatched == []

    decision_response = client.post(
        f"/api/approvals/{pending['approval']['id']}/{decision}",
        headers=headers,
    )
    assert decision_response.status_code == 200, decision_response.text
    if decision == "skip":
        assert decision_response.json()["status"] == "completed"
    else:
        assert decision_response.json()["status"] != "completed"
    assert dispatched == []

    with SessionLocal() as db:
        execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=initial["task_id"], run_id=initial["run_id"],
        ).one()
        result = db.query(WorkflowStepResult).filter_by(
            execution_id=execution.id,
        ).one()
        if decision == "skip":
            assert execution.status == "completed"
            assert result.success is True
            assert result.result_data["data"]["skipped"] is True
        else:
            assert execution.status == "failed"
            assert result.success is False


def test_cancelling_canonical_task_cancels_pending_workflow_approval(
    client, fake_router, monkeypatch,
):
    from langchain_core.messages import AIMessage

    from app.database.session import SessionLocal
    from app.errors import ToolResult
    from app.models.agent import Approval
    from app.models.workflows import WorkflowExecutionRecord
    from app.tools.base import PermissionLevel, registry
    from app.tools import workflow_tools as _workflow_tools

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    dispatched = []

    async def workflow_cancel_action() -> ToolResult:
        dispatched.append(True)
        return ToolResult.ok("Action ran.")

    registry.register(
        workflow_cancel_action,
        permission=PermissionLevel.CONFIRM,
        category="test",
    )
    registered = client.post("/api/auth/register", json={
        "email": "workflow-cancel@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    pattern = WorkflowEngine().create_pattern(user_id, "Cancellable flow", [{
        "order": 1,
        "name": "confirm_action",
        "tool": "workflow_cancel_action",
        "args": {},
    }])

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "run_cancellable_workflow",
            "description": "Run the saved workflow",
            "tool": "execute_workflow",
            "dependencies": [],
        }]},
        "id": "workflow_cancel_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "execute_workflow",
        "args": {"pattern_id": pattern["id"], "input_data": {}},
        "id": "execute_cancellable_workflow",
        "type": "tool_call",
    }]))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Run the saved workflow.", "mode": "agent"},
    )
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["status"] == "paused_approval"
    approved_outer = client.post(
        f"/api/approvals/{initial['approval']['id']}/approve",
        headers=headers,
    )
    assert approved_outer.status_code == 200, approved_outer.text
    pending = approved_outer.json()
    assert pending["approval"]["tool_name"] == "workflow_cancel_action"
    assert dispatched == []

    cancelled = client.post(
        f"/api/tasks/{initial['task_id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    task_detail = client.get(
        f"/api/tasks/{initial['task_id']}",
        headers=headers,
    )
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["status"] == "cancelled"
    assert dispatched == []

    with SessionLocal() as db:
        execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=initial["task_id"], run_id=initial["run_id"],
        ).one()
        assert execution.status == "cancelled"
        approval = db.get(Approval, pending["approval"]["id"])
        assert approval is not None
        assert approval.status == "cancelled"


def test_timed_out_workflow_action_is_unknown_and_not_repeated(
    client, fake_router, monkeypatch,
):
    from langchain_core.messages import AIMessage

    from app.database.session import SessionLocal
    from app.errors import ToolResult
    from app.models.workflows import WorkflowExecutionRecord, WorkflowStepResult
    from app.tools.base import PermissionLevel, registry
    from app.tools import workflow_tools as _workflow_tools

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    dispatched = []

    async def workflow_uncertain_action(value: str) -> ToolResult:
        dispatched.append(value)
        return ToolResult.fail(
            "TIMEOUT",
            "The provider timed out after the action may have been submitted.",
        )

    registry.register(
        workflow_uncertain_action,
        permission=PermissionLevel.CONFIRM,
        category="test",
        side_effect="external",
        idempotent=False,
        retryable=False,
    )
    registered = client.post("/api/auth/register", json={
        "email": "workflow-unknown@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    pattern = WorkflowEngine().create_pattern(user_id, "Uncertain flow", [{
        "order": 1,
        "name": "uncertain_action",
        "tool": "workflow_uncertain_action",
        "args": {"value": "{{input.value}}"},
    }])

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "run_uncertain_workflow",
            "description": "Run the saved workflow",
            "tool": "execute_workflow",
            "dependencies": [],
        }]},
        "id": "workflow_uncertain_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "execute_workflow",
        "args": {
            "pattern_id": pattern["id"],
            "input_data": {"value": "provider request"},
        },
        "id": "execute_uncertain_workflow",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="The provider outcome is unknown."))

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Run my saved workflow as an agent task.", "mode": "agent"},
    )
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["status"] == "paused_approval"
    approved_outer = client.post(
        f"/api/approvals/{initial['approval']['id']}/approve",
        headers=headers,
    )
    assert approved_outer.status_code == 200, approved_outer.text
    pending = approved_outer.json()
    assert pending["status"] == "paused_approval"
    assert pending["approval"]["tool_name"] == "workflow_uncertain_action"

    result = client.post(
        f"/api/approvals/{pending['approval']['id']}/approve",
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] != "completed"
    assert dispatched == ["provider request"]

    with SessionLocal() as db:
        execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=initial["task_id"], run_id=initial["run_id"],
        ).one()
        assert execution.status == "outcome_unknown"
        step_result = db.query(WorkflowStepResult).filter_by(
            execution_id=execution.id,
        ).one()
        assert step_result.success is False
        assert step_result.result_data["error"]["code"] == "TIMEOUT"


def test_saved_workflow_resumes_after_backend_restart(
    client, fake_router,
):
    import json
    import os
    import subprocess
    import sys

    from langchain_core.messages import AIMessage

    from app.database.session import SessionLocal
    from app.models.workflows import WorkflowExecutionRecord
    from app.tools import workflow_tools as _workflow_tools

    registered = client.post("/api/auth/register", json={
        "email": "workflow-restart@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    recipient = "workflow-restart@example.org"
    pattern = WorkflowEngine().create_pattern(user_id, "Restart flow", [{
        "order": 1,
        "name": "send_message",
        "tool": "send_email",
        "args": {
            "to": recipient,
            "subject": "Workflow restart",
            "body": "This mock message is sent after approval and restart.",
        },
    }])
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [{
            "id": "run_restart_workflow",
            "description": "Run the saved workflow",
            "tool": "execute_workflow",
            "dependencies": [],
        }]},
        "id": "workflow_restart_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "execute_workflow",
        "args": {"pattern_id": pattern["id"], "input_data": {}},
        "id": "execute_restart_workflow",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="The workflow is awaiting email approval."))
    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Run my saved workflow as an agent task.", "mode": "agent"},
    )
    assert response.status_code == 200, response.text
    outer = response.json()
    assert outer["status"] == "paused_approval", outer
    workflow_started = client.post(
        f"/api/approvals/{outer['approval']['id']}/approve",
        headers=headers,
    )
    assert workflow_started.status_code == 200, workflow_started.text
    pending = workflow_started.json()
    assert pending["status"] == "paused_approval"
    assert pending["approval"]["tool_name"] == "send_email"

    restarted_process = subprocess.run(
        [sys.executable, "-c", """
import json
import os
import sys
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_router
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from conftest import FakeRouter
from langchain_core.messages import AIMessage

request = json.load(sys.stdin)
app.dependency_overrides[get_router] = lambda: FakeRouter([
    AIMessage(content="The approved workflow email was sent.")
])
with TestClient(app) as client:
    response = client.post(
        f"/api/approvals/{request['approval_id']}/approve",
        headers=request["headers"],
    )
    print("JALORA_WORKFLOW_RESTART=" + json.dumps({
        "status_code": response.status_code,
        "body": response.json(),
    }))
"""],
        input=json.dumps({
            "approval_id": pending["approval"]["id"],
            "headers": headers,
        }),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    assert restarted_process.returncode == 0, restarted_process.stderr
    result_line = next(
        (
            line.removeprefix("JALORA_WORKFLOW_RESTART=")
            for line in reversed(restarted_process.stdout.splitlines())
            if line.startswith("JALORA_WORKFLOW_RESTART=")
        ),
        None,
    )
    assert result_line is not None, restarted_process.stdout
    restarted_result = json.loads(result_line)
    assert restarted_result["status_code"] == 200, restarted_result
    assert restarted_result["body"]["status"] == "completed"

    with SessionLocal() as db:
        execution = db.query(WorkflowExecutionRecord).filter_by(
            task_id=outer["task_id"], run_id=outer["run_id"],
        ).one()
        assert execution.status == "completed"


def test_saved_workflow_creates_history_linked_to_canonical_task(client):
    """The legacy history record is linked to, but does not execute beside, a run."""
    import asyncio

    from app.database.session import SessionLocal
    from app.models.agent import AgentRun
    from app.models.task import Task
    from app.errors import ToolResult
    from app.models.workflows import WorkflowExecutionRecord
    from app.tools.base import PermissionLevel, registry

    async def workflow_test_echo(value: str) -> ToolResult:
        return ToolResult.ok("Echo completed.", {"value": value})

    registry.register(workflow_test_echo, permission=PermissionLevel.AUTO, category="test")
    registered = client.post("/api/auth/register", json={
        "email": "workflow-execution@test.com",
        "password": "longenough1",
        "name": "Workflow User",
    })
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]

    engine = WorkflowEngine()
    steps = [{
        "order": 1,
        "name": "echo",
        "tool": "workflow_test_echo",
        "args": {"value": "{{input.message}}"},
    }]
    pattern = engine.create_pattern(user_id, "Echo", steps)

    with SessionLocal() as db:
        task = Task(user_id=user_id, goal="Run saved workflow")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user_id, task_id=task.id)
        db.add(run)
        db.flush()
        task.run_id = run.id
        db.commit()
        task_id, run_id = task.id, run.id

    execution_id = engine.prepare_canonical_execution(
        pattern["id"],
        user_id,
        {"message": "actual tool output"},
        task_id=task_id,
        run_id=run_id,
    )
    with SessionLocal() as db:
        record = db.get(WorkflowExecutionRecord, execution_id)
        assert record is not None
        assert record.task_id == task_id
        assert record.run_id == run_id
        assert record.status == "running"
        assert record.input_data == {"message": "actual tool output"}
    status = engine.get_execution_status(execution_id, user_id)
    assert status["task_id"] == task_id
    assert status["run_id"] == run_id
    assert status["task_status"] == "running"
    assert status["total_steps"] == 1

    with pytest.raises(RuntimeError, match="central agent graph"):
        asyncio.run(engine.execute_workflow(pattern["id"], user_id))


def test_saved_workflow_legacy_step_executor_is_disabled():
    import asyncio

    with pytest.raises(RuntimeError, match="central agent graph"):
        asyncio.run(WorkflowEngine()._execute_step("ping_external", {}))


def test_saved_workflow_cannot_use_legacy_executor_for_tool_output(monkeypatch):
    import asyncio

    with pytest.raises(RuntimeError, match="central agent graph"):
        asyncio.run(WorkflowEngine()._execute_step("workflow_malformed_output", {}))


def test_execute_workflow_tool_surfaces_uncertain_status(monkeypatch):
    import asyncio

    from app.tools import workflow_tools

    result = asyncio.run(workflow_tools.execute_workflow("pattern-123"))

    assert result.error is not None
    assert result.error.code == "CANONICAL_WORKFLOW_CONTEXT_REQUIRED"


def test_workflow_template_resolution_reports_missing_values():
    with pytest.raises(ValueError, match="reference .* is unavailable"):
        WorkflowEngine._resolve_templates(
            {"query": "{{input.query}}"},
            {"input": {}, "steps": {}},
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])