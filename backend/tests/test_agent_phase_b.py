"""Phase B — agent loop completion: introspective research, intent, memory
capture, history summarization, replan-on-failure, source dedup and the
generic pause/resume lifecycle (spec §4, §9–13, §20–21, §24, §26, §29, §79)."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.intent import classify_intent
from app.services.memory_capture import extract_explicit_memory


def _token(client, email="phaseb@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Phase B",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _tc(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


# ---------------------------------------------------------------------------
# intent classification (spec §4)
# ---------------------------------------------------------------------------

def test_intent_scheduling_wins_over_coding_and_research():
    r = classify_intent("remind me to write a python script comparing prices")
    assert r.intent == "scheduling"
    assert "remind" in r.signals


def test_intent_research_beats_coding_and_task():
    r = classify_intent("debug this python file and research the latest prices")
    assert r.intent == "research"


def test_intent_research_via_url_and_keywords():
    r = classify_intent("look at https://example.com/about for sources")
    assert r.intent == "research"
    assert any("https://" in s for s in r.signals)


def test_intent_task_detection():
    r = classify_intent("write a draft proposal for the client")
    assert r.intent == "task"


def test_intent_chat_default():
    r = classify_intent("hello there, how are you?")
    assert r.intent == "chat"
    assert r.signals == ()


def test_research_intent_emits_event_and_injects_brief(client, fake_router):
    fake_router.script.append(AIMessage(content="Here is the research summary."))
    headers = _token(client, "res@test.com")

    with client.stream("POST", "/api/chat", json={
        "message": "research the latest AI news with sources", "stream": True,
    }, headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    lines = text.splitlines()
    assert "event: intent" in lines
    assert '"intent": "research"' in text
    assert "event: done" in lines

    first_call = fake_router.tape[0]
    sysmsg = next((m for m in first_call if isinstance(m, SystemMessage)), None)
    assert sysmsg is not None
    assert "Research mode:" in str(sysmsg.content)


def test_biomedical_hypothesis_gets_review_brief_without_planning(
    client, fake_router, monkeypatch
):
    from app.services import chat_service

    async def no_search_results(_request):
        return [], "test search unavailable"

    monkeypatch.setattr(
        chat_service, "_web_search_preflight", no_search_results
    )
    fake_router.script.append(AIMessage(
        content="I can assess the hypothesis conceptually, but not validate it clinically."
    ))
    headers = _token(client, "biomedical-review@test.com")

    with client.stream("POST", "/api/chat", json={
        "message": (
            "Novel biomedical hypothesis: CRISPR gene therapy for an HIV cure."
        ),
        "stream": True,
    }, headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    assert '"intent": "research"' in text
    assert '"biomedical_review"' in text
    assert "event: plan" not in text
    first_call = fake_router.tape[0]
    sysmsg = next(
        (message for message in first_call if isinstance(message, SystemMessage)),
        None,
    )
    assert sysmsg is not None
    assert "Biomedical hypothesis review:" in str(sysmsg.content)


def test_scheduling_intent_emits_event_and_injects_brief(client, fake_router):
    fake_router.script.append(AIMessage(content="I'll remind you to take a break."))
    headers = _token(client, "sched@test.com")

    with client.stream("POST", "/api/chat", json={
        "message": "remind me to take a break", "stream": True,
    }, headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    lines = text.splitlines()
    assert "event: intent" in lines
    assert '"intent": "scheduling"' in text
    assert "event: done" in lines

    first_call = fake_router.tape[0]
    sysmsg = next((m for m in first_call if isinstance(m, SystemMessage)), None)
    assert sysmsg is not None
    assert "Scheduling mode:" in str(sysmsg.content)


# ---------------------------------------------------------------------------
# explicit memory capture (spec §13 / §79)
# ---------------------------------------------------------------------------

def test_memory_capture_extraction():
    assert extract_explicit_memory("remember that my favorite color is blue") \
        == "my favorite color is blue"
    assert extract_explicit_memory("my favorite color is green") \
        == "The user's favorite color is green."
    assert extract_explicit_memory("don't forget to water the plants") \
        == "to water the plants"
    assert extract_explicit_memory("hello there") is None
    assert extract_explicit_memory("   ") is None
    assert extract_explicit_memory("") is None


def test_explicit_memory_capture_stores_recall(client, fake_router):
    fake_router.script.append(AIMessage(content="Noted! I'll remember that."))
    headers = _token(client, "mem@test.com")

    resp = client.post("/api/chat", json={
        "message": "remember that my favorite color is blue",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    mems = client.get("/api/memory", headers=headers).json()
    assert any("favorite color is blue" in m["content"] for m in mems)


def test_memory_capture_refuses_sensitive(client, fake_router):
    fake_router.script.append(AIMessage(content="I won't store that."))
    headers = _token(client, "sens@test.com")

    resp = client.post("/api/chat", json={
        "message": "remember that my password is hunter22",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    mems = client.get("/api/memory", headers=headers).json()
    assert all("password" not in m["content"] for m in mems)


# ---------------------------------------------------------------------------
# history summarization (spec §26 / §79)
# ---------------------------------------------------------------------------

@pytest.fixture
def small_summary_window(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("JALORA_SUMMARY_THRESHOLD", "4")
    monkeypatch.setenv("JALORA_SUMMARY_KEEP", "2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_compact_history_below_threshold_unchanged():
    from app.services.chat_service import compact_history

    msgs = [HumanMessage(content="a"), AIMessage(content="b")]
    assert compact_history(msgs, threshold=4, keep=2, summary="S") == msgs


def test_compact_history_replaces_older_part():
    from app.services.chat_service import compact_history

    msgs = [HumanMessage(content=f"m{i}") for i in range(6)]
    out = compact_history(msgs, threshold=4, keep=2, summary="The summary.")
    assert len(out) == 3
    assert isinstance(out[0], SystemMessage)
    assert "The summary." in out[0].content
    assert str(out[-1].content) == "m5"


def test_summarize_history_extracts_and_falls_back():
    from conftest import Box, FakeModel

    from app.services.chat_service import summarize_history

    async def _run():
        model = FakeModel(script=Box([AIMessage(content="  The summary.  ")]))
        got = await summarize_history(model, [HumanMessage(content="old")])
        assert got == "The summary."

        class _Boom:
            async def ainvoke(self, *a, **k):
                raise RuntimeError("model down")

        assert await summarize_history(_Boom(), [HumanMessage(content="old")]) is None
        assert await summarize_history(model, []) is None

    asyncio.run(_run())


def test_long_history_summarized_when_threshold_exceeded(
        client, fake_router, small_summary_window):
    headers = _token(client, "sum@test.com")
    fake_router.script.append(AIMessage(content="reply one"))
    fake_router.script.append(AIMessage(content="reply two"))
    r1 = client.post("/api/chat", json={"message": "first"}, headers=headers).json()
    r2 = client.post(
        "/api/chat",
        json={"message": "second", "conversation_id": r1["conversation_id"]},
        headers=headers,
    ).json()

    fake_router.script.append(AIMessage(content="compressed summary"))
    fake_router.script.append(AIMessage(content="final answer"))
    r3 = client.post(
        "/api/chat",
        json={"message": "third", "conversation_id": r2["conversation_id"]},
        headers=headers,
    ).json()
    assert r3["status"] == "completed"

    calls = list(fake_router.tape)
    summarize_call = calls[-2]  # last-but-one call is the summarization pass
    head = summarize_call[0]
    assert isinstance(head, SystemMessage)
    assert str(head.content).startswith("Summarize")

    final_call = calls[-1]
    assert any(
        isinstance(m, SystemMessage) and "Earlier conversation summary" in str(m.content)
        for m in final_call
    )


# ---------------------------------------------------------------------------
# replan on real tool failure (spec §9–10)
# ---------------------------------------------------------------------------

def test_tool_failure_marks_plan_step_failed_and_streams_revision(
        client, fake_router):
    headers = _token(client, "plan@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": [
            {
                "description": "open the source",
                "tool": "open_url",
                "arguments": {"url": "ftp://example.com/x"},
                "status": "pending",
            },
            {
                "description": "translate the source",
                "tool": "translate",
                "dependencies": ["step_1"],
            },
        ]}, "c_1"),
    ]))
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("open_url", {"url": "ftp://example.com/x"}, "c_2"),
    ]))
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": [
            {
                "id": "step_1",
                "description": "open the source",
                "tool": "open_url",
                "arguments": {"url": "ftp://example.com/x"},
            },
            {
                "id": "step_2",
                "description": "translate the source",
                "tool": "translate",
                "dependencies": ["step_1"],
            },
        ]}, "c_3"),
    ]))
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("translate", {"text": "source", "target": "sw"}, "c_4"),
    ]))

    with client.stream("POST", "/api/chat", json={
        "message": "Open the source and translate its contents into Swahili.",
        "stream": True,
    }, headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    plan_events = [line for line in text.splitlines() if line == "event: plan"]
    assert len(plan_events) >= 2, "expected initial plan + revised plan events"
    last_plan_data = text.rsplit("event: plan", 1)[1]
    assert "open_url failed" in last_plan_data
    assert '"status": "failed"' in last_plan_data
    assert len([line for line in text.splitlines() if line == "event: tool_end"]) >= 1

    tasks = client.get("/api/tasks", headers=headers).json()
    plan = tasks[0]["plan"]
    assert plan[0]["status"] == "failed"
    assert plan[1]["status"] == "pending"
    recovery_prompt = fake_router.model.tape[2]
    assert any(
        isinstance(message, SystemMessage)
        and "First revise the remaining work with update_plan" in str(message.content)
        for message in recovery_prompt
    )
    rejected_revision_turn = fake_router.model.tape[3]
    assert any(
        isinstance(message, ToolMessage)
        and "FAILED_STEP_UNCHANGED" in str(message.content)
        for message in rejected_revision_turn
    )
    guarded_turn = fake_router.model.tape[4]
    assert any(
        isinstance(message, ToolMessage)
        and "PLAN_REVISION_REQUIRED" in str(message.content)
        for message in guarded_turn
    )


def test_invalid_plan_is_reported_without_persisting_or_claiming_completion(
        client, fake_router):
    headers = _token(client, "invalid-plan@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc("update_plan", {"steps": [
            {"id": "first", "description": "First", "dependencies": ["second"]},
            {"id": "second", "description": "Second", "dependencies": ["first"]},
        ]}, "plan-cycle"),
    ]))
    fake_router.script.append(AIMessage(content="The plan was invalid and no steps ran."))

    data = client.post(
        "/api/chat", json={"message": "Plan a multi-step task"}, headers=headers
    ).json()
    assert data["status"] == "partial"
    assert "no steps ran" in data["reply"]
    task = client.get(f"/api/tasks/{data['task_id']}", headers=headers).json()
    assert task["plan"] is None
    messages = client.get(
        f"/api/conversations/{data['conversation_id']}/messages",
        headers=headers,
    ).json()
    assert any(
        message.get("role") == "tool" and "INVALID_PLAN" in message.get("content", "")
        for message in messages
    )


# ---------------------------------------------------------------------------
# source deduplication (spec §20–21)
# ---------------------------------------------------------------------------

def test_sources_deduped_across_searches(client, fake_router):
    headers = _token(client, "dedup@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[
        _tc(
            "search_web",
            {"query": "search for durable information about jalora"},
            "d1",
        ),
        _tc(
            "search_web",
            {"query": "search for durable information about jalora"},
            "d2",
        ),
    ]))
    fake_router.script.append(AIMessage(content="Here are the findings."))

    data = client.post("/api/chat", json={
        "message": "search for durable information about jalora",
    }, headers=headers).json()
    assert data["status"] == "completed"

    urls = [s["url"] for s in data["sources"]]
    assert len(urls) == len(set(urls)), "duplicate citations must be removed"
    assert 1 <= len(urls) <= 2  # mock provider yields 2 rows per query


# ---------------------------------------------------------------------------
# generic pause / resume (spec §24)
# ---------------------------------------------------------------------------

def test_generic_pause_defers_model_call_and_resume_continues(client, fake_router):
    from app.agents.graph import build_graph
    from app.services import run_registry
    from app.tools import registry

    fake_router.script.append(AIMessage(content="resumed reply"))
    uid, rid = "user-pause", "run-pause-1"
    run_registry.request_pause(uid, rid)

    def _state():
        return {
            "messages": [HumanMessage(content="hello", id="u1")],
            "steps": 0, "sources": [], "pending_approval": None,
            "approved_call_ids": [], "rejected_call_ids": [],
            "status": "running", "run_id": rid, "user_id": uid,
            "task_id": "", "plan": None, "tool_calls": 0, "policy": {},
        }

    async def _go():
        graph = build_graph(model=fake_router.model, registry=registry,
                            max_steps=8, tool_timeout=5.0, max_tool_calls=5,
                            max_retries=1, system_prompt="sys")
        paused = await graph.ainvoke(_state())
        assert paused["status"] == "paused"
        assert len(fake_router.tape) == 0, "no model call while paused"

        run_registry.clear_pause(uid, rid)
        resumed = await graph.ainvoke(paused)
        assert resumed["status"] == "completed"
        assert len(fake_router.tape) == 1

    try:
        asyncio.run(_go())
    finally:
        run_registry.clear_pause(uid, rid)


def test_pause_endpoint_needs_running_run(client, fake_router):
    headers = _token(client, "pause@test.com")
    fake_router.script.append(AIMessage(content="done"))
    run_id = client.post("/api/chat", json={"message": "hi"},
                         headers=headers).json()["run_id"]

    resp = client.post(f"/api/runs/{run_id}/pause", headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "RUN_NOT_PAUSABLE"


def test_pause_endpoint_ownership(client, fake_router):
    owner = _token(client, "ow@test.com")
    fake_router.script.append(AIMessage(content="done"))
    run_id = client.post("/api/chat", json={"message": "hi"},
                         headers=owner).json()["run_id"]

    intruder = _token(client, "intr@test.com")
    assert client.post(f"/api/runs/{run_id}/pause",
                       headers=intruder).status_code == 404
    assert client.post("/api/runs/nope-000000000000/pause",
                       headers=owner).status_code == 404
    assert client.post(f"/api/runs/{run_id}/pause").status_code == 401


def test_resume_endpoint_requires_paused_run(client, fake_router):
    headers = _token(client, "resume@test.com")
    fake_router.script.append(AIMessage(content="done"))
    run_id = client.post("/api/chat", json={"message": "hi"},
                         headers=headers).json()["run_id"]

    resp = client.post(f"/api/runs/{run_id}/resume", headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "RUN_NOT_RESUMABLE"


def test_resume_completes_persisted_paused_run(client, fake_router):
    from app.agents.graph import serialize_state
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun
    from app.models.conversation import Conversation

    headers = _token(client, "resume2@test.com")
    uid = client.get("/api/auth/me", headers=headers).json()["id"]
    rid = "run-persist-0001"
    state = {
        "messages": [HumanMessage(content="continue please", id="m1")],
        "steps": 0, "sources": [], "pending_approval": None,
        "approved_call_ids": [], "rejected_call_ids": [],
        "status": "paused", "run_id": rid, "user_id": uid,
        "task_id": "", "plan": None, "tool_calls": 0, "policy": {},
    }
    with SessionLocal() as db:
        conv = Conversation(user_id=uid, title="resume test")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(AgentRun(id=rid, user_id=uid, status="paused",
                        conversation_id=conv.id,
                        state_json=serialize_state(state), model="fake"))
        db.commit()

    fake_router.script.append(AIMessage(content="Back online!"))
    resp = client.post(f"/api/runs/{rid}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["reply"] == "Back online!"
    assert body["run_id"] == rid

    with SessionLocal() as db:
        run = db.get(AgentRun, rid)
        assert run.status == "completed"
        assert run.state_json is None, "state cleared after successful resume"


def test_resume_endpoint_ownership_and_404(client, fake_router):
    owner = _token(client, "resume-ow@test.com")
    fake_router.script.append(AIMessage(content="done"))
    run_id = client.post("/api/chat", json={"message": "hi"},
                         headers=owner).json()["run_id"]

    intruder = _token(client, "resume-intr@test.com")
    assert client.post(f"/api/runs/{run_id}/resume",
                       headers=intruder).status_code == 404
    assert client.post("/api/runs/nope-000000000000/resume",
                       headers=owner).status_code == 404
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 401