from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.models import MLPrediction, MarketSignal
from gld_scalper.paper_exploration import PaperExplorationPolicy, model_rejection_blocks
from gld_scalper.risk_engine import RiskEngine
from gld_scalper.trade_learning import TradeLearningAnalyzer


def _settings(tmp_path, **overrides):
    values = {
        "database_url": f"sqlite:///{tmp_path / 'learning.db'}",
        "enable_paper_exploration": True,
        "paper_exploration_max_notional": 1_000.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_completed_trade_becomes_review_and_supervised_label(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    entry = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    exit_time = entry + timedelta(minutes=5)
    for minute, prices in enumerate(
        [
            (100.0, 100.2, 99.9, 100.1),
            (100.1, 100.5, 100.0, 100.4),
            (100.4, 100.8, 100.3, 100.7),
            (100.7, 101.1, 100.6, 101.0),
            (101.0, 101.2, 100.9, 101.1),
            (101.1, 101.2, 101.0, 101.0),
        ]
    ):
        db.upsert_bars(
            [
                {
                    "symbol": "GLD",
                    "timeframe": "1Min",
                    "timestamp": entry + timedelta(minutes=minute),
                    "open": prices[0],
                    "high": prices[1],
                    "low": prices[2],
                    "close": prices[3],
                    "volume": 1_000,
                }
            ]
        )
    features = {
        "bullish_score": 86.0,
        "bearish_score": 24.0,
        "pattern_classification": "proper_break_up",
        "pattern_quality": 0.82,
        "liquidity_score": 0.86,
        "spread_pct": 0.0002,
        "agent_consensus": "LONG",
        "agent_confidence": 0.8,
        "playbook": "proper_breakout",
    }
    signal_id = db.insert_signal(
        {
            "timestamp": entry,
            "symbol": "GLD",
            "bullish_score": 86.0,
            "bearish_score": 24.0,
            "no_trade_score": 20.0,
            "regime": "bullish_trend",
            "decision": "LONG",
            "confidence": 0.86,
            "reason": "clean break",
            "feature_snapshot_json": features,
        }
    )
    journal_id = db.insert_trading_journal(
        {
            "timestamp": entry,
            "symbol": "GLD",
            "event_type": "TRADE_DECISION",
            "signal_id": signal_id,
            "decision": "LONG",
            "confidence": 0.86,
            "bullish_score": 86.0,
            "bearish_score": 24.0,
            "no_trade_score": 20.0,
            "regime": "bullish_trend",
            "reason": "clean break",
            "client_order_id": "trade-1",
            "feature_snapshot_json": features,
        }
    )
    outcome_id = db.insert_trade_outcome(
        {
            "trade_id": "trade-1",
            "symbol": "GLD",
            "direction": "LONG",
            "entry_time": entry,
            "exit_time": exit_time,
            "entry_price": 100.0,
            "exit_price": 101.0,
            "qty": 10,
            "notional": 1_000.0,
            "gross_pnl": 10.0,
            "net_pnl_estimated": 10.0,
            "pnl_pct": 0.01,
            "holding_seconds": 300,
            "exit_reason": "take_profit",
            "win_loss": "win",
            "strategy_version": settings.strategy_version,
        }
    )

    review = TradeLearningAnalyzer(settings, db).review(outcome_id)

    assert review is not None
    assert review["journal_id"] == journal_id
    assert review["result_label"] == "long_good"
    assert review["max_favorable_excursion"] >= 0.012
    assert db.count_rows("trade_reviews") == 1
    assert db.count_rows("outcome_labels") == 1
    assert db.conn.execute("SELECT mistake_category FROM trade_outcomes WHERE id = ?", (outcome_id,)).fetchone()[0] is None
    assert TradeLearningAnalyzer(settings, db).review(outcome_id) is None
    assert db.count_rows("trade_reviews") == 1


def test_paper_exploration_selects_near_valid_setup_and_uses_small_size(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.2,
        "trade_age_seconds": 0.1,
        "spread_pct": 0.0003,
        "spread_regime": "tight",
        "liquidity_score": 0.82,
        "event_risk_active": False,
        "headline_event_risk": 0.1,
        "options_event_risk": 0.1,
        "playbook_allowed": True,
        "playbook_direction": "LONG",
        "latest_price": 300.0,
        "atr_14": 0.5,
    }
    signal = MarketSignal(now, "GLD", "NO_TRADE", 78.0, 32.0, 45.0, "bullish_trend", 0.78, "no champion model", features)

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)
    plan = RiskEngine(settings).build_order_plan(
        selected,
        MLPrediction(None, "rule_only", 0.34, 0.33, 0.33),
        RiskEngine(settings).state_from_features(selected.features),
    )

    assert selected.decision == "LONG"
    assert selected.features["paper_exploration"] is True
    assert 0 < plan.estimated_notional <= settings.paper_exploration_max_notional


def test_paper_exploration_never_bypasses_stale_stream(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    features = {
        "websocket_connected": False,
        "stream_stale": True,
        "quote_age_seconds": 100.0,
        "trade_age_seconds": 100.0,
        "spread_pct": 0.0002,
        "liquidity_score": 0.9,
        "playbook_allowed": True,
        "playbook_direction": "LONG",
    }
    signal = MarketSignal(now, "GLD", "NO_TRADE", 80.0, 20.0, 40.0, "bullish_trend", 0.8, "stale", features)

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)

    assert selected.decision == "NO_TRADE"


def test_paper_exploration_requires_both_fresh_quote_and_trade(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.1,
        "trade_age_seconds": settings.quote_stale_seconds + 1,
        "spread_pct": 0.0002,
        "liquidity_score": 0.9,
        "playbook_allowed": True,
        "playbook_direction": "LONG",
    }
    signal = MarketSignal(now, "GLD", "NO_TRADE", 80.0, 20.0, 40.0, "bullish_trend", 0.8, "near valid", features)

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)

    assert selected.decision == "NO_TRADE"


