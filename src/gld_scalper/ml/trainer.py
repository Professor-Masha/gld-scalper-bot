from __future__ import annotations

import math
import hashlib
import json
import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import utc_now
from .archive_dataset import load_archive_training_artifact
from .calibration import NaturalFrequencyCalibrator, chronological_partitions, fit_natural_frequency_calibrator
from .dataset_builder import build_training_records, label_quality
from .evaluator import CLASSES, classification_metrics, optimize_policy_thresholds, policy_predictions, trading_metrics
from .feature_selection import select_stable_features
from .model_registry import ModelRegistry
from .scopes import model_scope_from_context
from .walk_forward import run_walk_forward_validation, save_walk_forward_experiment


FAST_FEATURE_TERMS = {
    "spread",
    "quote",
    "trade",
    "liquidity",
    "volatility",
    "midpoint",
    "bid_",
    "ask_",
    "pressure",
    "signed_volume",
    "return_1m",
    "range_1m",
    "volume_ratio",
    "breakout",
    "compression",
    "minute_",
}


class ProbabilityCalibratedModel:
    def __init__(self, base_model: Any, calibrator: Any | None, classes: Sequence[str]) -> None:
        self.base_model = base_model
        self.calibrator = calibrator
        self.classes_ = list(classes)

    def predict_proba(self, matrix: Sequence[Sequence[float]]):
        raw = self._aligned_base_probabilities(matrix)
        if self.calibrator is None:
            return raw
        calibrated = self.calibrator.predict_proba(raw)
        return _align_probabilities(calibrated, list(self.calibrator.classes_), self.classes_)

    def predict(self, matrix: Sequence[Sequence[float]]):
        probabilities = self.predict_proba(matrix)
        return [self.classes_[max(range(len(row)), key=row.__getitem__)] for row in probabilities]

    def _aligned_base_probabilities(self, matrix: Sequence[Sequence[float]]):
        raw = self.base_model.predict_proba(matrix)
        return _align_probabilities(raw, list(self.base_model.classes_), self.classes_)


