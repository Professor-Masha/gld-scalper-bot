from __future__ import annotations

from typing import Any

from .config import Settings
from .database import Database
from .models import RiskState


def populate_concurrent_risk_state(
    state: RiskState,
    *,
    symbol: str,
    positions: list[Any],
    open_orders: list[Any],
    database: Database,
    settings: Settings,
) -> None:
    symbol = symbol.upper()
    position = next(
        (
            item
            for item in positions
            if str(_field(item, "symbol", "")).upper() == symbol
            and abs(_float(_field(item, "qty"))) > 0
        ),
        None,
    )
    position_side = broker_position_direction(position)
    state.open_position = position is not None
    state.open_position_direction = position_side

    summary = database.active_trade_episode_summary(symbol)
    state.active_trade_count = int(summary["count"])
    state.active_trade_direction = str(summary["direction"])
    state.active_trade_notional = float(summary["notional"])
    if position is not None and state.active_trade_count == 0:
        state.active_trade_count = 1
        state.active_trade_direction = position_side
        state.active_trade_notional = abs(_float(_field(position, "market_value")))
    elif position_side and state.active_trade_direction and state.active_trade_direction != position_side:
        state.active_trade_direction = "MIXED"
    elif position_side and not state.active_trade_direction:
        state.active_trade_direction = position_side

    symbol_orders = [item for item in open_orders if str(_field(item, "symbol", symbol)).upper() == symbol]
    if settings.paper_learning_mode:
        state.unexpected_open_orders = any(not is_bot_managed_order(item, symbol, database) for item in symbol_orders)
    else:
        state.unexpected_open_orders = bool(symbol_orders)


def broker_position_direction(position: Any | None) -> str:
    if position is None:
        return ""
    side = _enum_text(_field(position, "side")).lower()
    qty = _float(_field(position, "qty"))
    if side == "long" or qty > 0:
        return "LONG"
    if side == "short" or qty < 0:
        return "SHORT"
    return ""


def is_bot_managed_order(order: Any, symbol: str, database: Database | None = None) -> bool:
    if _field(order, "parent_order_id"):
        return True
    client_order_id = str(_field(order, "client_order_id", ""))
    if client_order_id.upper().startswith(f"{symbol.upper()}-"):
        return True
    if database is None:
        return False
    order_id = str(_field(order, "id", "") or "")
    persisted = database.get_order(alpaca_order_id=order_id or None, client_order_id=client_order_id or None)
    return bool(persisted and persisted.get("parent_order_id"))


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
