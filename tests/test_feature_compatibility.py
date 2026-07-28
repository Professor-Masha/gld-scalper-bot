from datetime import datetime, timedelta, timezone

import pytest

from gld_scalper.feature_engine import build_archive_compatible_features, build_feature_snapshot
from gld_scalper.ml.archive_dataset import _bar_features


def _bars():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(60):
        close = 100.0 + index * 0.01
        rows.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=index),
                "dt": start + timedelta(minutes=index),
                "open": close - 0.005,
                "high": close + 0.01,
                "low": close - 0.01,
                "close": close,
                "volume": 1_000 + index,
                "trade_count": 10 + index,
                "vwap": close,
            }
        )
    return rows


def test_live_archive_features_match_training_formulas():
    bars = _bars()
    live = build_archive_compatible_features(bars)
    training = _bar_features(bars, len(bars) - 1)

    assert live.keys() == training.keys()
    for key, value in training.items():
        if isinstance(value, bool):
            assert live[key] is value
        else:
            assert live[key] == pytest.approx(value)


def test_live_snapshot_includes_model_quote_depth_inputs():
    bars = _bars()
    now = bars[-1]["timestamp"] + timedelta(seconds=30)
    features = build_feature_snapshot(
        bars_1m=bars,
        quote={
            "timestamp": now,
            "bid_price": 100.50,
            "ask_price": 100.52,
            "bid_size": 125,
            "ask_size": 75,
        },
        now=now,
    )

    assert features["bid_size"] == 125
    assert features["ask_size"] == 75
    assert features["quote_missing"] is False
