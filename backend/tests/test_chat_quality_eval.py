from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from app.agents.intent import classify_intent
from app.tools.routing import tool_names_for_intent

_DATASET = (
    Path(__file__).parent / "fixtures" / "chat_quality_eval.json"
)
_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "evaluate_chat_quality.py"


def _runner():
    spec = importlib.util.spec_from_file_location("chat_quality_eval_runner", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_eval_routing_cases_match_measured_baseline():
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))
    cases = [case for case in dataset["cases"] if case["evaluator"] == "routing"]
    assert len(cases) == 3

    for case in cases:
        result = classify_intent(case["prompt"])
        assert result.intent == case["expected_intent"], case["id"]
        selected = tool_names_for_intent(case["prompt"]) or []
        assert set(case["required_tools"]) <= set(selected), case["id"]


def test_quality_eval_defines_repeatable_scoring_and_thresholds():
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))
    assert dataset["version"] == 2
    thresholds = dataset["thresholds"]
    assert 0 < thresholds["model_case_pass_rate"] <= 1
    assert thresholds["critical_case_pass_rate"] == 1
    dimensions = {case["dimension"] for case in dataset["cases"]}
    assert {
        "factuality", "tool_selection", "task_decomposition",
        "argument_quality", "refusal", "latency", "cost", "recovery",
        "multilingual", "truthfulness", "uncertainty", "clarification",
    } <= dimensions
    recovery_tests = [
        case["test"] for case in dataset["cases"]
        if case["evaluator"] == "system_test"
    ]
    assert (
        "test_workspace_projects.py::"
        "test_external_agent_approval_rejection_recovers_after_backend_restart"
    ) in recovery_tests
    assert thresholds["maximum_average_latency_seconds"] > 0
    assert thresholds["maximum_local_cost_usd"] == 0


def test_quality_eval_scoring_rejects_empty_and_unconfirmed_actions():
    runner = _runner()
    sent_case = {"must_not_claim_sent": True}
    assert not runner._score(sent_case, "")
    assert not runner._score(sent_case, "The email has been sent.")
    assert not runner._score(sent_case, "I sent an email to the recipient.")
    assert runner._score(sent_case, "I prepared a draft; it has not been sent.")


def test_quality_eval_targets_are_restricted_to_loopback():
    runner = _runner()
    assert runner._validate_target(
        "http://127.0.0.1:1234/v1=qwen-local"
    ) == ("http://127.0.0.1:1234/v1", "qwen-local")
    try:
        runner._validate_target("https://example.com/v1=model")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-local evaluation targets must be rejected")
