#!/usr/bin/env python
"""Benchmark runner for Jalora optimization.

Usage:
    python scripts/run_benchmarks.py --output results.json
    
Runs the full evaluation suite and saves results as JSON and Markdown.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.structured_logger import get_system_metrics, get_gpu_memory_info


@dataclass
class BenchmarkResult:
    """Result of a single benchmark."""
    name: str
    category: str
    success: bool
    latency_ms: float
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results."""
    timestamp: str
    git_sha: str
    system_info: dict[str, Any]
    results: list[BenchmarkResult] = field(default_factory=list)
    
    @property
    def total_tests(self) -> int:
        return len(self.results)
    
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.success)
    
    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)
    
    @property
    def avg_latency_ms(self) -> float:
        latencies = [r.latency_ms for r in self.results if r.latency_ms > 0]
        return statistics.mean(latencies) if latencies else 0
    
    @property
    def p50_latency_ms(self) -> float:
        latencies = sorted([r.latency_ms for r in self.results if r.latency_ms > 0])
        if latencies:
            return latencies[len(latencies) // 2]
        return 0
    
    @property
    def p95_latency_ms(self) -> float:
        latencies = sorted([r.latency_ms for r in self.results if r.latency_ms > 0])
        if latencies:
            idx = int(len(latencies) * 0.95)
            return latencies[min(idx, len(latencies) - 1)]
        return 0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "git_sha": self.git_sha,
            "system_info": self.system_info,
            "summary": {
                "total_tests": self.total_tests,
                "passed": self.passed,
                "failed": self.failed,
                "pass_rate": self.passed / self.total_tests if self.total_tests > 0 else 0,
                "avg_latency_ms": self.avg_latency_ms,
                "p50_latency_ms": self.p50_latency_ms,
                "p95_latency_ms": self.p95_latency_ms,
            },
            "results": [
                {
                    "name": r.name,
                    "category": r.category,
                    "success": r.success,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                    "details": r.details,
                }
                for r in self.results
            ],
        }


