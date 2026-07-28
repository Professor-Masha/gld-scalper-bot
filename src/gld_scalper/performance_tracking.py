from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Database
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_now


@dataclass(frozen=True, slots=True)
class FillCostContext:
    expected_price: float
    submitted_price: float
    bid_price: float
    ask_price: float
    midpoint: float
    spread: float
    spread_pct: float
    slippage_per_share: float
    slippage_cost: float
    spread_cost: float
    estimated_fee: float
    estimated_live_cost: float
    client_order_id: str | None
    episode_id: str | None
    strategy_path: str

    def as_record(self) -> dict[str, Any]:
        record = self.__dict__ if hasattr(self, "__dict__") else {
            field: getattr(self, field) for field in self.__dataclass_fields__
        }
        return {**record, "spread_at_entry": self.spread, "slippage": self.slippage_per_share}


def build_fill_cost_context(
    database: Database,
    settings: Settings,
    *,
    order_id: str,
    client_order_id: str | None,
    symbol: str,
    side: str,
    qty: float,
    fill_price: float,
    fill_time: datetime | str,
) -> FillCostContext:
    order = database.get_order(alpaca_order_id=order_id, client_order_id=client_order_id) or {}
    root_client_id = str(order.get("client_order_id") or client_order_id or "")
    if order.get("parent_order_id"):
        parent = database.get_order(alpaca_order_id=str(order["parent_order_id"])) or {}
        root_client_id = str(parent.get("client_order_id") or root_client_id)
    episode_id = _root_episode_id(root_client_id) if root_client_id else None
    decision = database.fetch_trade_decision(root_client_id) or database.fetch_trade_decision(episode_id) or {}
    features = _json_mapping(decision.get("feature_snapshot_json"))
    quote = database.get_quote_at_or_before(symbol, fill_time) or database.get_latest_quote(symbol) or {}
    bid = _number(quote.get("bid_price"))
    ask = _number(quote.get("ask_price"))
    midpoint = (bid + ask) / 2 if bid > 0 and ask >= bid else _number(features.get("midpoint"), fill_price)
    spread = ask - bid if bid > 0 and ask >= bid else _number(features.get("spread"))
    spread_pct = safe_div(spread, midpoint)
    submitted = _number(order.get("limit_price")) or _number(order.get("stop_price")) or midpoint
    side_lower = side.lower()
    expected_touch = ask if side_lower == "buy" and ask > 0 else bid if side_lower == "sell" and bid > 0 else 0.0
    expected = expected_touch or _number(features.get("expected_price")) or submitted or midpoint or fill_price
    slippage_per_share = fill_price - expected if side_lower == "buy" else expected - fill_price
    slippage_cost = max(0.0, slippage_per_share) * qty
    spread_cost = max(0.0, spread) * qty / 2
    estimated_fee = max(settings.estimated_minimum_order_fee, settings.estimated_fee_per_share * qty)
    estimated_live_cost = slippage_cost + spread_cost + estimated_fee
    strategy_path = str(features.get("strategy_path") or ("fast" if "FAST" in root_client_id.upper() else "minute"))
    return FillCostContext(
        expected,
        submitted,
        bid,
        ask,
        midpoint,
        spread,
        spread_pct,
        slippage_per_share,
        slippage_cost,
        spread_cost,
        estimated_fee,
        estimated_live_cost,
        root_client_id or None,
        episode_id,
        strategy_path,
    )


