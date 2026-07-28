from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import Settings, load_settings
from .database import Database
from .performance_tracking import build_fill_cost_context
from .trade_learning import TradeLearningAnalyzer
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_now

logger = logging.getLogger(__name__)


FILLED_STATUS = "filled"


@dataclass(frozen=True, slots=True)
class BrokerOrderNode:
    order: Any
    parent_order_id: str | None
    root_direction: str | None


class PaperOrderReconciler:
    def __init__(self, settings: Settings | None = None, database: Database | None = None, trading_client: Any | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)
        self.trading_client = trading_client

    def sync(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utc_now()
        orders = self._load_recent_orders()
        order_nodes = _flatten_orders(orders)
        seen_order_ids: set[str] = set()
        persisted_orders = 0
        persisted_fills = 0
        persisted_outcomes = 0

        for node in order_nodes:
            order = node.order
            order_id = _as_str(_field(order, "id"))
            if order_id and order_id in seen_order_ids:
                continue
            if order_id:
                seen_order_ids.add(order_id)
            self._persist_order(order, node)
            persisted_orders += 1
            persisted_fills += self._persist_fill(order, now, node.root_direction)

        persisted_outcomes += self._persist_trade_outcomes(order_nodes, now)
        pending = TradeLearningAnalyzer(self.settings, self.database).review_pending(limit=25)
        if pending["failed"]:
            logger.error("pending post-trade reviews failed count=%s", pending["failed"])
        return {"orders": persisted_orders, "fills": persisted_fills, "trade_outcomes": persisted_outcomes}

    def _load_recent_orders(self) -> list[Any]:
        if self.trading_client is None:
            return []
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("alpaca-py order request classes are unavailable.") from exc

        request = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            limit=500,
            nested=True,
            symbols=[self.settings.bot_symbol.upper()],
        )
        return list(self.trading_client.get_orders(filter=request) or [])

    def _persist_order(self, order: Any, node: BrokerOrderNode) -> None:
        symbol = _as_str(_field(order, "symbol", self.settings.bot_symbol)).upper()
        if symbol != self.settings.bot_symbol.upper():
            return
        order_id = _as_str(_field(order, "id")) or None
        client_order_id = _as_str(_field(order, "client_order_id")) or None
        existing = self.database.get_order(alpaca_order_id=order_id, client_order_id=client_order_id) or {}
        side = _as_str(_field(order, "side")) or existing.get("side") or "unknown"
        position_side = (
            None
            if node.parent_order_id or _is_closing_order(order)
            else existing.get("position_side") or node.root_direction
        )
        self.database.insert_order(
            {
                "alpaca_order_id": order_id,
                "client_order_id": client_order_id,
                "parent_order_id": node.parent_order_id,
                "symbol": symbol,
                "side": side,
                "position_side": position_side,
                "qty": _as_float(_field(order, "qty")),
                "notional": _as_float(_field(order, "notional")),
                "order_type": _as_str(_field(order, "order_type", _field(order, "type"))),
                "order_class": _as_str(_field(order, "order_class")),
                "time_in_force": _as_str(_field(order, "time_in_force")),
                "limit_price": _as_float(_field(order, "limit_price")),
                "stop_price": _as_float(_field(order, "stop_price")),
                "take_profit_price": existing.get("take_profit_price"),
                "status": _as_str(_field(order, "status")),
                "submitted_at": _field(order, "submitted_at"),
                "filled_at": _field(order, "filled_at"),
                "filled_qty": _as_float(_field(order, "filled_qty")),
                "filled_avg_price": _as_float(_field(order, "filled_avg_price")),
                "cancel_reason": _as_str(_field(order, "cancel_reason")),
                "raw_json": _to_jsonable(order),
            }
        )
        episode_id = self._episode_id_for_order(order, node, existing)
        if episode_id:
            exists = self.database.conn.execute(
                "SELECT 1 FROM execution_episodes WHERE episode_id = ? LIMIT 1",
                (episode_id,),
            ).fetchone()
            if exists is not None:
                order_type = _as_str(_field(order, "order_type", _field(order, "type"))).lower()
                closing = bool(node.parent_order_id) or _is_closing_order(order)
                role = "stop" if order_type in {"stop", "stop_limit", "trailing_stop"} else "take_profit" if closing else "entry"
                status = _as_str(_field(order, "status")).lower()
                self.database.record_execution_episode_order(
                    episode_id,
                    {
                        "order_key": order_id or client_order_id,
                        "alpaca_order_id": order_id,
                        "client_order_id": client_order_id,
                        "role": role,
                        "intent_type": "exit" if closing and status == FILLED_STATUS else "protective" if closing else "entry",
                        "side": side,
                        "qty": _as_float(_field(order, "qty")),
                        "filled_qty": _as_float(_field(order, "filled_qty")),
                        "filled_avg_price": _as_float(_field(order, "filled_avg_price")) or None,
                        "status": status,
                        "submitted_at": _field(order, "submitted_at"),
                        "raw_json": _to_jsonable(order),
                        "updated_at": _field(order, "updated_at") or utc_now(),
                    },
                )

    def _episode_id_for_order(self, order: Any, node: BrokerOrderNode, existing: dict[str, Any]) -> str | None:
        client_order_id = _as_str(_field(order, "client_order_id")) or str(existing.get("client_order_id") or "")
        if node.parent_order_id:
            parent = self.database.get_order(alpaca_order_id=node.parent_order_id)
            if parent:
                client_order_id = str(parent.get("client_order_id") or client_order_id)
        if not client_order_id.upper().startswith(f"{self.settings.bot_symbol.upper()}-"):
            return None
        return _root_episode_id(client_order_id)

    def _persist_fill(self, order: Any, now: datetime, root_direction: str | None) -> int:
        order_id = _as_str(_field(order, "id")) or _as_str(_field(order, "client_order_id"))
        if not order_id or self.database.fill_exists(order_id):
            return 0
        filled_qty = _as_float(_field(order, "filled_qty"))
        filled_avg_price = _as_float(_field(order, "filled_avg_price"))
        if filled_qty <= 0 or filled_avg_price <= 0:
            return 0
        symbol = _as_str(_field(order, "symbol", self.settings.bot_symbol)).upper()
        side = _as_str(_field(order, "side"))
        filled_at = _field(order, "filled_at") or now
        client_order_id = _as_str(_field(order, "client_order_id")) or None
        cost = build_fill_cost_context(
            self.database,
            self.settings,
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            qty=filled_qty,
            fill_price=filled_avg_price,
            fill_time=filled_at,
        )
        self.database.insert_fill(
            {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "qty": filled_qty,
                "price": filled_avg_price,
                "timestamp": filled_at,
                **cost.as_record(),
                "mode": "paper",
                "raw_json": _to_jsonable(order),
            }
        )
        self.database.insert_trading_journal(
            {
                "timestamp": filled_at,
                "symbol": symbol,
                "event_type": "ORDER_FILLED",
                "decision": root_direction,
                "order_id": order_id,
                "client_order_id": client_order_id,
                "side": side,
                "qty": filled_qty,
                "price": filled_avg_price,
                "notional": filled_qty * filled_avg_price,
                "status": _as_str(_field(order, "status")),
                "broker_snapshot_json": _to_jsonable(order),
            }
        )
        return 1

    def _persist_trade_outcomes(self, nodes: list[BrokerOrderNode], now: datetime) -> int:
        orders_by_id = {
            _as_str(_field(node.order, "id")): node.order
            for node in nodes
            if node.parent_order_id is None and _as_str(_field(node.order, "id"))
        }
        children_by_parent: dict[str, list[Any]] = {}
        for node in nodes:
            if node.parent_order_id:
                children_by_parent.setdefault(node.parent_order_id, []).append(node.order)

        count = 0
        for parent_id, parent in orders_by_id.items():
            parent_side = _as_str(_field(parent, "side"))
            direction = _position_side_from_order(parent)
            if direction not in {"LONG", "SHORT"}:
                continue
            parent_status = _as_str(_field(parent, "status"))
            if parent_status != FILLED_STATUS:
                continue
            entry_qty = _as_float(_field(parent, "filled_qty"))
            entry_price = _as_float(_field(parent, "filled_avg_price"))
            if entry_qty <= 0 or entry_price <= 0:
                continue
            legs = _dedupe_orders([*_legs(parent), *children_by_parent.get(parent_id, [])])
            exit_legs = [leg for leg in legs if _as_str(_field(leg, "status")) == FILLED_STATUS and _as_float(_field(leg, "filled_qty")) > 0]
            if not exit_legs:
                continue
            exit_leg = sorted(exit_legs, key=lambda leg: str(_field(leg, "filled_at") or ""))[-1]
            trade_id = _as_str(_field(parent, "client_order_id")) or parent_id
            if self.database.trade_outcome_exists(trade_id):
                continue

            exit_qty = min(entry_qty, _as_float(_field(exit_leg, "filled_qty")))
            exit_price = _as_float(_field(exit_leg, "filled_avg_price"))
            if exit_qty <= 0 or exit_price <= 0:
                continue
            gross_pnl = (exit_price - entry_price) * exit_qty if parent_side == "buy" else (entry_price - exit_price) * exit_qty
            entry_time = _field(parent, "filled_at") or _field(parent, "submitted_at") or now
            exit_time = _field(exit_leg, "filled_at") or now
            holding_seconds = (ensure_utc(exit_time) - ensure_utc(entry_time)).total_seconds()
            notional = exit_qty * entry_price
            exit_reason = _exit_reason(exit_leg)
            if exit_reason == "take_profit":
                managed_exit = self.database.latest_position_management_event(
                    trade_id,
                    ("PROTECTED_EXIT_REQUESTED",),
                )
                if managed_exit and managed_exit.get("reason"):
                    exit_reason = str(managed_exit["reason"])
            elif exit_reason == "stop_loss":
                managed_stop = self.database.latest_position_management_event(
                    trade_id,
                    ("STOP_REPLACED",),
                )
                if managed_stop and managed_stop.get("reason"):
                    exit_reason = f"{managed_stop['reason']}_triggered"
            decision = self.database.fetch_trade_decision(trade_id) or {}
            decision_features = _json_mapping(decision.get("feature_snapshot_json"))
            exploration_trade = bool(decision.get("exploration_trade") or decision_features.get("paper_exploration"))
            exit_order_id = _as_str(_field(exit_leg, "id"))
            cost_row = self.database.conn.execute(
                """
                SELECT COALESCE(SUM(spread_cost), 0) AS spread_cost,
                       COALESCE(SUM(slippage_cost), 0) AS slippage_cost,
                       COALESCE(SUM(estimated_fee), 0) AS estimated_fees,
                       COALESCE(SUM(estimated_live_cost), 0) AS estimated_live_cost
                FROM fills WHERE order_id IN (?, ?)
                """,
                (parent_id, exit_order_id),
            ).fetchone()
            estimated_live_cost = _as_float(cost_row["estimated_live_cost"])
            net_after_costs = gross_pnl - estimated_live_cost
            root_episode_id = _root_episode_id(trade_id)
            strategy_path = str(
                decision_features.get("strategy_path")
                or ("fast" if "FAST" in root_episode_id.upper() else "minute")
            )
            outcome_id = self.database.insert_trade_outcome(
                {
                    "trade_id": trade_id,
                    "symbol": self.settings.bot_symbol,
                    "direction": direction,
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "qty": exit_qty,
                    "notional": notional,
                    "gross_pnl": gross_pnl,
                    "net_pnl_estimated": net_after_costs,
                    "pnl_pct": safe_div(net_after_costs, notional),
                    "holding_seconds": holding_seconds,
                    "exit_reason": exit_reason,
                    "win_loss": "win" if net_after_costs > 0 else "loss",
                    "setup_type": str(
                        decision_features.get("playbook")
                        or decision_features.get("pattern_classification")
                        or f"paper_bracket:{trade_id}"
                    ),
                    "model_version": decision.get("model_version"),
                    "strategy_version": self.settings.strategy_version,
                    "exploration_trade": exploration_trade,
                    "root_episode_id": root_episode_id,
                    "strategy_path": strategy_path,
                    "playbook": decision_features.get("playbook"),
                    "regime": decision.get("regime") or decision_features.get("regime"),
                    "ml_prediction": decision.get("model_prediction") or decision_features.get("ml_predicted_direction"),
                    "confidence": decision.get("confidence") or decision_features.get("confidence"),
                    "spread_cost": _as_float(cost_row["spread_cost"]),
                    "slippage_cost": _as_float(cost_row["slippage_cost"]),
                    "estimated_fees": _as_float(cost_row["estimated_fees"]),
                    "estimated_live_cost": estimated_live_cost,
                    "net_pnl_after_costs": net_after_costs,
                    "mode": "paper",
                }
            )
            self.database.insert_trading_journal(
                {
                    "timestamp": exit_time,
                    "symbol": self.settings.bot_symbol,
                    "event_type": "TRADE_CLOSED",
                    "decision": direction,
                    "reason": exit_reason,
                    "order_id": _as_str(_field(exit_leg, "id")),
                    "client_order_id": trade_id,
                    "side": _as_str(_field(exit_leg, "side")),
                    "qty": exit_qty,
                    "price": exit_price,
                    "notional": notional,
                    "status": _as_str(_field(exit_leg, "status")),
                    "pnl": net_after_costs,
                    "signal_id": decision.get("signal_id"),
                    "exploration_trade": exploration_trade,
                    "broker_snapshot_json": {"entry_order": _to_jsonable(parent), "exit_order": _to_jsonable(exit_leg)},
                }
            )
            try:
                TradeLearningAnalyzer(self.settings, self.database).review(outcome_id)
            except Exception as exc:
                logger.exception("post-trade learning review failed trade_id=%s: %s", trade_id, exc)
                self.database.log_event(
                    "ERROR",
                    __name__,
                    "trade_learning_review_failed",
                    str(exc),
                    {"trade_id": trade_id, "trade_outcome_id": outcome_id},
                )
            count += 1
        return count


