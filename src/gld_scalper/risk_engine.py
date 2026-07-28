from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .exit_policy import build_exit_geometry
from .models import MLPrediction, MarketSignal, OrderPlan, RiskState
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import market_session, utc_now


class OrderPlanRejected(ValueError):
    """Expected no-trade result when a valid signal cannot produce a safe order."""


if TYPE_CHECKING:
    from .database import Database


class RiskEngine:
    def __init__(self, settings: Settings | None = None, database: "Database | None" = None) -> None:
        self.settings = settings or load_settings()
        self.database = database

    def state_from_features(self, features: dict) -> RiskState:
        latest_price = float(features.get("latest_price") or features.get("close") or 0)
        spread_pct = float(features.get("spread_pct") or 0)
        bar_age = float(features.get("data_age_seconds") or 0)
        quote_age = _age_or_large(features.get("quote_age_seconds"))
        trade_age = _age_or_large(features.get("trade_age_seconds"))
        live_tick_age = min(quote_age, trade_age)
        market_data_stale = bar_age > self.settings.bar_stale_seconds and live_tick_age > self.settings.quote_stale_seconds
        return RiskState(
            account_equity=self.settings.paper_account_size,
            day_start_equity=self.settings.paper_account_size,
            latest_price=latest_price,
            atr=float(features.get("atr_14") or 0),
            spread_pct=spread_pct,
            data_stale=market_data_stale or bool(features.get("stream_stale")),
            websocket_connected=bool(features.get("websocket_connected", True)),
            market_open=market_session(extended_hours=self.settings.enable_extended_hours) != "closed",
            liquidity_score=float(features.get("liquidity_score") or 1.0),
            pattern_quality=float(features.get("pattern_quality") or 0.0),
            pattern_classification=str(features.get("pattern_classification") or ""),
            volatility_burst=bool(features.get("volatility_burst")),
            gold_volatility_regime=str(features.get("gold_volatility_regime") or ""),
            spread_regime=str(features.get("spread_regime") or ""),
            macro_bias=str(features.get("macro_bias") or "neutral_environment"),
            macro_confidence=float(features.get("macro_confidence") or 0.0),
            headline_event_risk=float(features.get("headline_event_risk") or 0.0),
            target_exposure_pct=float(features.get("target_exposure_pct") or 0.0),
            order_block_direction=str(features.get("order_block_direction") or ""),
            order_block_strength=float(features.get("order_block_strength") or 0.0),
            order_block_retest_active=bool(features.get("order_block_retest_active")),
            order_block_conflict=bool(features.get("order_block_conflict")),
            options_bias=str(features.get("options_bias") or "neutral"),
            options_confidence=float(features.get("options_confidence") or 0.0),
            options_score_adjustment=float(features.get("options_score_adjustment") or 0.0),
            options_event_risk=float(features.get("options_event_risk") or 0.0),
            options_stale=bool(features.get("options_stale", True)),
            paper_learning_mode=bool(self.settings.paper_learning_mode and features.get("paper_learning_mode", True)),
            exploration_trade=bool(features.get("paper_exploration")),
            session_drawdown_pct=float(features.get("session_drawdown_pct") or 0.0),
            order_count_last_minute=int(features.get("order_count_last_minute") or 0),
            recent_execution_errors=int(features.get("recent_execution_errors") or 0),
            correlated_exposure_notional=float(features.get("correlated_exposure_notional") or 0.0),
            validated_after_cost_profit_factor=float(features.get("ml_validated_after_cost_profit_factor") or 0.0),
            model_independently_validated=bool(features.get("ml_independently_validated")),
            model_direction_aligned=bool(features.get("ml_direction_aligned")),
        )

    def enrich_runtime_state(self, state: RiskState, *, now: datetime | None = None) -> RiskState:
        if self.database is None:
            return state
        now = now or utc_now()
        start, end = _new_york_day(now)
        row = self.database.conn.execute(
            """
            SELECT realized_pl, drawdown_pct, equity, session_start_equity
            FROM account_snapshots
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp DESC, id DESC LIMIT 1
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        if row is not None:
            state.daily_realized_pnl = float(row["realized_pl"] or 0.0)
            state.session_drawdown_pct = float(row["drawdown_pct"] or 0.0)
            state.account_equity = float(row["equity"] or state.account_equity)
            state.day_start_equity = float(row["session_start_equity"] or state.account_equity)
        state.trades_today = int(
            self.database.conn.execute(
                "SELECT COUNT(*) AS count FROM trade_outcomes WHERE exit_time >= ? AND exit_time < ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()["count"]
        )
        losses = self.database.conn.execute(
            """
            SELECT COALESCE(net_pnl_after_costs, net_pnl_estimated, 0) AS pnl
            FROM trade_outcomes WHERE exit_time >= ? AND exit_time < ?
            ORDER BY julianday(exit_time) DESC, id DESC LIMIT 50
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        state.consecutive_losses = 0
        for item in losses:
            if float(item["pnl"] or 0.0) >= 0:
                break
            state.consecutive_losses += 1
        minute_ago = (now - timedelta(minutes=1)).isoformat()
        state.order_count_last_minute = int(
            self.database.conn.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE submitted_at >= ?",
                (minute_ago,),
            ).fetchone()["count"]
        )
        error_since = (now - timedelta(minutes=5)).isoformat()
        state.recent_execution_errors = int(
            self.database.conn.execute(
                """
                SELECT COUNT(*) AS count FROM system_logs
                WHERE timestamp >= ? AND event_type IN (
                    'order_submit_failed', 'execution_safety_entry_block',
                    'broker_order_sync_failed', 'execution_reconciliation_failed'
                )
                """,
                (error_since,),
            ).fetchone()["count"]
        )
        return state

    def blocks_trading(self, state: RiskState, direction: str | None = None) -> tuple[bool, str | None]:
        if not state.db_available:
            return True, "database unavailable"
        if not state.position_reconciled:
            return True, "account position reconciliation failed"
        if state.unexpected_open_orders:
            return True, "unexpected open orders exist"
        if state.open_position:
            if not state.paper_learning_mode:
                return True, "open GLD position already exists"
            if not direction or not state.open_position_direction:
                return True, "could not verify concurrent GLD direction"
            if state.open_position_direction != direction:
                return True, "opposite-direction GLD position already exists"
        if state.paper_learning_mode:
            if state.active_trade_count >= self.settings.paper_learning_max_concurrent_trades:
                return True, "maximum concurrent paper-learning trades reached"
            if state.active_trade_direction == "MIXED":
                return True, "mixed-direction GLD episodes detected"
            if state.active_trade_direction and direction and state.active_trade_direction != direction:
                return True, "opposite-direction paper-learning episode already active"
            if state.active_trade_notional >= self.settings.paper_learning_max_aggregate_notional:
                return True, "maximum aggregate paper-learning notional reached"
            if (
                not state.exploration_trade
                and state.trades_today >= self.settings.paper_max_trades_per_day
            ):
                return True, "paper session trade limit reached"
            if state.consecutive_losses >= self.settings.paper_max_consecutive_losses:
                return True, "paper consecutive-loss limit reached"
            if state.session_drawdown_pct >= self.settings.paper_max_drawdown_pct:
                return True, "paper session drawdown limit reached"
            paper_loss_pct = abs(min(0.0, state.daily_realized_pnl)) / max(state.day_start_equity, 1.0)
            if paper_loss_pct >= self.settings.paper_max_session_loss_pct:
                return True, "paper session loss limit reached"
        if state.data_stale:
            return True, "market data stale"
        if not state.websocket_connected:
            return True, "websocket disconnected"
        if not state.market_open:
            return True, "market/session not allowed"
        maximum_spread = self.settings.paper_learning_max_spread_pct if state.paper_learning_mode else self.settings.max_spread_pct
        if state.spread_pct > maximum_spread:
            return True, "spread too wide"
        if state.paper_learning_mode:
            spread_to_stop = safe_div(
                state.spread_pct,
                self.settings.paper_learning_stop_loss_pct,
                default=1.0,
            )
            if spread_to_stop > self.settings.paper_learning_max_spread_to_stop_ratio:
                return True, (
                    "paper-learning spread consumes too much of the stop distance "
                    f"({spread_to_stop:.3f} > {self.settings.paper_learning_max_spread_to_stop_ratio:.3f})"
                )
        if not state.paper_learning_mode and state.spread_regime == "wide":
            return True, "spread regime wide"
        minimum_liquidity = self.settings.paper_learning_min_liquidity_score if state.paper_learning_mode else 0.25
        if state.liquidity_score < minimum_liquidity:
            return True, "liquidity score too low"
        if not state.paper_learning_mode and state.volatility_burst and state.gold_volatility_regime == "high":
            return True, "volatility burst in high gold-volatility regime"
        if not state.paper_learning_mode and state.trades_today >= self.settings.max_trades_per_day:
            return True, "max trades per day reached"
        daily_loss_pct = abs(min(0.0, state.daily_realized_pnl)) / max(state.day_start_equity, 1.0)
        if not state.paper_learning_mode and daily_loss_pct >= self.settings.max_daily_loss_pct:
            return True, "daily loss limit reached"
        now = utc_now()
        if not state.paper_learning_mode and state.consecutive_losses >= self.settings.max_consecutive_losses:
            if state.cooldown_until is None or state.cooldown_until > now:
                return True, "consecutive-loss cooldown active"
        order_limit = self.settings.paper_max_orders_per_minute if state.paper_learning_mode else self.settings.max_orders_per_minute
        if state.order_count_last_minute >= order_limit:
            return True, "order-rate limit reached"
        if state.recent_execution_errors >= self.settings.risk_execution_error_limit:
            return True, "recent execution-error limit reached"
        if state.correlated_exposure_notional > state.account_equity * self.settings.max_correlated_exposure_pct:
            return True, "correlated exposure limit reached"
        return False, None

    def build_order_plan(self, signal: MarketSignal, ml_result: MLPrediction, state: RiskState) -> OrderPlan:
        if signal.decision not in {"LONG", "SHORT"}:
            raise ValueError("Cannot build an order plan for a no-trade signal.")
        entry_price = self._entry_limit_price(signal, state)
        geometry = build_exit_geometry(self.settings, signal, state, entry_price)
        if geometry.rejected_reason:
            raise OrderPlanRejected(geometry.rejected_reason)
        target_notional, risk_multiplier = self._target_notional(
            signal,
            ml_result,
            state,
            stop_distance_pct=geometry.stop_distance_pct,
        )
        qty = order_size_from_notional(target_notional, entry_price)
        estimated_notional = qty * entry_price
        minimum_usable_notional = (
            entry_price * 0.9
            if state.paper_learning_mode or signal.features.get("paper_exploration")
            else self.settings.min_trade_notional * 0.9
        )
        if qty <= 0 or estimated_notional < minimum_usable_notional:
            raise OrderPlanRejected("Calculated order size is below the minimum usable notional.")
        take_profit, stop_loss = geometry.target_price, geometry.stop_price
        if state.paper_learning_mode:
            spread_distance = entry_price * max(state.spread_pct, 0.0)
            execution_stop_distance = entry_price * self.settings.paper_learning_stop_loss_pct
            spread_to_stop = safe_div(spread_distance, execution_stop_distance, default=1.0)
            if spread_to_stop > self.settings.paper_learning_max_spread_to_stop_ratio:
                raise OrderPlanRejected(
                    "Paper-learning spread consumes too much of the stop distance "
                    f"({spread_to_stop:.3f} > {self.settings.paper_learning_max_spread_to_stop_ratio:.3f})."
                )
        return OrderPlan(
            symbol=signal.symbol,
            direction=signal.decision,
            side="buy" if signal.decision == "LONG" else "sell",
            qty=qty,
            target_notional=target_notional,
            estimated_notional=estimated_notional,
            entry_limit_price=round(entry_price, 2),
            take_profit_price=round(take_profit, 2),
            stop_loss_price=round(stop_loss, 2),
            reason=signal.reason,
            stop_distance=geometry.stop_distance,
            target_distance=geometry.target_distance,
            maximum_loss=qty * geometry.stop_distance,
            economic_breakeven_pct=geometry.economic_breakeven_pct,
            playbook=geometry.playbook,
            strategy_path=str(signal.features.get("strategy_path") or "minute"),
            risk_multiplier=risk_multiplier,
            risk_details={
                "exit_method": geometry.method,
                "stop_distance_pct": geometry.stop_distance_pct,
                "target_distance_pct": geometry.target_distance_pct,
                "economic_breakeven_pct": geometry.economic_breakeven_pct,
            },
        )

    def _target_notional(
        self,
        signal: MarketSignal,
        ml_result: MLPrediction,
        state: RiskState,
        *,
        stop_distance_pct: float,
    ) -> tuple[float, float]:
        sizing_stop_pct = max(stop_distance_pct, 0.0001)
        maximum_loss = state.account_equity * self.settings.max_trade_risk_pct
        if state.paper_learning_mode or signal.features.get("paper_exploration"):
            equity_cap = maximum_loss / sizing_stop_pct
            experimental = (
                signal.features.get("paper_exploration")
                or state.validated_after_cost_profit_factor < self.settings.experimental_profit_factor_threshold
                or not (state.model_independently_validated and state.model_direction_aligned)
            )
            quality_aligned = (
                state.pattern_quality >= 0.75
                and state.liquidity_score >= 0.75
                and state.spread_regime != "wide"
                and not state.volatility_burst
                and not state.data_stale
            )
            cap = (
                self.settings.paper_learning_exploration_max_notional
                if state.paper_learning_mode and signal.features.get("paper_exploration")
                else self.settings.paper_exploration_max_notional
            )
            if not experimental and quality_aligned:
                cap = min(self.settings.max_trade_notional, self.settings.paper_learning_max_aggregate_notional)
            notional = min(cap, equity_cap)
            return notional, safe_div(notional, self.settings.max_trade_notional, default=0.0)
        score = max(signal.bullish_score, signal.bearish_score)
        if score >= 90 and signal.confidence >= 0.85:
            notional = self.settings.max_trade_notional
        elif score >= 82:
            notional = min(10_000.0, self.settings.max_trade_notional)
        else:
            notional = self.settings.min_trade_notional
        if ml_result.confidence:
            notional *= clamp(0.75 + ml_result.confidence, 0.75, 1.25)
        if state.consecutive_losses > 0:
            notional *= max(0.5, 1 - 0.2 * state.consecutive_losses)
        if state.spread_regime == "normal":
            notional *= 0.90
        elif state.spread_regime == "wide":
            notional *= 0.50
        if state.liquidity_score < 0.50:
            notional *= 0.60
        elif state.liquidity_score < 0.70:
            notional *= 0.82
        if state.volatility_burst or state.gold_volatility_regime == "high":
            notional *= 0.70
        if state.data_stale:
            notional *= 0.25
        if state.headline_event_risk > 0.65 or state.macro_bias == "event_risk_environment":
            notional *= 0.85
        macro_adjustment = min(self.settings.llm_context_max_sizing_adjustment, min(state.macro_confidence, 0.50) * 0.10)
        if signal.decision == "LONG" and state.macro_bias == "bullish_gold_environment":
            notional *= 1.0 + macro_adjustment
        if signal.decision == "SHORT" and state.macro_bias == "bearish_gold_environment":
            notional *= 1.0 + macro_adjustment
        if signal.decision == "LONG" and state.macro_bias == "bearish_gold_environment":
            notional *= 0.92
        if signal.decision == "SHORT" and state.macro_bias == "bullish_gold_environment":
            notional *= 0.92
        expected_order_block = "bullish" if signal.decision == "LONG" else "bearish"
        if state.order_block_conflict:
            notional *= 0.85
        elif (
            state.order_block_retest_active
            and state.order_block_direction == expected_order_block
            and state.order_block_strength >= 0.65
        ):
            notional *= 1.08
        elif state.order_block_direction and state.order_block_direction != expected_order_block and state.order_block_strength >= 0.65:
            notional *= 0.85
        if not state.options_stale and state.options_confidence >= 0.35:
            options_direction = "bullish" if signal.decision == "LONG" else "bearish"
            if state.options_bias == options_direction:
                notional *= 1.0 + min(abs(state.options_score_adjustment), 3.0) / 100.0
            elif state.options_bias not in {"neutral", options_direction}:
                notional *= 0.92
            if state.options_event_risk >= 0.80:
                notional *= 0.85
        clean_pattern = state.pattern_classification in {"proper_break_up", "proper_break_down", "pullback_up", "pullback_down"}
        independent_agreement = state.model_independently_validated and state.model_direction_aligned
        if clean_pattern and state.pattern_quality >= 0.75 and state.liquidity_score >= 0.75 and not state.volatility_burst and independent_agreement:
            notional *= 1.15
        if state.validated_after_cost_profit_factor < self.settings.experimental_profit_factor_threshold:
            notional *= self.settings.experimental_size_multiplier
        if state.correlated_exposure_notional > 0:
            exposure_fraction = safe_div(state.correlated_exposure_notional, state.account_equity)
            notional *= clamp(1.0 - exposure_fraction / max(self.settings.max_correlated_exposure_pct, 0.01), 0.25, 1.0)
        equity_cap = maximum_loss / sizing_stop_pct
        capped = clamp(notional, min(self.settings.min_trade_notional, equity_cap), min(self.settings.max_trade_notional, equity_cap))
        return capped, safe_div(capped, self.settings.max_trade_notional, default=0.0)

    def _entry_limit_price(self, signal: MarketSignal, state: RiskState) -> float:
        spread_pad = max(state.latest_price * state.spread_pct / 2, 0.01)
        if signal.decision == "LONG":
            return state.latest_price + spread_pad
        return max(0.01, state.latest_price - spread_pad)

    def _exit_prices(self, signal: MarketSignal, state: RiskState, entry_price: float) -> tuple[float, float]:
        if state.paper_learning_mode:
            spread_distance = entry_price * max(state.spread_pct, 0.0)
            stop_pct = self.settings.paper_learning_stop_loss_pct
            if self.settings.enable_dynamic_position_management:
                stop_pct = max(stop_pct, self.settings.position_emergency_stop_pct)
            stop_distance = max(entry_price * stop_pct, spread_distance * 1.5, 0.02)
            take_profit_distance = max(
                entry_price * self.settings.paper_learning_take_profit_pct,
                spread_distance * 2.0,
            )
            if not self.settings.enable_dynamic_position_management:
                take_profit_distance = max(take_profit_distance, stop_distance * 1.05)
            if signal.decision == "LONG":
                return entry_price + take_profit_distance, entry_price - stop_distance
            return entry_price - take_profit_distance, entry_price + stop_distance
        atr_distance = 0.75 * state.atr if state.atr else 0.0
        stop_pct = self.settings.stop_loss_pct_floor
        if self.settings.enable_dynamic_position_management:
            stop_pct = max(stop_pct, self.settings.position_emergency_stop_pct)
        stop_distance = max(entry_price * stop_pct, atr_distance)
        if state.spread_regime == "wide" or state.liquidity_score < self.settings.minimum_liquidity_score:
            stop_distance *= 1.15
        if state.volatility_burst or state.gold_volatility_regime == "high":
            stop_distance *= 1.10
        take_profit_pct = clamp(float(state.atr / entry_price) if state.atr else self.settings.take_profit_pct_min, self.settings.take_profit_pct_min, self.settings.take_profit_pct_max)
        playbook_r = float(signal.features.get("playbook_target_r_multiple") or 1.15)
        if state.pattern_quality >= 0.75 and signal.features.get("proper_break"):
            playbook_r = max(playbook_r, 1.45)
        if signal.features.get("playbook") == "false_break_reversal":
            playbook_r = min(playbook_r, 1.15)
        take_profit_distance = max(entry_price * take_profit_pct, stop_distance * clamp(playbook_r, 1.05, 1.80))
        if signal.decision == "LONG":
            return entry_price + take_profit_distance, entry_price - stop_distance
        return entry_price - take_profit_distance, entry_price + stop_distance

    @staticmethod
    def cooldown_until(minutes: int = 30):
        return utc_now() + timedelta(minutes=minutes)


def order_size_from_notional(notional: float, latest_price: float) -> int:
    if latest_price <= 0:
        return 0
    return int(math.floor(notional / latest_price))


def _age_or_large(value) -> float:
    if value is None:
        return 1_000_000.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1_000_000.0


def _new_york_day(now: datetime) -> tuple[datetime, datetime]:
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    start_local = datetime.combine(eastern.date(), time.min, tzinfo=eastern.tzinfo)
    return start_local.astimezone(ZoneInfo("UTC")), (start_local + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
