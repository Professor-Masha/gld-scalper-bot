from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import Settings, load_settings
from .concurrent_trading import is_bot_managed_order
from .database import Database
from .exit_policy import economic_breakeven_pct
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_now

if TYPE_CHECKING:
    from .execution_safety import OrderIntentCoordinator
    from .ml.transformer_runtime import AsyncTransformerShadowRuntime


logger = logging.getLogger(__name__)
TERMINAL_ORDER_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
    "stopped",
    "suspended",
}


@dataclass(slots=True)
class ExitDecision:
    should_exit: bool
    reason: str | None = None


@dataclass(slots=True)
class ManagedTrade:
    trade_id: str
    parent_order_id: str
    symbol: str
    direction: str
    role: str
    qty: float
    entry_time: datetime
    entry_price: float
    stop_order_id: str
    take_profit_order_id: str
    initial_stop_price: float
    current_stop_price: float
    take_profit_price: float
    high_water_price: float
    low_water_price: float
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    breakeven_armed: bool = False
    trailing_armed: bool = False
    exit_requested: bool = False
    last_action: str | None = None
    last_action_at: datetime | None = None
    status: str = "active"
    details: dict[str, Any] = field(default_factory=dict)

    def update_mark(self, mark: float) -> None:
        self.high_water_price = max(self.high_water_price, mark)
        self.low_water_price = min(self.low_water_price, mark)
        if self.direction == "LONG":
            favorable = safe_div(self.high_water_price - self.entry_price, self.entry_price)
            adverse = safe_div(self.entry_price - self.low_water_price, self.entry_price)
        else:
            favorable = safe_div(self.entry_price - self.low_water_price, self.entry_price)
            adverse = safe_div(self.high_water_price - self.entry_price, self.entry_price)
        self.max_favorable_excursion = max(self.max_favorable_excursion, favorable)
        self.max_adverse_excursion = max(self.max_adverse_excursion, adverse)

    def pnl_pct(self, mark: float) -> float:
        move = safe_div(mark - self.entry_price, self.entry_price)
        return move if self.direction == "LONG" else -move

    def pnl(self, mark: float) -> float:
        move = mark - self.entry_price
        return move * self.qty if self.direction == "LONG" else -move * self.qty


@dataclass(frozen=True, slots=True)
class PositionEvent:
    event_type: str
    payload: dict[str, Any]
    received_at: datetime


