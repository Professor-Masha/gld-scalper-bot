from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from typing import Any

from ..database import Database
from ..utils.time_utils import ensure_utc, utc_now


NON_FEATURE_COLUMNS = {
    "timestamp",
    "quote_timestamp",
    "received_at",
    "created_at",
    "symbol",
    "decision",
    "label",
    "source",
    "raw_json",
}
TRADE_GOOD_LABELS = {"long_good", "short_good"}
VALID_LABELS = {"long_good", "short_good", "no_trade"}


def build_training_dataset(
    database: Database,
    lookback_days: int = 90,
    *,
    use_llm_labels: bool | None = None,
) -> tuple[list[dict[str, float]], list[str], list[str]]:
    records = build_training_records(database, lookback_days, use_llm_labels=use_llm_labels)
    samples = [record["features"] for record in records]
    labels = [record["label"] for record in records]
    columns = sorted({key for sample in samples for key in sample})
    aligned = [{column: float(sample.get(column, 0.0)) for column in columns} for sample in samples]
    return aligned, labels, columns


def build_training_records(
    database: Database,
    lookback_days: int = 90,
    *,
    use_llm_labels: bool | None = None,
) -> list[dict[str, Any]]:
    if use_llm_labels is None:
        use_llm_labels = bool(getattr(database.settings, "enable_llm_training_labels", False))
    min_llm_confidence = float(getattr(database.settings, "llm_training_label_min_confidence", 0.70))
    since = utc_now() - timedelta(days=lookback_days)
    rows = database.conn.execute(
        """
        SELECT s.id AS signal_id, s.timestamp, s.symbol, s.decision, s.feature_snapshot_json,
               de.executed_action, de.execution_status, de.strategy_path, de.playbook AS executed_playbook,
               de.model_scope, de.spread_pct AS execution_spread_pct,
               de.expected_slippage_pct, de.fill_quality_score, de.direction_available,
               de.session_phase, de.execution_error,
               o.direction,
               COALESCE(o.net_pnl_after_costs, o.net_pnl_estimated) AS net_pnl_estimated,
               o.notional AS trade_notional,
               o.estimated_live_cost,
               (SELECT m.label FROM missed_opportunities m
                WHERE m.signal_id = s.id ORDER BY m.id DESC LIMIT 1) AS missed_label,
               (SELECT ol.label FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS outcome_label,
               (SELECT ol.forward_return_1m FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS forward_return_1m,
               (SELECT ol.forward_return_3m FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS forward_return_3m,
               (SELECT ol.forward_return_5m FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS forward_return_5m,
               (SELECT ol.forward_return_15m FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS forward_return_15m,
               (SELECT ol.realized_spread_cost FROM outcome_labels ol
                WHERE ol.signal_id = s.id ORDER BY ol.id DESC LIMIT 1) AS realized_spread_cost,
               (SELECT l.suggested_label FROM llm_signal_labels l
                WHERE l.signal_id = s.id ORDER BY l.id DESC LIMIT 1) AS llm_label,
               (SELECT l.confidence FROM llm_signal_labels l
                WHERE l.signal_id = s.id ORDER BY l.id DESC LIMIT 1) AS llm_confidence
        FROM signals s
        JOIN decision_executions de
          ON de.decision_source = 'signal' AND de.decision_id = s.id
        LEFT JOIN trade_outcomes o
          ON o.id = (
              SELECT candidate.id FROM trade_outcomes candidate
              WHERE candidate.root_episode_id = de.root_episode_id
                 OR candidate.trade_id = de.client_order_id
              ORDER BY candidate.id ASC LIMIT 1
          )
        WHERE julianday(s.timestamp) >= julianday(?)
        ORDER BY julianday(s.timestamp), s.id
        """,
        (since.isoformat(),),
    ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        raw_features = json.loads(row["feature_snapshot_json"] or "{}")
        raw_features.update(
            {
                "executed_action": row["executed_action"],
                "execution_status": row["execution_status"],
                "execution_spread_pct": row["execution_spread_pct"],
                "expected_slippage_pct": row["expected_slippage_pct"],
                "fill_quality_score": row["fill_quality_score"],
                "direction_available": bool(row["direction_available"]),
                "session_phase": row["session_phase"],
                "execution_error": row["execution_error"] or "none",
                "model_scope": row["model_scope"] or "entry:all",
            }
        )
        features = _flatten_features(raw_features)
        if not features:
            continue
        label = _label(
            row["executed_action"],
            row["direction"],
            row["net_pnl_estimated"],
            row["missed_label"],
            row["outcome_label"],
            row["llm_label"] if use_llm_labels and _f(row["llm_confidence"]) >= min_llm_confidence else None,
        )
        if label is None:
            continue
        realized_cost_pct = _f(row["estimated_live_cost"]) / max(abs(_f(row["trade_notional"])), 1.0)
        cost = max(
            _f(row["realized_spread_cost"]),
            realized_cost_pct,
            _f(row["execution_spread_pct"]),
            _f(row["expected_slippage_pct"]),
            _f(features.get("spread_pct")),
            0.0001,
        )
        forward_return = row["forward_return_15m"]
        if forward_return is not None:
            raw_return = _f(forward_return)
            net_long = raw_return - cost
            net_short = -raw_return - cost
        elif row["net_pnl_estimated"] is not None:
            pnl_return = _f(row["net_pnl_estimated"]) / max(abs(_f(row["trade_notional"], 1.0)), 1.0)
            net_long = pnl_return if row["direction"] == "LONG" else -pnl_return - cost
            net_short = pnl_return if row["direction"] == "SHORT" else -pnl_return - cost
        else:
            net_long = cost if label == "long_good" else -cost
            net_short = cost if label == "short_good" else -cost
        outcomes_by_horizon: dict[int, dict[str, float]] = {}
        for minutes in (1, 3, 5, 15):
            value = row[f"forward_return_{minutes}m"]
            if value is not None:
                outcomes_by_horizon[minutes] = _directional_outcome(_f(value), cost)
        records.append(
            {
                "timestamp": ensure_utc(row["timestamp"]),
                "signal_id": row["signal_id"],
                "features": features,
                "label": label,
                "outcome": {
                    "label": label,
                    "net_return_long": net_long,
                    "net_return_short": net_short,
                    "spread_cost": cost,
                },
                "outcomes_by_horizon": outcomes_by_horizon,
                "horizon_minutes": 15,
                "playbook": str(row["executed_playbook"] or raw_features.get("playbook") or "all"),
                "strategy_path": str(row["strategy_path"] or "minute"),
                "regime": str(raw_features.get("regime") or "unknown"),
                "executed_action": str(row["executed_action"]),
                "model_scope": str(row["model_scope"] or "entry:all"),
                "source": "paper",
            }
        )
    fast_rows = database.conn.execute(
        """
        SELECT f.id AS decision_id, f.timestamp, f.symbol, f.decision, f.feature_snapshot_json,
               f.spread_pct,
               de.executed_action, de.execution_status, de.playbook AS executed_playbook,
               de.model_scope, de.expected_slippage_pct, de.fill_quality_score,
               de.direction_available, de.session_phase, de.execution_error,
               ol.label AS outcome_label,
               ol.forward_return_1m,
               ol.forward_return_3m,
               ol.forward_return_5m,
               ol.forward_return_15m,
               ol.realized_spread_cost
        FROM fast_scalp_decisions f
        JOIN decision_executions de
          ON de.decision_source = 'fast_scalp' AND de.decision_id = f.id
        JOIN outcome_labels ol
          ON ol.id = (
              SELECT MAX(candidate.id)
              FROM outcome_labels candidate
              WHERE candidate.decision_source = 'fast_scalp'
                AND candidate.decision_id = f.id
          )
        WHERE julianday(f.timestamp) >= julianday(?)
        ORDER BY julianday(f.timestamp), f.id
        """,
        (since.isoformat(),),
    ).fetchall()
    for row in fast_rows:
        label = row["outcome_label"]
        if label not in VALID_LABELS:
            continue
        raw_features = json.loads(row["feature_snapshot_json"] or "{}")
        raw_features.update(
            {
                "executed_action": row["executed_action"],
                "execution_status": row["execution_status"],
                "expected_slippage_pct": row["expected_slippage_pct"],
                "fill_quality_score": row["fill_quality_score"],
                "direction_available": bool(row["direction_available"]),
                "session_phase": row["session_phase"],
                "execution_error": row["execution_error"] or "none",
                "model_scope": row["model_scope"] or "entry:fast_microstructure",
            }
        )
        features = _flatten_features(raw_features)
        if not features:
            continue
        cost = max(
            _f(row["realized_spread_cost"]),
            _f(row["spread_pct"]),
            _f(row["expected_slippage_pct"]),
            _f(features.get("spread_pct")),
            0.0001,
        )
        outcomes_by_horizon: dict[int, dict[str, float]] = {}
        for minutes in (1, 3, 5, 15):
            value = row[f"forward_return_{minutes}m"]
            if value is not None:
                outcomes_by_horizon[minutes] = _directional_outcome(_f(value), cost)
        if not outcomes_by_horizon:
            continue
        canonical_horizon = max(outcomes_by_horizon)
        canonical = outcomes_by_horizon[canonical_horizon]
        records.append(
            {
                "timestamp": ensure_utc(row["timestamp"]),
                "decision_id": row["decision_id"],
                "features": features,
                "label": label,
                "outcome": {
                    "label": label,
                    "net_return_long": canonical["net_return_long"],
                    "net_return_short": canonical["net_return_short"],
                    "spread_cost": cost,
                },
                "outcomes_by_horizon": outcomes_by_horizon,
                "horizon_minutes": canonical_horizon,
                "playbook": str(row["executed_playbook"] or raw_features.get("playbook") or "all"),
                "strategy_path": "fast",
                "regime": str(raw_features.get("regime") or raw_features.get("gold_volatility_regime") or "unknown"),
                "executed_action": str(row["executed_action"]),
                "model_scope": str(row["model_scope"] or "entry:fast_microstructure"),
                "source": "paper_fast",
            }
        )
    records.sort(key=lambda item: (item["timestamp"], int(item.get("signal_id") or item.get("decision_id") or 0)))
    return records


def align_records(records: list[dict[str, Any]]) -> tuple[list[list[float]], list[str]]:
    columns = sorted({key for record in records for key in record["features"]})
    return [[float(record["features"].get(column, 0.0)) for column in columns] for record in records], columns


def label_quality(labels: list[str]) -> tuple[bool, str]:
    if len(set(labels)) < 2:
        return False, "training labels need at least two classes"
    if not any(label in TRADE_GOOD_LABELS for label in labels):
        return False, "training labels need at least one profitable long/short example"
    return True, "label quality ok"


def _label(
    decision: str | None,
    direction: str | None,
    pnl: Any,
    missed_label: str | None = None,
    outcome_label: str | None = None,
    llm_label: str | None = None,
) -> str | None:
    if outcome_label in VALID_LABELS:
        return outcome_label
    if missed_label == "MISSED_LONG":
        return "long_good"
    if missed_label == "MISSED_SHORT":
        return "short_good"
    if missed_label == "VALID_NO_TRADE":
        return "no_trade"
    if direction and pnl is not None:
        pnl_value = float(pnl)
        if direction == "LONG":
            return "long_good" if pnl_value > 0 else "no_trade"
        if direction == "SHORT":
            return "short_good" if pnl_value > 0 else "no_trade"
    if llm_label in VALID_LABELS:
        return llm_label
    return None


def _flatten_features(raw: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in raw.items():
        if key in NON_FEATURE_COLUMNS or key.endswith("_json"):
            continue
        if isinstance(value, bool):
            flat[key] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            numeric = float(value)
            flat[key] = numeric if math.isfinite(numeric) else 0.0
            if not math.isfinite(numeric):
                flat[f"{key}__missing"] = 1.0
        elif value is None:
            flat[key] = 0.0
            flat[f"{key}__missing"] = 1.0
        elif isinstance(value, str):
            try:
                numeric = float(value)
                flat[key] = numeric if math.isfinite(numeric) else 0.0
            except ValueError:
                token = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")[:48]
                if token:
                    flat[f"{key}__{token}"] = 1.0
    return flat


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _directional_outcome(raw_return: float, cost: float) -> dict[str, float]:
    net_long = raw_return - cost
    net_short = -raw_return - cost
    if net_long > 0.0 and net_long > net_short:
        label = "long_good"
    elif net_short > 0.0 and net_short > net_long:
        label = "short_good"
    else:
        label = "no_trade"
    return {
        "label": label,
        "raw_forward_return": raw_return,
        "net_return_long": net_long,
        "net_return_short": net_short,
        "spread_cost": cost,
    }
