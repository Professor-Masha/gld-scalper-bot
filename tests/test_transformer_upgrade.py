from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ml.model_registry import ModelRegistry
from gld_scalper.ml.transformer_dataset import (
    build_transformer_sequence_artifact,
    load_transformer_sequence_artifact,
    sequence_defaults,
    transformer_registry_scope,
)
from gld_scalper.ml.transformer_evaluation import evaluate_transformer_paper_models
from gld_scalper.ml.transformer_runtime import AsyncTransformerShadowRuntime


def test_transformer_scope_defaults_are_independent() -> None:
    fast = sequence_defaults("fast_microstructure")
    minute = sequence_defaults("minute")
    news = sequence_defaults("news_event")
    exit_model = sequence_defaults("exit")

    assert fast != minute
    assert news != exit_model
    assert transformer_registry_scope("fast_microstructure") == "transformer:entry:fast_microstructure"
    assert transformer_registry_scope("minute") == "transformer:entry:minute"
    assert transformer_registry_scope("news_event") == "transformer:entry:news_event"
    assert transformer_registry_scope("exit") == "transformer:exit"


def test_raw_minute_builder_creates_memmap_sequence_artifact(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = []
    for index in range(220):
        wave = 0.30 if (index // 20) % 2 == 0 else -0.30
        price = 180.0 + index * 0.002 + wave * (index % 20)
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=index),
                "open": price,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price + wave * 0.25,
                "volume": 10_000 + index * 10,
                "trade_count": 100 + index,
                "vwap": price,
            }
        )
    database.upsert_bars(bars)

    output = tmp_path / "minute_sequences"
    result = build_transformer_sequence_artifact(
        database,
        settings,
        scope="minute",
        source="raw",
        start=start,
        end=start + timedelta(minutes=220),
        output=output,
        sequence_length=30,
        window_seconds=30 * 60,
        max_samples=100,
        overwrite=True,
    )
    artifact = load_transformer_sequence_artifact(output)

    assert result.samples > 10
    assert artifact.features.shape[1] == result.feature_count
    assert artifact.returns.shape[1] == 4
    assert artifact.baseline_probabilities.shape == (result.samples, 3)
    assert isinstance(artifact.features, np.memmap)
    assert artifact.manifest["sequence_length"] == 30
    assert set(artifact.manifest["classes"]) == {"long_good", "short_good", "no_trade"}


