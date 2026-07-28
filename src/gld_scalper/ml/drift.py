from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

from ..database import Database
from ..utils.time_utils import utc_iso, utc_now
from .dataset_builder import _flatten_features
from .model_registry import ModelRegistry


def generate_drift_report(
    database: Database,
    *,
    limit: int = 1000,
    model_scope: str | None = None,
    allow_demotion: bool = True,
) -> dict[str, Any]:
    registry = ModelRegistry(database)
    champion = registry.get_champion(model_scope)
    if champion is None:
        return {"status": "skipped", "reason": "no champion model"}
    path = Path(champion["path"])
    if not path.is_absolute():
        path = Path.cwd() / path
    payload = _load_payload(path)
    statistics = payload.get("feature_statistics") or {}
    columns = list(payload.get("feature_columns") or [])
    rows = database.conn.execute(
        """
        SELECT feature_snapshot_json FROM model_predictions
        WHERE model_version = ?
        ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?
        """,
        (champion["model_version"], max(1, limit)),
    ).fetchall()
    samples = [_flatten_features(json.loads(row["feature_snapshot_json"] or "{}")) for row in rows]
    if not samples:
        return {"status": "skipped", "reason": "no recent champion predictions", "model_version": champion["model_version"]}
    details: dict[str, dict[str, float]] = {}
    missing_total = 0
    outlier_total = 0
    checked_total = 0
    feature_scores: list[float] = []
    for column in columns:
        stats = statistics.get(column) or {}
        values = [sample[column] for sample in samples if column in sample]
        missing_fraction = 1.0 - len(values) / len(samples)
        missing_total += sum(column not in sample for sample in samples)
        if not values:
            details[column] = {"missing_fraction": 1.0, "mean_shift_z": 0.0, "outlier_fraction": 0.0}
            continue
        recent_mean = sum(values) / len(values)
        training_mean = float(stats.get("mean", 0.0) or 0.0)
        training_std = float(stats.get("std", 0.0) or 0.0)
        mean_shift_z = abs(recent_mean - training_mean) / training_std if training_std > 1e-12 else 0.0
        outliers = sum(abs(value - training_mean) / training_std > 6.0 for value in values) if training_std > 1e-12 else 0
        outlier_fraction = outliers / len(values)
        outlier_total += outliers
        checked_total += len(values)
        feature_scores.append(min(mean_shift_z / 5.0, 1.0))
        details[column] = {
            "training_mean": training_mean,
            "recent_mean": recent_mean,
            "mean_shift_z": mean_shift_z,
            "missing_fraction": missing_fraction,
            "outlier_fraction": outlier_fraction,
        }
    missing_fraction = missing_total / max(len(samples) * len(columns), 1)
    outlier_fraction = outlier_total / max(checked_total, 1)
    drift_score = sum(feature_scores) / max(len(feature_scores), 1)
    if drift_score >= database.settings.drift_demotion_score or outlier_fraction >= 0.20:
        status = "drifted"
    elif drift_score >= 0.20 or missing_fraction >= 0.20:
        status = "watch"
    else:
        status = "stable"
    paper_performance = _paper_performance(database, champion["model_version"], database.settings.drift_min_paper_outcomes)
    validated = json.loads(champion.get("metrics_json") or "{}")
    performance_reasons: list[str] = []
    if paper_performance["outcome_count"] >= database.settings.drift_min_paper_outcomes:
        validated_pf = float(validated.get("paper_profit_factor", validated.get("profit_factor", 0.0)) or 0.0)
        if validated_pf > 0 and paper_performance["profit_factor"] < validated_pf * database.settings.drift_profit_factor_floor_ratio:
            performance_reasons.append("paper profit factor fell outside validated range")
        validated_drawdown = float(validated.get("max_drawdown", 0.0) or 0.0)
        if validated_drawdown > 0 and paper_performance["max_drawdown"] > validated_drawdown * database.settings.drift_drawdown_limit_multiplier:
            performance_reasons.append("paper drawdown exceeded validated range")
    demotion_reasons: list[str] = []
    if len(samples) >= database.settings.drift_min_predictions and status == "drifted":
        demotion_reasons.append("feature drift exceeded limit")
    demotion_reasons.extend(performance_reasons)
    demoted = False
    if allow_demotion and demotion_reasons:
        demoted, _ = registry.demote_champion(
            str(champion.get("model_scope") or "entry:all"),
            "; ".join(demotion_reasons),
        )
        if demoted:
            status = "demoted"
    report = {
        "timestamp": utc_now().isoformat(),
        "model_version": champion["model_version"],
        "sample_count": len(samples),
        "drift_score": drift_score,
        "missing_feature_fraction": missing_fraction,
        "outlier_feature_fraction": outlier_fraction,
        "status": status,
        "model_scope": champion.get("model_scope") or "entry:all",
        "paper_outcome_count": paper_performance["outcome_count"],
        "paper_profit_factor": paper_performance["profit_factor"],
        "paper_drawdown": paper_performance["max_drawdown"],
        "champion_demoted": demoted,
        "demotion_reason": "; ".join(demotion_reasons) if demotion_reasons else None,
        "feature_details": details,
    }
    with database.conn:
        database.conn.execute(
            """
            INSERT INTO model_drift_reports(timestamp, model_version, sample_count, drift_score,
                missing_feature_fraction, outlier_feature_fraction, status, paper_outcome_count,
                paper_profit_factor, paper_drawdown, champion_demoted, demotion_reason,
                feature_details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["timestamp"],
                report["model_version"],
                report["sample_count"],
                report["drift_score"],
                report["missing_feature_fraction"],
                report["outlier_feature_fraction"],
                report["status"],
                report["paper_outcome_count"],
                report["paper_profit_factor"],
                report["paper_drawdown"],
                1 if report["champion_demoted"] else 0,
                report["demotion_reason"],
                json.dumps(details, sort_keys=True),
                utc_iso(utc_now()),
            ),
        )
    return report


def _paper_performance(database: Database, model_version: str, minimum: int) -> dict[str, Any]:
    rows = database.conn.execute(
        """
        SELECT COALESCE(NULLIF(root_episode_id, ''), NULLIF(trade_id, ''), 'outcome:' || id) AS episode_key,
               SUM(COALESCE(net_pnl_after_costs, net_pnl_estimated, 0)) AS pnl,
               SUM(ABS(COALESCE(notional, 0))) AS notional
        FROM trade_outcomes
        WHERE model_version = ?
        GROUP BY episode_key
        ORDER BY MAX(julianday(exit_time)) DESC
        LIMIT ?
        """,
        (model_version, max(minimum * 5, 100)),
    ).fetchall()
    returns = [float(row["pnl"] or 0.0) / max(float(row["notional"] or 0.0), 1.0) for row in rows]
    gross_profit = sum(value for value in returns if value > 0)
    gross_loss = abs(sum(value for value in returns if value < 0))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in reversed(returns):
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "outcome_count": len(returns),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "max_drawdown": max_drawdown,
    }


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        import joblib

        return joblib.load(path)
    except Exception:
        with path.open("rb") as file_handle:
            return pickle.load(file_handle)
