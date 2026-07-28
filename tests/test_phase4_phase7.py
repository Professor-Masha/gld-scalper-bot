from __future__ import annotations

import json
import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.exit_policy import build_exit_geometry, economic_breakeven_pct
from gld_scalper.fingpt_offline import load_fingpt_source_profile
from gld_scalper.llm_analysis import LLMAnalysisService
from gld_scalper.llm_provider import LLMResponse
from gld_scalper.ml.drift import _paper_performance, generate_drift_report
from gld_scalper.ml.exit_trainer import train_exit_candidate
from gld_scalper.ml.model_registry import ModelRegistry
from gld_scalper.ml.predictor import Predictor
from gld_scalper.ml.scopes import model_scope_from_features
from gld_scalper.models import MLPrediction, MarketSignal, RiskState
from gld_scalper.position_manager import DynamicPositionRuntime, ManagedTrade
from gld_scalper.risk_engine import RiskEngine


class FixedProbabilityModel:
    classes_ = ["long_good", "short_good", "no_trade"]

    def predict_proba(self, matrix):
        return [[0.80, 0.10, 0.10] for _ in matrix]


class FakeNewsClient:
    provider = "ollama"
    model = "llama3.2:1b"

    def __init__(self, news_id: int) -> None:
        self.news_id = news_id

    def chat_json(self, *, system, user):
        content = {
            "items": [
                {
                    "news_id": self.news_id,
                    "sentiment_score": 0.8,
                    "confidence_score": 0.9,
                    "novelty_score": 0.8,
                    "event_risk": 0.6,
                    "gold_impact": "bullish",
                    "event_type": "fed_rates",
                    "summary": "Lower yields support gold.",
                }
            ]
        }
        return LLMResponse(provider=self.provider, model=self.model, content=json.dumps(content), raw={})


def _database(tmp_path, **settings_overrides):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'phase47.db'}", **settings_overrides)
    database = Database(settings=settings)
    database.init_db()
    return settings, database


def _signal(*, features=None):
    return MarketSignal(
        timestamp=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
        symbol="GLD",
        decision="LONG",
        bullish_score=90,
        bearish_score=10,
        no_trade_score=10,
        regime="bullish_trend",
        confidence=0.9,
        reason="clean continuation",
        features=features or {"playbook": "trend_continuation", "strategy_path": "minute"},
    )


def _risk_state(*, paper=True):
    return RiskState(
        account_equity=1_000_000,
        day_start_equity=1_000_000,
        latest_price=200.0,
        spread_pct=0.0002,
        atr=0.30,
        liquidity_score=0.90,
        pattern_quality=0.90,
        pattern_classification="proper_break_up",
        spread_regime="tight",
        market_open=True,
        paper_learning_mode=paper,
    )


def _episode(now: datetime) -> ManagedTrade:
    return ManagedTrade(
        trade_id="GLD-PHASE47-RUN",
        parent_order_id="parent",
        symbol="GLD",
        direction="LONG",
        role="runner",
        qty=2,
        entry_time=now,
        entry_price=200.0,
        stop_order_id="stop",
        take_profit_order_id="target",
        initial_stop_price=199.7,
        current_stop_price=199.7,
        take_profit_price=200.5,
        high_water_price=200.0,
        low_water_price=200.0,
    )


def test_economic_breakeven_includes_spread_slippage_fees_and_buffer():
    settings = Settings(
        estimated_round_trip_slippage_pct=0.0002,
        economic_breakeven_safety_buffer_pct=0.0001,
    )
    value = economic_breakeven_pct(settings, spread_pct=0.0003, fee_pct=0.00005)
    assert value == pytest.approx(0.00065)