def train_candidate_model(
    database: Database,
    settings: Settings,
    lookback_days: int = 90,
    *,
    use_llm_labels: bool | None = None,
    records: list[dict[str, Any]] | None = None,
    archive_artifact: str | Path | None = None,
    allow_promotion: bool = True,
    experiment_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    training_start = utc_now()
    if archive_artifact is not None:
        records = load_archive_training_artifact(archive_artifact)
    if records is None:
        records = build_training_records(database, lookback_days, use_llm_labels=use_llm_labels)
    records = sorted(records, key=lambda item: item["timestamp"])
    if not records:
        raise RuntimeError("No labeled signal/trade data is available for training yet.")
    labels = [str(record["label"]) for record in records]
    labels_ok, label_reason = label_quality(labels)
    if not labels_ok:
        raise RuntimeError(f"Cannot train candidate model: {label_reason}.")

    partitions = _partition_training_records(records, purge_minutes=settings.ml_purge_minutes)
    train_records = partitions["train"]
    calibration_records = partitions["calibration"]
    threshold_records = partitions["threshold"]
    test_records = partitions["holdout"]
    labels_ok, label_reason = label_quality([record["label"] for record in train_records])
    if not labels_ok or not test_records:
        raise RuntimeError(f"Cannot create chronological validation split: {label_reason}.")
    selected = _train_latency_aware_candidate_models(
        settings,
        train_records,
        calibration_records,
        threshold_records,
        test_records,
        experiment_context=experiment_context,
    )
    holdout_metrics = dict(selected["metrics"])
    metrics = dict(holdout_metrics)
    metrics.update({f"holdout_{key}": value for key, value in holdout_metrics.items()})
    walk_forward = run_walk_forward_validation(
        database,
        settings,
        lookback_days=max(lookback_days, 365),
        records=records,
        minimum_confidence=float(selected["minimum_confidence"]),
        minimum_margin=float(selected["minimum_margin"]),
        model_template=selected["model"].base_model,
        feature_columns=selected["feature_columns"],
        model_name=selected["model_type"],
    )
    if walk_forward.get("status") == "completed":
        save_walk_forward_experiment(database, walk_forward, policy_name=selected["model_type"])
        summary = walk_forward.get("summary") or {}
        for key, value in summary.items():
            metrics[f"walk_forward_{key}"] = value
        for key in (
            "profit_factor",
            "win_rate",
            "max_drawdown",
            "average_pnl_per_trade",
            "net_return",
            "trade_count",
            "trade_coverage",
            "gross_profit",
            "gross_loss",
            "win_count",
            "loss_count",
        ):
            if key in summary:
                metrics[key] = summary[key]
        metrics["trade_label_count"] = float(summary.get("trade_count", 0.0) or 0.0)
        metrics["slippage_adjusted_return"] = float(summary.get("net_return", 0.0) or 0.0)
        metrics["profitable_fold_ratio"] = float(summary.get("profitable_fold_ratio", 0.0) or 0.0)
        metrics["walk_forward_completed"] = 1.0
    else:
        metrics["walk_forward_completed"] = 0.0

    paper_metrics = _evaluate_subset(selected, [record for record in test_records if str(record.get("source") or "").startswith("paper")])
    metrics.update({f"paper_{key}": value for key, value in paper_metrics.items()})
    metrics["paper_sample_count"] = float(len([record for record in test_records if str(record.get("source") or "").startswith("paper")]))
    regime_metrics = _evaluate_regimes(selected, test_records)
    metrics["validated_regime_count"] = float(sum(1 for item in regime_metrics.values() if float(item.get("trade_count", 0.0) or 0.0) > 0))
    metrics["regime_metrics"] = regime_metrics

    context_suffix = str((experiment_context or {}).get("experiment_key") or "")[:10]
    suffix = f"-{context_suffix}" if context_suffix else ""
    model_version = f"candidate-{training_start.strftime('%Y%m%d-%H%M%S-%f')}{suffix}"
    model_dir = PROJECT_ROOT / "models" / settings.data_mode
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"{model_version}.pkl"
    model_scope = model_scope_from_context(experiment_context)
    model_parameters = _model_parameters(selected["model"].base_model)
    thresholds = {
        "minimum_confidence": selected["minimum_confidence"],
        "minimum_margin": selected["minimum_margin"],
        "minimum_expected_edge": selected["threshold_metrics"].get("minimum_expected_edge", 0.0001),
        "max_missing_fraction": settings.ml_max_missing_feature_fraction,
        "max_outlier_fraction": settings.ml_max_outlier_feature_fraction,
    }
    model_state_fingerprint = _model_state_fingerprint(selected["model"])
    return_model_state_fingerprint = _model_state_fingerprint(selected.get("return_models") or {})
    artifact_fingerprint = _artifact_fingerprint(
        model_scope=model_scope,
        feature_columns=selected["feature_columns"],
        feature_profile=selected["feature_profile"],
        model_parameters=model_parameters,
        thresholds=thresholds,
        model_state_fingerprint=model_state_fingerprint,
        data_start=records[0]["timestamp"],
        data_end=records[-1]["timestamp"],
    )
    payload = {
        "format_version": 4,
        "model": selected["model"],
        "model_scope": model_scope,
        "artifact_fingerprint": artifact_fingerprint,
        "model_state_fingerprint": model_state_fingerprint,
        "return_model_state_fingerprint": return_model_state_fingerprint,
        "feature_columns": selected["feature_columns"],
        "feature_profile": selected["feature_profile"],
        "feature_statistics": selected["feature_statistics"],
        "expected_return_by_class": _expected_return_by_class(train_records),
        "expected_gross_return_by_class": _expected_gross_return_by_class(train_records),
        "return_models": selected.get("return_models") or {},
        "calibration": selected["calibration_metrics"],
        "feature_selection": selected["feature_selection"],
        "data_partitions": {name: len(items) for name, items in partitions.items()},
        "abstention": thresholds,
        "metrics": metrics,
        "holdout_metrics": holdout_metrics,
        "walk_forward_metrics": (walk_forward.get("summary") or {}) if walk_forward.get("status") == "completed" else {},
        "paper_metrics": paper_metrics,
        "regime_metrics": regime_metrics,
        "model_parameters": model_parameters,
        "training_data_range": {"start": records[0]["timestamp"], "end": records[-1]["timestamp"]},
        "candidate_models": selected["candidate_models"],
        "experiment_context": experiment_context or {},
    }
    _dump_model(payload, path)

    registry = ModelRegistry(database, settings)
    parent_champion = registry.get_champion(model_scope)
    registry.register_candidate(
        model_version=model_version,
        model_type=selected["model_type"],
        path=str(path),
        feature_columns=selected["feature_columns"],
        metrics=metrics,
        training_start=training_start,
        training_end=utc_now(),
        model_scope=model_scope,
        feature_profile=selected["feature_profile"],
        model_parameters=model_parameters,
        thresholds=thresholds,
        artifact_fingerprint=artifact_fingerprint,
        training_data_start=records[0]["timestamp"],
        training_data_end=records[-1]["timestamp"],
        parent_champion_version=parent_champion.get("model_version") if parent_champion else None,
    )
    if allow_promotion:
        promoted, reason = registry.promote_if_better(model_version, metrics, model_scope=model_scope)
    else:
        promoted, reason = False, "shadow candidate retained; automatic promotion disabled for offline loop"
    result = {
        "model_version": model_version,
        "model_type": selected["model_type"],
        "model_scope": model_scope,
        "artifact_fingerprint": artifact_fingerprint,
        "feature_profile": selected["feature_profile"],
        "path": str(path),
        "samples": len(records),
        "train_samples": len(train_records),
        "validation_samples": len(test_records),
        "calibration_samples": len(calibration_records),
        "threshold_samples": len(threshold_records),
        "features": len(selected["feature_columns"]),
        "class_counts": dict(Counter(labels)),
        "metrics": metrics,
        "holdout_metrics": holdout_metrics,
        "walk_forward_metrics": (walk_forward.get("summary") or {}) if walk_forward.get("status") == "completed" else {},
        "paper_metrics": paper_metrics,
        "regime_metrics": regime_metrics,
        "promotion_metrics": metrics,
        "walk_forward": walk_forward,
        "promoted": promoted,
        "registry_reason": reason,
        "optimized_thresholds": {
            "minimum_confidence": selected["minimum_confidence"],
            "minimum_margin": selected["minimum_margin"],
            "tuning_metrics": selected["threshold_metrics"],
        },
        "model_parameters": model_parameters,
        "calibration": selected["calibration_metrics"],
        "feature_selection": selected["feature_selection"],
        "data_partitions": {name: len(items) for name, items in partitions.items()},
        "experiment_context": experiment_context or {},
    }
    manifest_dir = model_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{model_version}.json"
    manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    result["manifest_path"] = str(manifest_path)
    return result


def _dump_model(payload: dict[str, Any], path: Path) -> None:
    try:
        import joblib

        joblib.dump(payload, path)
    except Exception:
        with path.open("wb") as file_handle:
            pickle.dump(payload, file_handle)


def _train_latency_aware_candidate_models(
    settings: Settings,
    train_records: list[dict[str, Any]],
    calibration_records: list[dict[str, Any]],
    threshold_records: list[dict[str, Any]],
    test_records: list[dict[str, Any]],
    *,
    experiment_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.naive_bayes import GaussianNB
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("scikit-learn is required to train candidate models.") from exc

    all_columns = sorted({key for record in train_records for key in record["features"]})
    fast_columns = [column for column in all_columns if any(term in column.lower() for term in FAST_FEATURE_TERMS)]
    model_scope = model_scope_from_context(experiment_context)
    if model_scope == "entry:fast_microstructure" and len(fast_columns) >= 5:
        profiles = [("fast", fast_columns, 96)]
    else:
        profiles = [("context", all_columns, 128)]
    search_round = max(0, int((experiment_context or {}).get("search_round", 0) or 0))
    random_seed = 7 + search_round * 101
    logistic_c_values = [0.05, 0.10, 0.25, 0.50, 1.0, 2.0, 5.0, 10.0]
    logistic_c = logistic_c_values[search_round % len(logistic_c_values)]
    forest_estimators = 80 + (search_round % 9) * 20
    forest_depth = 4 + (search_round % 7)
    forest_leaf = 4 + (search_round % 10) * 2
    boost_estimators = 40 + (search_round % 10) * 10
    boost_depth = 1 + (search_round % 3)
    boost_rate = [0.03, 0.05, 0.08, 0.10][search_round % 4]
    factories = [
        (
            "logistic_regression",
            lambda: make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=1_000, class_weight="balanced", random_state=random_seed, C=logistic_c),
            ),
        ),
        ("gaussian_nb", GaussianNB),
        (
            "random_forest",
            lambda: RandomForestClassifier(
                n_estimators=forest_estimators,
                max_depth=forest_depth,
                min_samples_leaf=forest_leaf,
                random_state=random_seed,
                class_weight="balanced_subsample",
                n_jobs=1,
            ),
        ),
        (
            "gradient_boosting",
            lambda: GradientBoostingClassifier(
                n_estimators=boost_estimators,
                max_depth=boost_depth,
                learning_rate=boost_rate,
                random_state=random_seed,
            ),
        ),
    ]
    trained: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for profile_name, raw_columns, maximum_features in profiles:
        columns, feature_selection = select_stable_features(
            train_records,
            raw_columns,
            maximum_features=maximum_features,
            maximum_missing_fraction=settings.ml_max_missing_feature_fraction,
        )
        if len(columns) < 5:
            continue
        matrix_test = _matrix(test_records, columns)
        y_test = [record["label"] for record in test_records]
        outcomes_test = [record["outcome"] for record in test_records]
        for model_name, factory in factories:
            try:
                fit_started = time.perf_counter()
                model, calibration_metrics = _fit_with_time_calibration(
                    factory(), train_records, calibration_records, columns
                )
                return_models = _fit_return_models(train_records, columns, random_seed=random_seed)
                fit_seconds = time.perf_counter() - fit_started
                tuning_probabilities = _probabilities_in_class_order(
                    model.predict_proba(_matrix(threshold_records, columns)),
                    list(model.classes_),
                )
                gross_returns = _expected_gross_return_by_class(train_records)
                tuning_expected_returns = _predict_expected_returns(
                    return_models,
                    _matrix(threshold_records, columns),
                    fallback=gross_returns,
                )
                threshold_metrics = optimize_policy_thresholds(
                    tuning_probabilities,
                    [record["outcome"] for record in threshold_records],
                    classes=CLASSES,
                    minimum_trades=max(10, int(len(threshold_records) * 0.01)),
                    expected_returns=tuning_expected_returns,
                    expected_costs=[float(record["outcome"].get("spread_cost", 0.0) or 0.0) for record in threshold_records],
                )
                predict_started = time.perf_counter()
                probabilities = model.predict_proba(matrix_test)
                predict_seconds = time.perf_counter() - predict_started
                probability_rows = _probabilities_in_class_order(probabilities, list(model.classes_))
                predictions = policy_predictions(
                    probability_rows,
                    classes=CLASSES,
                    minimum_confidence=float(threshold_metrics["minimum_confidence"]),
                    minimum_margin=float(threshold_metrics["minimum_margin"]),
                )
                predictions = _edge_filter_predictions(
                    predictions,
                    _predict_expected_returns(return_models, matrix_test, fallback=gross_returns),
                    [record["outcome"] for record in test_records],
                    float(threshold_metrics.get("minimum_expected_edge", 0.0) or 0.0),
                )
                metrics = classification_metrics(y_test, predictions, probability_rows, CLASSES)
                metrics.update(trading_metrics(predictions, outcomes_test))
                metrics["fit_seconds"] = fit_seconds
                metrics["inference_latency_ms"] = predict_seconds / max(len(matrix_test), 1) * 1_000
                metrics["latency_target_ms"] = settings.ml_max_inference_latency_ms
                metrics["selection_score"] = _selection_score(metrics, settings)
                trained.append(
                    {
                        "model_type": f"{profile_name}_{model_name}",
                        "feature_profile": profile_name,
                        "feature_columns": columns,
                        "model": model,
                        "metrics": metrics,
                        "minimum_confidence": threshold_metrics["minimum_confidence"],
                        "minimum_margin": threshold_metrics["minimum_margin"],
                        "threshold_metrics": threshold_metrics,
                        "calibration_metrics": calibration_metrics,
                        "feature_selection": feature_selection,
                        "expected_gross_return_by_class": gross_returns,
                        "return_models": return_models,
                    }
                )
            except Exception as exc:
                last_error = exc
    if not trained:
        raise RuntimeError(f"Candidate model training failed: {last_error}") from last_error
    selected = max(trained, key=lambda item: float(item["metrics"].get("selection_score", -math.inf)))
    selected["candidate_models"] = [
        {
            "model_type": item["model_type"],
            "feature_profile": item["feature_profile"],
            "metrics": item["metrics"],
            "minimum_confidence": item["minimum_confidence"],
            "minimum_margin": item["minimum_margin"],
            "threshold_metrics": item["threshold_metrics"],
            "calibration_metrics": item["calibration_metrics"],
            "feature_selection": item["feature_selection"],
            "model_parameters": _model_parameters(item["model"].base_model),
        }
        for item in sorted(trained, key=lambda item: item["metrics"]["selection_score"], reverse=True)
    ]
    selected["feature_statistics"] = _feature_statistics(train_records, selected["feature_columns"])
    return selected


