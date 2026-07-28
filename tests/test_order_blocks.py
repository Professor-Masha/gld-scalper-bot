from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.order_blocks import analyze_order_blocks, live_order_block_retest


def _bar(timestamp, open_price, high, low, close, volume=1_000):
    return {
        "symbol": "GLD",
        "timeframe": "1Min",
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_detects_confirmed_bullish_order_block_and_retest():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(start + timedelta(minutes=index), 100.00, 100.05, 99.95, 100.02)
        for index in range(15)
    ]
    bars.extend(
        [
            _bar(start + timedelta(minutes=15), 100.00, 100.03, 99.85, 99.90),
            _bar(start + timedelta(minutes=16), 99.90, 100.30, 99.88, 100.25, 2_000),
            _bar(start + timedelta(minutes=17), 100.25, 100.55, 100.20, 100.50, 2_200),
            _bar(start + timedelta(minutes=18), 100.50, 100.52, 99.92, 100.02, 1_800),
        ]
    )
    settings = Settings(
        order_block_timeframes=[1],
        order_block_displacement_atr=0.80,
        order_block_min_volume_ratio=1.0,
        order_block_retest_tolerance_pct=0.0005,
    )

    features, records = analyze_order_blocks(
        bars,
        settings,
        now=start + timedelta(minutes=20),
    )

    bullish = [row for row in records if row["direction"] == "bullish"]
    assert bullish
    assert bullish[0]["break_of_structure"]
    assert features["order_block_bias"] == "bullish"
    assert features["order_block_retest_active"]
    assert features["order_block_retest_direction"] == "bullish"


def test_live_order_block_retest_uses_current_midpoint():
    features = {
        "order_block_zone_low": 199.80,
        "order_block_zone_high": 200.10,
        "order_block_direction": "bullish",
    }

    active, direction = live_order_block_retest(features, 200.05, 0.0005)
    outside, _ = live_order_block_retest(features, 201.00, 0.0005)

    assert active
    assert direction == "bullish"
    assert not outside
