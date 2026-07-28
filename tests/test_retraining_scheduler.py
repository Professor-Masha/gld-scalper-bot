from datetime import datetime, timezone

from gld_scalper.config import Settings
from gld_scalper.ml.retraining_scheduler import SafeRetrainingScheduler


def test_retraining_scheduler_disabled_does_not_start():
    settings = Settings(enable_scheduled_retraining=False)
    scheduler = SafeRetrainingScheduler(settings)
    assert scheduler.maybe_start(datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc)) is False


def test_retraining_scheduler_skips_regular_session_when_configured():
    settings = Settings(enable_scheduled_retraining=True, retrain_only_outside_regular_hours=True)
    scheduler = SafeRetrainingScheduler(settings)
    assert scheduler.maybe_start(datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)) is False


def test_retraining_scheduler_requires_clean_closed_episodes(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'retraining.db'}",
        retrain_min_clean_episodes=3,
    )
    scheduler = SafeRetrainingScheduler(settings)

    scheduler._run()

    result = scheduler.status()["last_result"]
    assert result == {
        "status": "skipped",
        "reason": "not enough clean completed execution episodes",
        "clean_episodes": 0,
        "minimum": 3,
    }
