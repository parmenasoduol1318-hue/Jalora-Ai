"""P3 agent core: task lifecycle, plan tool, policy engine, limits, retries
(spec §4, §10, §27, §28, §61, §63, §68)."""
import json

from langchain_core.messages import AIMessage

from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry

# --- test tools -------------------------------------------------------------
_RETRY = {"calls": 0}


async def flaky_fetch(url: str) -> ToolResult:
    """Fetch a URL from the network. May fail transiently."""
    _RETRY["calls"] += 1
    if _RETRY["calls"] == 1:
        return ToolResult.fail("TIMEOUT", "connection timed out",
                               message="The fetch timed out.")
    return ToolResult.ok("Fetched.", {"url": url, "content": "ok content"})


_PAID = []


async def fake_payment(amount: int) -> ToolResult:
    """Simulate a payment. High-risk and irreversible."""
    _PAID.append(amount)
    return ToolResult.ok("Paid.", {"amount": amount})


if registry.get("flaky_fetch") is None:
    registry.register(flaky_fetch, permission=PermissionLevel.AUTO,
                      category="test", requires_internet=True,
                      side_effect="read_only")
if registry.get("fake_payment") is None:
    registry.register(fake_payment, permission=PermissionLevel.STRICT,
                      category="test")

_ACTIVE = {"queued", "running", "waiting_for_approval"}


def _headers(client, email="tasks@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Tasker",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _tool_contents(client, headers, conv_id):
    """Persisted ToolMessage contents for a conversation."""
    msgs = client.get(f"/api/conversations/{conv_id}/messages",
                      headers=headers).json()
    return [m["content"] for m in msgs if m.get("role") == "tool"]


def _tc(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


# --- gate scenario: plan -> tool -> update -> final -------------------------
def test_gate_multi_step_task_lifecycle(client, fake_router):
    headers = _headers(client)
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": [
            {
                "description": "Save a fact",
                "tool": "store_memory",
                "arguments": {"content": "The user likes automated tests"},
                "status": "active",
            },
            {"description": "Report back", "kind": "response", "status": "pending"},
        ]}, "c_plan1"),
    ]))
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("store_memory", {"content": "The user likes automated tests"}, "c_mem"),
    ]))
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": [
            {
                "description": "Save a fact",
                "tool": "store_memory",
                "arguments": {"content": "The user likes automated tests"},
                "status": "done",
            },
            {"description": "Report back", "kind": "response", "status": "active"},
        ]}, "c_plan2"),
    ]))
    fake_router.script.append(AIMessage(content="Saved the fact and reported."))

    data = client.post("/api/chat", json={
        "message": "Store this user preference in memory and report back."
    },
                       headers=headers).json()
    assert data["status"] == "completed"
    assert data["reply"] == "Saved the fact and reported."
    assert data["task_id"], "chat response must carry the task id"

    task = client.get(f"/api/tasks/{data['task_id']}", headers=headers).json()
    assert task["status"] == "completed"
    assert task["progress"] == 100         # completed tasks always read 100%
    assert task["current_step"] == 0       # all plan steps completed
    assert len(task["plan"]) == 2
    assert [s["status"] for s in task["steps"]] == ["done", "done"]

    assert len(client.get("/api/memory", headers=headers).json()) == 1
    activity = client.get("/api/activity", headers=headers).json()
    assert any(a["tool"] == "store_memory" and a["success"] for a in activity)

    # a second turn creates a second task
    fake_router.script.append(AIMessage(content="Hello again."))
    client.post("/api/chat", json={"message": "hi"}, headers=headers)
    tasks = client.get("/api/tasks", headers=headers).json()
    assert len(tasks) == 2
    assert data["task_id"] in {t["id"] for t in tasks}