def test_paper_learning_mode_keeps_event_risk_as_no_trade(tmp_path):
    settings = _settings(tmp_path, paper_learning_mode=True)
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.1,
        "trade_age_seconds": 0.1,
        "spread_pct": 0.0010,
        "spread_regime": "wide",
        "liquidity_score": 0.20,
        "event_risk_active": True,
        "headline_event_risk": 0.95,
        "volatility_burst": True,
        "gold_volatility_regime": "high",
        "playbook_allowed": False,
        "order_block_conflict": True,
        "latest_price": 300.0,
    }
    signal = MarketSignal(now, "GLD", "NO_TRADE", 50.0, 50.0, 100.0, "high_volatility", 0.5, "event risk", features)

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)

    assert selected.decision == "NO_TRADE"
    assert model_rejection_blocks(settings, selected.features, True)


def test_paper_learning_zero_daily_limit_selects_and_records_propensity(tmp_path):
    settings = _settings(
        tmp_path,
        paper_learning_mode=True,
        paper_learning_exploration_sample_rate=1.0,
        paper_learning_max_exploration_trades_per_day=0,
    )
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.1,
        "trade_age_seconds": 0.1,
        "spread_pct": 0.0003,
        "spread_regime": "tight",
        "spread_stability_score": 0.9,
        "liquidity_score": 0.55,
        "trade_intensity": 1.0,
        "event_risk_active": False,
        "headline_event_risk": 0.1,
        "options_event_risk": 0.1,
        "playbook": "pullback_continuation",
        "playbook_allowed": False,
        "playbook_direction": "LONG",
        "playbook_score": 64.0,
        "playbook_confirmation_count": 3,
        "playbook_required_confirmations": 4,
        "pattern_classification": "pullback_up",
        "pattern_quality": 0.50,
        "latest_price": 300.0,
        "atr_14": 0.5,
    }
    signal = MarketSignal(
        now,
        "GLD",
        "NO_TRADE",
        66.0,
        45.0,
        100.0,
        "sideways_chop",
        0.66,
        "near-valid pullback",
        features,
    )

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)

    assert selected.decision == "LONG"
    assert selected.features["paper_exploration"] is True
    assert selected.features["exploration_selection_probability"] == 1.0
    assert selected.features["exploration_propensity_weight"] == 1.0
    Settings(
        paper_learning_mode=True,
        paper_learning_max_exploration_trades_per_day=0,
    ).validate_safety()


def test_paper_learning_false_break_does_not_require_proper_break(tmp_path):
    settings = _settings(
        tmp_path,
        paper_learning_mode=True,
        paper_learning_exploration_sample_rate=1.0,
        paper_learning_max_exploration_trades_per_day=0,
    )
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.1,
        "trade_age_seconds": 0.1,
        "spread_pct": 0.0003,
        "spread_regime": "tight",
        "liquidity_score": 0.50,
        "event_risk_active": False,
        "playbook": "false_break_reversal",
        "playbook_allowed": False,
        "playbook_direction": "SHORT",
        "playbook_score": 65.0,
        "playbook_confirmation_count": 2,
        "playbook_required_confirmations": 3,
        "pattern_classification": "false_break_up",
        "pattern_quality": 0.50,
        "false_break": True,
        "proper_break": False,
        "latest_price": 300.0,
    }
    signal = MarketSignal(
        now,
        "GLD",
        "NO_TRADE",
        45.0,
        65.0,
        100.0,
        "sideways_chop",
        0.65,
        "returned inside range",
        features,
    )

    selected = PaperExplorationPolicy(settings, db).maybe_select(signal, features, now=now)

    assert selected.decision == "SHORT"
    assert selected.features["playbook"] == "false_break_reversal"
    assert selected.features["proper_break"] is False
