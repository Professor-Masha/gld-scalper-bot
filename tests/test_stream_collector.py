from datetime import timedelta

from gld_scalper.config import Settings
from gld_scalper.stream_collector import LiveDataStreamRuntime
from gld_scalper.utils.time_utils import utc_now


def test_stream_health_reports_counts_ages_and_stale_reason():
    settings = Settings(stale_data_seconds=10)
    runtime = LiveDataStreamRuntime(settings)
    now = utc_now()
    runtime.started_at = now - timedelta(seconds=30)

    health = runtime.health(now)

    assert health["stream_bar_count"] == 0
    assert health["stream_quote_count"] == 0
    assert health["stream_trade_count"] == 0
    assert health["stream_stale"]
    assert health["stream_stale_reason"] == "stream thread not alive"
