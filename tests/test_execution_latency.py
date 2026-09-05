from __future__ import annotations

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.execution_latency import ExecutionLatencyTracker
from gld_scalper.dashboard.telemetry import TelemetryRepository
from gld_scalper.utils.time_utils import utc_now


def test_execution_latency_tracker_persists_trace_and_summary(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'latency.db'}")
    database = Database(settings=settings)
    database.init_db()
    database.close()
    tracker = ExecutionLatencyTracker(settings)
    trace = tracker.begin(strategy_path="fast", event_time=utc_now(), playbook="spread_capture")
    tracker.bind(trace, client_order_id="client-1")
    tracker.record(trace, "decision_complete", strategy_path="fast", playbook="spread_capture")
    tracker.record(trace, "fill_received", client_order_id="client-1", order_id="order-1", status="fill")
    tracker.stop()

    summary = TelemetryRepository(settings.database_path).latency_summary()
    assert summary["sample_count"] == 3
    assert summary["trace_count"] == 1
    assert summary["stages"]["fill_received"]["count"] == 1
    assert summary["model_target_ms"] == 5.0
