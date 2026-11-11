#!/usr/bin/env python
"""Load testing for Jalora — Phase 4.

Tests system stability under concurrent load.
Usage: python scripts/load_test.py [--concurrency N] [--requests N]
"""
import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LoadTestConfig:
    concurrency: int = 10
    requests_per_client: int = 20
    base_url: str = "http://localhost:8000"
    timeout: float = 60.0
    warmup: int = 5


@dataclass
class Metrics:
    total_requests: int = 0
    total_errors: int = 0
    latencies: list[float] = []
    status_codes: dict[int, int] = None
    
    def __post_init__(self):
        if self.status_codes is None:
            self.status_codes = {}


async def _make_request(
    client: httpx.AsyncClient,
    path: str,
    request_id: int,
    metrics: Metrics,
) -> None:
    """Make a single request and record metrics."""
    start = time.perf_counter()
    try:
        response = await client.get(path, timeout=metrics.timeout)
        latency = (time.perf_counter() - start) * 1000
        metrics.total_requests += 1
        metrics.latencies.append(latency)
        metrics.status_codes[response.status_code] = metrics.status_codes.get(response.status_code, 0) + 1
        if response.status_code >= 400:
            metrics.total_errors += 1
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        metrics.total_errors += 1
        metrics.latencies.append(latency)
        metrics.status_codes[499] = metrics.status_codes.get(499, 0) + 1  # Client error


async def _client_worker(
    client_id: int,
    config: LoadTestConfig,
    metrics: Metrics,
) -> None:
    """Worker that makes multiple requests."""
    async with httpx.AsyncClient(timeout=config.timeout) as client:
        for i in range(config.requests_per_client):
            await _make_request(
                client,
                "/api/health",
                client_id * config.requests_per_client + i,
                metrics,
            )


async def run_load_test(config: LoadTestConfig) -> Metrics:
    """Run the load test with configured concurrency."""
    metrics = Metrics()
    
    # Warmup
    print(f"Warming up ({config.warmup} requests)...")
    async with httpx.AsyncClient(timeout=config.timeout) as client:
        for _ in range(config.warmup):
            try:
                await client.get(f"{config.base_url}/api/health")
            except Exception:
                pass
    
    print(f"Running load test: {config.concurrency} clients x {config.requests_per_client} requests")
    start_time = time.perf_counter()
    
    tasks = [
        _client_worker(client_id, config, metrics)
        for client_id in range(config.concurrency)
    ]
    await asyncio.gather(*tasks)
    
    total_time = time.perf_counter() - start_time
    total_requests = metrics.total_requests or 1  # Avoid div by zero
    
    print(f"\n{'='*60}")
    print(f"LOAD TEST RESULTS")
    print(f"{'='*60}")
    print(f"Total requests: {metrics.total_requests}")
    print(f"Total errors: {metrics.total_errors}")
    print(f"Error rate: {100 * metrics.total_errors / total_requests:.2f}%")
    print(f"Total time: {total_time:.2f}s")
    print(f"Requests/sec: {total_requests / total_time:.2f}")
    
    if metrics.latencies:
        print(f"\nLatency (ms):")
        print(f"  min: {min(metrics.latencies):.2f}")
        print(f"  max: {max(metrics.latencies):.2f}")
        print(f"  avg: {statistics.mean(metrics.latencies):.2f}")
        print(f"  p50: {statistics.median(metrics.latencies):.2f}")
        if len(metrics.latencies) > 100:
            sorted_lats = sorted(metrics.latencies)
            p90 = sorted_lats[int(len(sorted_lats) * 0.9)]
            p99 = sorted_lats[int(len(sorted_lats) * 0.99)]
            print(f"  p90: {p90:.2f}")
            print(f"  p99: {p99:.2f}")
    
    print(f"\nStatus codes:")
    for code, count in sorted(metrics.status_codes.items()):
        print(f"  {code}: {count}")
    
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Jalora load test")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="Number of concurrent clients")
    parser.add_argument("--requests", "-n", type=int, default=20, help="Requests per client")
    parser.add_argument("--url", "-u", type=str, default="http://localhost:8000", help="Base URL")
    parser.add_argument("--timeout", "-t", type=float, default=60.0, help="Request timeout in seconds")
    parser.add_argument("--warmup", "-w", type=int, default=5, help="Warmup requests")
    
    args = parser.parse_args()
    
    config = LoadTestConfig(
        concurrency=args.concurrency,
        requests_per_client=args.requests,
        base_url=args.url,
        timeout=args.timeout,
        warmup=args.warmup,
    )
    
    metrics = asyncio.run(run_load_test(config))
    
    # Exit with error if error rate > 10%
    total = metrics.total_requests or 1
    if metrics.total_errors / total > 0.1:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()