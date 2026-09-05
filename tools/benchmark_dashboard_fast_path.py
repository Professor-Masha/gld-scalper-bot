"""Measure in-memory fast decisions with and without dashboard graph refresh load."""
from __future__ import annotations

import argparse
import json
import statistics
import threading
from datetime import timedelta
from pathlib import Path

from gld_scalper.config import PROJECT_ROOT, load_settings
from gld_scalper.dashboard.memory_graph import MemoryGraphRepository
from gld_scalper.fast_scalp import FastScalpEngine
from gld_scalper.utils.time_utils import utc_now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=4000)
    parser.add_argument("--target-p95-ms", type=float, default=5.0)
    args = parser.parse_args()
    settings = load_settings()
    settings.fast_scalp_interval_ms = 0
    settings.fast_scalp_min_quote_count = 1
    settings.fast_scalp_min_trade_count = 0
    repository = MemoryGraphRepository(PROJECT_ROOT, lambda: settings.database_path, ttl_seconds=30)
    repository.graph(types={"decision", "market", "model", "playbook", "risk"}, window="1d")

    baseline = run_engine(settings, args.iterations)
    stop = threading.Event()
    refreshes = [0]

    def dashboard_load() -> None:
        while not stop.is_set():
            repository.graph(types={"decision", "market", "model", "playbook", "risk"}, window="1d")
            refreshes[0] += 1

    thread = threading.Thread(target=dashboard_load, name="dashboard-benchmark-load", daemon=True)
    thread.start()
    loaded = run_engine(settings, args.iterations)
    stop.set(); thread.join(timeout=2)
    result = {
        "iterations": args.iterations,
        "dashboard_refreshes": refreshes[0],
        "target_p95_ms": args.target_p95_ms,
        "baseline": summarize(baseline),
        "dashboard_active": summarize(loaded),
    }
    result["passed"] = result["dashboard_active"]["p95_ms"] <= args.target_p95_ms
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(2)


def run_engine(settings, iterations: int) -> list[float]:
    engine = FastScalpEngine(settings)
    start = utc_now()
    latencies: list[float] = []
    for index in range(iterations):
        timestamp = start + timedelta(milliseconds=index)
        price = 400.0 + (index % 17) * 0.001
        decision = engine.on_event("quote", {
            "symbol": "GLD", "timestamp": timestamp, "bid_price": price,
            "ask_price": price + 0.01, "bid_size": 5000 + index % 200,
            "ask_size": 4800 + index % 150,
        }, timestamp)
        if decision is not None:
            latencies.append(decision.latency_ms)
    return latencies


def summarize(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    pick = lambda fraction: ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]
    return {"samples": len(ordered), "p50_ms": round(statistics.median(ordered), 4),
            "p95_ms": round(pick(0.95), 4), "p99_ms": round(pick(0.99), 4), "max_ms": round(ordered[-1], 4)}


if __name__ == "__main__":
    main()
