from datetime import datetime, timedelta, timezone

from gld_scalper.price_action import analyze_price_action


def _bar(ts, open_, high, low, close, volume=1000):
    return {
        "symbol": "GLD",
        "timeframe": "1Min",
        "timestamp": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_price_action_classifies_proper_break_up():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = []
    for idx in range(30):
        base = 99.78 + idx * 0.004
        bars.append(_bar(start + timedelta(minutes=idx), base, 100.00, 99.70, min(99.98, base + 0.03), 1200))
    bars.append(_bar(start + timedelta(minutes=31), 99.95, 100.38, 99.93, 100.32, 1800))

    result = analyze_price_action(bars)

    assert result["pattern_classification"] == "proper_break_up"
    assert result["proper_break"]
    assert result["pattern_quality"] >= 0.65


def test_price_action_classifies_false_break_up():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(start + timedelta(minutes=idx), 99.8, 100.0, 99.7, 99.9, 1000) for idx in range(30)]
    bars.append(_bar(start + timedelta(minutes=31), 99.92, 100.35, 99.85, 99.88, 1500))

    result = analyze_price_action(bars)

    assert result["pattern_classification"] == "false_break_up"
    assert result["false_break"]
