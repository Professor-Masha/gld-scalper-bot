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
