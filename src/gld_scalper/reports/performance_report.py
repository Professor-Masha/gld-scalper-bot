from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..backtester import calculate_metrics
from ..database import Database
from ..models import BacktestTrade
from ..utils.math_utils import safe_div
from ..utils.time_utils import ensure_utc


def root_episode_rows(
    database: Database,
    *,
    mode: str = "paper",
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["mode = ?"]
    params: list[Any] = [mode]
    if start is not None:
        clauses.append("entry_time >= ?")
        params.append(ensure_utc(start).isoformat())
    if end is not None:
        clauses.append("entry_time < ?")
        params.append(ensure_utc(end).isoformat())
    rows = database.conn.execute(
        f"SELECT * FROM trade_outcomes WHERE {' AND '.join(clauses)} ORDER BY entry_time, id",
        params,
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        grouped[str(item.get("root_episode_id") or _root_id(str(item.get("trade_id") or item["id"])))].append(item)

    episodes: list[dict[str, Any]] = []
    for root_id, tranches in grouped.items():
        qty = sum(_number(row.get("qty")) for row in tranches)
        entry_notional = sum(_number(row.get("entry_price")) * _number(row.get("qty")) for row in tranches)
        exit_notional = sum(_number(row.get("exit_price")) * _number(row.get("qty")) for row in tranches)
        first = tranches[0]
        net = sum(_number(row.get("net_pnl_after_costs"), _number(row.get("net_pnl_estimated"))) for row in tranches)
        gross = sum(_number(row.get("gross_pnl")) for row in tranches)
        episodes.append(
            {
                "root_episode_id": root_id,
                "tranche_count": len(tranches),
                "symbol": first.get("symbol"),
                "direction": first.get("direction"),
                "entry_time": min(row.get("entry_time") for row in tranches if row.get("entry_time")),
                "exit_time": max(row.get("exit_time") for row in tranches if row.get("exit_time")),
                "entry_price": safe_div(entry_notional, qty),
                "exit_price": safe_div(exit_notional, qty),
                "qty": qty,
                "notional": entry_notional,
                "gross_pnl": gross,
                "net_pnl_after_costs": net,
                "net_pnl_estimated": net,
                "spread_cost": sum(_number(row.get("spread_cost")) for row in tranches),
                "slippage_cost": sum(_number(row.get("slippage_cost")) for row in tranches),
                "estimated_fees": sum(_number(row.get("estimated_fees")) for row in tranches),
                "estimated_live_cost": sum(_number(row.get("estimated_live_cost")) for row in tranches),
                "opportunity_cost": sum(_number(row.get("opportunity_cost")) for row in tranches),
                "profit_given_back": sum(_number(row.get("profit_given_back")) for row in tranches),
                "max_favorable_excursion": max(_number(row.get("max_favorable_excursion")) for row in tranches),
                "max_adverse_excursion": max(_number(row.get("max_adverse_excursion")) for row in tranches),
                "holding_seconds": max(_number(row.get("holding_seconds")) for row in tranches),
                "exit_reason": _combined_value(tranches, "exit_reason"),
                "strategy_path": first.get("strategy_path") or ("fast" if "FAST" in root_id.upper() else "minute"),
                "playbook": first.get("playbook") or first.get("setup_type") or "unknown",
                "regime": first.get("regime") or "unknown",
                "ml_prediction": first.get("ml_prediction") or "none",
                "confidence": max(_number(row.get("confidence")) for row in tranches),
                "exploration_trade": any(bool(row.get("exploration_trade")) for row in tranches),
                "win_loss": "win" if net > 0 else "loss",
            }
        )
    return episodes


def performance_breakdown_from_database(
    database: Database,
    *,
    mode: str = "paper",
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> dict[str, Any]:
    episodes = root_episode_rows(database, mode=mode, start=start, end=end)
    tranche_count = sum(int(row["tranche_count"]) for row in episodes)
    summary = _group_metrics(episodes)
    summary.update({"root_episode_count": len(episodes), "partial_exit_tranche_count": tranche_count})
    normal_episodes = [row for row in episodes if not row["exploration_trade"]]
    exploration_episodes = [row for row in episodes if row["exploration_trade"]]
    return {
        "summary": summary,
        "summary_scope": "combined_normal_and_exploration",
        "normal_strategy_summary": _lane_summary(normal_episodes),
        "exploration_summary": _lane_summary(exploration_episodes),
        "by_evidence_lane": _breakdown(
            episodes,
            lambda row: "paper_exploration" if row["exploration_trade"] else "normal_strategy",
        ),
        "by_direction": _breakdown(episodes, lambda row: str(row["direction"])),
        "by_strategy_path": _breakdown(episodes, lambda row: str(row["strategy_path"])),
        "by_playbook": _breakdown(episodes, lambda row: str(row["playbook"])),
        "by_regime": _breakdown(episodes, lambda row: str(row["regime"])),
        "by_new_york_hour": _breakdown(episodes, _new_york_hour),
        "by_exit_reason": _breakdown(episodes, lambda row: str(row["exit_reason"])),
        "by_ml_prediction": _breakdown(episodes, lambda row: str(row["ml_prediction"])),
        "by_confidence_band": _breakdown(episodes, _confidence_band),
    }


def _lane_summary(rows: list[dict[str, Any]]) -> dict[str, float]:
    summary = _group_metrics(rows)
    summary.update(
        {
            "root_episode_count": len(rows),
            "partial_exit_tranche_count": sum(int(row["tranche_count"]) for row in rows),
        }
    )
    return summary


def trade_metrics_from_database(database: Database, mode: str = "paper") -> dict[str, float]:
    episodes = root_episode_rows(database, mode=mode)
    trades = [
        BacktestTrade(
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),
            decision_time=ensure_utc(row["entry_time"]),
            entry_time=ensure_utc(row["entry_time"]),
            exit_time=ensure_utc(row["exit_time"]),
            entry_price=_number(row["entry_price"]),
            exit_price=_number(row["exit_price"]),
            qty=max(1, int(_number(row["qty"]))),
            gross_pnl=_number(row["gross_pnl"]),
            net_pnl_estimated=_number(row["net_pnl_after_costs"]),
            exit_reason=str(row["exit_reason"]),
            regime=str(row["regime"]),
        )
        for row in episodes
    ]
    return calculate_metrics(trades, database.settings.paper_account_size)


def _breakdown(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key_fn(row)].append(row)
    return {key: _group_metrics(values) for key, values in sorted(grouped.items())}


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    pnls = [_number(row["net_pnl_after_costs"]) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    return {
        "episodes": len(rows),
        "net_pnl_after_costs": sum(pnls),
        "gross_pnl": sum(_number(row["gross_pnl"]) for row in rows),
        "estimated_live_cost": sum(_number(row["estimated_live_cost"]) for row in rows),
        "win_rate": safe_div(len(wins), len(rows)),
        "profit_factor": safe_div(sum(wins), abs(sum(losses)), default=999.0 if wins and not losses else 0.0),
        "opportunity_cost": sum(_number(row["opportunity_cost"]) for row in rows),
        "profit_given_back": sum(_number(row["profit_given_back"]) for row in rows),
        "max_favorable_excursion": max((_number(row["max_favorable_excursion"]) for row in rows), default=0.0),
        "max_adverse_excursion": max((_number(row["max_adverse_excursion"]) for row in rows), default=0.0),
    }


def _new_york_hour(row: dict[str, Any]) -> str:
    value = ensure_utc(row["entry_time"]).astimezone(ZoneInfo("America/New_York"))
    return f"{value.hour:02d}:00"


def _confidence_band(row: dict[str, Any]) -> str:
    confidence = _number(row.get("confidence"))
    if confidence < 0.60:
        return "<0.60"
    if confidence < 0.70:
        return "0.60-0.69"
    if confidence < 0.80:
        return "0.70-0.79"
    if confidence < 0.90:
        return "0.80-0.89"
    return ">=0.90"


def _combined_value(rows: list[dict[str, Any]], column: str) -> str:
    values = list(dict.fromkeys(str(row.get(column) or "unknown") for row in rows))
    return "+".join(values)


def _root_id(trade_id: str) -> str:
    upper = trade_id.upper()
    for suffix in ("-TAKE", "-RUN"):
        if upper.endswith(suffix):
            return trade_id[: -len(suffix)]
    return trade_id


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default
