from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ml.dataset_builder import label_quality
from gld_scalper.ml.trainer import train_candidate_model
from gld_scalper.utils.time_utils import utc_now


def test_label_quality_rejects_no_trade_only():
    ok, reason = label_quality(["no_trade", "no_trade"])
    assert not ok
    assert "two classes" in reason


def test_train_rejects_no_trade_only_dataset(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'ml.db'}")
    db = Database(settings=settings)
    db.init_db()
    db.insert_signal(
        {
            "timestamp": utc_now(),
            "symbol": "GLD",
            "decision": "NO_TRADE",
            "bullish_score": 10,
            "bearish_score": 20,
            "no_trade_score": 90,
            "regime": "unclear",
            "confidence": 0.1,
            "reason": "test",
            "feature_snapshot_json": {"close": 100.0, "rsi_14": 50.0},
        }
    )
    try:
        train_candidate_model(db, settings, lookback_days=1)
    except RuntimeError as exc:
        assert "No labeled signal/trade data" in str(exc)
    else:
        raise AssertionError("no-trade-only training data should not create a model")
