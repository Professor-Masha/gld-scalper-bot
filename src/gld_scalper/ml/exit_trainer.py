from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter, defaultdict
from typing import Any

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import ensure_utc, utc_now
from .calibration import chronological_partitions, fit_natural_frequency_calibrator
from .evaluator import classification_metrics
from .holding_labels import triple_barrier_holding_label
from .model_registry import ModelRegistry


EXIT_FEATURE_COLUMNS = [
    "pnl_pct", "mfe", "mae", "profit_given_back", "holding_seconds", "mark_price",
    "distance_to_stop_pct", "distance_to_target_pct", "economic_breakeven_pct", "spread_pct",
    "liquidity_score", "trade_intensity", "volatility_burst", "quote_age_seconds",
    "trade_age_seconds", "setup_invalidated", "session_minutes_remaining", "expected_exit_cost_pct",
]
UNCLEAN_REASONS = {
    "unprotected_residual_position", "startup_residual_position", "safety_flatten",
    "process_shutdown", "session_close", "broker_reconciliation",
}


class CalibratedExitModel:
    def __init__(self, base_model: Any, calibrator: Any, classes: list[str]) -> None:
        self.base_model = base_model
        self.calibrator = calibrator
        self.classes_ = classes

    def predict_proba(self, matrix):
        raw = self.base_model.predict_proba(matrix)
        aligned = _align(raw, list(self.base_model.classes_), self.classes_)
        return self.calibrator.predict_proba(aligned)

    def predict(self, matrix):
        return [self.classes_[max(range(len(row)), key=row.__getitem__)] for row in self.predict_proba(matrix)]


