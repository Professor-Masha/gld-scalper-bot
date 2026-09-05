from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


@dataclass(slots=True, frozen=True)
class ChronologicalPartitions:
    """Leakage-resistant partitions used in order, never shuffled across time."""

    train: np.ndarray
    calibration: np.ndarray
    threshold: np.ndarray
    holdout: np.ndarray


def chronological_partitions(
    timestamps: Sequence[float],
    *,
    fractions: tuple[float, float, float, float] = (0.60, 0.15, 0.10, 0.15),
    purge_seconds: float = 0.0,
    minimum_partition_size: int = 25,
) -> ChronologicalPartitions:
    """Split ordered observations and purge overlapping label horizons at boundaries."""

    values = np.asarray(timestamps, dtype=np.float64)
    count = len(values)
    if count < minimum_partition_size * 4:
        raise RuntimeError(
            f"chronological training requires at least {minimum_partition_size * 4} samples; found {count}"
        )
    if any(not math.isfinite(value) or value <= 0 for value in fractions) or not math.isclose(sum(fractions), 1.0):
        raise ValueError("chronological partition fractions must be positive and sum to one")
    first = max(minimum_partition_size, int(count * fractions[0]))
    second = max(first + minimum_partition_size, int(count * sum(fractions[:2])))
    third = max(second + minimum_partition_size, int(count * sum(fractions[:3])))
    positions = np.arange(count, dtype=np.int64)
    partitions = [positions[:first], positions[first:second], positions[second:third], positions[third:]]
    for index in range(1, len(partitions)):
        previous = partitions[index - 1]
        current = partitions[index]
        if len(previous) and len(current) and purge_seconds > 0:
            current = current[values[current] > values[previous[-1]] + purge_seconds]
            partitions[index] = current
    if min(map(len, partitions)) < minimum_partition_size:
        raise RuntimeError("chronological train/calibration/threshold/holdout split is too small after purge")
    return ChronologicalPartitions(*partitions)


class NaturalFrequencyCalibrator:
    """Multiclass calibration fitted on untouched, naturally distributed observations."""

    def __init__(self, estimator: Any | None, classes: Sequence[str], method: str = "identity") -> None:
        self.estimator = estimator
        self.classes_ = list(classes)
        self.method = method

    def predict_proba(self, raw_probabilities: Sequence[Sequence[float]]) -> np.ndarray:
        raw = _normalize(raw_probabilities)
        if self.estimator is None:
            return raw
        calibrated = self.estimator.predict_proba(_logit_features(raw))
        return _align(calibrated, list(self.estimator.classes_), self.classes_)


def fit_natural_frequency_calibrator(
    raw_probabilities: Sequence[Sequence[float]],
    labels: Sequence[str],
    classes: Sequence[str],
) -> tuple[NaturalFrequencyCalibrator, dict[str, float | str]]:
    """Select identity or unweighted multinomial calibration by proper scoring rules."""

    raw = _normalize(raw_probabilities)
    identity = NaturalFrequencyCalibrator(None, classes)
    candidates: list[NaturalFrequencyCalibrator] = [identity]
    if len(labels) >= 60 and len(set(labels)) >= 2:
        from sklearn.linear_model import LogisticRegression

        estimator = LogisticRegression(max_iter=1_000, class_weight=None, random_state=19)
        estimator.fit(_logit_features(raw), list(labels))
        candidates.append(NaturalFrequencyCalibrator(estimator, classes, method="multinomial_logit"))
    scored = []
    for candidate in candidates:
        probabilities = candidate.predict_proba(raw)
        metrics = probability_scores(labels, probabilities, classes)
        scored.append((metrics["log_loss"] + metrics["brier_score"], candidate, metrics))
    _, winner, metrics = min(scored, key=lambda item: item[0])
    return winner, {"method": winner.method, **metrics, "samples": float(len(labels))}


def probability_scores(
    labels: Sequence[str], probabilities: Sequence[Sequence[float]], classes: Sequence[str]
) -> dict[str, float]:
    values = _normalize(probabilities)
    class_index = {label: index for index, label in enumerate(classes)}
    log_losses: list[float] = []
    briers: list[float] = []
    confidence_rows: list[tuple[float, bool]] = []
    for label, row in zip(labels, values):
        actual = class_index.get(str(label))
        if actual is None:
            continue
        log_losses.append(-math.log(max(float(row[actual]), 1e-15)))
        target = np.zeros(len(classes), dtype=np.float64)
        target[actual] = 1.0
        briers.append(float(np.mean((row - target) ** 2)))
        predicted = int(np.argmax(row))
        confidence_rows.append((float(row[predicted]), predicted == actual))
    return {
        "log_loss": float(np.mean(log_losses)) if log_losses else 0.0,
        "brier_score": float(np.mean(briers)) if briers else 0.0,
        "expected_calibration_error": expected_calibration_error(confidence_rows),
    }


def expected_calibration_error(rows: Sequence[tuple[float, bool]], bins: int = 15) -> float:
    if not rows:
        return 0.0
    error = 0.0
    for bucket in range(bins):
        lower, upper = bucket / bins, (bucket + 1) / bins
        selected = [row for row in rows if lower <= row[0] <= upper] if bucket == bins - 1 else [
            row for row in rows if lower <= row[0] < upper
        ]
        if selected:
            confidence = sum(row[0] for row in selected) / len(selected)
            accuracy = sum(row[1] for row in selected) / len(selected)
            error += len(selected) / len(rows) * abs(confidence - accuracy)
    return error


def _normalize(probabilities: Sequence[Sequence[float]]) -> np.ndarray:
    values = np.asarray(probabilities, dtype=np.float64)
    values = np.clip(values, 1e-9, None)
    return values / np.maximum(values.sum(axis=1, keepdims=True), 1e-12)


def _logit_features(probabilities: np.ndarray) -> np.ndarray:
    return np.log(np.clip(probabilities, 1e-9, 1.0))


def _align(values: np.ndarray, source: Sequence[str], target: Sequence[str]) -> np.ndarray:
    result = np.zeros((len(values), len(target)), dtype=np.float64)
    source_index = {label: index for index, label in enumerate(source)}
    for target_index, label in enumerate(target):
        if label in source_index:
            result[:, target_index] = values[:, source_index[label]]
    return _normalize(result)