def test_transformer_predictions_are_persisted_and_exportable(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    database.insert_transformer_prediction(
        {
            "timestamp": datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
            "symbol": "GLD",
            "model_version": "transformer-minute-test",
            "model_scope": "transformer:entry:minute",
            "predicted_direction": "no_trade",
            "probability_long": 0.2,
            "probability_short": 0.3,
            "probability_no_trade": 0.5,
            "expected_return_1m": 0.0001,
            "expected_return_3m": 0.0002,
            "expected_return_5m": 0.0003,
            "expected_return_15m": 0.0004,
            "expected_cost": 0.0002,
            "uncertainty": 0.6,
            "inference_latency_ms": 1.2,
            "status": "shadow",
            "features": {"spread_pct": 0.0001},
        }
    )
    row = database.conn.execute("SELECT * FROM transformer_predictions").fetchone()
    assert row["model_scope"] == "transformer:entry:minute"
    assert row["status"] == "shadow"


def test_paper_evaluator_updates_only_matching_transformer_version(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    version = "transformer-minute-paper-test"
    with database.conn:
        database.conn.execute(
            """
            INSERT INTO model_versions(
                model_version, model_type, model_scope, path, created_at,
                feature_columns_json, metrics_json, status
            ) VALUES (?, 'causal_transformer_minute', 'transformer:entry:minute', ?, ?, '[]', '{}', 'candidate')
            """,
            (version, str(tmp_path / "manifest.json"), datetime.now(timezone.utc).isoformat()),
        )
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    cases = (
        ("long_good", 0.80, 0.10, 0.10, 0.0020),
        ("short_good", 0.10, 0.80, 0.10, -0.0015),
        ("no_trade", 0.10, 0.10, 0.80, 0.0001),
    )
    for index, (direction, p_long, p_short, p_no_trade, forward_return) in enumerate(cases):
        timestamp = start + timedelta(minutes=index)
        database.insert_transformer_prediction(
            {
                "timestamp": timestamp,
                "symbol": "GLD",
                "model_version": version,
                "model_scope": "transformer:entry:minute",
                "predicted_direction": direction,
                "probability_long": p_long,
                "probability_short": p_short,
                "probability_no_trade": p_no_trade,
                "expected_cost": 0.0001,
                "status": "shadow",
            }
        )
        database.insert_outcome_label(
            {
                "decision_source": "signal",
                "decision_id": index + 1,
                "timestamp": timestamp,
                "symbol": "GLD",
                "label_5m": direction,
                "forward_return_5m": forward_return,
                "realized_spread_cost": 0.0001,
            }
        )
    reports = evaluate_transformer_paper_models(database, settings, model_version=version)
    assert reports[0]["paper_prediction_count"] == 3
    assert reports[0]["paper_trade_count"] == 2
    assert reports[0]["paper_net_return"] > 0
    stored = database.conn.execute(
        "SELECT metrics_json FROM model_versions WHERE model_version = ?", (version,)
    ).fetchone()
    assert json.loads(stored["metrics_json"])["paper_profit_factor"] > 1


def test_exact_random_forest_baseline_is_mandatory_for_transformer_promotion(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'paper.db'}",
        promotion_require_paper_results=False,
        promotion_min_regime_count=1,
    )
    database = Database(settings=settings)
    database.init_db()
    registry = ModelRegistry(database, settings)
    metrics = {
        "profit_factor": 1.5,
        "win_rate": 0.60,
        "max_drawdown": 0.005,
        "trade_label_count": 1_000,
        "walk_forward_completed": 1,
        "walk_forward_fold_count": 3,
        "net_return": 0.05,
        "average_pnl_per_trade": 0.001,
        "profitable_fold_ratio": 1.0,
        "expected_calibration_error": 0.05,
        "inference_latency_ms": 1.0,
        "validated_regime_count": 3,
        "requires_baseline_comparison": 1,
        "baseline_comparison_passed": 0,
    }
    passed, reason = registry._passes_absolute_promotion_rules(metrics)
    assert not passed
    assert "exact saved random-forest baseline" in reason


def test_shadow_runtime_never_blocks_when_not_started() -> None:
    settings = Settings(enable_transformer_shadow=False)
    runtime = AsyncTransformerShadowRuntime(settings)
    result = runtime.submit(
        "minute",
        datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        {"spread_pct": 0.0001},
        fallback_model_version="rf-test",
    )
    assert result.status == "unavailable"
    assert result.as_features()["transformer_minute_shadow_only"] == 1.0


def test_compact_transformer_masks_padding_and_returns_all_heads() -> None:
    torch = pytest.importorskip("torch")
    from gld_scalper.ml.transformer_model import TransformerModelConfig, build_causal_transformer

    torch.manual_seed(7)
    config = TransformerModelConfig(feature_count=6, sequence_length=12, d_model=32, num_layers=2)
    model = build_causal_transformer(config).eval()
    values = torch.randn(2, 12, 6)
    missing = torch.zeros_like(values, dtype=torch.bool)
    valid = torch.ones(2, 12, dtype=torch.bool)
    session = torch.ones(2, 12, dtype=torch.bool)
    valid[:, -3:] = False
    session[:, -3:] = False
    with torch.inference_mode():
        logits, returns, cost, log_variance = model(values, missing, valid, session)
    assert logits.shape == (2, 3)
    assert returns.shape == (2, 4)
    assert cost.shape == (2, 1)
    assert log_variance.shape == (2, 4)
    assert torch.all(cost >= 0)


def test_tiny_offline_training_exports_reloadable_torchscript(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch")
    from gld_scalper.ml import transformer_trainer
    from gld_scalper.ml.transformer_trainer import TransformerTrainingOptions, train_transformer_candidate

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'paper.db'}",
        promotion_require_paper_results=False,
    )
    database = Database(settings=settings)
    database.init_db()
    bars = []
    for session_number, session_start in enumerate(
        (
            datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc),
        )
    ):
        for index in range(250):
            direction = 1.0 if (index // 25) % 2 == 0 else -1.0
            close = 180.0 + session_number + direction * (index % 25) * 0.08
            bars.append(
                {
                    "symbol": "GLD",
                    "timeframe": "1Min",
                    "timestamp": session_start + timedelta(minutes=index),
                    "open": close - direction * 0.02,
                    "high": close + 0.08,
                    "low": close - 0.08,
                    "close": close,
                    "volume": 20_000 + index * 20,
                    "trade_count": 200 + index,
                    "vwap": close - direction * 0.01,
                }
            )
    database.upsert_bars(bars)
    artifact_path = tmp_path / "training_sequences"
    build_transformer_sequence_artifact(
        database,
        settings,
        scope="minute",
        source="raw",
        start=datetime(2026, 1, 5, tzinfo=timezone.utc),
        end=datetime(2026, 1, 7, tzinfo=timezone.utc),
        output=artifact_path,
        sequence_length=30,
        window_seconds=30 * 60,
        max_samples=450,
        overwrite=True,
    )
    monkeypatch.setattr(transformer_trainer, "PROJECT_ROOT", tmp_path)
    result = train_transformer_candidate(
        database,
        settings,
        artifact_path=artifact_path,
        options=TransformerTrainingOptions(
            d_model=32,
            num_layers=2,
            dim_feedforward=64,
            batch_size=64,
            epochs=1,
            patience=1,
            walk_forward_folds=1,
            walk_forward_epochs=1,
        ),
    )
    assert result["status"] == "candidate_registered"
    assert result["promotion_ready"] is False
    assert Path(result["torchscript_path"]).is_file()
    assert result["holdout"]["sample_count"] > 0
    assert result["walk_forward"]["folds"]
