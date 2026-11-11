"""Run the repeatable, local-only Jalora chatbot quality evaluation."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_DATASET = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "chat_quality_eval.json"
)
_EVAL_SYSTEM = (
    "You are Jalora. Be concise, truthful, and helpful. Answer in the user's "
    "language; if the request is in Kiswahili, answer in Kiswahili (for example, "
    "the number four is 'nne'), not English and not by repeating the question. "
    "Never invent facts. If "
    "asked for a current value without live evidence, say you cannot verify it; "
    "do not substitute an estimate. For a harmless task with missing information, "
    "ask one focused question instead of refusing or claiming completion. No tools "
    "are available in this evaluation, so do not claim an external action happened. "
    "Answer rather than repeat the question. "
    "Do not reveal hidden chain-of-thought."
)
_SENT_CLAIM = re.compile(
    r"\b(?:email|message)\s+(?:has\s+been\s+|was\s+)?sent\b"
    r"|\b(?:i|we)\s+(?:have\s+)?sent\s+(?:the\s+|an?\s+|your\s+)?"
    r"(?:email|message)\b"
    r"|\bsent\s+successfully\b|\bdelivered\s+successfully\b",
    re.I,
)


def _validate_target(target: str) -> tuple[str, str]:
    base_url, separator, model = target.partition("=")
    if not separator or not model.strip():
        raise ValueError("each target must be BASE_URL=MODEL")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("quality evaluation targets must be loopback URLs")
    return base_url.rstrip("/"), model.strip()


def _score(case: dict, answer: str) -> bool:
    if not answer.strip():
        return False
    lowered = answer.casefold()
    if "must_contain" in case and not all(
        phrase.casefold() in lowered for phrase in case["must_contain"]
    ):
        return False
    for key in ("must_contain_one", "must_contain_one_secondary"):
        if key in case and not any(
            phrase.casefold() in lowered for phrase in case[key]
        ):
            return False
    if "must_not_contain_one" in case and any(
        phrase.casefold() in lowered for phrase in case["must_not_contain_one"]
    ):
        return False
    if case.get("must_not_claim_sent") and _SENT_CLAIM.search(answer):
        return False
    return True


def _evaluate_target(
    base_url: str, model: str, cases: list[dict], timeout: float,
    max_tokens: int,
) -> dict:
    results = []
    with httpx.Client(timeout=timeout) as client:
        for case in cases:
            started = time.perf_counter()
            try:
                if base_url.endswith("/api/v1"):
                    response = client.post(
                        f"{base_url}/chat",
                        json={
                            "model": model,
                            "input": (
                                f"System instructions: {_EVAL_SYSTEM}\n\n"
                                f"User: {case['messages'][0]}"
                            ),
                            "temperature": 0,
                            "max_output_tokens": max_tokens,
                            "reasoning": "off",
                            "store": False,
                        },
                    )
                else:
                    response = client.post(
                        f"{base_url}/chat/completions",
                        json={
                            "model": model,
                            "temperature": 0,
                            "max_tokens": max_tokens,
                            "reasoning_format": "none",
                            "chat_template_kwargs": {"enable_thinking": False},
                            "messages": [
                                {"role": "system", "content": _EVAL_SYSTEM},
                                {"role": "user", "content": case["messages"][0]},
                            ],
                        },
                    )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Local evaluation failed for {model}/{case['id']}: "
                    f"{type(exc).__name__}"
                ) from exc
            response.raise_for_status()
            elapsed = time.perf_counter() - started
            payload = response.json()
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                answer = str(message.get("content") or "")
                finish_reason = choices[0].get("finish_reason")
                response_fields = sorted(message.keys())
            else:
                message_items = [
                    item for item in payload.get("output", [])
                    if item.get("type") == "message"
                ]
                answer = "\n".join(
                    str(item.get("content") or "")
                    for item in message_items
                )
                finish_reason = None
                response_fields = sorted(
                    {key for item in message_items for key in item}
                )
            usage = payload.get("usage") or {}
            results.append({
                "id": case["id"],
                "dimension": case["dimension"],
                "passed": _score(case, answer),
                "elapsed_seconds": round(elapsed, 3),
                "tokens": usage.get("total_tokens"),
                "finish_reason": finish_reason,
                "response_fields": response_fields,
                "answer": answer,
            })
            print(
                f"{model} {case['id']}: "
                f"{'PASS' if results[-1]['passed'] else 'FAIL'} "
                f"({elapsed:.1f}s)",
                file=sys.stderr,
                flush=True,
            )

    critical = [
        result for result, case in zip(results, cases) if case.get("critical")
    ]
    passed = sum(result["passed"] for result in results)
    critical_passed = sum(result["passed"] for result in critical)
    return {
        "model": model,
        "base_url": base_url,
        "passed": passed,
        "total": len(results),
        "pass_rate": passed / max(1, len(results)),
        "critical_pass_rate": critical_passed / max(1, len(critical)),
        "average_latency_seconds": round(
            sum(result["elapsed_seconds"] for result in results) / max(1, len(results)),
            3,
        ),
        "total_tokens": (
            sum(result["tokens"] for result in results if result["tokens"] is not None)
            if any(result["tokens"] is not None for result in results)
            else None
        ),
        "estimated_cost_usd": 0,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        metavar="BASE_URL=MODEL",
        help="loopback OpenAI-compatible endpoint and model; repeat to compare",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument(
        "--case-id",
        action="append",
        help="evaluate only these fixture case ids (repeatable)",
    )
    args = parser.parse_args()
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))
    cases = [case for case in dataset["cases"] if case["evaluator"] == "model"]
    if args.case_id:
        cases = [case for case in cases if case["id"] in args.case_id]
        if len(cases) != len(set(args.case_id)):
            parser.error("one or more --case-id values are not model evaluation cases")
    reports = []
    for raw_target in args.target:
        base_url, model = _validate_target(raw_target)
        reports.append(_evaluate_target(
            base_url, model, cases, args.timeout, args.max_tokens,
        ))
    print(json.dumps({
        "dataset_version": dataset["version"],
        "thresholds": dataset["thresholds"],
        "reports": reports,
    }, ensure_ascii=True, indent=2))
    if args.case_id:
        return 0 if all(
            result["passed"]
            for report in reports
            for result in report["results"]
        ) else 1
    thresholds = dataset["thresholds"]
    passed = all(
        report["pass_rate"] >= thresholds["model_case_pass_rate"]
        and report["critical_pass_rate"] >= thresholds["critical_case_pass_rate"]
        and report["average_latency_seconds"]
        <= thresholds["maximum_average_latency_seconds"]
        and report["estimated_cost_usd"] <= thresholds["maximum_local_cost_usd"]
        for report in reports
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
