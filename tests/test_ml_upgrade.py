from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pickle

import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ml.archive_dataset import _outcome, build_archive_training_records
from gld_scalper.ml.evaluator import policy_predictions, trading_metrics, validation_trade_metrics
from gld_scalper.ml.model_registry import ModelRegistry
from gld_scalper.ml.predictor import Predictor
from gld_scalper.ml.trainer import _purged_holdout_split
from gld_scalper.ml.walk_forward import run_walk_forward_validation


class FixedProbabilityModel:
    classes_ = ["long_good", "short_good", "no_trade"]

    def predict_proba(self, matrix):
        return [[0.80, 0.10, 0.10] for _ in matrix]


def test_trading_metrics_use_predicted_actions_and_realized_returns():
    predictions = ["long_good", "short_good", "no_trade"]
    outcomes = [
        {"net_return_long": 0.004, "net_return_short": -0.005},
        {"net_return_long": 0.002, "net_return_short": -0.003},
        {"net_return_long": 0.010, "net_return_short": -0.011},
    ]
    metrics = trading_metrics(predictions, outcomes)
    assert metrics["trade_count"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["net_return"] == pytest.approx(0.001)
    assert metrics["profit_factor"] == pytest.approx(0.004 / 0.003)


def test_legacy_validation_wrapper_does_not_invent_profit():
    metrics = validation_trade_metrics(["long_good", "short_good", "no_trade"])
    assert metrics["profit_factor"] == 0.0
    assert metrics["win_rate"] == 0.0
    assert metrics["slippage_adjusted_return"] == 0.0


def test_policy_predictions_apply_live_confidence_and_margin_abstention():
    predictions = policy_predictions(
        [[0.55, 0.25, 0.20], [0.61, 0.35, 0.04], [0.10, 0.10, 0.80]],
        minimum_confidence=0.58,
        minimum_margin=0.08,
    )
    assert predictions == ["no_trade", "long_good", "no_trade"]


def test_cost_aware_label_requires_move_to_clear_spread_and_slippage():
    current = {"close": 100.0}
    future = [{"high": 100.04, "low": 99.99, "close": 100.03}]
    result = _outcome(
        current,
        future,
        spread_pct=0.0002,
        slippage_pct=0.0001,
        minimum_edge_pct=0.0001,
    )
    assert result["label"] == "no_trade"
    assert abs(result["net_return_long"]) < 1e-12


def test_holdout_split_purges_neighboring_training_rows():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records = [
        {"timestamp": start + timedelta(minutes=index), "features": {"x": float(index)}, "label": "no_trade", "outcome": {}}
        for index in range(100)
    ]
    train, test = _purged_holdout_split(records, test_fraction=0.20, purge_minutes=5)
    assert test[0]["timestamp"] == start + timedelta(minutes=80)
    assert train[-1]["timestamp"] == start + timedelta(minutes=74)


def test_predictor_abstains_when_live_quote_is_stale(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'predictor.db'}")
    database = Database(settings=settings)
    database.init_db()
    predictor = Predictor(database)
    predictor._payload = {
        "model": FixedProbabilityModel(),
        "feature_columns": ["spread_pct", "quote_imbalance"],
        "feature_statistics": {
            "spread_pct": {"mean": 0.0003, "std": 0.0001, "missing_fraction": 0.0},
            "quote_imbalance": {"mean": 0.0, "std": 0.2, "missing_fraction": 0.0},
        },
        "expected_return_by_class": {"long_good": 0.002, "short_good": 0.001},
        "abstention": {
            "minimum_confidence": 0.58,
            "minimum_margin": 0.08,
            "max_missing_fraction": 0.25,
            "max_outlier_fraction": 0.15,
        },
    }
    predictor._model_version = "test"
    result = predictor.predict({"spread_pct": 0.0003, "quote_imbalance": 0.1, "quote_age_seconds": 999})
    assert result.predicted_direction == "long_good"
    assert result.rejects_trade
    assert "stale" in (result.rejection_reason or "")
    assert result.expected_return == pytest.approx(0.0017)


def test_predictor_loads_eligible_candidate_as_paper_shadow_without_promoting(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'shadow.db'}",
        paper_learning_mode=True,
        paper_enable_shadow_model=True,
    )
    database = Database(settings=settings)
    database.init_db()
    artifact = tmp_path / "shadow.pkl"
    with artifact.open("wb") as file_handle:
        pickle.dump(
            {
                "model": FixedProbabilityModel(),
                "feature_columns": ["spread_pct"],
                "feature_statistics": {"spread_pct": {"mean": 0.0003, "std": 0.0001, "missing_fraction": 0.0}},
                "abstention": {"minimum_confidence": 0.58, "minimum_margin": 0.08},
            },
            file_handle,
        )
    registry = ModelRegistry(database, settings)
    registry.register_candidate(
        model_version="paper-shadow-test",
        model_type="fixed",
        path=str(artifact),
        feature_columns=["spread_pct"],
        metrics={
            "walk_forward_completed": 1,
            "walk_forward_fold_count": settings.promotion_min_fold_count,
            "trade_label_count": settings.promotion_min_trade_count,
            "inference_latency_ms": 1.0,
            "net_return": -0.01,
            "average_pnl_per_trade": -0.0001,
            "profit_factor": 0.9,
        },
    )

    predictor = Predictor(database)
    prediction = predictor.predict({"spread_pct": 0.0003})

    assert predictor.has_model
    assert not predictor.has_champion
    assert predictor.model_role == "paper_shadow"
    assert predictor.model_version == "paper-shadow-test"
    assert predictor.prediction_features(prediction)["ml_participating"] is True
    assert predictor.prediction_features(prediction)["ml_input_compatible"] is True
    assert registry.get_model("paper-shadow-test")["status"] == "candidate"


