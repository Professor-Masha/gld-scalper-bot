from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any, Sequence


CLASSES = ("long_good", "short_good", "no_trade")


def policy_predictions(
    probabilities: Sequence[Sequence[float]],
    *,
    classes: Sequence[str] = CLASSES,
    minimum_confidence: float = 0.0,
    minimum_margin: float = 0.0,
) -> list[str]:
    predictions: list[str] = []
    for row in probabilities:
        values = [float(value) for value in row]
        if not values:
            predictions.append("no_trade")
            continue
        ranked = sorted(range(len(values)), key=values.__getitem__, reverse=True)
        best = ranked[0]
        confidence = values[best]
        margin = confidence - (values[ranked[1]] if len(ranked) > 1 else 0.0)
        label = str(classes[best])
        if label == "no_trade" or confidence < minimum_confidence or margin < minimum_margin:
            predictions.append("no_trade")
        else:
            predictions.append(label)
    return predictions


def optimize_policy_thresholds(
    probabilities: Sequence[Sequence[float]],
    outcomes: Sequence[dict[str, Any]],
    *,
    classes: Sequence[str] = CLASSES,
    minimum_trades: int = 25,
    expected_returns: Sequence[dict[str, float]] | None = None,
    expected_costs: Sequence[float] | None = None,
    uncertainties: Sequence[float] | None = None,
    minimum_edge_values: Sequence[float] = (0.0, 0.00005, 0.0001, 0.0002),
) -> dict[str, float]:
    best: dict[str, float] | None = None
    confidence_values = [0.40, 0.46, 0.52, 0.58, 0.64, 0.70]
    margin_values = [0.00, 0.04, 0.08, 0.12, 0.16]
    for confidence in confidence_values:
        for margin in margin_values:
            for minimum_edge in minimum_edge_values:
                if expected_returns is None:
                    minimum_edge = 0.0
                predictions = policy_predictions(
                    probabilities,
                    classes=classes,
                    minimum_confidence=confidence,
                    minimum_margin=margin,
                )
                if expected_returns is not None:
                    predictions = _apply_expected_edge(
                        predictions,
                        expected_returns,
                        expected_costs or [0.0] * len(predictions),
                        uncertainties or [0.0] * len(predictions),
                        minimum_edge,
                    )
                metrics = trading_metrics(predictions, outcomes)
                trade_count = int(metrics["trade_count"])
                if trade_count >= minimum_trades:
                    score = (
                        float(metrics["average_pnl_per_trade"]) * 100.0
                        + min(float(metrics["profit_factor"]), 3.0) * 0.08
                        + float(metrics["win_rate"]) * 0.05
                        - float(metrics["max_drawdown"]) * 0.50
                    )
                    candidate = {
                        "minimum_confidence": confidence,
                        "minimum_margin": margin,
                        "minimum_expected_edge": minimum_edge,
                        "threshold_score": score,
                        **metrics,
                    }
                    if best is None or candidate["threshold_score"] > best["threshold_score"]:
                        best = candidate
                if expected_returns is None:
                    break
    if best is None:
        return {
            "minimum_confidence": 0.70,
            "minimum_margin": 0.16,
            "minimum_expected_edge": 0.0001,
            "threshold_score": -1.0,
            "trade_count": 0.0,
        }
    return best


def classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    probabilities: Sequence[Sequence[float]] | None = None,
    classes: Sequence[str] = CLASSES,
) -> dict[str, float]:
    if not y_true:
        return {
            "accuracy": 0.0,
            "balanced_accuracy": 0.0,
            "no_trade_accuracy": 0.0,
            "macro_f1": 0.0,
            "log_loss": 0.0,
            "brier_score": 0.0,
            "expected_calibration_error": 0.0,
        }
    pairs = list(zip(y_true, y_pred))
    counts = Counter(y_pred)
    recalls: list[float] = []
    f1_scores: list[float] = []
    metrics: dict[str, float] = {
        "total_samples": float(len(y_true)),
        "accuracy": sum(actual == predicted for actual, predicted in pairs) / len(y_true),
        "long_predictions": float(counts.get("long_good", 0)),
        "short_predictions": float(counts.get("short_good", 0)),
        "no_trade_predictions": float(counts.get("no_trade", 0)),
    }
    for label in classes:
        true_positive = sum(actual == label and predicted == label for actual, predicted in pairs)
        false_positive = sum(actual != label and predicted == label for actual, predicted in pairs)
        false_negative = sum(actual == label and predicted != label for actual, predicted in pairs)
        total_actual = true_positive + false_negative
        precision = true_positive / max(true_positive + false_positive, 1)
        recall = true_positive / max(total_actual, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        recalls.append(recall)
        f1_scores.append(f1)
        prefix = label.replace("_good", "")
        metrics[f"{prefix}_precision"] = precision
        metrics[f"{prefix}_recall"] = recall
        metrics[f"{prefix}_f1"] = f1
    metrics["balanced_accuracy"] = sum(recalls) / len(recalls)
    metrics["macro_f1"] = sum(f1_scores) / len(f1_scores)
    metrics["no_trade_accuracy"] = metrics.get("no_trade_recall", 0.0)
    if probabilities is not None and len(probabilities) > 0:
        probability_metrics = _probability_metrics(y_true, probabilities, classes)
        metrics.update(probability_metrics)
        metrics.update(selective_classification_metrics(y_true, y_pred, probabilities, classes))
    else:
        metrics.update({"log_loss": 0.0, "brier_score": 0.0, "expected_calibration_error": 0.0})
    return metrics


def selective_classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    probabilities: Sequence[Sequence[float]],
    classes: Sequence[str] = CLASSES,
) -> dict[str, float]:
    """Measure reliability among accepted directional predictions, not headline accuracy."""

    accepted = [index for index, value in enumerate(y_pred) if value != "no_trade"]
    correct = sum(y_true[index] == y_pred[index] for index in accepted)
    directional_actual = {"long_good", "short_good"}
    directional_total = sum(value in directional_actual for value in y_true)
    covered_directional = sum(y_true[index] in directional_actual for index in accepted)
    result = {
        "abstention_rate": 1.0 - len(accepted) / max(len(y_pred), 1),
        "selective_accuracy": correct / max(len(accepted), 1),
        "directional_coverage": covered_directional / max(directional_total, 1),
        "accepted_prediction_count": float(len(accepted)),
    }
    try:
        from sklearn.metrics import average_precision_score

        for label in ("long_good", "short_good"):
            class_index = list(classes).index(label)
            targets = [1 if actual == label else 0 for actual in y_true]
            scores = [float(row[class_index]) for row in probabilities]
            result[f"{label.replace('_good', '')}_pr_auc"] = float(average_precision_score(targets, scores))
    except (ImportError, ValueError):
        result.update({"long_pr_auc": 0.0, "short_pr_auc": 0.0})
    return result


def trading_metrics(
    predictions: Sequence[str],
    outcomes: Sequence[dict[str, Any]],
) -> dict[str, float]:
    returns: list[float] = []
    for prediction, outcome in zip(predictions, outcomes):
        if prediction == "long_good":
            returns.append(float(outcome.get("net_return_long", 0.0) or 0.0))
        elif prediction == "short_good":
            returns.append(float(outcome.get("net_return_short", 0.0) or 0.0))
        else:
            returns.append(0.0)
    traded = [value for prediction, value in zip(predictions, returns) if prediction != "no_trade"]
    gains = sum(value for value in traded if value > 0)
    losses = abs(sum(value for value in traded if value < 0))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    trade_count = len(traded)
    wins = sum(value > 0 for value in traded)
    losses_count = sum(value < 0 for value in traded)
    profit_factor = gains / losses if losses > 0 else (999.0 if gains > 0 else 0.0)
    confidence = bootstrap_return_confidence_interval(traded)
    return {
        "trade_count": float(trade_count),
        "trade_label_count": float(trade_count),
        "win_count": float(wins),
        "loss_count": float(losses_count),
        "win_rate": wins / max(trade_count, 1),
        "profit_factor": profit_factor,
        "gross_profit": gains,
        "gross_loss": losses,
        "net_return": sum(traded),
        "slippage_adjusted_return": sum(traded),
        "average_pnl_per_trade": sum(traded) / max(trade_count, 1),
        "max_drawdown": max_drawdown,
        "trade_coverage": trade_count / max(len(predictions), 1),
        **confidence,
    }


