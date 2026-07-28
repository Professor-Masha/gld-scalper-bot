from __future__ import annotations

import json
import logging
import random
import string
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .alpaca_clients import get_trading_client
from .config import Settings, load_settings
from .concurrent_trading import broker_position_direction, is_bot_managed_order
from .database import Database
from .models import OrderPlan
from .utils.time_utils import utc_now

if False:  # pragma: no cover - imported only for static type checking
    from .execution_safety import OrderIntentCoordinator


_ORDER_SUBMISSION_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubmittedEntry:
    order: Any
    client_order_id: str
    qty: int
    role: str
    plan: OrderPlan


@dataclass(frozen=True, slots=True)
class SubmissionGroup:
    entries: tuple[SubmittedEntry, ...]
    errors: tuple[str, ...] = ()

    @property
    def primary(self) -> SubmittedEntry:
        return self.entries[0]


class ExecutionEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        database: Database | None = None,
        trading_client: Any | None = None,
        coordinator: "OrderIntentCoordinator | None" = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.settings.validate_safety()
        self.database = database or Database(settings=self.settings)
        self.trading_client = trading_client
        self.coordinator = coordinator

    def submit_entry_with_protection(self, plan: OrderPlan) -> SubmissionGroup:
        if not plan.valid:
            raise ValueError("Invalid order plan; refusing to submit.")
        if plan.symbol.upper() != "GLD":
            raise RuntimeError("Refusing to trade any symbol except GLD.")
        plan.client_order_id = plan.client_order_id or make_client_order_id(plan.symbol, plan.direction)
        episode_id = str(plan.client_order_id)
        client = self.trading_client or get_trading_client(self.settings)
        try:
            from alpaca.trading.enums import OrderClass, OrderSide, OrderType, TimeInForce
            from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("alpaca-py trading request classes are unavailable.") from exc

        tranches = _protected_tranche_plans(plan, self.settings)
        submitted: list[SubmittedEntry] = []
        errors: list[str] = []
        with _ORDER_SUBMISSION_LOCK:
            if self.coordinator is not None:
                self.coordinator.prepare_entry(plan.direction)
            _assert_concurrent_gld_episode(
                client,
                plan,
                self.settings,
                self.database,
                planned_episode_count=len(tranches),
            )
            self.database.create_execution_episode(
                {
                    "episode_id": episode_id,
                    "symbol": plan.symbol,
                    "direction": plan.direction,
                    "source": "fast" if "FAST" in episode_id.upper() else "minute",
                    "strategy_path": plan.strategy_path,
                    "playbook": plan.playbook,
                    "planned_qty": plan.qty,
                    "opened_at": utc_now(),
                    "details": {
                        "reason": plan.reason,
                        "tranche_count": len(tranches),
                        "strategy_path": plan.strategy_path,
                        "playbook": plan.playbook,
                    },
                }
            )
            for tranche, role in tranches:
                order_side = OrderSide.BUY if tranche.side == "buy" else OrderSide.SELL
                request = LimitOrderRequest(
                    symbol=tranche.symbol,
                    qty=tranche.qty,
                    side=order_side,
                    type=OrderType.LIMIT,
                    time_in_force=TimeInForce.DAY,
                    limit_price=tranche.entry_limit_price,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tranche.take_profit_price),
                    stop_loss=StopLossRequest(stop_price=tranche.stop_loss_price),
                    client_order_id=tranche.client_order_id,
                )
                submitted_at = utc_now()
                try:
                    if self.coordinator is not None:
                        order = self.coordinator.submit_entry(
                            request,
                            client_order_id=str(tranche.client_order_id),
                            direction=tranche.direction,
                            episode_id=episode_id,
                        )
                    else:
                        order = client.submit_order(order_data=request)
                    self._persist_submission(tranche, order, submitted_at)
                    parent_order_id = str(getattr(order, "id", "")) or None
                    exit_side = "sell" if tranche.side == "buy" else "buy"
                    self.database.record_execution_bracket_bundle(
                        episode_id,
                        entry={
                            "order_key": str(tranche.client_order_id),
                            "alpaca_order_id": parent_order_id,
                            "client_order_id": tranche.client_order_id,
                            "role": role,
                            "intent_type": "entry",
                            "strategy_path": tranche.strategy_path,
                            "playbook": tranche.playbook,
                            "side": tranche.side,
                            "qty": tranche.qty,
                            "filled_qty": getattr(order, "filled_qty", 0),
                            "filled_avg_price": getattr(order, "filled_avg_price", None),
                            "status": str(getattr(getattr(order, "status", None), "value", getattr(order, "status", "submitted"))),
                            "submitted_at": submitted_at,
                            "raw_json": _order_to_json(order),
                        },
                        stop={
                            "order_key": f"{tranche.client_order_id}:expected-stop",
                            "parent_order_id": parent_order_id,
                            "role": "stop",
                            "intent_type": "protective",
                            "strategy_path": tranche.strategy_path,
                            "playbook": tranche.playbook,
                            "side": exit_side,
                            "qty": tranche.qty,
                            "status": "pending_activation",
                            "submitted_at": submitted_at,
                            "raw_json": {"expected_stop_price": tranche.stop_loss_price},
                        },
                        take_profit={
                            "order_key": f"{tranche.client_order_id}:expected-take-profit",
                            "parent_order_id": parent_order_id,
                            "role": "take_profit",
                            "intent_type": "protective",
                            "strategy_path": tranche.strategy_path,
                            "playbook": tranche.playbook,
                            "side": exit_side,
                            "qty": tranche.qty,
                            "status": "pending_activation",
                            "submitted_at": submitted_at,
                            "raw_json": {"expected_take_profit_price": tranche.take_profit_price},
                        },
                    )
                    submitted.append(
                        SubmittedEntry(
                            order=order,
                            client_order_id=str(tranche.client_order_id),
                            qty=tranche.qty,
                            role=role,
                            plan=tranche,
                        )
                    )
                except Exception as exc:  # pragma: no cover - network path
                    self._persist_submission_failure(tranche, submitted_at, exc)
                    self.database.record_execution_episode_order(
                        episode_id,
                        {
                            "order_key": str(tranche.client_order_id),
                            "client_order_id": tranche.client_order_id,
                            "role": role,
                            "intent_type": "entry",
                            "side": tranche.side,
                            "qty": tranche.qty,
                            "status": "submit_failed",
                            "submitted_at": submitted_at,
                            "raw_json": {"error": str(exc)},
                        },
                    )
                    errors.append(f"{role}: {exc}")
                    if not submitted:
                        self.database.finalize_execution_episode_submission(episode_id, failed=True)
                        raise
                    logger.exception(
                        "protected runner tranche submission failed after primary protection was accepted: %s",
                        exc,
                        extra={"event_type": "partial_tranche_submit_failed"},
                    )
                    break
            self.database.finalize_execution_episode_submission(episode_id, partial=bool(errors))
        return SubmissionGroup(tuple(submitted), tuple(errors))

    def _persist_submission(self, plan: OrderPlan, order: Any, submitted_at: datetime) -> None:
        self.database.insert_order(
            {
                "alpaca_order_id": str(getattr(order, "id", "")),
                "client_order_id": plan.client_order_id,
                "symbol": plan.symbol,
                "side": plan.side,
                "position_side": plan.direction,
                "qty": plan.qty,
                "notional": plan.estimated_notional,
                "order_type": "limit",
                "order_class": "bracket",
                "time_in_force": plan.time_in_force,
                "limit_price": plan.entry_limit_price,
                "stop_price": plan.stop_loss_price,
                "take_profit_price": plan.take_profit_price,
                "status": str(getattr(order, "status", "submitted")),
                "submitted_at": submitted_at,
                "strategy_path": plan.strategy_path,
                "playbook": plan.playbook,
                "maximum_loss": plan.maximum_loss,
                "economic_breakeven_pct": plan.economic_breakeven_pct,
                "risk_details": plan.risk_details,
                "raw_json": _order_to_json(order),
            }
        )

    def _persist_submission_failure(self, plan: OrderPlan, submitted_at: datetime, exc: Exception) -> None:
        self.database.insert_order(
            {
                "client_order_id": plan.client_order_id,
                "symbol": plan.symbol,
                "side": plan.side,
                "position_side": plan.direction,
                "qty": plan.qty,
                "notional": plan.estimated_notional,
                "order_type": "limit",
                "order_class": "bracket",
                "time_in_force": plan.time_in_force,
                "limit_price": plan.entry_limit_price,
                "stop_price": plan.stop_loss_price,
                "take_profit_price": plan.take_profit_price,
                "status": "submit_failed",
                "submitted_at": submitted_at,
                "strategy_path": plan.strategy_path,
                "playbook": plan.playbook,
                "maximum_loss": plan.maximum_loss,
                "economic_breakeven_pct": plan.economic_breakeven_pct,
                "risk_details": plan.risk_details,
                "raw_json": {"error": str(exc)},
            }
        )

    def cancel_stale_orders(self) -> None:
        client = self.trading_client or get_trading_client(self.settings)
        orders = client.get_orders()
        for order in orders:
            symbol = str(getattr(order, "symbol", "")).upper()
            if symbol == "GLD":
                order_id = getattr(order, "id")
                if self.coordinator is not None:
                    self.coordinator.cancel_order(str(order_id), reason="stale_order_cleanup")
                else:
                    client.cancel_order_by_id(order_id)


