from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gld_scalper.market_state import (
    DATA_UNAVAILABLE,
    MARKET_CLOSED,
    MARKET_OPEN,
    MarketStateHeartbeat,
    inspect_market_clock,
    inspect_market_snapshot,
    model_inference_block_reason,
    plausible_expected_cost,
)
from gld_scalper.ml.transformer_features import flatten_transformer_features


NOW = datetime(2026, 9, 8, 14, 31, tzinfo=timezone.utc)


def test_closed_alpaca_clock_reports_next_open() -> None:
    result = inspect_market_clock(
        SimpleNamespace(is_open=False, next_open=NOW + timedelta(days=1), next_close=NOW + timedelta(days=1, hours=6)),
        now=NOW,
    )
    assert result.state == MARKET_CLOSED
    assert not result.evaluation_allowed
    assert result.next_open == NOW + timedelta(days=1)
    assert "closed" in result.reason.lower()


def test_fresh_aligned_snapshot_allows_evaluation() -> None:
    result = inspect_market_snapshot(
        now=NOW,
        bars=[{"timestamp": NOW - timedelta(seconds=60)}],
        quote={"timestamp": NOW - timedelta(seconds=1)},
        trade={"timestamp": NOW - timedelta(seconds=2)},
        stream_health={"websocket_connected": True, "stream_stale": False},
        bar_max_age_seconds=180,
        quote_max_age_seconds=15,
        trade_max_age_seconds=30,
        alignment_tolerance_seconds=120,
    )
    assert result.state == MARKET_OPEN
    assert result.evaluation_allowed
    assert result.aligned is True


def test_mixed_stale_snapshot_is_data_unavailable_not_poor_liquidity() -> None:
    result = inspect_market_snapshot(
        now=NOW,
        bars=[{"timestamp": NOW - timedelta(days=3)}],
        quote={"timestamp": NOW - timedelta(days=12)},
        trade={"timestamp": NOW - timedelta(days=12, seconds=3)},
        stream_health={"websocket_connected": False, "stream_stale": True},
        bar_max_age_seconds=180,
        quote_max_age_seconds=15,
        trade_max_age_seconds=30,
        alignment_tolerance_seconds=120,
    )
    assert result.state == DATA_UNAVAILABLE
    assert not result.evaluation_allowed
    assert "timestamps differ" in result.reason


def test_heartbeat_emits_changes_and_periodic_refresh_only() -> None:
    limiter = MarketStateHeartbeat(900)
    first = inspect_market_clock(SimpleNamespace(is_open=False), now=NOW)
    repeated = inspect_market_clock(SimpleNamespace(is_open=False), now=NOW + timedelta(minutes=1))
    due = inspect_market_clock(SimpleNamespace(is_open=False), now=NOW + timedelta(minutes=15))
    assert limiter.should_emit(first)
    assert not limiter.should_emit(repeated)
    assert limiter.should_emit(due)


def test_model_and_transformer_guards_reject_unusable_inputs() -> None:
    assert model_inference_block_reason({"market_state": MARKET_CLOSED})
    assert model_inference_block_reason({"market_data_aligned": False})
    assert plausible_expected_cost(0.0005, 0.005)
    assert not plausible_expected_cost(0.143988, 0.005)

    flattened = flatten_transformer_features(
        {
            "return_1m": 0.001,
            "regime": "bullish_trend",
            "timestamp": "2026-09-08T14:31:00Z",
            "signal_reason": "free text must not become a feature",
            "headline": "gold rises",
            "numeric_identifier": 9918273,
        }
    )
    assert flattened["return_1m"] == 0.001
    assert flattened["regime__bullish_trend"] == 1.0
    assert not any("timestamp" in key or "reason" in key or "headline" in key or "identifier" in key for key in flattened)