def test_playbook_exit_geometry_bounds_stop_and_requires_reward_for_risk():
    settings = Settings(position_max_normal_stop_pct=0.002, position_min_reward_risk=1.20)
    state = _risk_state()
    geometry = build_exit_geometry(settings, _signal(), state, 200.0)
    assert geometry.rejected_reason is None
    assert geometry.stop_distance_pct <= settings.position_max_normal_stop_pct
    assert geometry.target_distance >= geometry.stop_distance * settings.position_min_reward_risk
    assert geometry.target_distance_pct > geometry.economic_breakeven_pct


def test_dynamic_exit_preserves_profit_after_large_mfe_giveback():
    settings = Settings(
        position_profit_giveback_min_mfe_pct=0.0008,
        position_max_profit_giveback_fraction=0.50,
    )
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
    episode = _episode(now)
    episode.update_mark(200.40)
    runtime._latest_quote = {"bid_price": 200.15, "ask_price": 200.17}
    action = runtime._next_action(episode, episode.pnl_pct(200.16), 0.0001, now + timedelta(minutes=1))
    assert action == ("request_exit", "maximum_profit_giveback", 0.0)


def test_session_close_management_tightens_then_reduces_then_flattens():
    settings = Settings(
        position_close_management_minutes_before_close=30,
        position_close_risk_reduction_minutes_before_close=15,
        position_force_flatten_minutes_before_close=8,
    )
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    episode = _episode(now - timedelta(minutes=5))
    episode.update_mark(200.20)
    runtime._latest_quote = {"bid_price": 200.18, "ask_price": 200.20}
    runtime._clock = SimpleNamespace(is_open=True, next_close=now + timedelta(minutes=25))
    assert runtime._next_action(episode, episode.pnl_pct(200.19), 0.0001, now)[1] == "session_close_staged_profit_lock"
    runtime._clock = SimpleNamespace(is_open=True, next_close=now + timedelta(minutes=12))
    assert runtime._next_action(episode, -0.0002, 0.0001, now) == (
        "request_exit",
        "session_close_risk_reduction",
        0.0,
    )
    runtime._clock = SimpleNamespace(is_open=True, next_close=now + timedelta(minutes=5))
    assert runtime._next_action(episode, 0.001, 0.0001, now) == (
        "request_exit",
        "session_close_protection",
        0.0,
    )


def test_experimental_paper_size_stays_small_until_model_and_market_are_validated():
    settings = Settings(paper_learning_mode=True, paper_exploration_max_notional=1_000)
    engine = RiskEngine(settings)
    state = _risk_state()
    prediction = MLPrediction("model", "long_good", 0.8, 0.1, 0.1, confidence=0.8)
    experimental = engine.build_order_plan(_signal(), prediction, state)
    assert experimental.estimated_notional <= settings.paper_exploration_max_notional
    state.validated_after_cost_profit_factor = 1.50
    state.model_independently_validated = True
    state.model_direction_aligned = True
    validated = engine.build_order_plan(_signal(), prediction, state)
    assert validated.estimated_notional > experimental.estimated_notional
    assert validated.maximum_loss <= state.account_equity * settings.max_trade_risk_pct


def test_scopes_separate_fast_minute_news_and_playbook_models():
    assert model_scope_from_features({"strategy_path": "fast", "playbook": "spread_capture"}) == "entry:fast_microstructure"
    assert model_scope_from_features({"strategy_path": "minute", "playbook": "news_event"}) == "entry:news_event"
    assert model_scope_from_features({"strategy_path": "minute", "playbook": "proper_breakout"}) == "entry:playbook:proper_breakout"
    assert model_scope_from_features({"strategy_path": "minute"}) == "entry:minute"


