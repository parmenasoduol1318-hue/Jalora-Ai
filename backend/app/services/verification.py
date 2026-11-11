"""Golden problem-set verification harness (Phase 8).

The compute engine (:mod:`app.services.math_engine`, plus the physics and
chemistry engines) is a *correctness-critical* component: unlike prose, a wrong
number is objectively wrong. This module turns a curated, version-controlled set
of natural-language problems with independently-known answers
(``data/golden_problems.json``) into a repeatable regression + coverage check.

For every problem it runs the engine and verifies:

* the computed :class:`~app.services.math_engine.MathResult` fields (ok / intent /
  exact / numeric / units / verified), and
* the level-aware offline rendering (:mod:`app.services.pedagogy`) — the verified
  answer block is always present and the level's derivation header appears only
  for the levels that define one.

It is import-safe (no network, no model) and usable both as a library (the test
suite asserts the whole set passes) and as a CLI
(``python -m app.services.verification``) that prints a report and exits non-zero
on any failure.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, get_settings
from app.services import math_engine, pedagogy
from app.services.math_engine import MathResult

logger = logging.getLogger(__name__)

GOLDEN_FILE = "golden_problems.json"

# The levels the set must exercise for "per level" coverage to hold.
KNOWN_LEVELS: tuple[str, ...] = pedagogy.LEVEL_ORDER


@dataclass(frozen=True)
class GoldenProblem:
    """One curated problem with its expected engine output."""

    id: str
    domain: str
    level: str
    query: str
    expect: dict


@dataclass
class CheckResult:
    """Outcome of checking a single problem against the engine."""

    problem: GoldenProblem
    passed: bool
    failures: list[str] = field(default_factory=list)
    result: MathResult | None = None


@dataclass
class Report:
    """Aggregate verification result over a set of problems."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def _group(self, key: str) -> dict[str, tuple[int, int]]:
        buckets: dict[str, list[CheckResult]] = {}
        for r in self.results:
            buckets.setdefault(getattr(r.problem, key), []).append(r)
        return {
            name: (sum(1 for r in items if r.passed), len(items))
            for name, items in sorted(buckets.items())
        }

    def by_domain(self) -> dict[str, tuple[int, int]]:
        """Mapping ``domain -> (passed, total)``."""
        return self._group("domain")

    def by_level(self) -> dict[str, tuple[int, int]]:
        """Mapping ``level -> (passed, total)``."""
        return self._group("level")

    def domains(self) -> set[str]:
        return {r.problem.domain for r in self.results}

    def levels(self) -> set[str]:
        return {r.problem.level for r in self.results}

    def summary(self) -> str:
        pct = (100.0 * self.passed / self.total) if self.total else 0.0
        return f"{self.passed}/{self.total} problems passed ({pct:.1f}%)"