def train_exit_candidate(
    database: Database,
    settings: Settings,
    *,
    strategy_path: str = "all",
    playbook: str = "all",
) -> dict[str, Any]:
    records = build_holding_training_records(database, strategy_path=strategy_path, playbook=playbook)
    if len(records) < settings.exit_model_min_trustworthy_outcomes:
        return {
            "status": "skipped", "reason": "not enough trustworthy path-aware exit observations",
            "samples": len(records), "minimum": settings.exit_model_min_trustworthy_outcomes,
        }
    if len({record["label"] for record in records}) < 2:
        return {"status": "skipped", "reason": "holding labels need at least two action classes", "samples": len(records)}

    timestamps = [ensure_utc(record["timestamp"]).timestamp() for record in records]
    minimum = max(10, min(25, len(records) // 8))
    partitions = chronological_partitions(timestamps, purge_seconds=15 * 60, minimum_partition_size=minimum)
    groups = {
        "train": [records[index] for index in partitions.train],
        "calibration": [records[index] for index in partitions.calibration],
        "threshold": [records[index] for index in partitions.threshold],
        "holdout": [records[index] for index in partitions.holdout],
    }
    from sklearn.ensemble import RandomForestClassifier

    model = RandomForestClassifier(
        n_estimators=160, max_depth=8, min_samples_leaf=12,
        class_weight="balanced_subsample", random_state=41, n_jobs=1,
    )
    model.fit(_matrix(groups["train"]), [record["label"] for record in groups["train"]])
    calibration_raw = _align(
        model.predict_proba(_matrix(groups["calibration"])), list(model.classes_), ("hold", "reduce", "close")
    )
    calibrator, calibration_metrics = fit_natural_frequency_calibrator(
        calibration_raw, [record["label"] for record in groups["calibration"]], ("hold", "reduce", "close")
    )
    wrapped = CalibratedExitModel(model, calibrator, ["hold", "reduce", "close"])
    threshold_probabilities = wrapped.predict_proba(_matrix(groups["threshold"]))
    exit_policy = _select_exit_policy(
        [record["label"] for record in groups["threshold"]], threshold_probabilities
    )
    holdout_probabilities = wrapped.predict_proba(_matrix(groups["holdout"]))
    holdout_predictions = _exit_policy_predictions(holdout_probabilities, exit_policy["minimum_confidence"])
    metrics = classification_metrics(
        [record["label"] for record in groups["holdout"]], holdout_predictions,
        holdout_probabilities.tolist(), ("hold", "reduce", "close"),
    )
    action_indices = [index for index, prediction in enumerate(holdout_predictions) if prediction != "hold"]
    holdout_labels = [record["label"] for record in groups["holdout"]]
    metrics["selective_accuracy"] = sum(
        holdout_predictions[index] == holdout_labels[index] for index in action_indices
    ) / max(len(action_indices), 1)
    metrics["abstention_rate"] = 1.0 - len(action_indices) / max(len(holdout_predictions), 1)
    metrics.update({
        "sample_count": len(records), "class_counts": dict(Counter(record["label"] for record in records)),
        "target": "path_aware_exit_action", "entry_target_shared": False,
        "calibration": calibration_metrics, "partitions": {name: len(items) for name, items in groups.items()},
        "label_policy": "triple_barrier_after_cost_v2", "exit_policy": exit_policy,
    })
    scope = f"exit:{strategy_path}:{playbook}"
    model_state_fingerprint = hashlib.sha256(pickle.dumps(wrapped, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()
    fingerprint = hashlib.sha256(json.dumps({
        "scope": scope, "start": str(records[0]["timestamp"]), "end": str(records[-1]["timestamp"]),
        "features": EXIT_FEATURE_COLUMNS, "metrics": metrics, "model_state": model_state_fingerprint,
    }, sort_keys=True, default=str).encode()).hexdigest()
    version = f"exit-candidate-{utc_now().strftime('%Y%m%d-%H%M%S-%f')}"
    path = PROJECT_ROOT / "models" / settings.data_mode / "exit" / f"{version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump({
        "format_version": 2, "model": wrapped, "model_scope": scope,
        "artifact_fingerprint": fingerprint, "model_state_fingerprint": model_state_fingerprint,
        "feature_columns": EXIT_FEATURE_COLUMNS, "target": "path_aware_exit_action",
        "classes": ["hold", "reduce", "close"], "metrics": metrics,
        "calibration": calibration_metrics, "abstention": exit_policy, "hard_safety_override": True,
    }, path)
    ModelRegistry(database, settings).register_candidate(
        model_version=version, model_type="path_aware_exit_random_forest", model_scope=scope,
        path=str(path), feature_columns=EXIT_FEATURE_COLUMNS, feature_profile="holding_path_v2",
        model_parameters=model.get_params(), artifact_fingerprint=fingerprint, metrics=metrics,
        training_start=ensure_utc(records[0]["timestamp"]), training_end=utc_now(),
        training_data_start=ensure_utc(records[0]["timestamp"]), training_data_end=ensure_utc(records[-1]["timestamp"]),
    )
    return {
        "status": "completed", "model_version": version, "model_scope": scope,
        "path": str(path), "metrics": metrics, "promotion_ready": False,
        "reason": "exit candidates remain advisory until chronological and paper validation pass",
    }


def build_holding_training_records(
    database: Database, *, strategy_path: str = "all", playbook: str = "all"
) -> list[dict[str, Any]]:
    rows = database.conn.execute("""
        SELECT e.*, o.strategy_path, o.playbook, o.exit_reason, o.exit_time, o.net_pnl_after_costs
        FROM position_management_events e
        JOIN trade_outcomes o ON o.trade_id = e.trade_id
        WHERE o.exit_time IS NOT NULL AND o.net_pnl_after_costs IS NOT NULL
          AND e.mark_price IS NOT NULL AND e.entry_price IS NOT NULL
        ORDER BY e.trade_id, julianday(e.timestamp), e.id
    """).fetchall()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if strategy_path != "all" and str(row["strategy_path"] or "minute") != strategy_path:
            continue
        if playbook != "all" and str(row["playbook"] or "unclassified") != playbook:
            continue
        if str(row["exit_reason"] or "") in UNCLEAN_REASONS:
            continue
        grouped[str(row["trade_id"])].append(row)
    records: list[dict[str, Any]] = []
    for events in grouped.values():
        start = ensure_utc(events[0]["timestamp"])
        for index, row in enumerate(events[:-1]):
            now = ensure_utc(row["timestamp"])
            details = json.loads(row["details_json"] or "{}")
            horizon_seconds = 60 if str(row["strategy_path"] or "minute") == "fast" else 5 * 60
            future = [candidate for candidate in events[index + 1:] if (ensure_utc(candidate["timestamp"]) - now).total_seconds() <= horizon_seconds]
            if not future:
                continue
            spread = _number(details.get("spread_pct"))
            expected_cost = max(_number(details.get("expected_exit_cost_pct")), spread, _number(details.get("economic_breakeven_pct")), 0.0001)
            label = triple_barrier_holding_label(
                direction=str(row["direction"] or "LONG"), current_price=_number(row["mark_price"]),
                future_prices=[_number(candidate["mark_price"]) for candidate in future],
                seconds_per_observation=max(1.0, (ensure_utc(future[0]["timestamp"]) - now).total_seconds()),
                profit_barrier_pct=max(expected_cost * 1.5, 0.0003),
                risk_barrier_pct=max(_number(details.get("stop_distance_pct")), 0.001),
                round_trip_cost_pct=expected_cost, invalidated="invalidat" in str(row["reason"] or "").lower(),
            )
            mfe, pnl = _number(row["max_favorable_excursion"]), _number(row["pnl_pct"])
            entry, mark = max(_number(row["entry_price"]), 1e-12), _number(row["mark_price"])
            old_stop, target = _number(row["old_stop_price"]), _number(details.get("take_profit_price"))
            records.append({"timestamp": now, "label": label.action, "barrier": label.barrier, "features": {
                "pnl_pct": pnl, "mfe": mfe, "mae": _number(row["max_adverse_excursion"]),
                "profit_given_back": max(0.0, mfe - max(pnl, 0.0)), "holding_seconds": max(0.0, (now - start).total_seconds()),
                "mark_price": mark, "distance_to_stop_pct": abs(mark - old_stop) / entry if old_stop else 0.0,
                "distance_to_target_pct": abs(target - mark) / entry if target else 0.0,
                "economic_breakeven_pct": _number(details.get("economic_breakeven_pct")), "spread_pct": spread,
                "liquidity_score": _number(details.get("liquidity_score")), "trade_intensity": _number(details.get("trade_intensity")),
                "volatility_burst": _number(details.get("volatility_burst")), "quote_age_seconds": _number(details.get("quote_age_seconds")),
                "trade_age_seconds": _number(details.get("trade_age_seconds")),
                "setup_invalidated": 1.0 if "invalidat" in str(row["reason"] or "").lower() else 0.0,
                "session_minutes_remaining": _number(details.get("session_minutes_remaining")), "expected_exit_cost_pct": expected_cost,
            }})
    return sorted(records, key=lambda record: record["timestamp"])


def _matrix(records: list[dict[str, Any]]) -> list[list[float]]:
    return [[_number(record["features"].get(column)) for column in EXIT_FEATURE_COLUMNS] for record in records]


def _align(values, source: list[str], target) -> list[list[float]]:
    index = {label: position for position, label in enumerate(source)}
    return [[float(row[index[label]]) if label in index else 0.0 for label in target] for row in values]


def _select_exit_policy(labels: list[str], probabilities) -> dict[str, float]:
    best = {"minimum_confidence": 0.70, "threshold_score": -1.0}
    for confidence in (0.40, 0.50, 0.60, 0.70, 0.80):
        predictions = _exit_policy_predictions(probabilities, confidence)
        directional = [index for index, prediction in enumerate(predictions) if prediction != "hold"]
        accuracy = sum(predictions[index] == labels[index] for index in directional) / max(len(directional), 1)
        coverage = len(directional) / max(len(labels), 1)
        score = accuracy * 0.75 + coverage * 0.25 if directional else 0.0
        if score > best["threshold_score"]:
            best = {
                "minimum_confidence": confidence,
                "threshold_score": score,
                "selective_accuracy": accuracy,
                "action_coverage": coverage,
            }
    return best


def _exit_policy_predictions(probabilities, minimum_confidence: float) -> list[str]:
    classes = ("hold", "reduce", "close")
    predictions: list[str] = []
    for row in probabilities:
        index = max(range(len(row)), key=lambda position: float(row[position]))
        predictions.append(classes[index] if float(row[index]) >= minimum_confidence else "hold")
    return predictions


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
