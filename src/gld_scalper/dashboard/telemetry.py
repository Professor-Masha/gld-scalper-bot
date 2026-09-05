from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TelemetryRepository:
    """Read dashboard telemetry without participating in trading transactions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def snapshot(self) -> dict[str, Any]:
        if not self.database_path.exists():
            return {"database_available": False, "database": str(self.database_path)}
        try:
            connection = self._connect()
        except sqlite3.OperationalError as exc:
            return {"database_available": False, "database": str(self.database_path), "database_error": str(exc)}
        try:
            performance = self._one(connection, """SELECT COUNT(*) AS trades,
                COALESCE(SUM(net_pnl_after_costs),0) AS net_pnl, COALESCE(SUM(gross_pnl),0) AS gross_pnl,
                COALESCE(SUM(CASE WHEN net_pnl_after_costs>0 THEN 1 ELSE 0 END),0) AS wins,
                COALESCE(AVG(holding_seconds),0) AS avg_holding_seconds
                FROM trade_outcomes WHERE COALESCE(exit_time,entry_time)>=datetime('now','start of day')""") or {}
            trades = int(performance.get("trades") or 0)
            performance["win_rate"] = float(performance.get("wins") or 0) / trades if trades else 0.0
            return {
                "database_available": True, "database": str(self.database_path),
                "server_time": datetime.now(timezone.utc).isoformat(),
                "account": self._one(connection, "SELECT * FROM account_snapshots ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "quote": self._one(connection, "SELECT * FROM quotes WHERE symbol='GLD' ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "signal": self._one(connection, "SELECT * FROM signals WHERE symbol='GLD' ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "transformer": self._one(connection, "SELECT * FROM transformer_predictions ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "macro": self._one(connection, "SELECT * FROM macro_context ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "safety": self._one(connection, "SELECT * FROM execution_safety_events ORDER BY timestamp DESC,id DESC LIMIT 1"),
                "performance": performance,
                "active_episodes": self._all(connection, """SELECT episode_id,direction,strategy_path,playbook,status,
                    filled_qty,remaining_qty,entry_avg_price,realized_pnl,opened_at,updated_at
                    FROM execution_episodes WHERE closed_at IS NULL ORDER BY opened_at DESC LIMIT 20"""),
                "counts": {name: self._count(connection, table) for name, table in {
                    "signals":"signals", "orders":"orders", "fills":"fills", "outcomes":"trade_outcomes", "models":"model_versions"
                }.items()},
            }
        finally:
            connection.close()

    def available(self) -> bool:
        if not self.database_path.exists():
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            if connection is not None:
                connection.close()

    def trades(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("""SELECT trade_id,direction,strategy_path,playbook,entry_time,exit_time,entry_price,
            exit_price,qty,gross_pnl,net_pnl_after_costs,pnl_pct,holding_seconds,exit_reason,win_loss,
            confidence,exploration_trade,regime,spread_cost,slippage_cost,estimated_fees,
            estimated_live_cost,profit_given_back,max_favorable_excursion,max_adverse_excursion
            FROM trade_outcomes
            ORDER BY COALESCE(exit_time,entry_time) DESC LIMIT ?""", (max(1,min(limit,5000)),))

    def decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("""SELECT timestamp,decision,confidence,bullish_score,bearish_score,no_trade_score,
            regime,reason,model_version FROM signals ORDER BY timestamp DESC,id DESC LIMIT ?""", (max(1,min(limit,500)),))

    def models(self) -> list[dict[str, Any]]:
        return self._query("""SELECT model_version,model_type,model_scope,status,created_at,training_start,
            training_end,feature_profile,metrics_json,rejection_reason FROM model_versions ORDER BY created_at DESC LIMIT 100""")

    def model_validation(self) -> dict[str, Any]:
        """Expose comparable calibration, selectivity and return evidence."""

        rows = self.models()
        models: list[dict[str, Any]] = []
        for row in rows:
            metrics = row.get("metrics_json") if isinstance(row.get("metrics_json"), dict) else {}
            holdout = metrics.get("holdout") if isinstance(metrics.get("holdout"), dict) else metrics
            walk_forward = metrics.get("walk_forward") if isinstance(metrics.get("walk_forward"), dict) else {}
            aggregate = walk_forward.get("aggregate") if isinstance(walk_forward.get("aggregate"), dict) else {}
            models.append({
                "model_version": row.get("model_version"),
                "model_type": row.get("model_type"),
                "model_scope": row.get("model_scope"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "feature_profile": row.get("feature_profile"),
                "holdout_ece": _metric(holdout, "expected_calibration_error"),
                "holdout_selective_accuracy": _metric(holdout, "selective_accuracy"),
                "holdout_abstention_rate": _metric(holdout, "abstention_rate"),
                "holdout_trade_count": _metric(holdout, "trade_count"),
                "holdout_profit_factor": _metric(holdout, "profit_factor"),
                "holdout_net_return": _metric(holdout, "net_return"),
                "holdout_return_ci_low": _metric(holdout, "average_return_ci_low"),
                "holdout_return_ci_high": _metric(holdout, "average_return_ci_high"),
                "walk_forward_profit_factor": _metric(aggregate, "profit_factor"),
                "walk_forward_net_return": _metric(aggregate, "net_return"),
                "walk_forward_completed": _metric(metrics, "walk_forward_completed"),
                "rejection_reason": row.get("rejection_reason"),
            })
        champions = [item for item in models if item.get("status") == "champion"]
        return {
            "models": models,
            "summary": {
                "registered": len(models),
                "champions": len(champions),
                "scopes": len({str(item.get("model_scope")) for item in models}),
                "calibrated_models": sum(item.get("holdout_ece") is not None for item in models),
            },
        }

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("""SELECT id,alpaca_order_id AS order_id,client_order_id,parent_order_id,
            symbol,side,qty,order_type,status,limit_price AS submitted_price,filled_avg_price,
            filled_qty,submitted_at,filled_at,cancel_reason,strategy_path,playbook
            FROM orders ORDER BY COALESCE(filled_at,submitted_at) DESC,id DESC LIMIT ?""",
            (max(1, min(limit, 1000)),))

    def equity_curve(self, limit: int = 500) -> list[dict[str, Any]]:
        return list(reversed(self._query("""SELECT timestamp,equity,realized_pl,unrealized_pl,drawdown_pct
            FROM account_snapshots ORDER BY timestamp DESC,id DESC LIMIT ?""", (max(10,min(limit,2000)),))))

    def market_series(self, limit: int = 390) -> list[dict[str, Any]]:
        return list(reversed(self._query("""SELECT timestamp,open,high,low,close,volume,vwap
            FROM bars WHERE symbol='GLD' AND timeframe='1Min'
            ORDER BY timestamp DESC,id DESC LIMIT ?""", (max(30,min(limit,2000)),))))

    def diagnostics(self, limit: int = 100) -> dict[str, Any]:
        return {
            "stream": self._query("SELECT * FROM stream_diagnostics ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,)),
            "safety": self._query("SELECT * FROM execution_safety_events ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,)),
            "audits": self._query("SELECT * FROM performance_consistency_audits ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,)),
        }

    def latency_summary(self, limit: int = 5000) -> dict[str, Any]:
        rows = self._query(
            "SELECT timestamp,trace_id,stage,elapsed_ms,stage_latency_ms,event_age_ms,strategy_path,playbook,client_order_id,order_id,status "
            "FROM execution_latency_events ORDER BY timestamp DESC,id DESC LIMIT ?",
            (max(10, min(limit, 20_000)),),
        )
        stages: dict[str, list[float]] = {}
        for row in rows:
            value = row.get("stage_latency_ms")
            if value is not None:
                stages.setdefault(str(row.get("stage")), []).append(float(value))
        return {
            "sample_count": len(rows),
            "trace_count": len({str(row.get("trace_id")) for row in rows}),
            "stages": {stage: _percentiles(values) for stage, values in stages.items()},
            "recent": rows[:100],
            "model_target_ms": 5.0,
        }

    def llm_activity(self, limit: int = 30) -> dict[str, list[dict[str, Any]]]:
        bounded = max(1, min(limit, 100))
        return {
            "reviews": self._query("SELECT * FROM llm_reviews ORDER BY id DESC LIMIT ?", (bounded,)),
            "advice": self._query("SELECT * FROM llm_training_advice ORDER BY id DESC LIMIT ?", (bounded,)),
            "labels": self._query("SELECT * FROM llm_signal_labels ORDER BY id DESC LIMIT ?", (bounded,)),
        }

    def _query(self, sql: str, parameters: tuple[Any,...] = ()) -> list[dict[str,Any]]:
        if not self.database_path.exists(): return []
        connection = self._connect()
        try: return self._all(connection, sql, parameters)
        except sqlite3.OperationalError: return []
        finally: connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True, timeout=3, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        return connection

    @staticmethod
    def _one(connection: sqlite3.Connection, sql: str) -> dict[str,Any] | None:
        try:
            row=connection.execute(sql).fetchone(); return _row(row) if row else None
        except sqlite3.OperationalError: return None
    @staticmethod
    def _all(connection: sqlite3.Connection, sql: str, parameters: tuple[Any,...]=()) -> list[dict[str,Any]]:
        try:
            return [_row(row) for row in connection.execute(sql,parameters).fetchall()]
        except sqlite3.OperationalError:
            return []
    @staticmethod
    def _count(connection: sqlite3.Connection, table: str) -> int:
        try: return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError: return 0


def _row(row: sqlite3.Row) -> dict[str,Any]:
    result=dict(row)
    for key,value in list(result.items()):
        if key.endswith("_json") and isinstance(value,str):
            try: result[key]=json.loads(value)
            except json.JSONDecodeError: pass
    return result


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    def percentile(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))]
    return {"count": len(ordered), "p50_ms": round(percentile(0.50), 4), "p95_ms": round(percentile(0.95), 4),
            "p99_ms": round(percentile(0.99), 4), "max_ms": round(ordered[-1], 4)}


def _metric(values: dict[str, Any], key: str) -> float | None:
    try:
        value = values.get(key)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
