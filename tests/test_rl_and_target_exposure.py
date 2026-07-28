from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.models import MarketSignal
from gld_scalper.rl_environment import GLDScalpingEnvironment, RLAction, run_offline_policy_preview
from gld_scalper.target_exposure import target_exposure_from_signal


def _bars(start, count=20):
    bars = []
    for idx in range(count):
        close = 100 + idx * 0.08
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=idx),
                "open": close - 0.02,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "volume": 1000,
            }
        )
    return bars


def test_rl_environment_rewards_long_in_rising_market(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'rl.db'}")
    db = Database(settings=settings)
    db.init_db()
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    db.upsert_bars(_bars(start))

    env = GLDScalpingEnvironment(db, settings, horizon_minutes=5)
    env.reset()
    _, reward, _, info = env.step(RLAction("LONG", 1.0))

    assert reward > 0
    assert info["step"].action == "LONG"


def test_rl_preview_persists_experiment(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'rl_preview.db'}")
    db = Database(settings=settings)
    db.init_db()
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    db.upsert_bars(_bars(start, count=40))

    result = run_offline_policy_preview(db, settings)

    assert result["promoted"] is False
    assert db.count_rows("rl_experiments") == 1


def test_target_exposure_reflects_direction_and_macro_alignment():
    signal = MarketSignal(
        timestamp=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        symbol="GLD",
        decision="LONG",
        bullish_score=85,
        bearish_score=20,
        no_trade_score=30,
        regime="breakout_up",
        confidence=0.85,
        reason="test",
    )
    exposure = target_exposure_from_signal(
        signal,
        {
            "pattern_quality": 0.9,
            "liquidity_score": 0.9,
            "macro_bias": "bullish_gold_environment",
            "macro_confidence": 0.5,
        },
    )

    assert exposure.desired_exposure_pct > 0
    assert exposure.direction == "LONG"