def test_task_detail_exposes_redacted_step_evidence_and_scoped_approval(client):
    from app.database.session import SessionLocal
    from app.models.agent import Approval
    from app.models.task import Task, TaskStep
    from app.models.user import User

    headers = _headers(client, "task-detail-contract@test.com")
    with SessionLocal() as db:
        user = db.query(User).filter_by(email="task-detail-contract@test.com").one()
        task = Task(
            user_id=user.id,
            run_id="task-detail-run",
            goal="Send a reviewed message",
            status="waiting_for_approval",
            plan=[{
                "id": "send-step",
                "description": "Send the message",
                "tool": "send_email",
                "status": "active",
            }],
            progress=25,
        )
        db.add(task)
        db.flush()
        step = TaskStep(
            task_id=task.id,
            idx=0,
            plan_step_id="send-step",
            description="Send the message",
            status="active",
            dependencies=[],
            dependency_step_ids=[],
            capability="send_email",
            tool_id="send_email",
            tool_version="1",
            outcome_status="awaiting_approval",
            verification_status="pending",
            result_data={
                "execution_result": {
                    "safe_summary": "Message is ready for review.",
                    "evidence": [{
                        "type": "source",
                        "url": "https://example.org/contact",
                        "api_key": "do-not-return",
                    }],
                },
            },
        )
        db.add(step)
        db.flush()
        approval = Approval(
            user_id=user.id,
            run_id=task.run_id,
            task_id=task.id,
            task_step_id=step.id,
            plan_step_id=step.plan_step_id,
            tool_name="send_email",
            arguments={"to": "review@example.org", "api_key": "do-not-return"},
            level=3,
            status="pending",
            decision_status="pending",
            claim_status="unclaimed",
            payload_version=2,
        )
        db.add(approval)
        db.flush()
        db.commit()
        task_id = task.id

    detail = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    step_data = detail.json()["steps"][0]
    assert step_data["plan_step_id"] == "send-step"
    assert step_data["capability"] == "send_email"
    assert step_data["outcome_status"] == "awaiting_approval"
    assert step_data["verification_status"] == "pending"
    assert step_data["approval_status"] == "pending"
    assert step_data["safe_summary"] == "Message is ready for review."
    assert step_data["evidence"][0]["url"] == "https://example.org/contact"
    assert step_data["evidence"][0]["api_key"] == "***redacted***"
    assert detail.json()["approvals"][0]["arguments"]["to"] == "review@example.org"
    assert detail.json()["approvals"][0]["arguments"]["api_key"] == "***redacted***"
    assert detail.json()["approvals"][0]["can_approve"] is False
    assert detail.json()["approvals"][0]["can_edit"] is False

    matching = client.get(
        "/api/tasks",
        headers=headers,
        params={"run_id": "task-detail-run"},
    )
    assert [row["id"] for row in matching.json()] == [task_id]
    other_user = _headers(client, "task-detail-other@test.com")
    assert client.get(
        "/api/tasks",
        headers=other_user,
        params={"run_id": "task-detail-run"},
    ).json() == []


# --- policy engine ----------------------------------------------------------
def test_autonomy_zero_blocks_tools_but_not_planning(client, fake_router):
    headers = _headers(client)
    assert client.patch("/api/settings", json={"autonomy": 0},
                        headers=headers).status_code == 200

    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": ["gather", "report"]}, "c_p"),
        _tc("store_memory", {"content": "should be blocked"}, "c_m"),
    ]))
    fake_router.script.append(AIMessage(content="Answered without tools."))

    data = client.post("/api/chat", json={"message": "remember this"},
                       headers=headers).json()
    assert data["status"] == "partial"
    assert data["reply"] == "Answered without tools."

    # the tool was denied, nothing stored
    assert client.get("/api/memory", headers=headers).json() == []
    contents = _tool_contents(client, headers, data["conversation_id"])
    assert any("AUTONOMY_DISABLED" in c for c in contents)

    # ...but the internal planner still worked
    task = client.get(f"/api/tasks/{data['task_id']}", headers=headers).json()
    assert [
        {
            key: step[key]
            for key in ("id", "description", "status", "dependencies")
        }
        for step in task["plan"]
    ] == [
        {
            "id": "step_1", "description": "gather",
            "status": "pending", "dependencies": [],
        },
        {
            "id": "step_2", "description": "report",
            "status": "pending", "dependencies": [],
        },
    ]
    assert all(step["canonical_step_id"] for step in task["plan"])


def test_assisted_mode_allows_read_only_blocks_actions(client, fake_router, confirm_calls):
    headers = _headers(client, "assist@test.com")
    assert client.patch("/api/settings", json={"autonomy": 1},
                        headers=headers).status_code == 200

    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("ping_external", {"message": "hi"}, "c_ping"),
    ]))
    fake_router.script.append(AIMessage(content="Done without sending."))

    data = client.post("/api/chat", json={"message": "ping someone"},
                       headers=headers).json()
    # no approval pause: the action is refused outright at L1
    assert data["status"] == "partial"
    assert data["approval"] is None
    assert confirm_calls == []
    contents = _tool_contents(client, headers, data["conversation_id"])
    assert any("AUTONOMY_TOO_LOW" in c for c in contents)


def test_user_can_deny_a_tool_via_policy(client, fake_router):
    headers = _headers(client, "deny@test.com")
    resp = client.patch("/api/settings",
                        json={"tool_policies": {"store_memory": "denied"}},
                        headers=headers)
    assert resp.status_code == 200

    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("store_memory", {"content": "never stored"}, "c_m"),
    ]))
    fake_router.script.append(AIMessage(content="Understood."))

    data = client.post("/api/chat",     json={"message": "store something in memory"},
                       headers=headers).json()
    assert data["status"] == "partial"
    assert client.get("/api/memory", headers=headers).json() == []
    contents = _tool_contents(client, headers, data["conversation_id"])
    assert any("TOOL_DENIED" in c for c in contents)


