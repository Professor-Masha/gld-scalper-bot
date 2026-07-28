from datetime import datetime, timedelta, timezone

from gld_scalper.gold_volatility import build_gold_volatility_features
from gld_scalper.microstructure import build_microstructure_features
from gld_scalper.reasoning_agents import combine_reasoning


def _bars(start, count=40):
    bars = []
    price = 180.0
    for idx in range(count):
        price += 0.03
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=idx),
                "open": price - 0.02,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 2000,
            }
        )
    return bars


def test_microstructure_scores_liquid_tight_market():
    now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    quote = {"bid_price": 180.00, "ask_price": 180.04, "bid_size": 300, "ask_size": 260}
    trades = [
        {"timestamp": now - timedelta(seconds=idx * 3), "price": 180.02, "size": 10}
        for idx in range(20)
    ]

    result = build_microstructure_features(bars=_bars(now - timedelta(minutes=40)), quote=quote, recent_trades=trades, now=now)

    assert result["spread_regime"] == "tight"
    assert result["trade_count_60s"] >= 19
    assert result["liquidity_score"] > 0.70


def test_gold_volatility_detects_opening_drive_segment():
    now = datetime(2026, 1, 2, 14, 45, tzinfo=timezone.utc)
    result = build_gold_volatility_features(_bars(now - timedelta(minutes=40)), now=now)

    assert result["gold_intraday_segment"] == "opening_drive"
    assert result["expected_volatility_profile"] == "high"
    assert result["hilbert_cycle_phase"] is not None


def test_reasoning_agents_confirm_clean_long_setup():
    features = {
        "close": 181.0,
        "vwap": 180.5,
        "ema_9": 180.9,
        "ema_21": 180.4,
        "ema_50": 180.0,
        "tf5_ema_9": 180.8,
        "tf5_ema_21": 180.3,
        "tf15_ema_9": 180.7,
        "tf15_ema_21": 180.1,
        "macd_histogram": 0.08,
        "macd_histogram_slope": 0.03,
        "rsi_14": 60.0,
        "adx": 24.0,
        "pattern_classification": "proper_break_up",
        "pattern_quality": 0.86,
        "liquidity_score": 0.85,
        "spread_regime": "tight",
        "gold_volatility_regime": "normal",
        "cycle_state": "rising",
        "websocket_connected": True,
        "stream_stale": False,
    }

    result = combine_reasoning(features)

    assert result["agent_consensus"] == "LONG"
    assert result["pattern_agent_score"] > 0
