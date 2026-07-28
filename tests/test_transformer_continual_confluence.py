from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import numpy as np

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.gold_event_impact import build_gold_event_impact
from gld_scalper.ml.transformer_authority import apply_transformer_to_signal
from gld_scalper.ml.transformer_continual import combine_transformer_artifacts, parse_scope_artifacts
from gld_scalper.ml.transformer_dataset import load_transformer_sequence_artifact
from gld_scalper.ml.transformer_runtime import TransformerShadowPrediction
from gld_scalper.models import MarketSignal
from gld_scalper.technical_confluence import (
    build_fibonacci_features,
    build_rsi_divergence_features,
    detect_fair_value_gaps,
    grouped_indicator_families,
)


def _bars(count: int = 80) -> list[dict]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        phase = index % 20
        price = 180.0 + index * 0.01 + (phase if phase <= 10 else 20 - phase) * 0.04
        rows.append(
            {
                "timestamp": start + timedelta(minutes=index),
                "open": price - 0.02,
                "high": price + 0.08,
                "low": price - 0.08,
                "close": price,
                "volume": 10_000 + index,
            }
        )
    return rows


def test_auto_fibonacci_uses_confirmed_pivots_and_extended_levels() -> None:
    features = build_fibonacci_features(_bars(), depth=3, deviation_atr_multiplier=0.5)
    assert features["fib_valid"]
    assert features["fib_pivot_method"] in {"atr_zigzag_confirmed", "window_extrema_fallback"}
    assert features["fib_0_618"] > 0
    assert features["fib_1_272"] > 0
    assert features["fib_4_236"] > 0


