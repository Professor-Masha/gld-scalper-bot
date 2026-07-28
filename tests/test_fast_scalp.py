from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.fast_scalp import FastScalpDecision, FastScalpEngine, _apply_paper_ml_advice
from gld_scalper.fast_scalp import FastScalpRuntime
from gld_scalper.models import MLPrediction, RiskState
from gld_scalper.paper_exploration import PaperExplorationPolicy
from gld_scalper.risk_engine import OrderPlanRejected
from gld_scalper.utils.time_utils import utc_now


def _settings() -> Settings:
    return Settings(
        fast_scalp_interval_ms=0,
        fast_scalp_min_quote_count=3,
        fast_scalp_min_trade_count=1,
        fast_scalp_min_trade_intensity=0.1,
        fast_scalp_min_confidence=0.55,
        fast_scalp_lookback_seconds=5,
        fast_scalp_breakout_min_move_pct=0.00005,
        max_spread_pct=0.002,
        fast_scalp_tight_spread_pct=0.0005,
    )


class _PaperShadowPredictor:
    has_model = True


class _RuntimePredictor(_PaperShadowPredictor):
    def predict(self, features):
        return MLPrediction("shadow", "short_good", 0.10, 0.80, 0.10, confidence=0.80)

    def prediction_features(self, prediction):
        return {"ml_input_compatible": True, "ml_advice_eligible": True}


class _Persistence:
    def __init__(self):
        self.fast_decisions = []
        self.no_trades = []

    def enqueue_fast_decision(self, record):
        self.fast_decisions.append(record)

    def enqueue_no_trade(self, record):
        self.no_trades.append(record)


class _Broker:
    def get_account(self):
        return SimpleNamespace(equity="1000000")

    def get_all_positions(self):
        return []

    def get_orders(self, filter=None):
        return []


class _RejectingRisk:
    def enrich_runtime_state(self, state, *, now=None):
        return state

    def state_from_features(self, features):
        return RiskState(
            account_equity=1_000_000,
            day_start_equity=1_000_000,
            latest_price=100.0,
            spread_pct=0.0005,
            market_open=True,
            paper_learning_mode=True,
        )

    def blocks_trading(self, state, direction):
        return False, None

    def build_order_plan(self, signal, prediction, state):
        raise OrderPlanRejected("expected plan block")


class _ExecutionMustNotRun:
    def submit_entry_with_protection(self, plan):
        raise AssertionError("execution should not run after an order-plan block")


def test_fast_scalp_detects_clean_breakout_long():
    settings = _settings()
    engine = FastScalpEngine(settings)
    now = utc_now()

    engine.on_event("quote", {"symbol": "GLD", "timestamp": now, "bid_price": 100.00, "ask_price": 100.02, "bid_size": 5000, "ask_size": 3000}, now)
    engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=100), "bid_price": 100.01, "ask_price": 100.03, "bid_size": 5200, "ask_size": 2800}, now + timedelta(milliseconds=100))
    engine.on_event("trade", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=150), "price": 100.05, "size": 250}, now + timedelta(milliseconds=150))
    decision = engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=200), "bid_price": 100.05, "ask_price": 100.06, "bid_size": 7000, "ask_size": 1000}, now + timedelta(milliseconds=200))

    assert decision is not None
    assert decision.decision == "LONG"
    assert decision.allowed
    assert decision.trigger_type in {"clean_breakout", "spread_capture"}
    assert decision.latency_ms < 10


def test_fast_scalp_blocks_spread_expansion():
    settings = _settings()
    engine = FastScalpEngine(settings)
    now = utc_now()

    engine.on_event("quote", {"symbol": "GLD", "timestamp": now, "bid_price": 100.00, "ask_price": 100.02, "bid_size": 5000, "ask_size": 3000}, now)
    engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=100), "bid_price": 100.01, "ask_price": 100.03, "bid_size": 5200, "ask_size": 2800}, now + timedelta(milliseconds=100))
    engine.on_event("trade", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=150), "price": 100.05, "size": 250}, now + timedelta(milliseconds=150))
    decision = engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=200), "bid_price": 100.00, "ask_price": 100.40, "bid_size": 200, "ask_size": 8000}, now + timedelta(milliseconds=200))

    assert decision is not None
    assert decision.decision == "NO_TRADE"
    assert not decision.allowed
    assert "spread expansion" in decision.reason


def test_paper_shadow_ml_cannot_reverse_a_fast_setup():
    settings = _settings()
    settings.paper_learning_mode = True
    decision_time = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    decision = FastScalpDecision(
        decision_time,
        "GLD",
        "SHORT",
        "paper_probe",
        True,
        0.40,
        40.0,
        "tiny-spread bid/ask pressure scalp",
        0.1,
        {"ml_advice_eligible": True},
    )
    prediction = MLPrediction("shadow", "long_good", 0.80, 0.10, 0.10, confidence=0.80)

    _apply_paper_ml_advice(decision, prediction, _PaperShadowPredictor(), settings)

    assert decision.decision == "SHORT"
    assert "paper_ml_advice_applied" not in decision.features

    aligned = MLPrediction("shadow", "short_good", 0.10, 0.80, 0.10, confidence=0.80)
    _apply_paper_ml_advice(decision, aligned, _PaperShadowPredictor(), settings)

    assert decision.decision == "SHORT"
    assert decision.features["paper_ml_advice_applied"] is True
    assert decision.score > 40.0