def get_git_sha() -> str:
    """Get the current git SHA."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=pathlib.Path(__file__).resolve().parents[1]
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def run_pytest_suite() -> BenchmarkSuite:
    """Run the pytest test suite."""
    suite = BenchmarkSuite(
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=get_git_sha(),
        system_info={
            "gpu": get_gpu_memory_info(),
            "system": get_system_metrics(),
        },
    )
    
    print("=" * 60)
    print("RUNNING PYTEST SUITE")
    print("=" * 60)
    
    start_time = time.perf_counter()
    
    result = pytest.main(
        ["-q", str(pathlib.Path(__file__).resolve().parents[1] / "backend" / "tests"), "--tb=no"],
        standalone_mode=False,
    )
    
    elapsed = (time.perf_counter() - start_time) * 1000
    
    suite.results.append(BenchmarkResult(
        name="pytest_suite",
        category="test_suite",
        success=result == 0,
        latency_ms=elapsed,
        details={"exit_code": result},
    ))
    
    return suite


def run_math_benchmarks() -> BenchmarkSuite:
    """Run the mathematical problem benchmarks from golden_problems.json."""
    suite = BenchmarkSuite(
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=get_git_sha(),
        system_info={
            "gpu": get_gpu_memory_info(),
            "system": get_system_metrics(),
        },
    )
    
    problems_path = pathlib.Path(__file__).resolve().parents[1] / "data" / "golden_problems.json"
    
    if not problems_path.exists():
        print("Warning: golden_problems.json not found, skipping math benchmarks")
        return suite
    
    with open(problems_path) as f:
        data = json.load(f)
    
    problems = data.get("problems", [])
    print("=" * 60)
    print(f"RUNNING {len(problems)} MATH BENCHMARKS")
    print("=" * 60)
    
    from app.services.math_engine import solve
    from app.services.verification import check_problem, GoldenProblem
    
    pass_count = 0
    
    for prob in problems:
        start = time.perf_counter()
        try:
            # Create GoldenProblem and use check_problem
            golden_prob = GoldenProblem(
                id=prob["id"],
                domain=prob["domain"],
                level=prob["level"],
                query=prob["query"],
                expect=prob["expect"],
            )
            check_result = check_problem(golden_prob)
            latency = (time.perf_counter() - start) * 1000
            passed = check_result.passed
            if passed:
                pass_count += 1
            suite.results.append(BenchmarkResult(
                name=prob["id"],
                category=f"math_{prob['domain']}",
                success=passed,
                latency_ms=latency,
                details={"query": prob["query"], "result": str(check_result.result)[:100] if check_result.result else None},
            ))
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            suite.results.append(BenchmarkResult(
                name=prob["id"],
                category=f"math_{prob['domain']}",
                success=False,
                latency_ms=latency,
                error=str(e),
            ))
    
    print(f"Math benchmarks: {pass_count}/{len(problems)} passed")
    return suite


def run_api_health_check() -> BenchmarkResult:
    """Run a basic API health check."""
    print("Running API health check...")
    
    with TestClient(app) as client:
        start = time.perf_counter()
        resp = client.get("/api/health")
        latency = (time.perf_counter() - start) * 1000
        
        return BenchmarkResult(
            name="api_health",
            category="api",
            success=resp.status_code == 200,
            latency_ms=latency,
            details={"status_code": resp.status_code},
        )


def run_chat_benchmark() -> BenchmarkResult:
    """Run a basic chat benchmark."""
    print("Running chat benchmark...")
    
    with TestClient(app) as client:
        # Register a test user
        register_resp = client.post("/api/auth/register", json={
            "email": f"bench-{uuid.uuid4().hex[:8]}@test.com",
            "password": "testpass123",
            "name": "Benchmark User",
        })
        
        if register_resp.status_code != 201:
            return BenchmarkResult(
                name="chat_benchmark",
                category="chat",
                success=False,
                latency_ms=0,
                error=f"Registration failed: {register_resp.text}",
            )
        
        token = register_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        start = time.perf_counter()
        resp = client.post("/api/chat", json={"message": "Hello, who are you?"}, headers=headers)
        latency = (time.perf_counter() - start) * 1000
        
        return BenchmarkResult(
            name="chat_benchmark",
            category="chat",
            success=resp.status_code == 200,
            latency_ms=latency,
            details={"status_code": resp.status_code},
        )


def run_retrieval_benchmark() -> BenchmarkResult:
    """Run a document retrieval benchmark."""
    print("Running retrieval benchmark...")
    
    with TestClient(app) as client:
        # Register a test user
        register_resp = client.post("/api/auth/register", json={
            "email": f"retr-{uuid.uuid4().hex[:8]}@test.com",
            "password": "testpass123",
            "name": "Retrieval User",
        })
        
        if register_resp.status_code != 201:
            return BenchmarkResult(
                name="retrieval_benchmark",
                category="rag",
                success=False,
                latency_ms=0,
                error=f"Registration failed: {register_resp.text}",
            )
        
        token = register_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        start = time.perf_counter()
        resp = client.get("/api/documents?limit=10", headers=headers)
        latency = (time.perf_counter() - start) * 1000
        
        return BenchmarkResult(
            name="retrieval_benchmark",
            category="rag",
            success=resp.status_code == 200,
            latency_ms=latency,
            details={"status_code": resp.status_code, "documents_returned": len(resp.json())},
        )


def load_previous_results(output_path: pathlib.Path) -> dict | None:
    """Load previous benchmark results for comparison."""
    if output_path.exists():
        with open(output_path) as f:
            return json.load(f)
    return None


def compare_results(current: BenchmarkSuite, previous: dict | None) -> dict[str, Any]:
    """Compare current results with previous."""
    if not previous:
        return {"message": "No previous results to compare"}
    
    curr_summary = current.to_dict()["summary"]
    prev_summary = previous.get("summary", {})
    
    comparison = {
        "total_tests": {
            "current": curr_summary["total_tests"],
            "previous": prev_summary.get("total_tests", 0),
        },
        "pass_rate": {
            "current": curr_summary["pass_rate"],
            "previous": prev_summary.get("pass_rate", 0),
            "change": curr_summary["pass_rate"] - prev_summary.get("pass_rate", 0),
        },
        "avg_latency_ms": {
            "current": curr_summary["avg_latency_ms"],
            "previous": prev_summary.get("avg_latency_ms", 0),
            "change": curr_summary["avg_latency_ms"] - prev_summary.get("avg_latency_ms", 0),
        },
        "regression": False,
    }
    
    # Check for regressions
    if curr_summary["pass_rate"] < prev_summary.get("pass_rate", 1):
        comparison["regression"] = True
    if curr_summary["avg_latency_ms"] > prev_summary.get("avg_latency_ms", 0) * 1.1:
        comparison["regression"] = True
    
    return comparison


def generate_markdown_report(suite: BenchmarkSuite, comparison: dict | None = None) -> str:
    """Generate a Markdown report of the benchmark results."""
    lines = [
        "# Jalora Benchmark Report",
        "",
        f"**Timestamp:** {suite.timestamp}",
        f"**Git SHA:** {suite.git_sha}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Tests | {suite.total_tests} |",
        f"| Passed | {suite.passed} |",
        f"| Failed | {suite.failed} |",
        f"| Pass Rate | {suite.passed/suite.total_tests*100:.1f}% |",
        f"| Avg Latency | {suite.avg_latency_ms:.2f}ms |",
        f"| P50 Latency | {suite.p50_latency_ms:.2f}ms |",
        f"| P95 Latency | {suite.p95_latency_ms:.2f}ms |",
        "",
    ]
    
    if comparison and not comparison.get("message"):
        regression = "Yes (WARNING)" if comparison.get("regression") else "No"
        lines.extend([
            "## Comparison with Previous Run",
            "",
            "| Metric | Change |",
            "|--------|--------|",
            f"| Pass Rate | {comparison['pass_rate']['change']*100:+.1f}% |",
            f"| Avg Latency | {comparison['avg_latency_ms']['change']:+.2f}ms |",
            "",
            f"**Regression:** {regression}",
            "",
        ])
    
    lines.extend([
        "## Results by Category",
        "",
    ])
    
    # Group by category
    categories: dict[str, list[BenchmarkResult]] = {}
    for result in suite.results:
        cat = result.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(result)
    
    for cat, results in sorted(categories.items()):
        passed = sum(1 for r in results if r.success)
        total = len(results)
        avg_lat = statistics.mean([r.latency_ms for r in results if r.latency_ms > 0]) if results else 0
        
        lines.append(f"### {cat.title()}")
        lines.append("")
        lines.append("| Tests | Passed | Pass Rate | Avg Latency |")
        lines.append("|-------|--------|-----------|-------------|")
        lines.append(f"| {total} | {passed} | {passed/total*100:.1f}% | {avg_lat:.2f}ms |")
        lines.append("")
    
    # Failed tests
    failed_results = [r for r in suite.results if not r.success]
    if failed_results:
        lines.extend([
            "## Failed Tests",
            "",
        ])
        for r in failed_results:
            lines.append(f"- **{r.name}**: {r.error or 'Unknown error'}")
        lines.append("")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run Jalora benchmarks")
    parser.add_argument(
        "--output", "-o",
        default="bench_results.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--compare", "-c",
        help="JSON file to compare against",
    )
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip pytest suite",
    )
    
    args = parser.parse_args()
    
    output_path = pathlib.Path(args.output)
    prev_path = output_path.parent
    prev_name = output_path.stem + "_prev.json"
    compare_path = pathlib.Path(args.compare) if args.compare else pathlib.Path(str(prev_path) + "/" + prev_name)
    
    print("=" * 60)
    print("JALORA BENCHMARK SUITE")
    print("=" * 60)
    print(f"Output: {output_path}")
    print(f"Compare: {compare_path}")
    print()
    
    # Load previous results if available
    previous = load_previous_results(compare_path) if compare_path.exists() else None
    
    # Run benchmarks
    all_results = []
    
    # API health check
    health_result = run_api_health_check()
    all_results.append(health_result)
    status = "PASS" if health_result.success else "FAIL"
    print(f"  Health check: {status} ({health_result.latency_ms:.2f}ms)")
    
    # Chat benchmark
    chat_result = run_chat_benchmark()
    all_results.append(chat_result)
    status = "PASS" if chat_result.success else "FAIL"
    print(f"  Chat benchmark: {status} ({chat_result.latency_ms:.2f}ms)")
    
    # Retrieval benchmark
    retr_result = run_retrieval_benchmark()
    all_results.append(retr_result)
    status = "PASS" if retr_result.success else "FAIL"
    print(f"  Retrieval benchmark: {status} ({retr_result.latency_ms:.2f}ms)")
    
    # Math benchmarks
    math_suite = run_math_benchmarks()
    all_results.extend(math_suite.results)
    print(f"  Math benchmarks: {math_suite.passed}/{len(math_suite.results)} passed")
    
    # Create final suite
    suite = BenchmarkSuite(
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=get_git_sha(),
        system_info={
            "gpu": get_gpu_memory_info(),
            "system": get_system_metrics(),
        },
        results=all_results,
    )
    
    # Compare with previous
    comparison = compare_results(suite, previous)
    
    # Generate report
    report = generate_markdown_report(suite, comparison)
    report_path = output_path.with_suffix(".md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\nMarkdown report saved to: {report_path}")
    
    # Save JSON results
    with open(output_path, "w") as f:
        json.dump(suite.to_dict(), f, indent=2)
    print(f"JSON results saved to: {output_path}")
    
    # Print summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total tests: {suite.total_tests}")
    print(f"Passed: {suite.passed}")
    print(f"Failed: {suite.failed}")
    print(f"Pass rate: {suite.passed/suite.total_tests*100:.1f}%")
    print(f"Avg latency: {suite.avg_latency_ms:.2f}ms")
    print(f"P50 latency: {suite.p50_latency_ms:.2f}ms")
    print(f"P95 latency: {suite.p95_latency_ms:.2f}ms")
    
    # Print results table
    for r in all_results:
        status = "PASS" if r.success else "FAIL"
        print(f"  {r.name}: {status} ({r.latency_ms:.2f}ms)")
    
    # Save as previous for next run
    shutil.copy(output_path, compare_path)
    
    return 0 if suite.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())