class PositionManager:
    """Pure exit policy retained for backtests and focused unit tests."""

    def __init__(self, settings: Settings | None = None, database: Database | None = None, trading_client: Any | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database
        self.trading_client = trading_client

    def evaluate_exit(
        self,
        *,
        direction: str,
        entry_time: datetime,
        latest_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        entry_price: float | None = None,
        trend_reversal: bool = False,
        emergency_risk: bool = False,
    ) -> ExitDecision:
        now = utc_now()
        holding_minutes = (now - ensure_utc(entry_time)).total_seconds() / 60
        if emergency_risk:
            return ExitDecision(True, "risk_engine_shutdown")
        if direction == "LONG":
            if latest_price >= take_profit_price:
                return ExitDecision(True, "take_profit")
            if latest_price <= stop_loss_price:
                return ExitDecision(True, "emergency_stop")
        else:
            if latest_price <= take_profit_price:
                return ExitDecision(True, "take_profit")
            if latest_price >= stop_loss_price:
                return ExitDecision(True, "emergency_stop")
        if entry_price:
            move = safe_div(latest_price - entry_price, entry_price)
            pnl_pct = move if direction == "LONG" else -move
            if holding_minutes >= self.settings.max_holding_minutes and pnl_pct >= self.settings.position_profitable_time_exit_buffer_pct:
                return ExitDecision(True, "max_holding_time_in_profit")
            if trend_reversal and pnl_pct <= -self.settings.position_invalidation_min_loss_pct:
                return ExitDecision(True, "setup_invalidated")
        return ExitDecision(False, None)

    def reconcile_positions(self) -> tuple[bool, str | None]:
        if self.trading_client is None:
            return True, None
        try:
            positions = self.trading_client.get_all_positions()
        except Exception as exc:  # pragma: no cover - broker path
            return False, f"could not load Alpaca positions: {exc}"
        non_gld = [p for p in positions if str(_field(p, "symbol", "")).upper() != "GLD"]
        if non_gld:
            return False, "unexpected non-GLD broker position"
        return True, None


class DynamicPositionRuntime:
    """Event-driven paper position manager with broker-resident catastrophic stops."""

    def __init__(
        self,
        settings: Settings | None = None,
        trading_client: Any | None = None,
        coordinator: "OrderIntentCoordinator | None" = None,
        transformer_runtime: "AsyncTransformerShadowRuntime | None" = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.trading_client = trading_client
        self.coordinator = coordinator
        self.transformer_runtime = transformer_runtime
        self._queue: queue.Queue[PositionEvent] = queue.Queue(maxsize=self.settings.fast_scalp_event_queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._context_lock = threading.Lock()
        self._context: dict[str, Any] = {}
        self._episodes: dict[str, ManagedTrade] = {}
        self._episodes_lock = threading.RLock()
        self._latest_quote: dict[str, Any] | None = None
        self._latest_trade: dict[str, Any] | None = None
        self._last_refresh_at: datetime | None = None
        self._last_clock_refresh_at: datetime | None = None
        self._clock: Any | None = None
        self._last_snapshot_at: datetime | None = None
        self._last_management_eval_at: datetime | None = None
        self._last_event_at: datetime | None = None
        self._dropped_events = 0
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dynamic-position-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def enqueue_event(self, event_type: str, payload: dict[str, Any], received_at: datetime | None = None) -> None:
        if event_type not in {"quote", "trade"}:
            return
        event = PositionEvent(event_type, dict(payload), ensure_utc(received_at or utc_now()))
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped_events += 1

    def update_context(self, features: dict[str, Any]) -> None:
        keys = {
            "signal_decision",
            "signal_confidence",
            "signal_reason",
            "ml_predicted_direction",
            "ml_confidence",
            "order_block_direction",
            "order_block_strength",
            "macro_bias",
            "macro_confidence",
            "liquidity_score",
            "spread_regime",
            "volatility_burst",
            "technical_direction",
            "technical_confidence",
            "technical_quality",
            "technical_big3_aligned",
            "technical_route",
            "fvg_direction",
            "fvg_midpoint",
            "ema_9",
            "ema_21",
            "vwap",
            "support_level",
            "resistance_level",
            "rsi_divergence",
        }
        with self._context_lock:
            self._context.update({key: features.get(key) for key in keys if key in features})
            if "decision" in features:
                self._context["signal_decision"] = features.get("decision")
            if "confidence" in features:
                self._context["signal_confidence"] = features.get("confidence")
            self._context["updated_at"] = utc_now()

    def status(self) -> dict[str, Any]:
        with self._episodes_lock:
            active_episode_count = len(self._episodes)
        return {
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "queue_size": self._queue.qsize(),
            "dropped_events": self._dropped_events,
            "active_episodes": active_episode_count,
            "last_event_at": self._last_event_at.isoformat() if self._last_event_at else None,
            "last_error": self._last_error,
        }

    def episode_snapshot(self) -> set[str]:
        with self._episodes_lock:
            return set(self._episodes)

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        client = self.trading_client
        if client is None:
            from .alpaca_clients import get_trading_client

            client = get_trading_client(self.settings)
        try:
            while not self._stop_event.is_set():
                try:
                    event = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                self._last_event_at = event.received_at
                self._accept_event(event)
                self._persist_price_snapshot(database, event.received_at)
                self._refresh_broker_state(database, client, event.received_at)
                mark = self._mark_price()
                if mark is None:
                    continue
                if self._last_management_eval_at is not None:
                    age_ms = (event.received_at - self._last_management_eval_at).total_seconds() * 1_000
                    if age_ms < self.settings.position_manager_interval_ms:
                        continue
                self._last_management_eval_at = event.received_at
                with self._episodes_lock:
                    episodes = list(self._episodes.values())
                for episode in episodes:
                    self._manage_episode(database, client, episode, mark, event.received_at)
        except Exception as exc:  # pragma: no cover - runtime guard
            self._last_error = str(exc)
            logger.exception("dynamic position runtime failed: %s", exc)
            database.log_event("ERROR", __name__, "dynamic_position_runtime_failed", str(exc), self.status())
        finally:
            database.close()

    def _accept_event(self, event: PositionEvent) -> None:
        if str(event.payload.get("symbol") or "").upper() != self.settings.bot_symbol.upper():
            return
        payload = {**event.payload, "received_at": event.received_at}
        if event.event_type == "quote":
            self._latest_quote = payload
        elif event.event_type == "trade":
            self._latest_trade = payload

    def _persist_price_snapshot(self, database: Database, now: datetime) -> None:
        interval = self.settings.price_snapshot_interval_seconds
        bucket_second = now.second - now.second % interval
        bucket = now.replace(second=bucket_second, microsecond=0)
        if self._last_snapshot_at is not None and bucket <= self._last_snapshot_at:
            return
        quote = self._latest_quote or {}
        trade = self._latest_trade or {}
        bid = _float(quote.get("bid_price"))
        ask = _float(quote.get("ask_price"))
        midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else _float(trade.get("price"))
        if midpoint <= 0:
            return
        spread = ask - bid if ask > 0 and bid > 0 else None
        quote_received = quote.get("received_at")
        trade_received = trade.get("received_at")
        database.upsert_price_snapshot(
            {
                "timestamp": bucket,
                "symbol": self.settings.bot_symbol,
                "bid_price": bid or None,
                "ask_price": ask or None,
                "midpoint": midpoint,
                "last_trade_price": _float(trade.get("price")) or None,
                "spread": spread,
                "spread_pct": safe_div(spread, midpoint) if spread is not None else None,
                "quote_age_seconds": (now - quote_received).total_seconds() if quote_received else None,
                "trade_age_seconds": (now - trade_received).total_seconds() if trade_received else None,
                "source": "alpaca_stream_1s",
            }
        )
        self._last_snapshot_at = bucket

    def _refresh_broker_state(self, database: Database, client: Any, now: datetime) -> None:
        if self._last_refresh_at is not None:
            age = (now - self._last_refresh_at).total_seconds()
            if age < self.settings.position_manager_broker_refresh_seconds:
                return
        self._last_refresh_at = now
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            request = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                limit=500,
                after=now - timedelta(days=1),
                nested=True,
                symbols=[self.settings.bot_symbol],
            )
            orders = list(client.get_orders(filter=request) or [])
            with self._episodes_lock:
                current_episodes = dict(self._episodes)
            discovered = _managed_trades_from_orders(orders, database, current_episodes)
            discovered_by_id = {episode.trade_id: episode for episode in discovered}
            for trade_id, episode in current_episodes.items():
                if trade_id in discovered_by_id:
                    continue
                episode.status = "closed" if database.trade_outcome_exists(trade_id) else "awaiting_reconciliation"
                episode.last_action = "broker_episode_inactive"
                episode.last_action_at = now
                self._persist_state(database, episode, now)
            with self._episodes_lock:
                self._episodes = discovered_by_id
            if self._last_clock_refresh_at is None or (now - self._last_clock_refresh_at).total_seconds() >= 30:
                self._clock = client.get_clock()
                self._last_clock_refresh_at = now
        except Exception as exc:  # pragma: no cover - broker path
            self._last_error = str(exc)
            logger.warning("position broker refresh failed: %s", exc, extra={"event_type": "position_refresh_failed"})

    def _manage_episode(self, database: Database, client: Any, episode: ManagedTrade, mark: float, now: datetime) -> None:
        episode.update_mark(mark)
        pnl_pct = episode.pnl_pct(mark)
        spread_pct = self._spread_pct(mark)
        if self.transformer_runtime is not None:
            with self._context_lock:
                context = dict(self._context)
            shadow = self.transformer_runtime.submit(
                "exit",
                now,
                {
                    **context,
                    "direction": episode.direction,
                    "entry_price": episode.entry_price,
                    "mark_price": mark,
                    "pnl_pct": pnl_pct,
                    "spread_pct": spread_pct,
                    "holding_seconds": (now - episode.entry_time).total_seconds(),
                    "maximum_favorable_excursion": episode.max_favorable_excursion,
                    "maximum_adverse_excursion": episode.max_adverse_excursion,
                    "current_stop_price": episode.current_stop_price,
                    "take_profit_price": episode.take_profit_price,
                    "breakeven_armed": episode.breakeven_armed,
                    "trailing_armed": episode.trailing_armed,
                },
            )
            context.update(shadow.as_features())
        action = self._next_action(episode, pnl_pct, spread_pct, now)
        if action is not None:
            kind, reason, price = action
            if kind == "replace_stop":
                self._replace_stop(database, client, episode, price, reason, mark, pnl_pct, now)
            elif kind == "request_exit":
                self._request_exit(database, client, episode, reason, mark, pnl_pct, now)
        self._persist_state(database, episode, now)

    def _next_action(
        self,
        episode: ManagedTrade,
        pnl_pct: float,
        spread_pct: float,
        now: datetime,
    ) -> tuple[str, str, float] | None:
        minutes_to_close = self._minutes_to_close(now)
        if minutes_to_close is not None and minutes_to_close <= self.settings.position_force_flatten_minutes_before_close:
            return "request_exit", "session_close_protection", 0.0
        economic_breakeven = self._economic_breakeven_pct(episode, spread_pct)
        if (
            minutes_to_close is not None
            and minutes_to_close <= self.settings.position_close_risk_reduction_minutes_before_close
            and (pnl_pct < economic_breakeven or episode.max_favorable_excursion < economic_breakeven * 1.5)
        ):
            return "request_exit", "session_close_risk_reduction", 0.0
        structural_votes, structural_reason = self._structural_reversal_votes(episode.direction)
        if (
            len(structural_votes) >= self.settings.position_structural_profit_exit_votes
            and pnl_pct >= economic_breakeven
        ):
            return "request_exit", f"structural_reversal_profit_exit: {structural_reason}", 0.0
        invalidated, invalidation_reason = self._setup_invalidated(episode.direction)
        holding_seconds = (now - episode.entry_time).total_seconds()
        if (
            self.settings.position_allow_discretionary_loss_exit
            and invalidated
            and holding_seconds >= self.settings.position_soft_exit_min_hold_seconds
            and pnl_pct <= -self.settings.position_invalidation_min_loss_pct
        ):
            return "request_exit", invalidation_reason, 0.0
        with self._context_lock:
            context = dict(self._context)
        if (
            pnl_pct >= economic_breakeven
            and (
                (1.0 if context.get("liquidity_score") is None else _float(context.get("liquidity_score"))) < 0.30
                or str(context.get("spread_regime") or "") == "wide"
            )
        ):
            return "request_exit", "liquidity_or_spread_deterioration", 0.0
        holding_minutes = (now - episode.entry_time).total_seconds() / 60
        profitable_exit = max(self.settings.position_profitable_time_exit_buffer_pct, economic_breakeven)
        if holding_minutes >= self.settings.max_holding_minutes and pnl_pct >= profitable_exit:
            return "request_exit", "max_holding_time_in_profit", 0.0

        if episode.max_favorable_excursion >= self.settings.position_profit_giveback_min_mfe_pct:
            giveback = episode.max_favorable_excursion - pnl_pct
            maximum_giveback = episode.max_favorable_excursion * self.settings.position_max_profit_giveback_fraction
            if giveback >= maximum_giveback and pnl_pct >= economic_breakeven:
                return "request_exit", "maximum_profit_giveback", 0.0

        trigger = max(self.settings.position_breakeven_trigger_pct, economic_breakeven * 1.10)
        candidate: float | None = None
        reason = ""
        if episode.max_favorable_excursion >= trigger and not episode.breakeven_armed:
            offset = episode.entry_price * max(economic_breakeven, self.settings.position_breakeven_offset_pct)
            candidate = episode.entry_price + offset if episode.direction == "LONG" else episode.entry_price - offset
            reason = "economic_breakeven_profit_lock"
        if episode.max_favorable_excursion >= self.settings.position_trailing_trigger_pct:
            if episode.direction == "LONG":
                trailing = episode.high_water_price * (1 - self.settings.position_trailing_distance_pct)
                candidate = max(candidate or 0.0, trailing)
            else:
                trailing = episode.low_water_price * (1 + self.settings.position_trailing_distance_pct)
                candidate = min(candidate if candidate is not None else float("inf"), trailing)
            reason = "trailing_profit_lock"
            structural_values = [
                _float(context.get("ema_9")),
                _float(context.get("ema_21")),
                _float(context.get("vwap")),
                _float(context.get("fvg_midpoint")),
                _float(context.get("support_level")) if episode.direction == "LONG" else _float(context.get("resistance_level")),
            ]
            if episode.direction == "LONG":
                usable = [value for value in structural_values if episode.entry_price < value < episode.high_water_price]
                if usable:
                    candidate = max(candidate or 0.0, max(usable))
                    reason = "ema_vwap_structure_trail"
            else:
                usable = [value for value in structural_values if episode.low_water_price < value < episode.entry_price]
                if usable:
                    candidate = min(candidate if candidate is not None else float("inf"), min(usable))
                    reason = "ema_vwap_structure_trail"
        if (
            minutes_to_close is not None
            and minutes_to_close <= self.settings.position_close_management_minutes_before_close
            and episode.max_favorable_excursion >= economic_breakeven
        ):
            locked_pct = max(economic_breakeven, episode.max_favorable_excursion * 0.35)
            close_lock = episode.entry_price * locked_pct
            close_candidate = episode.entry_price + close_lock if episode.direction == "LONG" else episode.entry_price - close_lock
            if episode.direction == "LONG":
                candidate = max(candidate or 0.0, close_candidate)
            else:
                candidate = min(candidate if candidate is not None else float("inf"), close_candidate)
            reason = "session_close_staged_profit_lock"
        if candidate is None:
            return None
        quote = self._latest_quote or {}
        bid = _float(quote.get("bid_price")) or episode.high_water_price
        ask = _float(quote.get("ask_price")) or episode.low_water_price
        if episode.direction == "LONG":
            candidate = min(candidate, bid - 0.01)
            improved = candidate - episode.current_stop_price
        else:
            candidate = max(candidate, ask + 0.01)
            improved = episode.current_stop_price - candidate
        if improved + 1e-9 < self.settings.position_min_stop_improvement:
            return None
        if episode.last_action_at is not None:
            age = (now - episode.last_action_at).total_seconds()
            if age < self.settings.position_stop_replace_cooldown_seconds:
                return None
        return "replace_stop", reason, round(candidate, 2)

    def _replace_stop(
        self,
        database: Database,
        client: Any,
        episode: ManagedTrade,
        new_stop: float,
        reason: str,
        mark: float,
        pnl_pct: float,
        now: datetime,
    ) -> None:
        from alpaca.trading.requests import ReplaceOrderRequest

        old_stop = episode.current_stop_price
        try:
            request = ReplaceOrderRequest(stop_price=new_stop)
            if self.coordinator is not None:
                replacement = self.coordinator.replace_order(
                    episode.stop_order_id,
                    request,
                    idempotency_key=f"{episode.trade_id}:stop:{new_stop:.2f}:{reason}",
                    episode_id=episode.trade_id,
                )
            else:
                replacement = client.replace_order_by_id(episode.stop_order_id, order_data=request)
            episode.stop_order_id = _string(_field(replacement, "id")) or episode.stop_order_id
            episode.current_stop_price = new_stop
            episode.breakeven_armed = episode.breakeven_armed or reason in {
                "economic_breakeven_profit_lock",
                "session_close_staged_profit_lock",
            }
            episode.trailing_armed = episode.trailing_armed or reason == "trailing_profit_lock"
            episode.last_action = reason
            episode.last_action_at = now
            self._record_event(database, episode, "STOP_REPLACED", reason, mark, pnl_pct, now, old_stop, new_stop, "submitted")
            logger.info(
                "position stop replaced trade_id=%s reason=%s old=%.2f new=%.2f pnl_pct=%.6f",
                episode.trade_id,
                reason,
                old_stop,
                new_stop,
                pnl_pct,
                extra={"event_type": "position_stop_replaced"},
            )
        except Exception as exc:  # pragma: no cover - broker path
            self._last_error = str(exc)
            self._record_event(database, episode, "STOP_REPLACE_FAILED", reason, mark, pnl_pct, now, old_stop, new_stop, "failed", {"error": str(exc)})
            logger.warning("position stop replacement failed trade_id=%s error=%s", episode.trade_id, exc)

    def _request_exit(
        self,
        database: Database,
        client: Any,
        episode: ManagedTrade,
        reason: str,
        mark: float,
        pnl_pct: float,
        now: datetime,
    ) -> None:
        if episode.last_action_at is not None and (episode.exit_requested or episode.last_action == reason):
            age = (now - episode.last_action_at).total_seconds()
            if age < max(5, self.settings.position_stop_replace_cooldown_seconds):
                return
        from alpaca.trading.requests import ReplaceOrderRequest

        quote = self._latest_quote or {}
        marketable_price = _float(quote.get("bid_price")) if episode.direction == "LONG" else _float(quote.get("ask_price"))
        marketable_limit = _bracket_safe_exit_limit(
            episode.direction,
            marketable_price or mark,
            episode.current_stop_price,
        )
        if marketable_limit is None:
            episode.last_action = reason
            episode.last_action_at = now
            self._record_event(
                database,
                episode,
                "PROTECTED_EXIT_DEFERRED",
                reason,
                mark,
                pnl_pct,
                now,
                None,
                None,
                "protected_by_stop",
                {
                    "marketable_price": marketable_price or mark,
                    "stop_price": episode.current_stop_price,
                    "reason": "marketable limit would violate Alpaca bracket price ordering",
                },
            )
            logger.info(
                "protected exit deferred to broker stop trade_id=%s reason=%s marketable=%.2f stop=%.2f",
                episode.trade_id,
                reason,
                marketable_price or mark,
                episode.current_stop_price,
                extra={"event_type": "protected_exit_deferred"},
            )
            return
        try:
            request = ReplaceOrderRequest(limit_price=marketable_limit)
            if self.coordinator is not None:
                replacement = self.coordinator.replace_order(
                    episode.take_profit_order_id,
                    request,
                    idempotency_key=f"{episode.trade_id}:exit:{marketable_limit:.2f}:{reason}",
                    episode_id=episode.trade_id,
                )
            else:
                replacement = client.replace_order_by_id(episode.take_profit_order_id, order_data=request)
            episode.take_profit_order_id = _string(_field(replacement, "id")) or episode.take_profit_order_id
            episode.take_profit_price = marketable_limit
            episode.exit_requested = True
            episode.last_action = reason
            episode.last_action_at = now
            self._record_event(database, episode, "PROTECTED_EXIT_REQUESTED", reason, mark, pnl_pct, now, None, None, "submitted", {"limit_price": marketable_limit})
            logger.warning(
                "protected position exit requested trade_id=%s reason=%s limit=%.2f pnl_pct=%.6f",
                episode.trade_id,
                reason,
                marketable_limit,
                pnl_pct,
                extra={"event_type": "protected_position_exit"},
            )
        except Exception as exc:  # pragma: no cover - broker path
            self._last_error = str(exc)
            self._record_event(database, episode, "PROTECTED_EXIT_FAILED", reason, mark, pnl_pct, now, None, None, "failed", {"error": str(exc)})
            logger.error("protected position exit failed trade_id=%s error=%s", episode.trade_id, exc)

    def _setup_invalidated(self, direction: str) -> tuple[bool, str]:
        with self._context_lock:
            context = dict(self._context)
        opposite_signal = "SHORT" if direction == "LONG" else "LONG"
        opposite_context = "bearish" if direction == "LONG" else "bullish"
        opposite_ml = "short_good" if direction == "LONG" else "long_good"
        opposite_macro = "bearish_gold_environment" if direction == "LONG" else "bullish_gold_environment"
        votes: list[str] = []
        vote_groups: set[str] = set()
        reason = str(context.get("signal_reason") or "").lower()
        if (
            context.get("signal_decision") == opposite_signal
            and _float(context.get("signal_confidence")) >= self.settings.position_invalidation_min_confidence
            and "paper learning probe" not in reason
        ):
            votes.append("opposite deterministic signal")
            vote_groups.add("deterministic")
        if (
            context.get("ml_predicted_direction") == opposite_ml
            and _float(context.get("ml_confidence")) >= self.settings.position_invalidation_min_confidence
        ):
            votes.append("opposite ML signal")
            vote_groups.add("ml")
        if (
            context.get("order_block_direction") == opposite_context
            and _float(context.get("order_block_strength")) >= 0.65
        ):
            votes.append("opposite order block")
            vote_groups.add("structure")
        if (
            context.get("technical_direction") == opposite_signal
            and _float(context.get("technical_confidence")) >= self.settings.position_invalidation_min_confidence
        ):
            votes.append("opposite grouped technical route")
            vote_groups.add("technical")
        if context.get("rsi_divergence") == opposite_context:
            votes.append("opposite RSI divergence")
            vote_groups.add("structure")
        if context.get("fvg_direction") == opposite_context:
            votes.append("opposite fair-value-gap structure")
            vote_groups.add("structure")
        if (
            context.get("macro_bias") == opposite_macro
            and _float(context.get("macro_confidence")) >= 0.60
        ):
            votes.append("opposite macro context")
            vote_groups.add("macro")
        required = self.settings.position_invalidation_required_votes
        return len(vote_groups) >= required, f"setup_invalidated: {', '.join(votes)}"

    def _structural_reversal_votes(self, direction: str) -> tuple[list[str], str]:
        with self._context_lock:
            context = dict(self._context)
        opposite = "bearish" if direction == "LONG" else "bullish"
        votes: list[str] = []
        if (
            context.get("order_block_direction") == opposite
            and _float(context.get("order_block_strength")) >= 0.65
        ):
            votes.append("opposite order block")
        if context.get("rsi_divergence") == opposite:
            votes.append("opposite RSI divergence")
        if context.get("fvg_direction") == opposite:
            votes.append("opposite fair-value-gap structure")
        return votes, ", ".join(votes)

    def _minutes_to_close(self, now: datetime) -> float | None:
        clock = self._clock
        if clock is None or not bool(_field(clock, "is_open", False)):
            return None
        next_close = _field(clock, "next_close")
        if next_close is None:
            return None
        remaining = (ensure_utc(next_close) - now).total_seconds() / 60
        return remaining if remaining >= 0 else None

    def _economic_breakeven_pct(self, episode: ManagedTrade, spread_pct: float) -> float:
        stored = _float(episode.details.get("economic_breakeven_pct"))
        runtime = economic_breakeven_pct(self.settings, spread_pct=spread_pct)
        entry_cost_pct = _float(episode.details.get("realized_entry_cost_pct"))
        fee_pct = safe_div(self.settings.estimated_fee_per_share, episode.entry_price)
        expected_exit_cost_pct = (
            max(0.0, spread_pct) / 2.0
            + self.settings.estimated_round_trip_slippage_pct / 2.0
            + fee_pct
        )
        realized_cost_floor = entry_cost_pct + expected_exit_cost_pct + self.settings.position_min_net_profit_pct
        return max(stored, runtime, realized_cost_floor)

    def _spread_pct(self, mark: float) -> float:
        quote = self._latest_quote or {}
        bid = _float(quote.get("bid_price"))
        ask = _float(quote.get("ask_price"))
        return safe_div(ask - bid, mark) if bid > 0 and ask > 0 else 0.0

    def _mark_price(self) -> float | None:
        quote = self._latest_quote or {}
        bid = _float(quote.get("bid_price"))
        ask = _float(quote.get("ask_price"))
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        trade = self._latest_trade or {}
        value = _float(trade.get("price"))
        return value if value > 0 else None

    def _persist_state(self, database: Database, episode: ManagedTrade, now: datetime) -> None:
        record = {
            field_name: getattr(episode, field_name)
            for field_name in episode.__dataclass_fields__
        }
        record["updated_at"] = now
        database.upsert_position_management_state(record)

    def _record_event(
        self,
        database: Database,
        episode: ManagedTrade,
        action: str,
        reason: str,
        mark: float,
        pnl_pct: float,
        now: datetime,
        old_stop: float | None,
        new_stop: float | None,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        event_details = {
            "economic_breakeven_pct": episode.details.get("economic_breakeven_pct"),
            "playbook": episode.details.get("playbook"),
            "strategy_path": episode.details.get("strategy_path"),
            "spread_pct": self._spread_pct(mark),
            **(details or {}),
        }
        database.insert_position_management_event(
            {
                "timestamp": now,
                "trade_id": episode.trade_id,
                "parent_order_id": episode.parent_order_id,
                "symbol": episode.symbol,
                "direction": episode.direction,
                "role": episode.role,
                "action": action,
                "reason": reason,
                "qty": episode.qty,
                "entry_price": episode.entry_price,
                "mark_price": mark,
                "pnl": episode.pnl(mark),
                "pnl_pct": pnl_pct,
                "old_stop_price": old_stop,
                "new_stop_price": new_stop,
                "high_water_price": episode.high_water_price,
                "low_water_price": episode.low_water_price,
                "max_favorable_excursion": episode.max_favorable_excursion,
                "max_adverse_excursion": episode.max_adverse_excursion,
                "status": status,
                "details": event_details,
            }
        )


def _managed_trades_from_orders(
    orders: list[Any],
    database: Database,
    existing: dict[str, ManagedTrade] | None = None,
) -> list[ManagedTrade]:
    existing = existing or {}
    result: list[ManagedTrade] = []
    for order in orders:
        if _is_closing_order(order) or not is_bot_managed_order(order, "GLD", database):
            continue
        direction = _entry_direction(order)
        if direction is None or _status(order) != "filled":
            continue
        trade_id = _string(_field(order, "client_order_id")) or _string(_field(order, "id"))
        if not trade_id or database.trade_outcome_exists(trade_id):
            continue
        legs = list(_field(order, "legs", []) or [])
        stop_leg = _active_leg(legs, {"stop", "stop_limit"})
        take_profit_leg = _active_leg(legs, {"limit"})
        if stop_leg is None or take_profit_leg is None:
            continue
        entry_price = _float(_field(order, "filled_avg_price"))
        qty = _float(_field(order, "filled_qty", _field(order, "qty")))
        if entry_price <= 0 or qty <= 0:
            continue
        prior = existing.get(trade_id)
        stored = database.get_position_management_state(trade_id) if prior is None else None
        root_trade_id = _root_episode_id(trade_id)
        stored_order = database.get_order(client_order_id=trade_id) or database.get_order(client_order_id=root_trade_id) or {}
        risk_details = _json_mapping(stored_order.get("risk_details_json"))
        entry_cost_row = database.conn.execute(
            "SELECT COALESCE(SUM(estimated_live_cost), 0) AS cost FROM fills WHERE order_id = ?",
            (_string(_field(order, "id")),),
        ).fetchone()
        entry_live_cost = _float(entry_cost_row["cost"]) if entry_cost_row is not None else 0.0
        decision = database.fetch_trade_decision(trade_id) or database.fetch_trade_decision(root_trade_id) or {}
        decision_features = _json_mapping(decision.get("feature_snapshot_json"))
        stop_price = _float(_field(stop_leg, "stop_price"))
        take_profit_price = _float(_field(take_profit_leg, "limit_price"))
        entry_time = ensure_utc(_field(order, "filled_at") or _field(order, "submitted_at") or utc_now())
        mark_seed = entry_price
        stored_stop = _float(stored.get("current_stop_price")) if stored else 0.0
        if prior is not None:
            stored_stop = prior.current_stop_price
        if stored_stop > 0:
            if direction == "LONG":
                stop_price = max(stop_price, stored_stop)
            else:
                stop_price = min(stop_price or stored_stop, stored_stop)
        prior_high = prior.high_water_price if prior else _float(stored.get("high_water_price")) if stored else mark_seed
        prior_low = prior.low_water_price if prior else _float(stored.get("low_water_price")) if stored else mark_seed
        stored_action_at = ensure_utc(stored["last_action_at"]) if stored and stored.get("last_action_at") else None
        result.append(
            ManagedTrade(
                trade_id=trade_id,
                parent_order_id=_string(_field(order, "id")),
                symbol="GLD",
                direction=direction,
                role="runner" if trade_id.endswith("-RUN") else "take_profit" if trade_id.endswith("-TAKE") else "single",
                qty=qty,
                entry_time=entry_time,
                entry_price=entry_price,
                stop_order_id=_string(_field(stop_leg, "id")),
                take_profit_order_id=_string(_field(take_profit_leg, "id")),
                initial_stop_price=prior.initial_stop_price if prior else _float(stored.get("initial_stop_price")) if stored else stop_price,
                current_stop_price=stop_price,
                take_profit_price=take_profit_price,
                high_water_price=prior_high or mark_seed,
                low_water_price=prior_low or mark_seed,
                max_favorable_excursion=prior.max_favorable_excursion if prior else _float(stored.get("max_favorable_excursion")) if stored else 0.0,
                max_adverse_excursion=prior.max_adverse_excursion if prior else _float(stored.get("max_adverse_excursion")) if stored else 0.0,
                breakeven_armed=prior.breakeven_armed if prior else bool(stored.get("breakeven_armed")) if stored else False,
                trailing_armed=prior.trailing_armed if prior else bool(stored.get("trailing_armed")) if stored else False,
                exit_requested=prior.exit_requested if prior else bool(stored.get("exit_requested")) if stored else False,
                last_action=prior.last_action if prior else str(stored.get("last_action") or "") or None if stored else None,
                last_action_at=prior.last_action_at if prior else stored_action_at,
                details={
                    "broker_order_status": _status(order),
                    "economic_breakeven_pct": stored_order.get("economic_breakeven_pct") or risk_details.get("economic_breakeven_pct"),
                    "realized_entry_cost_pct": safe_div(entry_live_cost, entry_price * qty),
                    "playbook": stored_order.get("playbook") or decision_features.get("playbook"),
                    "strategy_path": stored_order.get("strategy_path") or decision_features.get("strategy_path"),
                },
            )
        )
    return result


def _active_leg(legs: list[Any], order_types: set[str]) -> Any | None:
    candidates = [
        leg
        for leg in legs
        if _string(_field(leg, "order_type", _field(leg, "type"))).lower() in order_types
        and _status(leg) not in TERMINAL_ORDER_STATUSES
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda leg: str(_field(leg, "updated_at", _field(leg, "submitted_at", ""))))[-1]


def _entry_direction(order: Any) -> str | None:
    intent = _string(_field(order, "position_intent")).lower()
    if intent == "buy_to_open":
        return "LONG"
    if intent == "sell_to_open":
        return "SHORT"
    if intent.endswith("_to_close"):
        return None
    side = _string(_field(order, "side")).lower()
    return "LONG" if side == "buy" else "SHORT" if side == "sell" else None


def _is_closing_order(order: Any) -> bool:
    return _string(_field(order, "position_intent")).lower().endswith("_to_close")


def _status(order: Any) -> str:
    return _string(_field(order, "status")).lower()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _string(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _root_episode_id(trade_id: str) -> str:
    upper = trade_id.upper()
    for suffix in ("-TAKE", "-RUN"):
        if upper.endswith(suffix):
            return trade_id[: -len(suffix)]
    return trade_id


def _bracket_safe_exit_limit(direction: str, marketable_price: float, stop_price: float) -> float | None:
    """Return a marketable child-limit only when it preserves Alpaca bracket ordering."""
    marketable = round(marketable_price, 2)
    stop = round(stop_price, 2)
    if marketable <= 0 or stop <= 0:
        return None
    if direction == "LONG":
        minimum = round(stop + 0.01, 2)
        return marketable if marketable >= minimum else None
    maximum = round(stop - 0.01, 2)
    return marketable if marketable <= maximum else None