def make_client_order_id(symbol: str, direction: str, now: datetime | None = None) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    timestamp = (now or utc_now()).strftime("%Y%m%d-%H%M%S")
    return f"{symbol.upper()}-{timestamp}-{direction.upper()}-{suffix}"


def _assert_concurrent_gld_episode(
    client: Any,
    plan: OrderPlan,
    settings: Settings,
    database: Database,
    planned_episode_count: int = 1,
) -> None:
    symbol = plan.symbol.upper()
    positions = client.get_all_positions()
    position = next(
        (
            item
            for item in positions
            if str(getattr(item, "symbol", "")).upper() == symbol
            and abs(float(getattr(item, "qty", 0) or 0)) > 0
        ),
        None,
    )
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    open_orders = client.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
    )
    if not settings.paper_learning_mode:
        if position is not None:
            raise RuntimeError("GLD order submission gate: an open position already exists")
        if open_orders:
            raise RuntimeError("GLD order submission gate: an open order already exists")
        return

    unknown_orders = [item for item in open_orders if not is_bot_managed_order(item, symbol, database)]
    if unknown_orders:
        raise RuntimeError("GLD order submission gate: an unknown open order exists")
    summary = database.active_trade_episode_summary(symbol)
    active_count = int(summary["count"])
    active_direction = str(summary["direction"])
    active_notional = float(summary["notional"])
    position_direction = broker_position_direction(position)
    if position is not None and active_count == 0:
        active_count = 1
        active_direction = position_direction
        active_notional = abs(float(getattr(position, "market_value", 0) or 0))
    if active_direction == "MIXED" or (position_direction and active_direction and position_direction != active_direction):
        raise RuntimeError("GLD order submission gate: mixed-direction episodes detected")
    existing_direction = active_direction or position_direction
    if existing_direction and existing_direction != plan.direction:
        raise RuntimeError("GLD order submission gate: opposite-direction tranche rejected")
    if active_count + planned_episode_count > settings.paper_learning_max_concurrent_trades:
        raise RuntimeError("GLD order submission gate: concurrent tranche limit reached")
    if active_notional + plan.estimated_notional > settings.paper_learning_max_aggregate_notional + 1e-6:
        raise RuntimeError("GLD order submission gate: aggregate tranche notional limit reached")