def test_policy_can_require_approval_for_an_auto_tool(client, fake_router):
    headers = _headers(client, "tighten@test.com")
    assert client.patch("/api/settings",
                        json={"tool_policies": {"store_memory": "approval"}},
                        headers=headers).status_code == 200

    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("store_memory", {"content": "needs approval"}, "c_m"),
    ]))
    fake_router.script.append(AIMessage(content="Stored."))

    data = client.post("/api/chat",     json={"message": "store it in memory"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"
    assert data["approval"]["tool_name"] == "store_memory"
    assert client.get("/api/memory", headers=headers).json() == []

    # task reflects the pause, then completes after approval
    task = client.get(f"/api/tasks/{data['task_id']}", headers=headers).json()
    assert task["status"] == "waiting_for_approval"

    result = client.post(f"/api/approvals/{data['approval']['id']}/approve",
                         headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert len(client.get("/api/memory", headers=headers).json()) == 1
    task = client.get(f"/api/tasks/{data['task_id']}", headers=headers).json()
    assert task["status"] == "completed"


def test_automatic_policy_respects_level_and_strict_tools(client, fake_router, confirm_calls):
    headers = _headers(client, "auto@test.com")

    # L2: "automatic" cannot loosen a CONFIRM tool
    assert client.patch("/api/settings", json={
        "autonomy": 2, "tool_policies": {"ping_external": "automatic"},
    }, headers=headers).status_code == 200
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("ping_external", {"message": "x"}, "c_p"),
    ]))
    fake_router.script.append(AIMessage(content="waiting"))
    data = client.post("/api/chat", json={"message": "ping"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"
    assert confirm_calls == []
    client.post(f"/api/approvals/{data['approval']['id']}/reject", headers=headers)

    # L3 + "automatic": CONFIRM tool runs without asking (by design)
    assert client.patch("/api/settings", json={
        "autonomy": 3, "tool_policies": {"ping_external": "automatic"},
    }, headers=headers).status_code == 200
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("ping_external", {"message": "y"}, "c_p2"),
    ]))
    fake_router.script.append(AIMessage(content="sent"))
    data = client.post("/api/chat", json={"message": "ping again"},
                       headers=headers).json()
    assert data["status"] == "completed"
    assert data["reply"] == "sent"
    assert confirm_calls == [{"message": "y"}]

    # STRICT is never loosened, even at L3 with "automatic"
    assert client.patch("/api/settings", json={
        "autonomy": 3, "tool_policies": {"fake_payment": "automatic"},
    }, headers=headers).status_code == 200
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("fake_payment", {"amount": 5}, "c_pay"),
    ]))
    fake_router.script.append(AIMessage(content="payment pending"))
    data = client.post("/api/chat", json={"message": "pay 5"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"
    assert _PAID == []
    client.post(f"/api/approvals/{data['approval']['id']}/reject", headers=headers)


# --- retries (spec §61) -----------------------------------------------------
def test_transient_tool_failure_is_retried(client, fake_router):
    headers = _headers(client, "retry@test.com")
    _RETRY["calls"] = 0

    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("flaky_fetch", {"url": "https://example.com"}, "c_f"),
    ]))
    fake_router.script.append(AIMessage(content="Got the page."))

    data = client.post("/api/chat", json={"message": "fetch a page"},
                       headers=headers).json()
    assert data["status"] == "completed"
    assert _RETRY["calls"] == 2, "first attempt failed, second succeeded"

    activity = client.get("/api/activity", headers=headers).json()
    flaky = [a for a in activity if a["tool"] == "flaky_fetch"]
    assert flaky and flaky[0]["success"]  # audited with the final honest result


# --- tool-call budget (spec §63) -------------------------------------------
def test_tool_call_budget_stops_execution_and_wraps_up(client, fake_router):
    headers = _headers(client, "budget@test.com")
    calls = [_tc("store_memory", {"content": f"budget note {i}"}, f"b{i}")
             for i in range(7)]  # test env sets the budget to 5
    fake_router.script.append(AIMessage(content="", tool_calls=calls))
    fake_router.script.append(AIMessage(content="Budget reached; summary done."))

    data = client.post("/api/chat", json={"message": "store everything in memory"},
                       headers=headers).json()
    assert data["status"] == "partial"
    assert data["reply"] == "Budget reached; summary done."

    memories = client.get("/api/memory", headers=headers).json()
    assert len(memories) == 5, "6th call must be blocked by the budget"
    contents = _tool_contents(client, headers, data["conversation_id"])
    assert any("TOOL_LIMIT" in c for c in contents)
    assert any("SKIPPED" in c for c in contents)  # wrap-up fillers