def test_tampered_fitted_model_state_is_not_loaded(tmp_path):
    settings, database = _database(tmp_path)
    artifact = tmp_path / "tampered.pkl"
    with artifact.open("wb") as handle:
        pickle.dump(
            {
                "format_version": 3,
                "model": FixedProbabilityModel(),
                "model_scope": "entry:all",
                "artifact_fingerprint": "registered",
                "model_state_fingerprint": "not-the-fitted-model-hash",
                "feature_columns": ["x"],
                "feature_statistics": {"x": {"mean": 0.0, "std": 1.0, "missing_fraction": 0.0}},
            },
            handle,
        )
    registry = ModelRegistry(database, settings)
    registry.register_candidate(
        model_version="tampered",
        model_type="fixed",
        model_scope="entry:all",
        path=str(artifact),
        feature_columns=["x"],
        artifact_fingerprint="registered",
        metrics={},
    )
    with database.conn:
        database.conn.execute("UPDATE model_versions SET status = 'champion' WHERE model_version = 'tampered'")
    predictor = Predictor(database)
    assert predictor.predict({"x": 0.0}).predicted_direction == "rule_only"
    assert predictor.model_role == "none"


def test_drift_demotes_champion_and_keeps_history(tmp_path):
    settings, database = _database(tmp_path, drift_min_predictions=3, drift_demotion_score=0.10)
    artifact = tmp_path / "champion.pkl"
    with artifact.open("wb") as handle:
        pickle.dump(
            {
                "model": FixedProbabilityModel(),
                "model_scope": "entry:minute",
                "artifact_fingerprint": "champion-fingerprint",
                "feature_columns": ["x"],
                "feature_statistics": {"x": {"mean": 0.0, "std": 1.0, "missing_fraction": 0.0}},
            },
            handle,
        )
    registry = ModelRegistry(database, settings)
    registry.register_candidate(
        model_version="minute-champion",
        model_type="fixed",
        model_scope="entry:minute",
        path=str(artifact),
        feature_columns=["x"],
        artifact_fingerprint="champion-fingerprint",
        metrics={"profit_factor": 1.5, "max_drawdown": 0.01},
    )
    with database.conn:
        database.conn.execute("UPDATE model_versions SET status = 'champion' WHERE model_version = 'minute-champion'")
    for index in range(3):
        database.insert_model_prediction(
            {
                "timestamp": datetime(2026, 7, 15, 15, index, tzinfo=timezone.utc),
                "symbol": "GLD",
                "model_version": "minute-champion",
                "model_scope": "entry:minute",
                "predicted_direction": "long_good",
                "features": {"x": 20.0},
            }
        )
    report = generate_drift_report(database, model_scope="entry:minute")
    assert report["status"] == "demoted"
    assert registry.get_champion("entry:minute") is None
    history = database.conn.execute("SELECT action FROM model_champion_history").fetchall()
    assert [row["action"] for row in history] == ["demoted"]


def test_champions_are_versioned_and_can_be_rolled_back(tmp_path):
    settings, database = _database(tmp_path)
    registry = ModelRegistry(database, settings)
    first_path = tmp_path / "first.pkl"
    second_path = tmp_path / "second.pkl"
    first_path.write_bytes(b"first preserved champion")
    second_path.write_bytes(b"second preserved champion")
    base_metrics = {
        "profit_factor": 1.50,
        "win_rate": 0.60,
        "balanced_accuracy": 0.60,
        "max_drawdown": 0.005,
        "trade_label_count": 200,
        "walk_forward_completed": 1,
        "walk_forward_fold_count": 4,
        "net_return": 0.10,
        "average_pnl_per_trade": 0.001,
        "profitable_fold_ratio": 0.75,
        "expected_calibration_error": 0.05,
        "inference_latency_ms": 1.0,
        "paper_trade_count": 50,
        "paper_profit_factor": 1.30,
        "validated_regime_count": 3,
    }
    registry.register_candidate(
        model_version="champion-v1",
        model_type="test",
        model_scope="entry:minute",
        path=str(first_path),
        feature_columns=["x"],
        artifact_fingerprint="v1",
        metrics=base_metrics,
    )
    assert registry.promote_if_better("champion-v1", base_metrics, model_scope="entry:minute")[0]
    improved_metrics = {
        **base_metrics,
        "profit_factor": 1.80,
        "win_rate": 0.65,
        "balanced_accuracy": 0.65,
        "max_drawdown": 0.004,
        "net_return": 0.13,
        "average_pnl_per_trade": 0.0012,
        "paper_profit_factor": 1.50,
    }
    registry.register_candidate(
        model_version="champion-v2",
        model_type="test",
        model_scope="entry:minute",
        path=str(second_path),
        feature_columns=["x"],
        artifact_fingerprint="v2",
        metrics=improved_metrics,
    )
    assert registry.promote_if_better("champion-v2", improved_metrics, model_scope="entry:minute")[0]
    assert registry.get_model("champion-v1")["status"] == "archived"
    rolled_back, _ = registry.rollback("entry:minute", "champion-v1", reason="paper regression")
    assert rolled_back
    assert registry.get_champion("entry:minute")["model_version"] == "champion-v1"
    history = database.conn.execute(
        "SELECT action, model_version FROM model_champion_history ORDER BY id"
    ).fetchall()
    assert [(row["action"], row["model_version"]) for row in history] == [
        ("promoted", "champion-v1"),
        ("promoted", "champion-v2"),
        ("rollback", "champion-v1"),
    ]