def test_strict_promotion_requires_walk_forward_proof(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'registry.db'}")
    database = Database(settings=settings)
    database.init_db()
    registry = ModelRegistry(database, settings)
    passed, reason = registry._passes_absolute_promotion_rules(
        {
            "profit_factor": 2.0,
            "win_rate": 0.70,
            "max_drawdown": 0.001,
            "trade_label_count": 500,
            "net_return": 0.10,
            "average_pnl_per_trade": 0.001,
            "expected_calibration_error": 0.05,
            "inference_latency_ms": 1.0,
        }
    )
    assert not passed
    assert "walk-forward" in reason


def test_archive_builder_reads_bars_and_creates_cost_aware_records(tmp_path):
    path = tmp_path / "archive.db"
    settings = Settings(database_url=f"sqlite:///{path}", related_symbols=[])
    database = Database(settings=settings)
    database.init_db()
    start = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = []
    quotes = []
    for index in range(100):
        timestamp = start + timedelta(minutes=index)
        price = 100.0 + index * 0.01
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": timestamp,
                "open": price - 0.005,
                "high": price + 0.01,
                "low": price - 0.01,
                "close": price,
                "volume": 1_000 + index,
                "trade_count": 10,
                "vwap": price,
            }
        )
        quotes.append(("GLD", (timestamp + timedelta(seconds=50)).isoformat(), price - 0.01, 100, price + 0.01, 100, 0.02, 0.0002, 0.0))
    database.upsert_bars(bars)
    with database.conn:
        database.conn.executemany(
            """
            INSERT INTO quotes(symbol, timestamp, bid_price, bid_size, ask_price, ask_size, spread, spread_pct, quote_imbalance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            quotes,
        )
    database.close()
    read_only = Database(database_url=f"sqlite:///{path}", settings=settings, read_only=True)
    records = build_archive_training_records(
        read_only,
        settings,
        start=start,
        end=start + timedelta(minutes=100),
        stride_minutes=5,
        horizon_minutes=5,
        max_samples=3,
    )
    read_only.close()
    assert len(records) == 3
    assert all(record["outcome"]["total_cost"] > 0 for record in records)
    assert all("spread_pct" in record["features"] for record in records)


def test_walk_forward_uses_requested_candidate_and_feature_columns(tmp_path):
    from sklearn.naive_bayes import GaussianNB

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'walk-forward.db'}",
        walk_forward_train_months=1,
        walk_forward_test_months=1,
        ml_purge_minutes=0,
        ml_embargo_minutes=0,
        promotion_min_trade_count=10,
    )
    database = Database(settings=settings)
    database.init_db()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = []
    labels = ["long_good", "short_good", "no_trade"]
    for index in range(180):
        label = labels[index % len(labels)]
        records.append(
            {
                "timestamp": start + timedelta(days=index),
                "features": {"selected_feature": float(index % 7), "excluded_feature": float(index)},
                "label": label,
                "outcome": {
                    "label": label,
                    "net_return_long": 0.002 if label == "long_good" else -0.001,
                    "net_return_short": 0.002 if label == "short_good" else -0.001,
                },
            }
        )
    result = run_walk_forward_validation(
        database,
        settings,
        records=records,
        model_template=GaussianNB(var_smoothing=0.123),
        feature_columns=["selected_feature"],
        model_name="fast_gaussian_nb",
    )

    assert result["status"] == "completed"
    assert result["evaluator_model_type"] == "fast_gaussian_nb"
    assert result["feature_columns"] == ["selected_feature"]
    assert result["model_parameters"]["var_smoothing"] == pytest.approx(0.123)
