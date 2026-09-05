from __future__ import annotations

import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from .config import Settings
from .database import Database
from .utils.time_utils import ensure_utc, utc_now


class ExecutionLatencyTracker:
    """Non-blocking event-to-fill trace ledger for paper/live observability."""

    def __init__(self, settings: Settings, maximum_queue: int = 10_000) -> None:
        self.settings = settings
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=maximum_queue)
        self._starts: dict[str, tuple[int, int]] = {}
        self._client_traces: dict[str, str] = {}
        self._order_traces: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped_events = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="execution-latency-writer", daemon=True)
        self._thread.start()

    def begin(self, *, strategy_path: str, event_time: datetime | None = None, playbook: str | None = None, started_ns: int | None = None) -> str:
        self.start()
        trace_id = uuid.uuid4().hex
        now_ns = time.perf_counter_ns()
        with self._lock:
            self._starts[trace_id] = (started_ns or now_ns, now_ns)
        age_ms = None
        if event_time is not None:
            age_ms = max(0.0, (utc_now() - ensure_utc(event_time)).total_seconds() * 1000.0)
        self.record(trace_id, "event_received", strategy_path=strategy_path, playbook=playbook, event_age_ms=age_ms)
        return trace_id

    def bind(self, trace_id: str, *, client_order_id: str | None = None, order_id: str | None = None) -> None:
        with self._lock:
            if client_order_id:
                self._client_traces[str(client_order_id)] = trace_id
            if order_id:
                self._order_traces[str(order_id)] = trace_id

    def trace_for(self, *, client_order_id: str | None = None, order_id: str | None = None) -> str | None:
        with self._lock:
            return self._client_traces.get(str(client_order_id)) if client_order_id else self._order_traces.get(str(order_id)) if order_id else None

    def record(self, trace_id: str | None, stage: str, **values: Any) -> None:
        if not trace_id:
            return
        now_ns = time.perf_counter_ns()
        with self._lock:
            start_ns, previous_ns = self._starts.get(trace_id, (now_ns, now_ns))
            self._starts[trace_id] = (start_ns, now_ns)
        payload = {
            "timestamp": utc_now(), "trace_id": trace_id, "stage": stage,
            "elapsed_ms": (now_ns - start_ns) / 1_000_000.0,
            "stage_latency_ms": (now_ns - previous_ns) / 1_000_000.0,
            **values,
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self.dropped_events += 1

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is None:
                    self._queue.task_done()
                    continue
                try:
                    database.insert_execution_latency_event(item)
                finally:
                    self._queue.task_done()
        finally:
            database.close()
