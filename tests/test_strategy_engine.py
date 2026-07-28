from datetime import datetime, timezone

from gld_scalper.config import Settings
from gld_scalper.strategy_engine import StrategyEngine


def _base_features():
    return {
        "timestamp": datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        "close": 101.0,
        "latest_price": 101.0,
        "ema_9": 100.8,
        "ema_21": 100.0,
        "ema_50": 99.0,
        "vwap": 100.4,
        "macd_histogram": 0.10,
        "macd_histogram_slope": 0.04,
        "rsi_14": 60.0,
        "relative_volume": 1.5,
        "spread_pct": 0.0005,
        "atr_pct": 0.002,
        "atr_14": 0.20,
        "breakout_candle": True,
        "breakdown_candle": False,
        "recent_high_20": 100.5,
        "recent_low_20": 98.0,
        "tf5_ema_9": 100.5,
        "tf5_ema_21": 100.0,
        "tf15_ema_9": 100.3,
        "tf15_ema_21": 99.8,
        "tf15_close": 100.8,
        "tf15_vwap": 100.1,
        "UUP_roc": -0.001,
        "IAU_roc": 0.001,
        "GDX_roc": 0.001,
        "similar_setup_win_rate": 0.58,
        "vwap_deviation": 0.005,
        "adx": 25.0,
        "bollinger_bandwidth": 0.02,
        "realized_volatility": 0.02,
        "data_age_seconds": 30.0,
        "is_shortable": True,
    }


def test_bullish_signal_generation():
    signal = StrategyEngine(Settings()).evaluate(_base_features(), has_champion_model=False)
    assert signal.decision == "LONG"
    assert signal.bullish_score >= 75


def test_paper_ml_advice_is_bounded_and_adds_to_matching_direction():
    settings = Settings(paper_learning_mode=True, paper_ml_max_score_adjustment=5.0)
    engine = StrategyEngine(settings)
    baseline, _ = engine._bullish_score(_base_features())
    features = _base_features()
    features.update(
        {
            "ml_participating": True,
            "ml_model_role": "paper_shadow",
            "ml_predicted_direction": "long_good",
            "ml_confidence": 0.80,
            "ml_advice_eligible": True,
        }
    )
    advised, reasons = engine._bullish_score(features)

    assert advised - baseline == 4.0
    assert "paper ML adviser supports long" in reasons


def test_required_paper_ml_profile_blocks_rule_signal_when_features_are_incompatible():
    settings = Settings(paper_learning_mode=True, paper_require_ml_model=True)
    features = _base_features()
    features["ml_input_compatible"] = False

    signal = StrategyEngine(settings).evaluate(features, has_champion_model=False)

    assert signal.decision == "NO_TRADE"
    assert "required ML feature profile unavailable" in signal.reason


def test_bearish_signal_generation():
    features = _base_features()
    features.update(
        {
            "close": 99.0,
            "latest_price": 99.0,
            "ema_9": 99.0,
            "ema_21": 100.0,
            "ema_50": 101.0,
            "vwap": 100.2,
            "macd_histogram": -0.10,
            "macd_histogram_slope": -0.04,
            "rsi_14": 40.0,
            "breakout_candle": False,
            "breakdown_candle": True,
            "recent_high_20": 102.0,
            "recent_low_20": 99.5,
            "tf5_ema_9": 99.0,
            "tf5_ema_21": 100.0,
            "tf15_ema_9": 99.2,
            "tf15_ema_21": 99.8,
            "tf15_close": 99.0,
            "tf15_vwap": 99.5,
            "UUP_roc": 0.002,
            "IAU_roc": -0.002,
            "GDX_roc": -0.002,
            "vwap_deviation": -0.004,
        }
    )
    signal = StrategyEngine(Settings()).evaluate(features, has_champion_model=False)
    assert signal.decision == "SHORT"
    assert signal.bearish_score >= 75


def test_no_trade_decision():
    features = _base_features()
    features.update(
        {
            "ema_9": 100.0,
            "ema_21": 100.0,
            "close": 100.0,
            "latest_price": 100.0,
            "vwap": 100.0,
            "spread_pct": 0.005,
            "relative_volume": 0.3,
            "atr_pct": 0.0001,
            "rsi_14": 82.0,
            "data_age_seconds": 500.0,
            "breakout_candle": False,
            "breakdown_candle": False,
        }
    )
    signal = StrategyEngine(Settings()).evaluate(features, has_champion_model=False)
    assert signal.decision == "NO_TRADE"
    assert signal.no_trade_score >= 60
