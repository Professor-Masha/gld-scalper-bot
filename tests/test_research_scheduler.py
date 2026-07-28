from datetime import datetime, timezone
import threading
import time

from gld_scalper.config import Settings
from gld_scalper.research_data import ResearchDataScheduler


def test_research_scheduler_poll_never_blocks_live_decision_thread(monkeypatch):
    settings = Settings(
        enable_news_collection=True,
        enable_macro_series_collection=False,
        enable_market_calendar_collection=False,
        enable_feature_audit_tables=False,
    )
    scheduler = ResearchDataScheduler(settings)
    release = threading.Event()

    def slow_background(now, include_heavy):
        release.wait(timeout=2)

    monkeypatch.setattr(scheduler, "_run_background", slow_background)
    started = time.perf_counter()
    result = scheduler.poll(datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc))
    elapsed = time.perf_counter() - started

    assert result == []
    assert elapsed < 0.25
    assert scheduler.status()["in_progress"] is True
    release.set()
    assert scheduler.stop(timeout=1) is True


def test_research_scheduler_defers_heavy_labeling_during_regular_session():
    settings = Settings(
        enable_news_collection=False,
        enable_macro_series_collection=False,
        enable_market_calendar_collection=False,
        enable_feature_audit_tables=True,
    )
    scheduler = ResearchDataScheduler(settings)

    result = scheduler.poll(
        datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
        include_heavy=False,
    )

    assert result == []
    assert scheduler.status()["in_progress"] is False
