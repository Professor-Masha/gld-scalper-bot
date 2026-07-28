from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Decision = Literal["LONG", "SHORT", "NO_TRADE"]
OrderSide = Literal["buy", "sell"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class MarketSignal:
    timestamp: datetime
    symbol: str
    decision: Decision
    bullish_score: float
    bearish_score: float
    no_trade_score: float
    regime: str
    confidence: float
    reason: str
    features: dict[str, Any] = field(default_factory=dict)
    model_version: str | None = None

    @property
    def is_trade(self) -> bool:
        return self.decision in {"LONG", "SHORT"}


@dataclass(slots=True)
class MLPrediction:
    model_version: str | None
    predicted_direction: str
    probability_long: float
    probability_short: float
    probability_no_trade: float
    expected_return: float = 0.0
    confidence: float = 0.0
    rejection_reason: str | None = None

    @property
    def rejects_trade(self) -> bool:
        return self.rejection_reason is not None or self.predicted_direction == "no_trade"


@dataclass(slots=True)
class RiskState:
    account_equity: float
    day_start_equity: float
    daily_realized_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    open_position: bool = False
    unexpected_open_orders: bool = False
    data_stale: bool = False
    websocket_connected: bool = True
    db_available: bool = True
    position_reconciled: bool = True
    market_open: bool = True
    spread_pct: float = 0.0
    latest_price: float = 0.0
    atr: float = 0.0
    liquidity_score: float = 1.0
    pattern_quality: float = 0.0
    pattern_classification: str = ""
    volatility_burst: bool = False
    gold_volatility_regime: str = ""
    spread_regime: str = ""
    macro_bias: str = "neutral_environment"
    macro_confidence: float = 0.0
    headline_event_risk: float = 0.0
    target_exposure_pct: float = 0.0
    order_block_direction: str = ""
    order_block_strength: float = 0.0
    order_block_retest_active: bool = False
    order_block_conflict: bool = False
    options_bias: str = "neutral"
    options_confidence: float = 0.0
    options_score_adjustment: float = 0.0
    options_event_risk: float = 0.0
    options_stale: bool = True
    paper_learning_mode: bool = False
    exploration_trade: bool = False
    active_trade_count: int = 0
    active_trade_direction: str = ""
    active_trade_notional: float = 0.0
    open_position_direction: str = ""
    cooldown_until: datetime | None = None
    session_drawdown_pct: float = 0.0
    order_count_last_minute: int = 0
    recent_execution_errors: int = 0
    correlated_exposure_notional: float = 0.0
    validated_after_cost_profit_factor: float = 0.0
    model_independently_validated: bool = False
    model_direction_aligned: bool = False


@dataclass(slots=True)
class OrderPlan:
    symbol: str
    direction: Decision
    side: OrderSide
    qty: int
    target_notional: float
    estimated_notional: float
    entry_limit_price: float
    take_profit_price: float
    stop_loss_price: float
    time_in_force: str = "day"
    client_order_id: str | None = None
    reason: str = ""
    stop_distance: float = 0.0
    target_distance: float = 0.0
    maximum_loss: float = 0.0
    economic_breakeven_pct: float = 0.0
    playbook: str = ""
    strategy_path: str = "minute"
    risk_multiplier: float = 1.0
    risk_details: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return (
            self.direction in {"LONG", "SHORT"}
            and self.qty > 0
            and self.entry_limit_price > 0
            and self.take_profit_price > 0
            and self.stop_loss_price > 0
        )


@dataclass(slots=True)
class BacktestTrade:
    symbol: str
    direction: Decision
    decision_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: int
    gross_pnl: float
    net_pnl_estimated: float
    exit_reason: str
    regime: str


@dataclass(slots=True)
class BacktestResult:
    start: datetime
    end: datetime
    trades: list[BacktestTrade]
    no_trade_count: int
    metrics: dict[str, float]
