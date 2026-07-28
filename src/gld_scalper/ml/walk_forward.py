from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from ..config import Settings
from ..database import Database
from ..utils.time_utils import utc_now
from .dataset_builder import build_training_records, label_quality
from .evaluator import CLASSES, classification_metrics, optimize_policy_thresholds, policy_predictions, trading_metrics


def run_walk_forward_validation(
    database: Database,
    settings: Settings,
    *,
    lookback_days: int = 730,
    records: list[dict[str, Any]] | None = None,
    minimum_confidence: float | None = None,
    minimum_margin: float | None = None,
    model_template: Any | None = None,
    feature_columns: Sequence[str] | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    rows = sorted(records or build_training_records(database, lookback_days), key=lambda item: item["timestamp"])
    if len(rows) < settings.promotion_min_trade_count:
        return {"status": "skipped", "reason": "not enough labeled rows", "rows": len(rows), "folds": []}
    try:
        from sklearn.base import clone
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover
        return {"status": "skipped", "reason": f"scikit-learn unavailable: {exc}", "rows": len(rows), "folds": []}

    columns = list(feature_columns) if feature_columns is not None else sorted({key for row in rows for key in row["features"]})
    evaluator_name = model_name or (type(model_template).__name__ if model_template is not None else "context_logistic_regression")
    train_days = max(settings.walk_forward_train_months * 30, 30)
    test_days = max(settings.walk_forward_test_months * 30, 15)
    purge = timedelta(minutes=max(settings.ml_purge_minutes, 0))
    embargo = timedelta(minutes=max(settings.ml_embargo_minutes, 0))
    start = rows[0]["timestamp"]
    end = rows[-1]["timestamp"]
    cursor = start
    folds: list[dict[str, Any]] = []
    while cursor + timedelta(days=train_days + test_days) + purge <= end:
        train_start = cursor
        train_end = cursor + timedelta(days=train_days)
        test_start = train_end + purge
        test_end = test_start + timedelta(days=test_days)
        train = [row for row in rows if train_start <= row["timestamp"] < train_end]
        test = [row for row in rows if test_start <= row["timestamp"] < test_end]
        labels_ok, reason = label_quality([row["label"] for row in train])
        if labels_ok and test:
            tuning_size = max(25, int(len(train) * 0.15))
            fit_train = train[:-tuning_size]
            tuning = train[-tuning_size:]
            if len(set(row["label"] for row in fit_train)) < 2:
                fit_train = train
                tuning = []
            base_model = (
                clone(model_template)
                if model_template is not None
                else make_pipeline(
                    StandardScaler(),
                    LogisticRegression(max_iter=1_000, class_weight="balanced", random_state=17),
                )
            )
            model = _fit_probability_model(base_model, fit_train, columns)
            if tuning:
                tuning_probabilities = _predict_probabilities(model, tuning, columns)
                thresholds = optimize_policy_thresholds(
                    tuning_probabilities,
                    [row["outcome"] for row in tuning],
                    classes=CLASSES,
                    minimum_trades=max(10, int(len(tuning) * 0.01)),
                )
            else:
                thresholds = {"trade_count": 0.0, "threshold_score": -1.0}
            if int(thresholds.get("trade_count", 0.0)) == 0:
                thresholds["minimum_confidence"] = minimum_confidence if minimum_confidence is not None else settings.ml_min_confidence
                thresholds["minimum_margin"] = minimum_margin if minimum_margin is not None else settings.ml_min_probability_margin
            y_true = [row["label"] for row in test]
            aligned_probabilities = _predict_probabilities(model, test, columns)
            y_pred = policy_predictions(
                aligned_probabilities,
                classes=CLASSES,
                minimum_confidence=float(thresholds["minimum_confidence"]),
                minimum_margin=float(thresholds["minimum_margin"]),
            )
            metrics = classification_metrics(y_true, y_pred, aligned_probabilities, CLASSES)
            metrics.update(trading_metrics(y_pred, [row["outcome"] for row in test]))
            metrics["optimized_minimum_confidence"] = float(thresholds["minimum_confidence"])
            metrics["optimized_minimum_margin"] = float(thresholds["minimum_margin"])
            metrics["threshold_tuning_score"] = float(thresholds.get("threshold_score", 0.0))
            folds.append(
                {
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "purge_minutes": settings.ml_purge_minutes,
                    "embargo_minutes": settings.ml_embargo_minutes,
                    "train_rows": len(train),
                    "test_rows": len(test),
                    "metrics": metrics,
                }
            )
        else:
            folds.append(
                {
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "train_rows": len(train),
                    "test_rows": len(test),
                    "skipped_reason": reason if not labels_ok else "empty test fold",
                }
            )
        cursor = cursor + timedelta(days=test_days) + embargo
    completed = [fold for fold in folds if "metrics" in fold]
    if not completed:
        return {"status": "skipped", "reason": "no completed folds", "rows": len(rows), "folds": folds}
    summary = _aggregate_metrics([fold["metrics"] for fold in completed])
    summary["profitable_fold_ratio"] = sum(fold["metrics"].get("net_return", 0.0) > 0 for fold in completed) / len(completed)
    summary["worst_fold_net_return"] = min(float(fold["metrics"].get("net_return", 0.0)) for fold in completed)
    summary["fold_count"] = float(len(completed))
    return {
        "status": "completed",
        "rows": len(rows),
        "fold_count": len(completed),
        "evaluator_model_type": evaluator_name,
        "feature_columns": columns,
        "feature_count": len(columns),
        "model_parameters": _model_parameters(model_template),
        "summary": summary,
        "folds": folds,
    }


def save_walk_forward_experiment(
    database: Database,
    result: dict[str, Any],
    *,
    policy_name: str = "logistic_regression_purged_walk_forward",
) -> int:
    summary = result.get("summary") or {}
    return database.insert_rl_experiment(
        {
            "timestamp": utc_now(),
            "experiment_name": "purged_walk_forward_ml_validation",
            "policy_name": policy_name,
            "total_reward": summary.get("net_return"),
            "total_trades": summary.get("trade_count"),
            "win_rate": summary.get("win_rate"),
            "max_drawdown": summary.get("max_drawdown"),
            "benchmark_reward": None,
            "promoted": False,
            "promotion_reason": result.get("status"),
            "metrics": result,
        }
    )


def _matrix(rows: list[dict[str, Any]], columns: list[str]) -> list[list[float]]:
    return [[float(row["features"].get(column, 0.0)) for column in columns] for row in rows]


def _fit_probability_model(base_model: Any, rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    calibration_size = max(1, int(len(rows) * 0.15))
    fit_rows = rows[:-calibration_size]
    calibration_rows = rows[-calibration_size:]
    if len(set(row["label"] for row in fit_rows)) < 2:
        fit_rows = rows
        calibration_rows = []
    base_model.fit(_matrix(fit_rows, columns), [row["label"] for row in fit_rows])
    calibrator = None
    if calibration_rows and len(set(row["label"] for row in calibration_rows)) >= 2:
        from sklearn.linear_model import LogisticRegression

        raw = _align_probabilities(
            base_model.predict_proba(_matrix(calibration_rows, columns)),
            list(base_model.classes_),
            CLASSES,
        )
        calibrator = LogisticRegression(max_iter=500, class_weight="balanced", random_state=19)
        calibrator.fit(raw, [row["label"] for row in calibration_rows])
    return {"base_model": base_model, "calibrator": calibrator}


def _predict_probabilities(model: dict[str, Any], rows: list[dict[str, Any]], columns: list[str]) -> list[list[float]]:
    base_model = model["base_model"]
    raw = _align_probabilities(
        base_model.predict_proba(_matrix(rows, columns)),
        list(base_model.classes_),
        CLASSES,
    )
    calibrator = model["calibrator"]
    if calibrator is None:
        return raw
    return _align_probabilities(calibrator.predict_proba(raw), list(calibrator.classes_), CLASSES)


def _model_parameters(model: Any | None) -> dict[str, Any]:
    if model is None or not hasattr(model, "get_params"):
        return {}
    result: dict[str, Any] = {}
    for key, value in model.get_params(deep=True).items():
        if value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = repr(value)
    return result


def _align_probabilities(probabilities: Any, source_classes: Sequence[str], target_classes: Sequence[str]) -> list[list[float]]:
    source_index = {label: index for index, label in enumerate(source_classes)}
    return [
        [float(row[source_index[label]]) if label in source_index else 0.0 for label in target_classes]
        for row in probabilities
    ]


def _aggregate_metrics(items: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for item in items for key in item})
    result: dict[str, float] = {}
    additive = {"net_return", "gross_profit", "gross_loss", "trade_count", "trade_label_count", "win_count", "loss_count"}
    for key in keys:
        values = [float(item.get(key, 0.0) or 0.0) for item in items]
        result[key] = sum(values) if key in additive else sum(values) / len(values)
    total_loss = result.get("gross_loss", 0.0)
    total_gain = result.get("gross_profit", 0.0)
    result["profit_factor"] = total_gain / total_loss if total_loss > 0 else (999.0 if total_gain > 0 else 0.0)
    result["win_rate"] = result.get("win_count", 0.0) / max(result.get("trade_count", 0.0), 1.0)
    result["average_pnl_per_trade"] = result.get("net_return", 0.0) / max(result.get("trade_count", 0.0), 1.0)
    return result