def _field(message: Any, name: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _legs(order: Any) -> list[Any]:
    legs = _field(order, "legs", None)
    if not legs:
        return []
    return list(legs)


def _flatten_orders(orders: list[Any]) -> list[BrokerOrderNode]:
    flat: list[BrokerOrderNode] = []
    for order in orders:
        direction = _position_side_from_order(order)
        flat.append(BrokerOrderNode(order=order, parent_order_id=None, root_direction=direction))
        parent_id = _as_str(_field(order, "id")) or _as_str(_field(order, "client_order_id"))
        flat.extend(_flatten_legs(_legs(order), parent_id=parent_id, root_direction=direction))
    return flat


def _flatten_legs(legs: list[Any], *, parent_id: str, root_direction: str | None) -> list[BrokerOrderNode]:
    flat: list[BrokerOrderNode] = []
    for leg in legs:
        flat.append(BrokerOrderNode(order=leg, parent_order_id=parent_id or None, root_direction=root_direction))
        leg_id = _as_str(_field(leg, "id")) or parent_id
        flat.extend(_flatten_legs(_legs(leg), parent_id=leg_id, root_direction=root_direction))
    return flat


def _dedupe_orders(orders: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for order in orders:
        order_id = _as_str(_field(order, "id")) or _as_str(_field(order, "client_order_id"))
        key = order_id or repr(order)
        if key in seen:
            continue
        seen.add(key)
        result.append(order)
    return result


def _position_side_from_order(order: Any) -> str | None:
    if _is_closing_order(order):
        return None
    position_intent = _as_str(_field(order, "position_intent")).lower()
    if position_intent == "buy_to_open":
        return "LONG"
    if position_intent == "sell_to_open":
        return "SHORT"
    side = _as_str(_field(order, "side"))
    if side == "buy":
        return "LONG"
    if side == "sell":
        return "SHORT"
    return None


def _is_closing_order(order: Any) -> bool:
    return _as_str(_field(order, "position_intent")).lower().endswith("_to_close")


def _exit_reason(order: Any) -> str:
    order_type = _as_str(_field(order, "order_type", _field(order, "type")))
    if order_type == "limit":
        return "take_profit"
    if order_type in {"stop", "stop_limit"}:
        return "stop_loss"
    return order_type or "closed"


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return {key: item for key, item in value.__dict__.items() if not key.startswith("_")}
    return {"repr": repr(value)}


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        import json

        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _root_episode_id(client_order_id: str) -> str:
    value = str(client_order_id)
    for suffix in ("-TAKE", "-RUN"):
        if value.upper().endswith(suffix):
            return value[: -len(suffix)]
    return value