# --- tasks API --------------------------------------------------------------
def test_task_cancel_during_approval(client, fake_router, confirm_calls):
    headers = _headers(client, "cancel@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("ping_external", {"message": "do not send"}, "c_ping"),
    ]))
    fake_router.script.append(AIMessage(content="cancelled"))

    data = client.post("/api/chat", json={"message": "ping"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"
    task_id = data["task_id"]

    resp = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    approvals = client.get("/api/approvals", headers=headers).json()
    approval = next(row for row in approvals if row["id"] == data["approval"]["id"])
    assert approval["status"] == "cancelled"
    assert approval["decision_status"] == "cancelled"

    # A late approval must be rejected persistently, not just by an in-memory stop flag.
    late_approval = client.post(
        f"/api/approvals/{data['approval']['id']}/approve",
        headers=headers,
    )
    assert late_approval.status_code == 400
    assert late_approval.json()["error"]["code"] == "APPROVAL_DECIDED"
    assert confirm_calls == []
    task = client.get(f"/api/tasks/{task_id}", headers=headers).json()
    assert task["status"] == "cancelled", "cancel decision must survive resume"
    assert task["resumable"] is False
    runs = client.get("/api/runs", headers=headers).json()
    run = next(row for row in runs if row["id"] == task["run_id"])
    assert run["status"] == "stopped"


def test_task_cancel_refuses_while_approved_action_is_in_progress(
    client, fake_router, confirm_calls,
):
    from app.database.session import SessionLocal
    from app.models.agent import Approval

    headers = _headers(client, "cancel-in-flight@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("ping_external", {"message": "already approved"}, "c_ping"),
    ]))
    data = client.post("/api/chat", json={"message": "ping"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"

    with SessionLocal() as db:
        approval = db.get(Approval, data["approval"]["id"])
        approval.status = "sending"
        db.commit()

    response = client.post(
        f"/api/tasks/{data['task_id']}/cancel",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TASK_ACTION_IN_PROGRESS"
    task = client.get(
        f"/api/tasks/{data['task_id']}",
        headers=headers,
    ).json()
    assert task["status"] == "waiting_for_approval"
    assert confirm_calls == []


def test_task_api_guards(client, fake_router):
    headers = _headers(client, "guards1@test.com")
    other = _headers(client, "guards2@test.com")

    fake_router.script.append(AIMessage(content="quick answer"))
    data = client.post("/api/chat", json={"message": "hello"},
                       headers=headers).json()
    task_id = data["task_id"]

    # completed tasks cannot be cancelled
    resp = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TASK_NOT_CANCELLABLE"

    # other users and unknown ids are 404
    assert client.get(f"/api/tasks/{task_id}", headers=other).status_code == 404
    assert client.get("/api/tasks/nonexistenttaskid000000000000000001",
                      headers=headers).status_code == 404
    assert client.post("/api/tasks/nonexistenttaskid000000000000000001/cancel",
                       headers=headers).status_code == 404

    # authentication is required
    assert client.get("/api/tasks").status_code == 401


# --- settings API -----------------------------------------------------------
def test_settings_api_roundtrip(client):
    headers = _headers(client, "settings@test.com")

    got = client.get("/api/settings", headers=headers)
    assert got.status_code == 200
    body = got.json()
    assert body["autonomy"] == 2
    assert body["tool_policies"] == {}
    names = {t["name"] for t in body["tools"]}
    assert {"store_memory", "update_plan", "search_web"} <= names

    bad = client.patch("/api/settings", json={"autonomy": 9}, headers=headers)
    assert bad.status_code == 422

    bad = client.patch("/api/settings",
                       json={"tool_policies": {"no_such_tool": "denied"}},
                       headers=headers)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "UNKNOWN_TOOL"

    bad = client.patch("/api/settings",
                       json={"tool_policies": {"store_memory": "wild"}},
                       headers=headers)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_POLICY"

    ok = client.patch("/api/settings", json={
        "autonomy": 3, "memory_enabled": False, "locale": "sw",
        "tool_policies": {"store_memory": "denied"},
    }, headers=headers)
    assert ok.status_code == 200
    body = ok.json()
    assert body["autonomy"] == 3
    assert body["memory_enabled"] is False
    assert body["locale"] == "sw"
    assert body["tool_policies"] == {"store_memory": "denied"}

    assert client.get("/api/settings").status_code == 401
