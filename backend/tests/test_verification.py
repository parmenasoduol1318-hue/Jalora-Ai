"""Golden problem-set verification harness tests (Phase 8, spec §math/science).

These tests guard the compute engine against regressions via a curated dataset
of natural-language problems with independently-known answers
(``data/golden_problems.json``), checked by :mod:`app.services.verification`:
every problem must still compute to the expected result *and* render a
level-appropriate (offline) explanation. Coverage assertions ensure the set
keeps spanning the domain and pedagogical-level matrix.

Run from ``jalora/backend`` (venv python)."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the backend importable both when run under the backend cwd and from
# the repo root (pytest rootdir is the backend tests/ package).
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import verification as v  # noqa: E402

# Domains the golden set must exercise (a subset of the full domain matrix).
_REQUIRED_DOMAINS = {
    "arithmetic", "algebra", "calculus", "geometry", "units",
    "number_theory", "linalg", "complex", "statistics", "regression",
    "probability", "series", "physics", "chemistry",
}


def _problems():
    return v.load_golden()


# --------------------------------------------------------------------------- #
# dataset integrity + coverage
# --------------------------------------------------------------------------- #
def test_golden_dataset_loads():
    problems = _problems()
    assert problems, "golden_problems.json did not load any problems"
    ids = [p.id for p in problems]
    assert len(ids) == len(set(ids)), "golden problem ids must be unique"
    for p in problems:
        assert p.id and p.domain and p.level and p.query
        assert "ok" in p.expect, f"{p.id}: every problem must state expected ok"


def test_golden_dataset_covers_all_levels():
    levels = {p.level for p in _problems()}
    missing = set(v.KNOWN_LEVELS) - levels
    assert not missing, f"golden set is missing levels: {sorted(missing)}"


def test_golden_dataset_covers_required_domains():
    domains = {p.domain for p in _problems()}
    missing = _REQUIRED_DOMAINS - domains
    assert not missing, f"golden set is missing domains: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# the engine must satisfy the whole set
# --------------------------------------------------------------------------- #
def test_all_golden_problems_pass():
    report = v.run_all(_problems())
    assert report.total > 0
    if not report.ok:
        detail = "\n".join(
            f"[{c.problem.id}] {c.problem.query!r}: {'; '.join(c.failures)}"
            for c in report.failures()
        )
        raise AssertionError(f"{report.failed}/{report.total} golden problems failed:\n{detail}")


def test_report_breakdown_totals_are_consistent():
    report = v.run_all(_problems())
    assert sum(t for _, t in report.by_domain().values()) == report.total
    assert sum(t for _, t in report.by_level().values()) == report.total
    assert report.passed + report.failed == report.total
    assert report.summary().startswith(f"{report.passed}/{report.total}")


# --------------------------------------------------------------------------- #
# the harness itself must actually detect mismatches (guard the guard)
# --------------------------------------------------------------------------- #
def test_harness_detects_wrong_exact_value():
    wrong = v.GoldenProblem(
        id="selftest-wrong-exact", domain="arithmetic", level="primary",
        query="2+2", expect={"ok": True, "intent": "arithmetic", "exact": "5"},
    )
    check = v.check_problem(wrong)
    assert not check.passed
    assert any(f.startswith("exact:") for f in check.failures)


def test_harness_detects_ok_mismatch():
    wrong = v.GoldenProblem(
        id="selftest-not-computed", domain="arithmetic", level="primary",
        query="tell me a joke about penguins",
        expect={"ok": True, "exact": "42"},
    )
    check = v.check_problem(wrong)
    assert not check.passed
    assert any(f.startswith("ok:") for f in check.failures)


def test_harness_detects_units_mismatch():
    wrong = v.GoldenProblem(
        id="selftest-wrong-units", domain="physics", level="stem",
        query="kinetic energy of a 2 kg mass moving at 3 m/s",
        expect={"ok": True, "exact": "9", "units": "N"},
    )
    check = v.check_problem(wrong)
    assert not check.passed
    assert any(f.startswith("units:") for f in check.failures)


def test_harness_detects_numeric_approx_miss():
    wrong = v.GoldenProblem(
        id="selftest-wrong-numeric", domain="chemistry", level="stem",
        query="molar mass of H2O",
        expect={"ok": True, "numeric_approx": 100.0, "numeric_tol": 0.001},
    )
    check = v.check_problem(wrong)
    assert not check.passed
    assert any(f.startswith("numeric_approx:") for f in check.failures)


def test_harness_reports_engine_exception_as_failure(monkeypatch):
    def _boom(_text):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(v.math_engine, "solve", _boom)
    check = v.check_problem(v.GoldenProblem(
        id="selftest-crash", domain="arithmetic", level="primary",
        query="2+2", expect={"ok": True},
    ))
    assert not check.passed
    assert any("engine raised" in f for f in check.failures)


# --------------------------------------------------------------------------- #
# rendering + report plumbing
# --------------------------------------------------------------------------- #
def test_passing_problem_renders_level_header():
    problem = v.GoldenProblem(
        id="selftest-render", domain="arithmetic", level="highschool",
        query="2+2", expect={"ok": True, "exact": "4"},
    )
    check = v.check_problem(problem)
    assert check.passed, check.failures
    assert check.result is not None


def test_render_report_lists_domains_and_levels():
    report = v.run_all(_problems())
    text = v.render_report(report)
    assert "Golden problem-set verification" in text
    assert "By domain:" in text and "By level:" in text


def test_run_all_accepts_subset():
    subset = [
        v.GoldenProblem(id="s1", domain="arithmetic", level="primary",
                        query="2+2", expect={"ok": True, "exact": "4"}),
        v.GoldenProblem(id="s2", domain="arithmetic", level="primary",
                        query="3+4", expect={"ok": True, "exact": "7"}),
    ]
    report = v.run_all(subset)
    assert report.total == 2 and report.ok
    assert report.by_domain()["arithmetic"] == (2, 2)


def test_golden_path_resolution_and_missing_file(tmp_path):
    assert v.golden_path() is not None
    assert v.golden_path(tmp_path / "nope.json") is None
    assert v.load_golden(tmp_path / "nope.json") == []