def test_fast_runtime_treats_order_plan_rejection_as_no_trade(tmp_path):
    settings = _settings()
    settings.paper_learning_mode = True
    settings.enable_fast_scalp_order_submission = True
    settings.database_url = f"sqlite:///{tmp_path / 'fast-plan-block.db'}"
    database = Database(settings=settings)
    database.init_db()
    persistence = _Persistence()
    runtime = FastScalpRuntime(settings)
    decision = FastScalpDecision(
        datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
        "GLD",
        "SHORT",
        "paper_probe",
        True,
        0.5,
        50.0,
        "tiny-spread bid/ask pressure scalp",
        0.1,
        {
            "latest_price": 100.0,
            "spread_pct": 0.0002,
            "spread_regime": "tight",
            "spread_stability_score": 1.0,
            "websocket_connected": True,
            "stream_stale": False,
            "quote_age_seconds": 0.1,
            "trade_age_seconds": 0.1,
            "trade_intensity": 1.0,
            "liquidity_score": 0.9,
            "regime": "bullish_trend",
            "playbook": "spread_capture",
            "playbook_direction": "SHORT",
            "playbook_allowed": True,
            "playbook_score": 80,
        },
    )

    runtime._handle_decision(
        database,
        persistence,
        _Broker(),
        _RuntimePredictor(),
        _RejectingRisk(),
        _ExecutionMustNotRun(),
        decision,
    )

    assert persistence.no_trades[-1]["reason"] == "expected plan block"
    assert persistence.fast_decisions[-1]["status"] == "expected plan block"


def test_fast_scalp_paper_learning_mode_does_not_manufacture_neutral_probe():
    settings = _settings()
    settings.paper_learning_mode = True
    engine = FastScalpEngine(settings)
    now = utc_now()

    engine.on_event("quote", {"symbol": "GLD", "timestamp": now, "bid_price": 100.00, "ask_price": 100.02, "bid_size": 3000, "ask_size": 3000}, now)
    engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=100), "bid_price": 100.00, "ask_price": 100.02, "bid_size": 3000, "ask_size": 3000}, now + timedelta(milliseconds=100))
    engine.on_event("trade", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=150), "price": 100.01, "size": 25}, now + timedelta(milliseconds=150))
    decision = engine.on_event("quote", {"symbol": "GLD", "timestamp": now + timedelta(milliseconds=200), "bid_price": 100.00, "ask_price": 100.02, "bid_size": 3000, "ask_size": 3000}, now + timedelta(milliseconds=200))

    assert decision is not None
    assert not decision.allowed
    assert decision.decision == "NO_TRADE"
    assert decision.features["paper_learning_mode"] is True
    assert decision.features["paper_exploration"] is False


def test_fast_paper_exploration_promotes_only_a_structured_candidate(tmp_path):
    settings = _settings()
    settings.paper_learning_mode = True
    settings.paper_learning_exploration_sample_rate = 1.0
    settings.paper_learning_max_exploration_trades_per_day = 0
    settings.database_url = f"sqlite:///{tmp_path / 'fast-exploration.db'}"
    database = Database(settings=settings)
    database.init_db()
    now = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    decision = FastScalpDecision(
        now,
        "GLD",
        "NO_TRADE",
        "fast_block",
        False,
        0.60,
        60.0,
        "fast confidence 0.60 below 0.62",
        0.1,
        {
            "fast_candidate_direction": "LONG",
            "fast_candidate_score": 60.0,
            "fast_candidate_trigger": "spread_capture",
            "latest_price": 300.0,
            "websocket_connected": True,
            "stream_stale": False,
            "quote_age_seconds": 0.1,
            "trade_age_seconds": 0.1,
            "spread_pct": 0.0003,
            "spread_regime": "tight",
            "spread_stability_score": 0.9,
            "liquidity_score": 0.50,
            "trade_intensity": 1.0,
            "playbook": "spread_capture",
            "playbook_allowed": True,
            "playbook_direction": "LONG",
            "playbook_score": 60.0,
            "playbook_confirmation_count": 4,
            "playbook_required_confirmations": 4,
            "pattern_classification": "microstructure_pressure_up",
            "pattern_quality": 0.60,
            "regime": "sideways_chop",
        },
    )

    selected = PaperExplorationPolicy(settings, database).maybe_select_fast(decision, now=now)

    assert selected.decision == "LONG"
    assert selected.allowed is True
    assert selected.features["paper_exploration"] is True
    assert selected.features["exploration_selection_probability"] == 1.0
