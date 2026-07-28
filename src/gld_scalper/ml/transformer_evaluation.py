from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Settings
from ..database import Database
from ..utils.time_utils import ensure_utc
from .model_registry import ModelRegistry


def evaluate_transformer_paper_models(
    database: Database,
    settings: Settings,
    *,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM model_versions
        WHERE model_type LIKE 'causal_transformer_%' AND status IN ('candidate', 'champion')
    """
    params: tuple[Any, ...] = ()
    if model_version:
        sql += " AND model_version = ?"
        params = (model_version,)
    models = [dict(row) for row in database.conn.execute(sql + " ORDER BY datetime(created_at), id", params).fetchall()]
    reports: list[dict[str, Any]] = []
    for model in models:
        scope = str(model.get("model_scope") or "")
        if scope.endswith("fast_microstructure"):
            source, horizon, tolerance = "fast_scalp", 1, 2.0
        elif scope.endswith("minute") or scope.endswith("news_event"):
            source, horizon, tolerance = "signal", 5, 65.0
        else:
            reports.append(
                {
                    "model_version": model["model_version"],
                    "status": "skipped",
                    "reason": "exit Transformer requires trustworthy exit-specific labels",
                }
            )
            continue
        report = _evaluate_entry_model(database, settings, model, source, horizon, tolerance)
        reports.append(report)
    return reports


def _evaluate_entry_model(
    database: Database,
    settings: Settings,
    model: dict[str, Any],
    source: str,
    horizon: int,
    tolerance_seconds: float,
) -> dict[str, Any]:
    predictions = [
        dict(row)
        for row in database.conn.execute(
            """
            SELECT * FROM transformer_predictions
            WHERE model_version = ? AND status IN ('shadow', 'bounded_adviser', 'paper_champion')
            ORDER BY julianday(timestamp), id
            """,
            (model["model_version"],),
        ).fetchall()
    ]
    labels = [
        dict(row)
        for row in database.conn.execute(
            f"""
            SELECT timestamp, label_{horizon}m AS outcome_label,
                   forward_return_{horizon}m AS forward_return,
                   realized_spread_cost
            FROM outcome_labels
            WHERE decision_source = ? AND label_{horizon}m IS NOT NULL
              AND forward_return_{horizon}m IS NOT NULL
            ORDER BY julianday(timestamp), id
            """,
            (source,),
        ).fetchall()
    ]
    label_epochs = [ensure_utc(row["timestamp"]).timestamp() for row in labels]
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_labels: set[int] = set()
    for prediction in predictions:
        epoch = ensure_utc(prediction["timestamp"]).timestamp()
        insertion = bisect.bisect_left(label_epochs, epoch)
        candidates = [index for index in (insertion - 1, insertion, insertion + 1) if 0 <= index < len(labels)]
        candidates.sort(key=lambda index: abs(label_epochs[index] - epoch))
        selected = next(
            (
                index
                for index in candidates
                if index not in used_labels and abs(label_epochs[index] - epoch) <= tolerance_seconds
            ),
            None,
        )
        if selected is not None:
            used_labels.add(selected)
            matched.append((prediction, labels[selected]))

    pnl: list[float] = []
    correct: list[float] = []
    confidences: list[float] = []
    for prediction, outcome in matched:
        direction = str(prediction.get("predicted_direction") or "").lower()
        raw_return = float(outcome.get("forward_return") or 0.0)
        spread = abs(float(outcome.get("realized_spread_cost") or prediction.get("expected_cost") or 0.0))
        cost = spread + settings.outcome_label_slippage_pct
        probabilities = {
            "long_good": float(prediction.get("probability_long") or 0.0),
            "short_good": float(prediction.get("probability_short") or 0.0),
            "no_trade": float(prediction.get("probability_no_trade") or 0.0),
        }
        confidences.append(max(probabilities.values()))
        correct.append(float(direction == str(outcome.get("outcome_label") or "").lower()))
        if direction == "long_good":
            pnl.append(raw_return - cost)
        elif direction == "short_good":
            pnl.append(-raw_return - cost)

    gains = sum(value for value in pnl if value > 0)
    losses = -sum(value for value in pnl if value < 0)
    cumulative = np.concatenate(([0.0], np.cumsum(pnl))) if pnl else np.asarray([0.0])
    drawdown = float(np.max(np.maximum.accumulate(cumulative) - cumulative))
    paper_metrics = {
        "paper_prediction_count": float(len(matched)),
        "paper_trade_count": float(len(pnl)),
        "paper_win_rate": sum(value > 0 for value in pnl) / max(len(pnl), 1),
        "paper_profit_factor": gains / losses if losses > 1e-12 else (99.0 if gains > 0 else 0.0),
        "paper_net_return": float(sum(pnl)),
        "paper_average_pnl_per_trade": float(np.mean(pnl)) if pnl else 0.0,
        "paper_drawdown": drawdown,
        "paper_accuracy": float(np.mean(correct)) if correct else 0.0,
        "paper_mean_confidence": float(np.mean(confidences)) if confidences else 0.0,
        "paper_evaluation_horizon_minutes": float(horizon),
        "paper_evaluation_after_costs": 1.0,
    }
    metrics = json.loads(model.get("metrics_json") or "{}")
    metrics.update(paper_metrics)
    with database.conn:
        database.conn.execute(
            "UPDATE model_versions SET metrics_json = ? WHERE model_version = ?",
            (json.dumps(metrics, sort_keys=True), model["model_version"]),
        )
    lifecycle = _apply_paper_lifecycle(database, settings, model, metrics)
    return {
        "model_version": model["model_version"],
        "model_scope": model["model_scope"],
        "status": "completed",
        "decision_source": source,
        **paper_metrics,
        **lifecycle,
    }


def _apply_paper_lifecycle(
    database: Database,
    settings: Settings,
    model: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    registry = ModelRegistry(database, settings)
    scope = str(model.get("model_scope") or "")
    paper_trades = float(metrics.get("paper_trade_count") or 0.0)
    paper_pf = float(metrics.get("paper_profit_factor") or 0.0)
    paper_net = float(metrics.get("paper_net_return") or 0.0)
    paper_drawdown = float(metrics.get("paper_drawdown") or 0.0)
    status = str(model.get("status") or "candidate")
    if (
        status == "champion"
        and settings.transformer_auto_demotion
        and paper_trades >= settings.transformer_demotion_min_paper_trades
        and (
            paper_pf < settings.transformer_demotion_profit_factor
            or paper_net <= 0.0
            or paper_drawdown > settings.promotion_max_drawdown * 1.5
        )
    ):
        reason = (
            f"paper evidence outside validated range: trades={paper_trades:.0f}, "
            f"profit_factor={paper_pf:.3f}, net={paper_net:.6f}, drawdown={paper_drawdown:.6f}"
        )
        demoted, message = registry.demote_champion(scope, reason)
        if demoted:
            _set_manifest_role(model.get("path"), "paper_shadow", True)
        return {"auto_demoted": demoted, "lifecycle_reason": message}
    if status == "candidate" and settings.transformer_auto_promotion:
        ready, readiness_reason = registry.promotion_readiness(metrics)
        if ready:
            promoted, promotion_reason = registry.promote_if_better(
                str(model["model_version"]),
                metrics,
                model_scope=scope,
            )
            if promoted:
                _set_manifest_role(model.get("path"), "paper_champion", False)
            return {"auto_promoted": promoted, "lifecycle_reason": promotion_reason}
        return {"auto_promoted": False, "lifecycle_reason": readiness_reason}
    return {"lifecycle_reason": "paper lifecycle unchanged"}


def _set_manifest_role(path: Any, role: str, shadow_only: bool) -> None:
    try:
        manifest_path = Path(str(path))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["role"] = role
        manifest["shadow_only"] = shadow_only
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError):
        return