class AccountPerformanceTracker:
    """Capture broker/account performance on a stable session baseline."""

    def __init__(self, settings: Settings, database: Database, trading_client: Any) -> None:
        self.settings = settings
        self.database = database
        self.trading_client = trading_client
        self._last_capture_at: datetime | None = None

    def capture(self, stage: str, *, now: datetime | None = None, force: bool = False) -> dict[str, Any] | None:
        now = ensure_utc(now or utc_now())
        if not force and self._last_capture_at is not None:
            if (now - self._last_capture_at).total_seconds() < self.settings.performance_snapshot_interval_seconds:
                return None
        account = self.trading_client.get_account()
        positions = list(self.trading_client.get_all_positions() or [])
        open_orders = _open_orders(self.trading_client, self.settings.bot_symbol)
        start, end = _new_york_session_day(now)
        prior = self.database.conn.execute(
            """
            SELECT equity, session_start_equity, session_peak_equity
            FROM account_snapshots WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp DESC, id DESC LIMIT 1
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        equity = _number(_field(account, "equity"))
        session_start = _number(prior["session_start_equity"], equity) if prior else equity
        previous_peak = _number(prior["session_peak_equity"], equity) if prior else equity
        peak = max(previous_peak, equity)
        drawdown = max(0.0, peak - equity)
        realized = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(net_pnl_after_costs, net_pnl_estimated, 0)), 0) AS value
            FROM trade_outcomes WHERE exit_time >= ? AND exit_time < ?
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()["value"]
        unrealized = sum(_number(_field(position, "unrealized_pl")) for position in positions)
        record = {
            "timestamp": now,
            "stage": stage,
            "equity": equity,
            "cash": _number(_field(account, "cash")),
            "buying_power": _number(_field(account, "buying_power")),
            "daytrade_count": int(_number(_field(account, "daytrade_count"))),
            "portfolio_value": _number(_field(account, "portfolio_value"), equity),
            "multiplier": _number(_field(account, "multiplier"), 1.0),
            "long_market_value": _number(_field(account, "long_market_value")),
            "short_market_value": _number(_field(account, "short_market_value")),
            "realized_pl": _number(realized),
            "unrealized_pl": unrealized,
            "session_start_equity": session_start,
            "session_peak_equity": peak,
            "drawdown": drawdown,
            "drawdown_pct": safe_div(drawdown, peak),
            "open_position_count": len(positions),
            "open_order_count": len(open_orders),
            "details": {"positions": [_jsonable(item) for item in positions]},
        }
        self.database.insert_account_snapshot(record)
        self._last_capture_at = now
        return record


def run_performance_consistency_audit(
    database: Database,
    settings: Settings,
    trading_client: Any,
    *,
    stage: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = ensure_utc(now or utc_now())
    unmatched = database.conn.execute(
        """
        SELECT COUNT(*) AS count FROM fills f
        WHERE NOT EXISTS (
            SELECT 1 FROM orders o
            WHERE o.alpaca_order_id = f.order_id OR o.client_order_id = f.order_id
               OR (f.client_order_id IS NOT NULL AND o.client_order_id = f.client_order_id)
        )
        """
    ).fetchone()["count"]
    orphan = database.conn.execute(
        """
        SELECT COUNT(*) AS count FROM orders child
        WHERE child.parent_order_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM orders parent WHERE parent.alpaca_order_id = child.parent_order_id)
        """
    ).fetchone()["count"]
    episodes = database.fetch_active_execution_episodes(settings.bot_symbol)
    database_qty = sum(_number(row.get("remaining_qty")) for row in episodes)
    positions = list(trading_client.get_all_positions() or [])
    position = next((item for item in positions if str(_field(item, "symbol")).upper() == settings.bot_symbol.upper()), None)
    broker_qty = abs(_number(_field(position, "qty")))
    open_orders = _open_orders(trading_client, settings.bot_symbol)
    reasons: list[str] = []
    if unmatched:
        reasons.append(f"{unmatched} unmatched fills")
    if orphan:
        reasons.append(f"{orphan} orphan child orders")
    if episodes:
        reasons.append(f"{len(episodes)} open database episodes")
    if abs(database_qty - broker_qty) > 0.000001:
        reasons.append("database and broker position quantities differ")
    if broker_qty:
        reasons.append("broker position remains open")
    if open_orders:
        reasons.append("broker orders remain open")
    result = {
        "timestamp": now,
        "stage": stage,
        "consistent": not reasons,
        "unmatched_fill_count": int(unmatched),
        "orphan_order_count": int(orphan),
        "open_episode_count": len(episodes),
        "broker_position_qty": broker_qty,
        "broker_open_order_count": len(open_orders),
        "database_position_qty": database_qty,
        "reasons": reasons,
        "details": {"active_episode_ids": [row.get("episode_id") for row in episodes]},
    }
    database.insert_performance_consistency_audit(result)
    return result


def _open_orders(trading_client: Any, symbol: str) -> list[Any]:
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])) or [])
    except TypeError:
        return list(trading_client.get_orders() or [])


def _new_york_session_day(now: datetime) -> tuple[datetime, datetime]:
    eastern = ensure_utc(now).astimezone(ZoneInfo("America/New_York"))
    start_local = datetime.combine(eastern.date(), time.min, tzinfo=eastern.tzinfo)
    return start_local.astimezone(ZoneInfo("UTC")), (start_local + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _root_episode_id(client_order_id: str) -> str:
    upper = client_order_id.upper()
    for suffix in ("-TAKE", "-RUN"):
        if upper.endswith(suffix):
            return client_order_id[: -len(suffix)]
    return client_order_id


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return default


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"repr": repr(value)}