def _order_to_json(order: Any) -> str:
    if hasattr(order, "model_dump"):
        return json.dumps(order.model_dump(mode="json"), default=str)
    if hasattr(order, "__dict__"):
        return json.dumps(order.__dict__, default=str)
    return json.dumps({"repr": repr(order)})


def _protected_tranche_plans(plan: OrderPlan, settings: Settings) -> list[tuple[OrderPlan, str]]:
    if not settings.enable_partial_profit_tranches or plan.qty < 2:
        return [(plan, "single")]
    take_qty = max(1, min(plan.qty - 1, int(plan.qty * settings.partial_profit_fraction)))
    runner_qty = plan.qty - take_qty
    root_id = str(plan.client_order_id)
    take_id = _role_client_order_id(root_id, "TAKE")
    runner_id = _role_client_order_id(root_id, "RUN")
    target_distance = abs(plan.take_profit_price - plan.entry_limit_price)
    runner_distance = target_distance * settings.partial_profit_runner_target_multiplier
    runner_target = (
        plan.entry_limit_price + runner_distance
        if plan.direction == "LONG"
        else plan.entry_limit_price - runner_distance
    )
    take_plan = replace(
        plan,
        qty=take_qty,
        estimated_notional=take_qty * plan.entry_limit_price,
        client_order_id=take_id,
    )
    runner_plan = replace(
        plan,
        qty=runner_qty,
        estimated_notional=runner_qty * plan.entry_limit_price,
        take_profit_price=round(runner_target, 2),
        client_order_id=runner_id,
    )
    return [(take_plan, "take_profit"), (runner_plan, "runner")]


def _role_client_order_id(root: str, role: str) -> str:
    suffix = f"-{role}"
    return f"{root[: 48 - len(suffix)]}{suffix}"