def test_paper_drift_performance_uses_episode_returns_not_dollar_drawdown(tmp_path):
    _, database = _database(tmp_path)
    now = datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
    for trade_id, pnl in (("one", 1.0), ("two", -2.0)):
        database.insert_trade_outcome(
            {
                "trade_id": trade_id,
                "symbol": "GLD",
                "direction": "LONG",
                "exit_time": now,
                "notional": 1_000.0,
                "net_pnl_after_costs": pnl,
                "model_version": "scope-model",
            }
        )
    result = _paper_performance(database, "scope-model", 2)
    assert result["outcome_count"] == 2
    assert result["profit_factor"] == pytest.approx(0.5)
    assert result["max_drawdown"] == pytest.approx(0.002)


def test_exit_model_waits_for_trustworthy_completed_outcomes(tmp_path):
    settings, database = _database(tmp_path, exit_model_min_trustworthy_outcomes=2)
    result = train_exit_candidate(database, settings)
    assert result["status"] == "skipped"
    assert result["minimum"] == 2


def test_news_sentiment_requires_both_price_and_spread_linkage(tmp_path):
    settings, database = _database(tmp_path)
    news_id = database.insert_news_item(
        {
            "timestamp": datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
            "source": "test",
            "headline": "Fed easing sends gold higher",
        }
    )
    with database.conn:
        database.conn.execute(
            "UPDATE news_items SET related_move_15m = 0.01, outcome_linked = 1 WHERE id = ?",
            (news_id,),
        )
    service = LLMAnalysisService(settings, database, client=FakeNewsClient(news_id))
    assert service.classify_recent_news(limit=1)[0]["trusted"] is False
    with database.conn:
        database.conn.execute(
            "UPDATE news_items SET related_spread_change_5m = 0.0002 WHERE id = ?",
            (news_id,),
        )
    assert service.classify_recent_news(limit=1)[0]["trusted"] is True


def test_local_fingpt_profile_fingerprints_only_available_framework_files(tmp_path):
    root = tmp_path / "fingpt"
    forecaster = root / "FinGPT_Forecaster"
    forecaster.mkdir(parents=True)
    (forecaster / "prompt.py").write_text("PROMPT = 'financial forecast'\n", encoding="utf-8")
    settings = Settings(fingpt_source_dir=str(root))
    profile = load_fingpt_source_profile(settings)
    assert profile.files == ("FinGPT_Forecaster\\prompt.py",)
    assert len(profile.fingerprint) == 64


def test_llm_live_order_path_configuration_is_rejected():
    with pytest.raises(RuntimeError, match="must remain false"):
        Settings(enable_llm_live_trading=True).validate_safety()
