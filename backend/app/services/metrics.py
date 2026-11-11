"""Request metrics collection (Phase L, sec 58).

A lightweight, thread-safe in-memory metrics collector.  Counts requests,
errors, and records per-endpoint latency.  In a multi-worker deployment the
numbers are per-worker (use the /api/metrics endpoint for a snapshot) — for
an aggregated view Redis-backed counters can be swapped in later.

The middleware that wires this up lives in ``app/main.py``.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class _Metrics:
    """Singleton metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._per_path_requests: dict[str, int] = defaultdict(int)
        self._per_path_errors: dict[str, int] = defaultdict(int)
        # Store all latencies for a rough average, plus per-path.
        self._all_latencies: list[float] = []
        self._per_path_latencies: dict[str, list[float]] = defaultdict(list)

    # -- mutation -----------------------------------------------------------
    def record(self, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._total_requests += 1
            self._per_path_requests[path] += 1
            if status_code >= 400:
                self._total_errors += 1
                self._per_path_errors[path] += 1
            self._all_latencies.append(latency_ms)
            self._per_path_latencies[path].append(latency_ms)
            # Cap stored latencies at 10 000 entries to bound memory.
            if len(self._all_latencies) > 10_000:
                # Keep the most recent half.
                keep = len(self._all_latencies) // 2
                self._all_latencies[:] = self._all_latencies[-keep:]
            for p, lats in list(self._per_path_latencies.items()):
                if len(lats) > 10_000:
                    self._per_path_latencies[p] = lats[-5_000:]

    # -- read ---------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            all_lats = list(self._all_latencies)
            per_path: dict[str, dict[str, Any]] = {}
            for path, lats in self._per_path_latencies.items():
                avg = sum(lats) / len(lats) if lats else 0.0
                per_path[path] = {
                    "requests": self._per_path_requests[path],
                    "errors": self._per_path_errors.get(path, 0),
                    "avg_response_ms": round(avg, 2),
                }
            avg_all = sum(all_lats) / len(all_lats) if all_lats else 0.0
            return {
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "avg_response_ms": round(avg_all, 2),
                "per_path": per_path,
            }

    def reset(self) -> None:
        """Reset all counters (useful in tests)."""
        with self._lock:
            self._total_requests = 0
            self._total_errors = 0
            self._per_path_requests.clear()
            self._per_path_errors.clear()
            self._all_latencies.clear()
            self._per_path_latencies.clear()


metrics = _Metrics()


def record_request(path: str, status_code: int, latency_ms: float) -> None:
    """Public helper — called by the metrics middleware in main.py."""
    metrics.record(path, status_code, latency_ms)


def get_snapshot() -> dict[str, Any]:
    """Public helper — returns the current metrics snapshot dict."""
    return metrics.snapshot()


def reset_metrics() -> None:
    """Public helper — resets all counters (primarily for tests)."""
    metrics.reset()


def _record_start() -> float:
    """Returns a timestamp to pass to record_request."""
    return time.perf_counter()


__all__ = [
    "metrics",
    "record_request",
    "get_snapshot",
    "reset_metrics",
    "_record_start",
]