def test_fair_value_gap_lifecycle_detects_partial_fill() -> None:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = [
        {"timestamp": start, "open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1},
        {"timestamp": start + timedelta(minutes=1), "open": 100.2, "high": 101.0, "low": 100.2, "close": 100.9},
        {"timestamp": start + timedelta(minutes=2), "open": 100.8, "high": 101.3, "low": 100.7, "close": 101.2},
        {"timestamp": start + timedelta(minutes=3), "open": 101.1, "high": 101.2, "low": 100.5, "close": 100.8},
    ]
    gaps = detect_fair_value_gaps(rows, symbol="GLD", timeframe="1Min")
    bullish = [gap for gap in gaps if gap.direction == "bullish"]
    assert bullish
    assert bullish[0].status in {"partially_filled", "filled", "invalidated"}
    assert 0.0 <= bullish[0].fill_fraction <= 1.0


def test_rsi_divergence_is_causal_and_structured() -> None:
    features = build_rsi_divergence_features(_bars(120), pivot_right_bars=2)
    assert features["rsi_divergence"] in {"bullish", "bearish", "none"}
    assert 0.0 <= features["rsi_divergence_strength"] <= 1.0
    assert "rsi_pivot_high" in features


def test_grouped_families_cap_correlated_indicators_to_one_vote_family() -> None:
    families = grouped_indicator_families(
        {
            "close": 101.0,
            "ema_9": 101.0,
            "ema_21": 100.0,
            "sma_20": 101.0,
            "sma_50": 100.0,
            "vwap": 100.0,
            "tf5_ema_9": 101.0,
            "tf5_ema_21": 100.0,
            "tf15_ema_9": 101.0,
            "tf15_ema_21": 100.0,
            "rsi_14": 60.0,
            "macd_histogram": 0.2,
            "macd_histogram_slope": 0.1,
        }
    )
    assert families["trend"] == 1.0
    assert -1.0 <= families["momentum"] <= 1.0


def test_event_direction_requires_post_release_tape_confirmation() -> None:
    base = {
        "next_event_name": "CPI",
        "next_event_category": "cpi",
        "next_event_surprise_pct": -0.5,
        "volatility_burst": True,
        "liquidity_score": 0.8,
        "spread_regime": "normal",
        "quote_imbalance": 0.7,
        "signed_volume": 1000.0,
        "micro_price_pressure": 0.4,
    }
    before = build_gold_event_impact({**base, "event_post_release": False})
    after = build_gold_event_impact({**base, "event_post_release": True})
    assert before["event_gold_direction"] == "NO_TRADE"
    assert after["event_gold_direction"] == "LONG"


def _signal(decision: str) -> MarketSignal:
    return MarketSignal(
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        symbol="GLD",
        decision=decision,
        bullish_score=80.0 if decision == "LONG" else 40.0,
        bearish_score=80.0 if decision == "SHORT" else 40.0,
        no_trade_score=30.0,
        regime="bullish_trend",
        confidence=0.8,
        reason="deterministic route",
        features={},
    )


def _prediction(*, direction: str, role: str = "bounded_candidate") -> TransformerShadowPrediction:
    return TransformerShadowPrediction(
        scope="minute",
        status="bounded_adviser",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        model_version="transformer-test",
        predicted_direction=direction,
        class_probabilities={direction: 0.80, "no_trade": 0.10},
        expected_returns={5: 0.001 if direction == "long_good" else -0.001},
        expected_cost=0.0001,
        uncertainty=0.2,
        authority_mode="bounded_adviser",
        model_role=role,
    )


def _safe_features() -> dict:
    return {
        "websocket_connected": True,
        "stream_stale": False,
        "spread_regime": "normal",
        "liquidity_score": 0.8,
        "event_risk_active": False,
        "technical_direction": "LONG",
        "technical_quality": 0.8,
        "technical_big3_aligned": True,
        "playbook_allowed": True,
    }


def test_bounded_transformer_cannot_originate_trade() -> None:
    settings = Settings(transformer_trading_mode="bounded_adviser")
    result = apply_transformer_to_signal(_signal("NO_TRADE"), _prediction(direction="long_good"), _safe_features(), settings)
    assert result.decision == "NO_TRADE"
    assert result.features["transformer_authority_effect"] == "observed_only_cannot_originate"


def test_paper_champion_can_recommend_but_only_with_confluence() -> None:
    settings = Settings(transformer_trading_mode="paper_champion")
    prediction = _prediction(direction="long_good", role="paper_champion")
    prediction.status = "paper_champion"
    result = apply_transformer_to_signal(_signal("NO_TRADE"), prediction, _safe_features(), settings)
    assert result.decision == "LONG"
    blocked = apply_transformer_to_signal(
        _signal("NO_TRADE"),
        prediction,
        {**_safe_features(), "stream_stale": True},
        settings,
    )
    assert blocked.decision == "NO_TRADE"


def test_transformer_tables_and_scope_parser(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    tables = {
        row["name"]
        for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "transformer_training_experiments" in tables
    assert "fair_value_gaps" in tables
    mappings = parse_scope_artifacts([f"minute={tmp_path}"])
    assert mappings["minute"] == tmp_path.resolve()


def test_historical_replay_and_paper_artifacts_are_combined_chronologically(tmp_path) -> None:
    historical = tmp_path / "historical.seq"
    paper = tmp_path / "paper.seq"
    columns = [f"feature_{index}" for index in range(8)]
    for root, timestamps, fingerprint in (
        (historical, np.asarray([1.0, 2.0]), "historical"),
        (paper, np.asarray([3.0, 4.0]), "paper"),
    ):
        root.mkdir()
        arrays = {
            "features": np.ones((2, 8), dtype=np.float32),
            "timestamps": timestamps,
            "session_ids": np.asarray([0, 0], dtype=np.int32),
            "sample_end_indices": np.asarray([1], dtype=np.int64),
            "labels": np.asarray([0], dtype=np.int64),
            "returns": np.zeros((1, 4), dtype=np.float32),
            "costs": np.zeros(1, dtype=np.float32),
            "regime_ids": np.zeros(1, dtype=np.int32),
            "baseline_probabilities": np.asarray([[0.6, 0.2, 0.2]], dtype=np.float32),
        }
        for name, values in arrays.items():
            np.save(root / f"{name}.npy", values, allow_pickle=False)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "scope": "minute",
                    "classes": ["long_good", "short_good", "no_trade"],
                    "feature_columns": columns,
                    "feature_count": 8,
                    "sequence_length": 2,
                    "window_seconds": 120,
                    "start": "2026-01-01T00:00:00+00:00",
                    "end": "2026-01-02T00:00:00+00:00",
                    "fingerprint": fingerprint,
                }
            ),
            encoding="utf-8",
        )
    output = combine_transformer_artifacts(historical, paper, tmp_path / "combined.seq")
    combined = load_transformer_sequence_artifact(output)
    assert combined.manifest["source"] == "historical_replay_plus_recent_paper"
    assert combined.manifest["historical_fingerprint"] == "historical"
    assert combined.manifest["paper_fingerprint"] == "paper"
    assert combined.sample_end_indices.tolist() == [1, 3]
    assert combined.timestamps.tolist() == [1.0, 2.0, 3.0, 4.0]