def bootstrap_return_confidence_interval(
    returns: Sequence[float], *, samples: int = 500, confidence: float = 0.95, seed: int = 73
) -> dict[str, float]:
    """Deterministic bootstrap interval for mean after-cost return."""

    values = [float(value) for value in returns]
    if len(values) < 2:
        point = values[0] if values else 0.0
        return {"average_return_ci_low": point, "average_return_ci_high": point}
    generator = random.Random(seed)
    means = sorted(sum(generator.choice(values) for _ in values) / len(values) for _ in range(samples))
    tail = (1.0 - confidence) / 2.0
    low = means[max(0, int(samples * tail))]
    high = means[min(samples - 1, int(samples * (1.0 - tail)))]
    return {"average_return_ci_low": low, "average_return_ci_high": high}


def _apply_expected_edge(
    predictions: Sequence[str],
    expected_returns: Sequence[dict[str, float]],
    expected_costs: Sequence[float],
    uncertainties: Sequence[float],
    minimum_edge: float,
) -> list[str]:
    result: list[str] = []
    for prediction, returns, cost, uncertainty in zip(predictions, expected_returns, expected_costs, uncertainties):
        key = "long_good" if prediction == "long_good" else "short_good"
        edge = float(returns.get(key, 0.0) or 0.0) - max(float(cost or 0.0), 0.0) - max(float(uncertainty or 0.0), 0.0)
        result.append(prediction if prediction != "no_trade" and edge > minimum_edge else "no_trade")
    return result


def validation_trade_metrics(
    labels_or_predictions: Sequence[str],
    outcomes: Sequence[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Backward-compatible wrapper; financial metrics require realized outcomes."""
    if outcomes is None:
        return {
            "trade_label_count": float(sum(label in {"long_good", "short_good"} for label in labels_or_predictions)),
            "no_trade_label_count": float(sum(label == "no_trade" for label in labels_or_predictions)),
            "profit_factor": 0.0,
            "average_pnl_per_trade": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "slippage_adjusted_return": 0.0,
            "net_return": 0.0,
            "trade_coverage": 0.0,
        }
    metrics = trading_metrics(labels_or_predictions, outcomes)
    metrics["no_trade_label_count"] = float(sum(label == "no_trade" for label in labels_or_predictions))
    return metrics


def _probability_metrics(
    y_true: Sequence[str],
    probabilities: Sequence[Sequence[float]],
    classes: Sequence[str],
) -> dict[str, float]:
    class_index = {label: index for index, label in enumerate(classes)}
    log_losses: list[float] = []
    brier_scores: list[float] = []
    confidence_rows: list[tuple[float, bool]] = []
    for actual, raw_probabilities in zip(y_true, probabilities):
        values = [max(0.0, float(value)) for value in raw_probabilities]
        total = sum(values) or 1.0
        values = [value / total for value in values]
        actual_index = class_index.get(actual)
        if actual_index is None or actual_index >= len(values):
            continue
        log_losses.append(-math.log(max(values[actual_index], 1e-15)))
        target = [1.0 if index == actual_index else 0.0 for index in range(len(classes))]
        brier_scores.append(sum((value - target[index]) ** 2 for index, value in enumerate(values)) / len(classes))
        predicted_index = max(range(len(values)), key=values.__getitem__)
        confidence_rows.append((values[predicted_index], predicted_index == actual_index))
    return {
        "log_loss": sum(log_losses) / max(len(log_losses), 1),
        "brier_score": sum(brier_scores) / max(len(brier_scores), 1),
        "expected_calibration_error": _expected_calibration_error(confidence_rows),
    }


def _expected_calibration_error(rows: Sequence[tuple[float, bool]], bins: int = 10) -> float:
    if not rows:
        return 0.0
    total = len(rows)
    error = 0.0
    for bucket in range(bins):
        lower = bucket / bins
        upper = (bucket + 1) / bins
        if bucket == bins - 1:
            selected = [row for row in rows if lower <= row[0] <= upper]
        else:
            selected = [row for row in rows if lower <= row[0] < upper]
        if not selected:
            continue
        confidence = sum(row[0] for row in selected) / len(selected)
        accuracy = sum(row[1] for row in selected) / len(selected)
        error += len(selected) / total * abs(accuracy - confidence)
    return error
