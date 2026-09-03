from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .telemetry import TelemetryRepository


class PerformanceAnalytics:
    """Build chart-ready summaries from closed root trading episodes."""

    def __init__(self, database_path: Path) -> None:
        self.telemetry = TelemetryRepository(database_path)

    def build(self, limit: int = 5000) -> dict[str, Any]:
        trades = self.telemetry.trades(limit)
        ordered = sorted(trades, key=lambda row: str(row.get("exit_time") or row.get("entry_time") or ""))
        net_values = [self._number(row.get("net_pnl_after_costs")) for row in ordered]
        gross_values = [self._number(row.get("gross_pnl")) for row in ordered]
        wins = sum(value > 0 for value in net_values)
        cumulative = 0.0
        pnl_series: list[dict[str, Any]] = []
        for row, net in zip(ordered, net_values):
            cumulative += net
            pnl_series.append({"timestamp": row.get("exit_time"), "net": net, "cumulative": cumulative})
        return {
            "summary": {
                "trades": len(ordered),
                "wins": wins,
                "losses": len(ordered) - wins,
                "win_rate": wins / len(ordered) if ordered else 0.0,
                "gross_pnl": sum(gross_values),
                "net_pnl": sum(net_values),
                "estimated_costs": sum(gross_values) - sum(net_values),
                "average_net": sum(net_values) / len(ordered) if ordered else 0.0,
                "average_hold_seconds": sum(self._number(row.get("holding_seconds")) for row in ordered) / len(ordered) if ordered else 0.0,
                "profit_factor": self._profit_factor(net_values),
            },
            "pnl_series": pnl_series,
            "breakdowns": {
                "direction": self._group(ordered, "direction"),
                "playbook": self._group(ordered, "playbook"),
                "regime": self._group(ordered, "regime"),
                "exit_reason": self._group(ordered, "exit_reason"),
                "strategy_path": self._group(ordered, "strategy_path"),
                "holding_time": self._holding_buckets(ordered),
            },
        }

    def _group(self, rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            label = str(row.get(field) or "unknown")
            if field == "playbook" and label.startswith("paper_bracket:"):
                label = "legacy_unclassified"
            groups[label].append(self._number(row.get("net_pnl_after_costs")))
        result = []
        for label, values in groups.items():
            result.append({
                "label": label, "trades": len(values), "wins": sum(value > 0 for value in values),
                "net_pnl": sum(values), "average_net": sum(values) / len(values),
                "win_rate": sum(value > 0 for value in values) / len(values),
            })
        return sorted(result, key=lambda item: (item["trades"], abs(item["net_pnl"])), reverse=True)

    def _holding_buckets(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets = (("<30s", 0, 30), ("30-60s", 30, 60), ("1-3m", 60, 180), ("3-5m", 180, 300), ("5-15m", 300, 900), (">15m", 900, float("inf")))
        result = []
        for label, lower, upper in buckets:
            selected = [row for row in rows if lower <= self._number(row.get("holding_seconds")) < upper]
            values = [self._number(row.get("net_pnl_after_costs")) for row in selected]
            result.append({"label": label, "trades": len(values), "net_pnl": sum(values), "wins": sum(value > 0 for value in values)})
        return result

    @staticmethod
    def _profit_factor(values: list[float]) -> float:
        profit = sum(value for value in values if value > 0)
        loss = abs(sum(value for value in values if value < 0))
        return profit / loss if loss else (999.0 if profit else 0.0)

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
