from __future__ import annotations

import threading
from collections import defaultdict, deque
from time import monotonic
from typing import Any


class RequestLatencyMonitor:
    """Bounded in-memory timing for the local dashboard gateway."""

    def __init__(self, *, samples_per_route: int = 256) -> None:
        self.samples_per_route = max(32, min(int(samples_per_route), 2_000))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.samples_per_route)
        )
        self._statuses: dict[str, dict[int, int]] = defaultdict(dict)
        self._lock = threading.Lock()
        self.started_at = monotonic()

    def observe(self, method: str, path: str, elapsed_ms: float, status_code: int) -> None:
        route = f"{method.upper()} {_normalized_path(path)}"
        with self._lock:
            self._samples[route].append(max(0.0, float(elapsed_ms)))
            statuses = self._statuses[route]
            statuses[int(status_code)] = statuses.get(int(status_code), 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = {route: list(values) for route, values in self._samples.items()}
            statuses = {route: dict(values) for route, values in self._statuses.items()}
        all_values = [value for values in samples.values() for value in values]
        return {
            "uptime_seconds": round(monotonic() - self.started_at, 3),
            "sample_count": len(all_values),
            "overall": _summary(all_values),
            "routes": {
                route: {**_summary(values), "status_counts": statuses.get(route, {})}
                for route, values in sorted(samples.items())
            },
            "bounded_samples_per_route": self.samples_per_route,
        }


def _normalized_path(path: str) -> str:
    value = (path or "/").split("?", 1)[0]
    if value.startswith("/api/v1/memory-graph/nodes/"):
        return "/api/v1/memory-graph/nodes/{id}"
    if value.startswith("/api/results/"):
        return "/api/results/{name}"
    if value.startswith("/api/logs/"):
        return "/api/logs/{name}"
    return value


def _summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
        return round(ordered[index], 3)

    return {
        "count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": round(ordered[-1], 3),
    }