def _model_parameters(model: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in model.get_params(deep=True).items():
        if value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = repr(value)
    return result


def _fit_with_time_calibration(
    base_model: Any,
    fit_records: list[dict[str, Any]],
    calibration_records: list[dict[str, Any]],
    columns: list[str],
) -> tuple[ProbabilityCalibratedModel, dict[str, Any]]:
    balanced_fit_records = _balanced_training_records(fit_records)
    base_model.fit(_matrix(balanced_fit_records, columns), [record["label"] for record in balanced_fit_records])
    calibrator: NaturalFrequencyCalibrator | None = None
    metrics: dict[str, Any] = {"method": "identity", "samples": 0.0}
    if calibration_records and len(set(record["label"] for record in calibration_records)) >= 2:
        raw = _align_probabilities(
            base_model.predict_proba(_matrix(calibration_records, columns)),
            list(base_model.classes_),
            CLASSES,
        )
        calibrator, metrics = fit_natural_frequency_calibrator(
            raw, [record["label"] for record in calibration_records], CLASSES
        )
    return ProbabilityCalibratedModel(base_model, calibrator, CLASSES), metrics


def _balanced_training_records(records: list[dict[str, Any]], *, majority_ratio: int = 2) -> list[dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = {label: [] for label in CLASSES}
    for record in records:
        by_label.setdefault(str(record["label"]), []).append(record)
    non_empty = [items for items in by_label.values() if items]
    if len(non_empty) < 2:
        return records
    minority = min(len(items) for items in non_empty)
    target = max(minority, min(max(len(items) for items in non_empty), minority * majority_ratio))
    balanced: list[dict[str, Any]] = []
    for label in CLASSES:
        items = by_label.get(label) or []
        if not items:
            continue
        if len(items) >= target:
            step = len(items) / target
            balanced.extend(items[min(int(index * step), len(items) - 1)] for index in range(target))
        else:
            balanced.extend(items[index % len(items)] for index in range(target))
    return sorted(balanced, key=lambda item: item["timestamp"])


def _evaluate_subset(selected: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "sample_count": 0.0,
            "trade_count": 0.0,
            "profit_factor": 0.0,
            "net_return": 0.0,
            "average_pnl_per_trade": 0.0,
        }
    model = selected["model"]
    probabilities = _probabilities_in_class_order(
        model.predict_proba(_matrix(records, selected["feature_columns"])),
        list(model.classes_),
    )
    predictions = policy_predictions(
        probabilities,
        classes=CLASSES,
        minimum_confidence=float(selected["minimum_confidence"]),
        minimum_margin=float(selected["minimum_margin"]),
    )
    predictions = _edge_filter_predictions(
        predictions,
        _predict_expected_returns(
            selected.get("return_models") or {},
            _matrix(records, selected["feature_columns"]),
            fallback=selected.get("expected_gross_return_by_class") or {},
        ),
        [record["outcome"] for record in records],
        float(selected.get("threshold_metrics", {}).get("minimum_expected_edge", 0.0) or 0.0),
    )
    metrics = classification_metrics([record["label"] for record in records], predictions, probabilities, CLASSES)
    metrics.update(trading_metrics(predictions, [record["outcome"] for record in records]))
    metrics["sample_count"] = float(len(records))
    return metrics


def _evaluate_regimes(selected: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        regime = str(record.get("regime") or "unknown")
        grouped.setdefault(regime, []).append(record)
    return {regime: _evaluate_subset(selected, items) for regime, items in grouped.items() if len(items) >= 5}


def _artifact_fingerprint(
    *,
    model_scope: str,
    feature_columns: list[str],
    feature_profile: str,
    model_parameters: dict[str, Any],
    thresholds: dict[str, Any],
    model_state_fingerprint: str,
    data_start: Any,
    data_end: Any,
) -> str:
    payload = {
        "model_scope": model_scope,
        "feature_columns": feature_columns,
        "feature_profile": feature_profile,
        "model_parameters": model_parameters,
        "thresholds": thresholds,
        "model_state_fingerprint": model_state_fingerprint,
        "training_data_start": str(data_start),
        "training_data_end": str(data_end),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _model_state_fingerprint(model: Any) -> str:
    return hashlib.sha256(pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()


def _purged_holdout_split(
    records: list[dict[str, Any]],
    *,
    test_fraction: float,
    purge_minutes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    split = min(max(1, int(len(records) * (1.0 - test_fraction))), len(records) - 1)
    test_start = records[split]["timestamp"]
    purge_before = test_start.timestamp() - max(0, purge_minutes) * 60
    train = [record for record in records[:split] if record["timestamp"].timestamp() < purge_before]
    return train, records[split:]


def _matrix(records: list[dict[str, Any]], columns: list[str]) -> list[list[float]]:
    return [[float(record["features"].get(column, 0.0)) for column in columns] for record in records]


def _align_probabilities(probabilities: Any, source_classes: Sequence[str], target_classes: Sequence[str]):
    source_index = {label: index for index, label in enumerate(source_classes)}
    return [
        [float(row[source_index[label]]) if label in source_index else 0.0 for label in target_classes]
        for row in probabilities
    ]


def _probabilities_in_class_order(probabilities: Any, classes: Sequence[str]) -> list[list[float]]:
    return _align_probabilities(probabilities, classes, CLASSES)


def _feature_statistics(records: list[dict[str, Any]], columns: list[str]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for column in columns:
        values = [float(record["features"].get(column, 0.0)) for record in records]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        result[column] = {
            "mean": mean,
            "std": math.sqrt(variance),
            "minimum": min(values),
            "maximum": max(values),
            "missing_fraction": sum(column not in record["features"] for record in records) / len(records),
        }
    return result


def _expected_return_by_class(records: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {label: [] for label in CLASSES}
    for record in records:
        label = str(record["label"])
        if label == "long_good":
            values[label].append(float(record["outcome"].get("net_return_long", 0.0) or 0.0))
        elif label == "short_good":
            values[label].append(float(record["outcome"].get("net_return_short", 0.0) or 0.0))
        else:
            values[label].append(0.0)
    return {label: sum(items) / max(len(items), 1) for label, items in values.items()}


def _expected_gross_return_by_class(records: list[dict[str, Any]]) -> dict[str, float]:
    net = _expected_return_by_class(records)
    costs = [float(record["outcome"].get("spread_cost", 0.0) or 0.0) for record in records]
    average_cost = sum(costs) / max(len(costs), 1)
    return {
        "long_good": max(0.0, net.get("long_good", 0.0) + average_cost),
        "short_good": max(0.0, net.get("short_good", 0.0) + average_cost),
        "no_trade": 0.0,
    }


def _edge_filter_predictions(
    predictions: Sequence[str],
    expected_gross_returns: dict[str, float] | list[dict[str, float]],
    outcomes: Sequence[dict[str, Any]],
    minimum_edge: float,
) -> list[str]:
    filtered: list[str] = []
    expected_rows = (
        expected_gross_returns
        if isinstance(expected_gross_returns, list)
        else [expected_gross_returns] * len(predictions)
    )
    for prediction, outcome, expected in zip(predictions, outcomes, expected_rows):
        cost = float(outcome.get("spread_cost", 0.0) or 0.0)
        edge = float(expected.get(prediction, 0.0) or 0.0) - cost
        filtered.append(prediction if prediction != "no_trade" and edge > minimum_edge else "no_trade")
    return filtered


def _fit_return_models(
    records: list[dict[str, Any]], columns: list[str], *, random_seed: int
) -> dict[str, Any]:
    """Fit direction-specific gross-return regressors on the fit partition only."""

    from sklearn.ensemble import RandomForestRegressor

    matrix = _matrix(records, columns)
    raw_returns = [float(record["outcome"].get("raw_forward_return", 0.0) or 0.0) for record in records]
    result: dict[str, Any] = {}
    for label, sign, offset in (("long_good", 1.0, 1), ("short_good", -1.0, 2)):
        regressor = RandomForestRegressor(
            n_estimators=80,
            max_depth=6,
            min_samples_leaf=8,
            random_state=random_seed + offset,
            n_jobs=1,
        )
        regressor.fit(matrix, [sign * value for value in raw_returns])
        result[label] = regressor
    return result


def _predict_expected_returns(
    models: dict[str, Any], matrix: list[list[float]], *, fallback: dict[str, float]
) -> list[dict[str, float]]:
    if not matrix:
        return []
    values: dict[str, list[float]] = {}
    for label in ("long_good", "short_good"):
        model = models.get(label)
        values[label] = (
            [float(value) for value in model.predict(matrix)]
            if model is not None
            else [float(fallback.get(label, 0.0) or 0.0)] * len(matrix)
        )
    return [
        {"long_good": values["long_good"][index], "short_good": values["short_good"][index]}
        for index in range(len(matrix))
    ]


def _partition_training_records(
    records: list[dict[str, Any]], *, purge_minutes: int
) -> dict[str, list[dict[str, Any]]]:
    timestamps = [record["timestamp"].timestamp() for record in records]
    minimum = max(10, min(25, len(records) // 8))
    split = chronological_partitions(
        timestamps,
        purge_seconds=max(0, purge_minutes) * 60,
        minimum_partition_size=minimum,
    )
    return {
        "train": [records[index] for index in split.train],
        "calibration": [records[index] for index in split.calibration],
        "threshold": [records[index] for index in split.threshold],
        "holdout": [records[index] for index in split.holdout],
    }


def _selection_score(metrics: dict[str, Any], settings: Settings) -> float:
    balanced_accuracy = float(metrics.get("balanced_accuracy", 0.0) or 0.0)
    macro_f1 = float(metrics.get("macro_f1", 0.0) or 0.0)
    expectancy = float(metrics.get("average_pnl_per_trade", 0.0) or 0.0)
    profit_factor = min(float(metrics.get("profit_factor", 0.0) or 0.0), 3.0) / 3.0
    drawdown = float(metrics.get("max_drawdown", 0.0) or 0.0)
    calibration_error = float(metrics.get("expected_calibration_error", 1.0) or 1.0)
    latency_ms = float(metrics.get("inference_latency_ms", 0.0) or 0.0)
    score = balanced_accuracy * 0.25 + macro_f1 * 0.20 + profit_factor * 0.20
    score += max(-0.20, min(expectancy * 100, 0.20))
    score -= min(drawdown * 10, 0.25)
    score -= min(calibration_error, 0.25)
    if settings.enable_latency_aware_ml:
        target = max(settings.ml_max_inference_latency_ms, 0.001)
        score -= max(0.0, latency_ms - target) / target * 0.10
    return score