def golden_path(path: str | Path | None = None) -> Path | None:
    """Locate the golden dataset (explicit path, data dir, then package data)."""
    if path is not None:
        p = Path(path)
        return p if p.is_file() else None
    candidates = [get_settings().data_dir / GOLDEN_FILE,
                  BASE_DIR / "data" / GOLDEN_FILE]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_problem(raw: dict) -> GoldenProblem | None:
    try:
        return GoldenProblem(
            id=str(raw["id"]),
            domain=str(raw["domain"]),
            level=str(raw["level"]),
            query=str(raw["query"]),
            expect=dict(raw["expect"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("skipping malformed golden problem: %r", raw)
        return None


def load_golden(path: str | Path | None = None) -> list[GoldenProblem]:
    """Load the golden problems from the dataset file (empty list if missing)."""
    resolved = golden_path(path)
    if resolved is None:
        logger.warning("golden_problems.json not found (looked in %s and %s)",
                       get_settings().data_dir, BASE_DIR / "data")
        return []
    raw = json.loads(Path(resolved).read_text(encoding="utf-8"))
    problems: list[GoldenProblem] = []
    for item in raw.get("problems", []):
        parsed = _parse_problem(item)
        if parsed is not None:
            problems.append(parsed)
    return problems


# --------------------------------------------------------------------------- #
# checking
# --------------------------------------------------------------------------- #

def _as_float(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _check_expect(expect: dict, result: MathResult) -> list[str]:
    """Return a list of human-readable expectation failures (empty == passed)."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    if "ok" in expect and bool(result.ok) != bool(expect["ok"]):
        fail(f"ok: expected {expect['ok']!r}, got {result.ok!r}"
             + (f" (error={result.error!r})" if result.error else ""))
    if "intent" in expect and result.intent != expect["intent"]:
        fail(f"intent: expected {expect['intent']!r}, got {result.intent!r}")
    if "exact" in expect and result.exact != expect["exact"]:
        fail(f"exact: expected {expect['exact']!r}, got {result.exact!r}")
    if "exact_contains" in expect:
        haystack = f"{result.exact} {result.numeric}"
        for needle in expect["exact_contains"]:
            if str(needle) not in haystack:
                fail(f"exact_contains: {needle!r} not in {haystack!r}")
    if "numeric" in expect and result.numeric != expect["numeric"]:
        fail(f"numeric: expected {expect['numeric']!r}, got {result.numeric!r}")
    if "numeric_approx" in expect:
        target = _as_float(expect["numeric_approx"])
        got = _as_float(result.numeric or result.exact)
        tol = _as_float(expect.get("numeric_tol", 1e-6)) or 0.0
        if target is None:
            fail(f"numeric_approx: expected value {expect['numeric_approx']!r} is not numeric")
        elif got is None:
            fail(f"numeric_approx: engine produced no numeric value "
                 f"(exact={result.exact!r}, numeric={result.numeric!r})")
        elif abs(got - target) > tol:
            fail(f"numeric_approx: expected {target} (+/-{tol}), got {got}")
    if "units" in expect and result.units != expect["units"]:
        fail(f"units: expected {expect['units']!r}, got {result.units!r}")
    if "verified" in expect and bool(result.verified) != bool(expect["verified"]):
        fail(f"verified: expected {expect['verified']!r}, got {result.verified!r}")
    return failures


def _check_render(problem: GoldenProblem, result: MathResult) -> list[str]:
    """Verify the level-aware offline render for a computed result."""
    if not result.ok:
        return []
    failures: list[str] = []
    block = pedagogy.render(result, problem.level)
    if "**Answer:**" not in block:
        failures.append("render: verified answer block missing")
    spec = pedagogy.LEVELS.get(problem.level)
    if spec is not None and spec.header and not block.startswith(spec.header):
        failures.append(f"render: expected level header {spec.header!r}")
    answer = math_engine.format_answer(result)
    if not answer.startswith("> **Answer:**"):
        failures.append(f"format_answer: expected answer-only block, got {answer!r}")
    return failures


def check_problem(problem: GoldenProblem) -> CheckResult:
    """Run one problem through the engine and compare against its expectation."""
    try:
        result = math_engine.solve(problem.query)
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, not an error
        return CheckResult(problem, passed=False,
                           failures=[f"engine raised: {exc!r}"], result=None)
    failures = _check_expect(problem.expect, result)
    failures += _check_render(problem, result)
    return CheckResult(problem, passed=not failures, failures=failures, result=result)


def run_all(problems: list[GoldenProblem] | None = None) -> Report:
    """Verify every problem (defaults to the full golden dataset)."""
    if problems is None:
        problems = load_golden()
    return Report([check_problem(p) for p in problems])


# --------------------------------------------------------------------------- #
# reporting / CLI
# --------------------------------------------------------------------------- #

def render_report(report: Report) -> str:
    """Render a human-readable report (summary + per-domain/level + failures)."""
    lines = [
        "=== Golden problem-set verification ===",
        report.summary(),
        "",
        "By domain:",
    ]
    lines += [f"  {domain:<16} {passed}/{total}"
              for domain, (passed, total) in report.by_domain().items()]
    lines += ["", "By level:"]
    lines += [f"  {level:<16} {passed}/{total}"
              for level, (passed, total) in report.by_level().items()]
    failures = report.failures()
    if failures:
        lines += ["", f"Failures ({len(failures)}):"]
        for check in failures:
            lines.append(f"  - [{check.problem.id}] {check.problem.query!r}")
            lines += [f"      {detail}" for detail in check.failures]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: verify the golden set, print a report, exit 0/1."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else None
    problems = load_golden(path)
    if not problems:
        print("No golden problems found.", file=sys.stderr)
        return 1
    report = run_all(problems)
    print(render_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
