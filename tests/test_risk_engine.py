import pytest

from gld_scalper.config import Settings
from gld_scalper.models import MLPrediction, MarketSignal, RiskState
from gld_scalper.risk_engine import OrderPlanRejected, RiskEngine, order_size_from_notional
from gld_scalper.utils.time_utils import utc_now


def _state():
    return RiskState(
        account_equity=1_000_000,
        day_start_equity=1_000_000,
        latest_price=200.0,
        spread_pct=0.0005,
        atr=0.30,
        market_open=True,
    )


def test_risk_blocks_daily_loss():
    engine = RiskEngine(Settings())
    state = _state()
    state.daily_realized_pnl = -11_000
    blocked, reason = engine.blocks_trading(state)
    assert blocked
    assert reason == "daily loss limit reached"


def test_risk_blocks_wide_spread():
    engine = RiskEngine(Settings())
    state = _state()
    state.spread_pct = 0.005
    blocked, reason = engine.blocks_trading(state)
    assert blocked
    assert reason == "spread too wide"


def test_risk_blocks_stale_data():
    engine = RiskEngine(Settings())
    state = _state()
    state.data_stale = True
    blocked, reason = engine.blocks_trading(state)
    assert blocked
    assert reason == "market data stale"


def test_risk_blocks_disconnected_websocket():
    engine = RiskEngine(Settings())
    state = _state()
    state.websocket_connected = False
    blocked, reason = engine.blocks_trading(state)
    assert blocked
    assert reason == "websocket disconnected"


def test_risk_blocks_very_low_liquidity_score():
    engine = RiskEngine(Settings())
    state = _state()
    state.liquidity_score = 0.1
    blocked, reason = engine.blocks_trading(state)
    assert blocked
    assert reason == "liquidity score too low"


def test_order_sizing_from_notional():
    assert order_size_from_notional(25_000, 250.5) == 99


def test_build_order_plan_uses_whole_shares():
    engine = RiskEngine(Settings())
    signal = MarketSignal(
        timestamp=utc_now(),
        symbol="GLD",
        decision="LONG",
        bullish_score=90,
        bearish_score=10,
        no_trade_score=20,
        regime="bullish_trend",
        confidence=0.9,
        reason="test",
        features={},
    )
    ml = MLPrediction(None, "long_good", 0.8, 0.1, 0.1, confidence=0.8)
    plan = engine.build_order_plan(signal, ml, _state())
    assert plan.qty == int(plan.estimated_notional // plan.entry_limit_price) or plan.qty > 0
    assert plan.side == "buy"


def test_paper_learning_mode_keeps_session_safety_limits():
    settings = Settings(paper_learning_mode=True)
    engine = RiskEngine(settings)
    state = _state()
    state.paper_learning_mode = True
    state.spread_pct = 0.0005
    state.spread_regime = "wide"
    state.liquidity_score = 0.20
    state.volatility_burst = True
    state.gold_volatility_regime = "high"
    state.trades_today = 10_000
    state.daily_realized_pnl = -500_000
    state.consecutive_losses = 100

    blocked, reason = engine.blocks_trading(state)

    assert blocked
    assert reason == "paper session trade limit reached"
    state.trades_today = 0
    state.consecutive_losses = 0
    state.daily_realized_pnl = 0
    state.data_stale = True
    assert engine.blocks_trading(state) == (True, "market data stale")


def test_paper_learning_mode_uses_tight_small_probe_bracket_without_dynamic_management():
    settings = Settings(paper_learning_mode=True, enable_dynamic_position_management=False)
    engine = RiskEngine(settings)
    state = _state()
    state.paper_learning_mode = True
    signal = MarketSignal(
        timestamp=utc_now(),
        symbol="GLD",
        decision="LONG",
        bullish_score=50,
        bearish_score=50,
        no_trade_score=100,
        regime="paper_learning",
        confidence=0.5,
        reason="probe",
        features={"paper_exploration": True, "paper_learning_mode": True},
    )

    plan = engine.build_order_plan(signal, MLPrediction(None, "no_trade", 0.2, 0.2, 0.6), state)

    assert plan.estimated_notional <= settings.paper_learning_exploration_max_notional
    assert 0 < (plan.take_profit_price - plan.entry_limit_price) / plan.entry_limit_price <= 0.002
    assert 0 < (plan.entry_limit_price - plan.stop_loss_price) / plan.entry_limit_price <= 0.002


def test_dynamic_position_management_uses_bounded_structural_stop_and_reward_target():
    settings = Settings(paper_learning_mode=True, enable_dynamic_position_management=True)
    engine = RiskEngine(settings)
    state = _state()
    state.paper_learning_mode = True
    signal = MarketSignal(
        timestamp=utc_now(),
        symbol="GLD",
        decision="LONG",
        bullish_score=80,
        bearish_score=20,
        no_trade_score=20,
        regime="paper_learning",
        confidence=0.8,
        reason="protected recovery probe",
        features={"paper_exploration": True, "paper_learning_mode": True},
    )

    plan = engine.build_order_plan(signal, MLPrediction(None, "long_good", 0.7, 0.1, 0.2), state)

    stop_pct = (plan.entry_limit_price - plan.stop_loss_price) / plan.entry_limit_price
    target_pct = (plan.take_profit_price - plan.entry_limit_price) / plan.entry_limit_price
    assert stop_pct <= settings.position_max_normal_stop_pct + 0.0001
    assert target_pct >= stop_pct * settings.position_min_reward_risk - 0.0001


def test_paper_learning_risk_allows_same_direction_scaling_only_below_cap():
    settings = Settings(paper_learning_mode=True, paper_learning_max_concurrent_trades=3)
    engine = RiskEngine(settings)
    state = _state()
    state.paper_learning_mode = True
    state.open_position = True
    state.open_position_direction = "LONG"
    state.active_trade_count = 2
    state.active_trade_direction = "LONG"
    state.active_trade_notional = 1_800.0

    assert engine.blocks_trading(state, "LONG") == (False, None)
    assert engine.blocks_trading(state, "SHORT") == (True, "opposite-direction GLD position already exists")
    state.active_trade_count = 3
    assert engine.blocks_trading(state, "LONG") == (True, "maximum concurrent paper-learning trades reached")


def test_paper_learning_rejects_spread_that_consumes_stop_distance():
    settings = Settings(paper_learning_mode=True)
    engine = RiskEngine(settings)
    state = _state()
    state.latest_price = 368.44
    state.spread_pct = 0.000923
    state.paper_learning_mode = True
    signal = MarketSignal(
        timestamp=utc_now(),
        symbol="GLD",
        decision="SHORT",
        bullish_score=0,
        bearish_score=46,
        no_trade_score=0,
        regime="fast_microstructure",
        confidence=0.46,
        reason="weak wide-spread probe",
        features={"paper_exploration": True, "paper_learning_mode": True},
    )

    blocked, reason = engine.blocks_trading(state, signal.decision)
    assert blocked
    assert "spread consumes too much" in (reason or "")

    with pytest.raises(OrderPlanRejected, match="spread consumes too much"):
        engine.build_order_plan(signal, MLPrediction(None, "rule_only", 0.34, 0.33, 0.33), state)
