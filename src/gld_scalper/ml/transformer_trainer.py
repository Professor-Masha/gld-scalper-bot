from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import ensure_utc
from .model_registry import ModelRegistry
from .transformer_dataset import (
    LoadedSequenceArtifact,
    load_transformer_sequence_artifact,
    transformer_registry_scope,
)
from .transformer_model import HORIZONS_MINUTES, TransformerModelConfig, build_causal_transformer, require_torch


@dataclass(slots=True)
class TransformerTrainingOptions:
    d_model: int = 48
    num_layers: int = 2
    nhead: int = 4
    dim_feedforward: int = 128
    dropout: float = 0.10
    batch_size: int = 32
    epochs: int = 15
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    patience: int = 4
    walk_forward_folds: int = 3
    walk_forward_epochs: int = 4
    minimum_confidence: float = 0.58
    minimum_margin: float = 0.08
    export_onnx: bool = False
    random_seed: int = 73
    sequence_length_override: int | None = None
    window_seconds_override: int | None = None
    warm_start_manifest_path: str | None = None
    parent_model_version: str | None = None

    def validate(self) -> None:
        if self.batch_size < 1 or self.epochs < 1 or self.patience < 1:
            raise ValueError("batch size, epochs, and patience must be positive")
        if self.walk_forward_folds < 1 or self.walk_forward_epochs < 1:
            raise ValueError("walk-forward folds and epochs must be positive")
        if self.learning_rate <= 0 or self.weight_decay < 0:
            raise ValueError("learning rate and weight decay are invalid")
        if not 0.0 <= self.minimum_confidence <= 1.0 or not 0.0 <= self.minimum_margin <= 1.0:
            raise ValueError("abstention thresholds must be between zero and one")
        if self.sequence_length_override is not None and self.sequence_length_override < 2:
            raise ValueError("sequence length override must be at least two")
        if self.window_seconds_override is not None and self.window_seconds_override < 2:
            raise ValueError("window seconds override must be at least two")


class _WindowDataset:
    def __init__(
        self,
        artifact: LoadedSequenceArtifact,
        sample_positions: Sequence[int],
        mean: np.ndarray,
        std: np.ndarray,
    ) -> None:
        self.artifact = artifact
        self.sample_positions = np.asarray(sample_positions, dtype=np.int64)
        self.mean = mean.astype(np.float32)
        self.std = std.astype(np.float32)
        self.sequence_length = int(artifact.manifest["sequence_length"])
        self.window_seconds = float(artifact.manifest["window_seconds"])
        self.torch = require_torch()

    def __len__(self) -> int:
        return len(self.sample_positions)

    def __getitem__(self, item: int):
        sample_position = int(self.sample_positions[item])
        end = int(self.artifact.sample_end_indices[sample_position])
        cutoff = float(self.artifact.timestamps[end]) - self.window_seconds
        time_start = int(np.searchsorted(self.artifact.timestamps, cutoff, side="left"))
        start = max(time_start, end - self.sequence_length + 1, 0)
        raw = np.asarray(self.artifact.features[start : end + 1], dtype=np.float32)
        missing = ~np.isfinite(raw)
        normalized = (np.where(missing, self.mean, raw) - self.mean) / self.std
        length = len(raw)
        values = np.zeros((self.sequence_length, raw.shape[1]), dtype=np.float32)
        missing_mask = np.ones_like(values, dtype=np.bool_)
        valid_mask = np.zeros(self.sequence_length, dtype=np.bool_)
        session_mask = np.zeros(self.sequence_length, dtype=np.bool_)
        values[:length] = normalized
        missing_mask[:length] = missing
        valid_mask[:length] = True
        current_session = int(self.artifact.session_ids[end])
        session_mask[:length] = np.asarray(self.artifact.session_ids[start : end + 1]) == current_session
        return (
            self.torch.from_numpy(values),
            self.torch.from_numpy(missing_mask),
            self.torch.from_numpy(valid_mask),
            self.torch.from_numpy(session_mask),
            self.torch.tensor(int(self.artifact.labels[sample_position]), dtype=self.torch.long),
            self.torch.from_numpy(np.asarray(self.artifact.returns[sample_position], dtype=np.float32).copy()),
            self.torch.tensor(float(self.artifact.costs[sample_position]), dtype=self.torch.float32),
            self.torch.tensor(sample_position, dtype=self.torch.long),
        )


def train_transformer_candidate(
    database: Database,
    settings: Settings,
    *,
    artifact_path: str | Path,
    options: TransformerTrainingOptions | None = None,
) -> dict[str, Any]:
    options = options or TransformerTrainingOptions(
        minimum_confidence=settings.ml_min_confidence,
        minimum_margin=settings.ml_min_probability_margin,
    )
    options.validate()
    torch = require_torch()
    torch.manual_seed(options.random_seed)
    np.random.seed(options.random_seed)
    try:
        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    artifact = load_transformer_sequence_artifact(artifact_path)
    artifact.manifest = dict(artifact.manifest)
    if options.sequence_length_override is not None:
        artifact.manifest["sequence_length"] = min(
            int(options.sequence_length_override),
            int(artifact.manifest["sequence_length"]),
        )
    if options.window_seconds_override is not None:
        artifact.manifest["window_seconds"] = min(
            int(options.window_seconds_override),
            int(artifact.manifest["window_seconds"]),
        )
    sample_count = len(artifact.sample_end_indices)
    if sample_count < 300:
        raise RuntimeError(f"Transformer training needs at least 300 labeled sequences; found {sample_count}.")
    if len(set(np.asarray(artifact.labels).tolist())) < 2:
        raise RuntimeError("Transformer training labels need at least two classes.")

    train_positions, validation_positions, test_positions = _chronological_split(artifact)
    mean, std = _feature_scaler(artifact, train_positions)
    config = TransformerModelConfig(
        feature_count=int(artifact.manifest["feature_count"]),
        class_count=len(artifact.manifest["classes"]),
        sequence_length=int(artifact.manifest["sequence_length"]),
        d_model=options.d_model,
        nhead=options.nhead,
        num_layers=options.num_layers,
        dim_feedforward=options.dim_feedforward,
        dropout=options.dropout,
    )
    model = build_causal_transformer(config)
    warm_start = _load_compatible_warm_start(
        model,
        options.warm_start_manifest_path,
        config=config,
        feature_columns=list(artifact.manifest["feature_columns"]),
        classes=list(artifact.manifest["classes"]),
    )
    training = _fit_model(
        model,
        artifact,
        train_positions,
        validation_positions,
        mean,
        std,
        options,
        epochs=options.epochs,
    )
    temperature = _fit_temperature(model, artifact, validation_positions, mean, std, options.batch_size)
    model.set_temperature(temperature)
    holdout = _evaluate_model(model, artifact, test_positions, mean, std, options)
    baseline = _evaluate_recorded_baseline(artifact, test_positions, options)
    baseline_gate = _baseline_comparison(holdout, baseline)
    walk_forward = _walk_forward_validation(artifact, config, options)

    model_root = PROJECT_ROOT / "models" / settings.data_mode / "transformer"
    model_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    version = f"transformer-{artifact.manifest['scope']}-{stamp}"
    checkpoint_path = model_root / f"{version}.checkpoint.pt"
    script_path = model_root / f"{version}.torchscript.pt"
    manifest_path = model_root / f"{version}.json"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "config": config.as_dict(),
            "mean": mean.tolist(),
            "std": std.tolist(),
            "temperature": temperature,
            "feature_columns": artifact.manifest["feature_columns"],
            "classes": artifact.manifest["classes"],
            "dataset_fingerprint": artifact.manifest["fingerprint"],
        },
        checkpoint_path,
    )
    _export_torchscript(model, config, script_path)
    onnx_path, onnx_error = _export_onnx(model, config, model_root / f"{version}.onnx") if options.export_onnx else (None, None)
    latency = _measure_script_latency(script_path, config)

    aggregate = dict(walk_forward["aggregate"])
    metrics: dict[str, Any] = {
        **aggregate,
        "model_family": "causal_time_series_transformer",
        "scope": artifact.manifest["scope"],
        "sample_count": sample_count,
        "trade_label_count": aggregate.get("trade_label_count", holdout.get("trade_label_count", 0.0)),
        "walk_forward_completed": 1.0 if walk_forward["completed"] else 0.0,
        "walk_forward_fold_count": float(len(walk_forward["folds"])),
        "profitable_fold_ratio": walk_forward["profitable_fold_ratio"],
        "expected_calibration_error": holdout["expected_calibration_error"],
        "inference_latency_ms": latency["median_ms"],
        "inference_latency_p95_ms": latency["p95_ms"],
        "validated_regime_count": holdout["validated_regime_count"],
        "paper_trade_count": 0.0,
        "paper_profit_factor": 0.0,
        "requires_baseline_comparison": 1.0,
        "baseline_comparison_passed": 1.0 if baseline_gate["passed"] else 0.0,
        "holdout": holdout,
        "walk_forward": walk_forward,
        "random_forest_baseline": baseline,
        "baseline_comparison": baseline_gate,
        "training": training,
        "warm_start": warm_start,
    }
    artifact_fingerprint = _artifact_fingerprint(script_path, artifact.manifest["fingerprint"], config.as_dict(), mean, std)
    manifest = {
        "format_version": 1,
        "model_version": version,
        "model_family": "causal_time_series_transformer",
        "scope": artifact.manifest["scope"],
        "registry_scope": transformer_registry_scope(str(artifact.manifest["scope"])),
        "role": "paper_shadow",
        "shadow_only": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config.as_dict(),
        "feature_columns": artifact.manifest["feature_columns"],
        "classes": artifact.manifest["classes"],
        "window_seconds": artifact.manifest["window_seconds"],
        "horizons_minutes": list(HORIZONS_MINUTES),
        "mean": mean.tolist(),
        "std": std.tolist(),
        "temperature": temperature,
        "abstention": {
            "minimum_confidence": options.minimum_confidence,
            "minimum_margin": options.minimum_margin,
        },
        "dataset_path": str(Path(artifact_path).resolve()),
        "dataset_fingerprint": artifact.manifest["fingerprint"],
        "warm_start": warm_start,
        "parent_model_version": options.parent_model_version,
        "baseline_model_versions": artifact.manifest.get("baseline_model_versions", []),
        "checkpoint_path": str(checkpoint_path.resolve()),
        "torchscript_path": str(script_path.resolve()),
        "onnx_path": str(onnx_path.resolve()) if onnx_path else None,
        "onnx_export_error": onnx_error,
        "metrics": metrics,
        "artifact_fingerprint": artifact_fingerprint,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")

    registry = ModelRegistry(database, settings)
    registry.register_candidate(
        model_version=version,
        model_type=f"causal_transformer_{artifact.manifest['scope']}",
        path=str(manifest_path.resolve()),
        feature_columns=list(artifact.manifest["feature_columns"]),
        feature_profile=f"sequence:{artifact.manifest['scope']}:{config.sequence_length}",
        model_parameters=config.as_dict(),
        thresholds=manifest["abstention"],
        metrics=metrics,
        training_start=ensure_utc(artifact.manifest["start"]),
        training_end=ensure_utc(artifact.manifest["end"]),
        training_data_start=ensure_utc(artifact.manifest["start"]),
        training_data_end=ensure_utc(artifact.manifest["end"]),
        model_scope=manifest["registry_scope"],
        artifact_fingerprint=artifact_fingerprint,
        parent_champion_version=options.parent_model_version,
    )
    return {
        "status": "candidate_registered",
        "model_version": version,
        "registry_scope": manifest["registry_scope"],
        "manifest_path": str(manifest_path),
        "torchscript_path": str(script_path),
        "onnx_path": str(onnx_path) if onnx_path else None,
        "baseline_comparison_passed": baseline_gate["passed"],
        "holdout": holdout,
        "walk_forward": walk_forward,
        "inference_latency": latency,
        "promotion_ready": False,
        "promotion_blockers": _promotion_blockers(metrics, settings),
        "warm_start": warm_start,
    }


def _load_compatible_warm_start(
    model: Any,
    manifest_path: str | None,
    *,
    config: TransformerModelConfig,
    feature_columns: list[str],
    classes: list[str],
) -> dict[str, Any]:
    if not manifest_path:
        return {"loaded": False, "reason": "no compatible parent requested"}
    path = Path(manifest_path)
    if not path.is_file():
        return {"loaded": False, "reason": f"parent manifest missing: {path}"}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        parent_config = dict(manifest.get("config") or {})
        compatible = (
            list(manifest.get("feature_columns") or []) == feature_columns
            and list(manifest.get("classes") or []) == classes
            and all(parent_config.get(key) == value for key, value in config.as_dict().items())
        )
        if not compatible:
            return {"loaded": False, "reason": "parent feature profile or architecture differs"}
        torch = require_torch()
        checkpoint = torch.load(manifest["checkpoint_path"], map_location="cpu", weights_only=False)
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        return {
            "loaded": True,
            "parent_model_version": manifest.get("model_version"),
            "parent_manifest": str(path.resolve()),
        }
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return {"loaded": False, "reason": f"warm start rejected: {exc}"}


def _chronological_split(artifact: LoadedSequenceArtifact) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = len(artifact.sample_end_indices)
    train_end = max(1, int(count * 0.70))
    validation_end = max(train_end + 1, int(count * 0.85))
    positions = np.arange(count, dtype=np.int64)
    timestamps = artifact.timestamps[artifact.sample_end_indices]
    purge = max(HORIZONS_MINUTES) * 60
    train = positions[:train_end]
    validation = positions[train_end:validation_end]
    test = positions[validation_end:]
    if len(validation):
        validation = validation[timestamps[validation] > timestamps[train[-1]] + purge]
    if len(test) and len(validation):
        test = test[timestamps[test] > timestamps[validation[-1]] + purge]
    if min(len(train), len(validation), len(test)) < 30:
        raise RuntimeError("Chronological train/calibration/holdout split needs at least 30 samples in each partition after purge.")
    return train, validation, test


def _feature_scaler(artifact: LoadedSequenceArtifact, sample_positions: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    maximum_row = int(artifact.sample_end_indices[int(max(sample_positions))]) + 1
    feature_count = int(artifact.manifest["feature_count"])
    sums = np.zeros(feature_count, dtype=np.float64)
    squared = np.zeros(feature_count, dtype=np.float64)
    counts = np.zeros(feature_count, dtype=np.float64)
    for start in range(0, maximum_row, 50_000):
        chunk = np.asarray(artifact.features[start : min(maximum_row, start + 50_000)], dtype=np.float64)
        finite = np.isfinite(chunk)
        values = np.where(finite, chunk, 0.0)
        sums += values.sum(axis=0)
        squared += (values * values).sum(axis=0)
        counts += finite.sum(axis=0)
    mean = sums / np.maximum(counts, 1.0)
    variance = squared / np.maximum(counts, 1.0) - mean * mean
    std = np.sqrt(np.maximum(variance, 1e-8))
    return mean.astype(np.float32), std.astype(np.float32)


def _fit_model(
    model,
    artifact: LoadedSequenceArtifact,
    train_positions: Sequence[int],
    validation_positions: Sequence[int],
    mean: np.ndarray,
    std: np.ndarray,
    options: TransformerTrainingOptions,
    *,
    epochs: int,
) -> dict[str, Any]:
    torch = require_torch()
    train_dataset = _WindowDataset(artifact, train_positions, mean, std)
    validation_dataset = _WindowDataset(artifact, validation_positions, mean, std)
    generator = torch.Generator().manual_seed(options.random_seed)
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=options.batch_size,
        shuffle=True,
        num_workers=0,
        generator=generator,
    )
    validation_loader = torch.utils.data.DataLoader(
        validation_dataset,
        batch_size=options.batch_size,
        shuffle=False,
        num_workers=0,
    )
    counts = np.bincount(np.asarray(artifact.labels)[np.asarray(train_positions)], minlength=len(artifact.manifest["classes"]))
    weights = counts.sum() / np.maximum(counts * len(counts), 1)
    class_weights = torch.tensor(weights, dtype=torch.float32)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=options.learning_rate,
        weight_decay=options.weight_decay,
    )
    best_loss = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    stale_epochs = 0
    history: list[dict[str, float]] = []
    for epoch in range(epochs):
        model.train()
        train_losses: list[float] = []
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            loss = _batch_loss(model, batch, class_weights)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_losses.append(float(loss.detach()))
        validation_loss = _loader_loss(model, validation_loader, class_weights)
        history.append(
            {
                "epoch": float(epoch + 1),
                "train_loss": float(np.mean(train_losses)) if train_losses else float("inf"),
                "validation_loss": validation_loss,
            }
        )
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= options.patience:
                break
    model.load_state_dict(best_state)
    return {"epochs_completed": len(history), "best_validation_loss": best_loss, "history": history}


def _batch_loss(model, batch, class_weights):
    torch = require_torch()
    values, missing, valid, session, labels, target_returns, target_cost, _ = batch
    logits, expected_returns, expected_cost, log_variance = model(values, missing, valid, session)
    classification = torch.nn.functional.cross_entropy(logits, labels, weight=class_weights)
    finite = torch.isfinite(target_returns)
    if finite.any():
        squared = (expected_returns - torch.nan_to_num(target_returns, nan=0.0)) ** 2
        nll = 0.5 * (torch.exp(-log_variance) * squared + log_variance)
        return_loss = nll.masked_select(finite).mean()
    else:
        return_loss = classification.new_tensor(0.0)
    cost_loss = torch.nn.functional.smooth_l1_loss(expected_cost.squeeze(-1), target_cost.clamp_min(0.0))
    return classification + 0.50 * return_loss + 0.20 * cost_loss


def _loader_loss(model, loader, class_weights) -> float:
    torch = require_torch()
    model.eval()
    values: list[float] = []
    with torch.inference_mode():
        for batch in loader:
            values.append(float(_batch_loss(model, batch, class_weights)))
    return float(np.mean(values)) if values else float("inf")


def _fit_temperature(
    model,
    artifact: LoadedSequenceArtifact,
    positions: Sequence[int],
    mean: np.ndarray,
    std: np.ndarray,
    batch_size: int,
) -> float:
    logits, labels, *_ = _collect_outputs(model, artifact, positions, mean, std, batch_size)
    if not len(labels):
        return 1.0
    candidates = np.linspace(0.50, 3.00, num=101)
    losses = [_numpy_cross_entropy(logits / value, labels) for value in candidates]
    return float(candidates[int(np.argmin(losses))])


def _evaluate_model(
    model,
    artifact: LoadedSequenceArtifact,
    positions: Sequence[int],
    mean: np.ndarray,
    std: np.ndarray,
    options: TransformerTrainingOptions,
) -> dict[str, Any]:
    logits, labels, returns, costs, sample_positions, log_variance = _collect_outputs(
        model,
        artifact,
        positions,
        mean,
        std,
        options.batch_size,
    )
    probabilities = _softmax(logits)
    metrics = _metrics_from_probabilities(
        probabilities,
        labels,
        returns,
        costs,
        classes=list(artifact.manifest["classes"]),
        primary_horizon=_primary_horizon(str(artifact.manifest["scope"])),
        minimum_confidence=options.minimum_confidence,
        minimum_margin=options.minimum_margin,
    )
    uncertainties = np.sqrt(np.exp(np.clip(log_variance, -12.0, 4.0))).mean(axis=1)
    metrics["mean_prediction_uncertainty"] = float(np.mean(uncertainties)) if len(uncertainties) else 0.0
    regimes = np.asarray(artifact.regime_ids)[sample_positions]
    metrics["validated_regime_count"] = float(sum(count >= 10 for count in Counter(regimes.tolist()).values()))
    return metrics


def _collect_outputs(model, artifact, positions, mean, std, batch_size):
    torch = require_torch()
    loader = torch.utils.data.DataLoader(
        _WindowDataset(artifact, positions, mean, std),
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
    )
    model.eval()
    logits: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    returns: list[np.ndarray] = []
    costs: list[np.ndarray] = []
    sample_positions: list[np.ndarray] = []
    log_variance: list[np.ndarray] = []
    with torch.inference_mode():
        for batch in loader:
            output = model(*batch[:4])
            logits.append(output[0].detach().cpu().numpy())
            log_variance.append(output[3].detach().cpu().numpy())
            labels.append(batch[4].cpu().numpy())
            returns.append(batch[5].cpu().numpy())
            costs.append(batch[6].cpu().numpy())
            sample_positions.append(batch[7].cpu().numpy())
    return tuple(
        np.concatenate(values, axis=0) if values else np.empty((0,), dtype=np.float32)
        for values in (logits, labels, returns, costs, sample_positions, log_variance)
    )


def _metrics_from_probabilities(
    probabilities: np.ndarray,
    labels: np.ndarray,
    returns: np.ndarray,
    costs: np.ndarray,
    *,
    classes: list[str],
    primary_horizon: int,
    minimum_confidence: float,
    minimum_margin: float,
) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

    if not len(labels):
        return _empty_metrics()
    predicted = probabilities.argmax(axis=1)
    ordered = np.sort(probabilities, axis=1)
    confidence = ordered[:, -1]
    margin = ordered[:, -1] - ordered[:, -2]
    no_trade_index = classes.index("no_trade") if "no_trade" in classes else None
    policy = predicted.copy()
    if no_trade_index is not None:
        policy[(confidence < minimum_confidence) | (margin < minimum_margin)] = no_trade_index
    correct = policy == labels
    ece = _expected_calibration_error(confidence, correct)
    one_hot = np.eye(len(classes), dtype=np.float32)[labels]
    brier = float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))
    trading = _trading_metrics(policy, returns, costs, classes, primary_horizon)
    return {
        "accuracy": float(accuracy_score(labels, policy)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, policy)),
        "macro_f1": float(f1_score(labels, policy, average="macro", zero_division=0)),
        "expected_calibration_error": ece,
        "brier_score": brier,
        "mean_confidence": float(np.mean(confidence)),
        "abstention_rate": float(np.mean(policy == no_trade_index)) if no_trade_index is not None else 0.0,
        "sample_count": float(len(labels)),
        "trade_label_count": float(sum(classes[int(value)] != "no_trade" for value in labels)) if "no_trade" in classes else 0.0,
        **trading,
    }


def _trading_metrics(policy, returns, costs, classes, primary_horizon):
    if "no_trade" not in classes:
        return {
            "trade_count": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_return": 0.0,
            "average_pnl_per_trade": 0.0,
            "max_drawdown": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
        }
    horizon_index = list(HORIZONS_MINUTES).index(primary_horizon)
    pnl: list[float] = []
    for prediction, raw_returns, cost in zip(policy, returns, costs):
        raw_return = float(raw_returns[horizon_index])
        if not math.isfinite(raw_return):
            continue
        label = classes[int(prediction)]
        if label == "long_good":
            pnl.append(raw_return - float(cost))
        elif label == "short_good":
            pnl.append(-raw_return - float(cost))
    gains = sum(value for value in pnl if value > 0)
    losses = -sum(value for value in pnl if value < 0)
    cumulative = np.cumsum(pnl) if pnl else np.asarray([], dtype=np.float64)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative))) if len(cumulative) else np.asarray([0.0])
    equity = np.concatenate(([0.0], cumulative)) if len(cumulative) else np.asarray([0.0])
    drawdown = float(np.max(peaks - equity))
    return {
        "trade_count": float(len(pnl)),
        "win_rate": sum(value > 0 for value in pnl) / max(len(pnl), 1),
        "profit_factor": gains / losses if losses > 1e-12 else (99.0 if gains > 0 else 0.0),
        "net_return": float(sum(pnl)),
        "average_pnl_per_trade": float(np.mean(pnl)) if pnl else 0.0,
        "max_drawdown": drawdown,
        "gross_profit": gains,
        "gross_loss": losses,
    }


def _evaluate_recorded_baseline(
    artifact: LoadedSequenceArtifact,
    positions: Sequence[int],
    options: TransformerTrainingOptions,
) -> dict[str, Any]:
    probabilities = np.asarray(artifact.baseline_probabilities)[np.asarray(positions)]
    valid = np.isfinite(probabilities).all(axis=1) & (np.abs(probabilities.sum(axis=1) - 1.0) < 0.05)
    if not valid.any():
        return {"status": "unavailable", "reason": "no recorded exact-model probabilities on the holdout rows", "sample_count": 0}
    labels = np.asarray(artifact.labels)[np.asarray(positions)][valid]
    returns = np.asarray(artifact.returns)[np.asarray(positions)][valid]
    costs = np.asarray(artifact.costs)[np.asarray(positions)][valid]
    metrics = _metrics_from_probabilities(
        probabilities[valid],
        labels,
        returns,
        costs,
        classes=list(artifact.manifest["classes"]),
        primary_horizon=_primary_horizon(str(artifact.manifest["scope"])),
        minimum_confidence=options.minimum_confidence,
        minimum_margin=options.minimum_margin,
    )
    return {
        "status": "completed",
        "evaluation_source": "recorded_predictions_from_exact_saved_model",
        "model_versions": artifact.manifest.get("baseline_model_versions", []),
        **metrics,
    }


def _baseline_comparison(transformer: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    minimum_rows = max(50, int(float(transformer.get("sample_count", 0.0)) * 0.50))
    if baseline.get("status") != "completed" or float(baseline.get("sample_count", 0.0)) < minimum_rows:
        return {
            "passed": False,
            "reason": "insufficient exact random-forest baseline rows; remain shadow-only",
            "minimum_rows": minimum_rows,
        }
    baseline_net = float(baseline.get("net_return", 0.0))
    transformer_net = float(transformer.get("net_return", 0.0))
    net_floor = baseline_net * 1.01 if baseline_net > 0 else 0.0
    checks = {
        "after_cost_net_return": transformer_net > net_floor,
        "profit_factor": float(transformer.get("profit_factor", 0.0)) >= max(1.0, float(baseline.get("profit_factor", 0.0)) * 1.02),
        "balanced_accuracy": float(transformer.get("balanced_accuracy", 0.0)) >= float(baseline.get("balanced_accuracy", 0.0)),
        "calibration": float(transformer.get("expected_calibration_error", 1.0)) <= float(baseline.get("expected_calibration_error", 1.0)) + 0.02,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failures,
        "reason": "passed exact random-forest baseline" if not failures else "failed: " + ", ".join(failures),
        "checks": checks,
        "transformer_net_return": transformer_net,
        "baseline_net_return": baseline_net,
    }


def _walk_forward_validation(
    artifact: LoadedSequenceArtifact,
    config: TransformerModelConfig,
    options: TransformerTrainingOptions,
) -> dict[str, Any]:
    count = len(artifact.sample_end_indices)
    initial_train = max(200, int(count * 0.50))
    test_size = max(30, (count - initial_train) // options.walk_forward_folds)
    folds: list[dict[str, Any]] = []
    for fold in range(options.walk_forward_folds):
        test_start = initial_train + fold * test_size
        test_end = count if fold == options.walk_forward_folds - 1 else min(count, test_start + test_size)
        if test_end - test_start < 30:
            break
        prefix = np.arange(test_start, dtype=np.int64)
        validation_size = max(30, int(len(prefix) * 0.15))
        train_positions = prefix[:-validation_size]
        validation_positions = prefix[-validation_size:]
        test_positions = np.arange(test_start, test_end, dtype=np.int64)
        sample_times = artifact.timestamps[artifact.sample_end_indices]
        purge_seconds = max(HORIZONS_MINUTES) * 60
        if len(validation_positions):
            train_positions = train_positions[
                sample_times[train_positions] < sample_times[validation_positions[0]] - purge_seconds
            ]
        if len(test_positions) and len(validation_positions):
            test_positions = test_positions[
                sample_times[test_positions] > sample_times[validation_positions[-1]] + purge_seconds
            ]
        if min(len(train_positions), len(validation_positions), len(test_positions)) < 30:
            continue
        if len(set(np.asarray(artifact.labels)[train_positions].tolist())) < 2:
            continue
        mean, std = _feature_scaler(artifact, train_positions)
        model = build_causal_transformer(config)
        _fit_model(
            model,
            artifact,
            train_positions,
            validation_positions,
            mean,
            std,
            options,
            epochs=options.walk_forward_epochs,
        )
        model.set_temperature(_fit_temperature(model, artifact, validation_positions, mean, std, options.batch_size))
        metrics = _evaluate_model(model, artifact, test_positions, mean, std, options)
        folds.append(
            {
                "fold": fold + 1,
                "train_samples": len(train_positions),
                "test_samples": len(test_positions),
                "train_end": _sample_timestamp(artifact, int(train_positions[-1])),
                "test_start": _sample_timestamp(artifact, int(test_positions[0])),
                "test_end": _sample_timestamp(artifact, int(test_positions[-1])),
                "metrics": metrics,
            }
        )
    aggregate = _aggregate_fold_metrics(folds)
    return {
        "completed": len(folds) == options.walk_forward_folds,
        "folds": folds,
        "aggregate": aggregate,
        "profitable_fold_ratio": sum(float(item["metrics"].get("net_return", 0.0)) > 0 for item in folds) / max(len(folds), 1),
    }


def _aggregate_fold_metrics(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if not folds:
        return _empty_metrics()
    metrics = [item["metrics"] for item in folds]
    gains = sum(float(item.get("gross_profit", 0.0)) for item in metrics)
    losses = sum(float(item.get("gross_loss", 0.0)) for item in metrics)
    trades = sum(float(item.get("trade_count", 0.0)) for item in metrics)
    return {
        "balanced_accuracy": float(np.mean([item.get("balanced_accuracy", 0.0) for item in metrics])),
        "macro_f1": float(np.mean([item.get("macro_f1", 0.0) for item in metrics])),
        "win_rate": sum(float(item.get("win_rate", 0.0)) * float(item.get("trade_count", 0.0)) for item in metrics) / max(trades, 1.0),
        "profit_factor": gains / losses if losses > 1e-12 else (99.0 if gains > 0 else 0.0),
        "net_return": sum(float(item.get("net_return", 0.0)) for item in metrics),
        "average_pnl_per_trade": sum(float(item.get("net_return", 0.0)) for item in metrics) / max(trades, 1.0),
        "max_drawdown": max(float(item.get("max_drawdown", 0.0)) for item in metrics),
        "trade_count": trades,
        "trade_label_count": sum(float(item.get("trade_label_count", 0.0)) for item in metrics),
        "gross_profit": gains,
        "gross_loss": losses,
    }


def _export_torchscript(model, config: TransformerModelConfig, path: Path) -> None:
    torch = require_torch()
    model.eval()
    example = (
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.float32),
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
    )
    with torch.inference_mode():
        traced = torch.jit.trace(model, example, strict=False)
        traced.save(str(path))


def _export_onnx(model, config: TransformerModelConfig, path: Path) -> tuple[Path | None, str | None]:
    torch = require_torch()
    example = (
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.float32),
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
    )
    try:
        torch.onnx.export(
            model.eval(),
            example,
            str(path),
            input_names=["values", "missing_mask", "valid_mask", "session_mask"],
            output_names=["logits", "expected_returns", "expected_cost", "log_variance"],
            opset_version=18,
            dynamo=True,
        )
    except Exception as exc:  # ONNX remains optional; TorchScript is always produced.
        path.unlink(missing_ok=True)
        return None, str(exc)
    return path, None


def _measure_script_latency(path: Path, config: TransformerModelConfig) -> dict[str, float]:
    torch = require_torch()
    model = torch.jit.load(str(path), map_location="cpu").eval()
    example = (
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.float32),
        torch.zeros((1, config.sequence_length, config.feature_count), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
        torch.ones((1, config.sequence_length), dtype=torch.bool),
    )
    with torch.inference_mode():
        for _ in range(10):
            model(*example)
        values: list[float] = []
        for _ in range(100):
            started = time.perf_counter_ns()
            model(*example)
            values.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(values)
    return {
        "median_ms": statistics.median(ordered),
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "maximum_ms": max(ordered),
    }


def _primary_horizon(scope: str) -> int:
    return 1 if scope == "fast_microstructure" else 5


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    values = np.exp(shifted)
    return values / np.maximum(values.sum(axis=1, keepdims=True), 1e-12)


def _numpy_cross_entropy(logits: np.ndarray, labels: np.ndarray) -> float:
    probabilities = _softmax(logits)
    selected = probabilities[np.arange(len(labels)), labels.astype(np.int64)]
    return float(-np.log(np.clip(selected, 1e-12, 1.0)).mean())


def _expected_calibration_error(confidence: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    total = max(len(confidence), 1)
    error = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (confidence >= lower) & (confidence < upper if index < bins - 1 else confidence <= upper)
        if mask.any():
            error += float(mask.sum()) / total * abs(float(confidence[mask].mean()) - float(correct[mask].mean()))
    return error


def _sample_timestamp(artifact: LoadedSequenceArtifact, sample_position: int) -> str:
    row = int(artifact.sample_end_indices[sample_position])
    return datetime.fromtimestamp(float(artifact.timestamps[row]), tz=timezone.utc).isoformat()


def _artifact_fingerprint(script_path: Path, dataset_fingerprint: str, config: dict[str, Any], mean, std) -> str:
    digest = hashlib.sha256()
    digest.update(script_path.read_bytes())
    digest.update(dataset_fingerprint.encode("utf-8"))
    digest.update(json.dumps(config, sort_keys=True).encode("utf-8"))
    digest.update(np.asarray(mean, dtype=np.float32).tobytes())
    digest.update(np.asarray(std, dtype=np.float32).tobytes())
    return digest.hexdigest()


def _promotion_blockers(metrics: dict[str, Any], settings: Settings) -> list[str]:
    blockers = ["minimum paper-trading evidence has not yet been collected"]
    if not metrics.get("baseline_comparison_passed"):
        blockers.append("exact random-forest baseline has not been beaten")
    if float(metrics.get("profit_factor", 0.0)) < settings.promotion_min_profit_factor:
        blockers.append("walk-forward profit factor below promotion floor")
    if float(metrics.get("expected_calibration_error", 1.0)) > settings.promotion_max_calibration_error:
        blockers.append("holdout calibration error above promotion ceiling")
    if float(metrics.get("inference_latency_ms", math.inf)) > settings.ml_max_inference_latency_ms:
        blockers.append("inference latency above live-model budget")
    return blockers


def _empty_metrics() -> dict[str, float]:
    return {
        "accuracy": 0.0,
        "balanced_accuracy": 0.0,
        "macro_f1": 0.0,
        "expected_calibration_error": 1.0,
        "sample_count": 0.0,
        "trade_label_count": 0.0,
        "trade_count": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "net_return": 0.0,
        "average_pnl_per_trade": 0.0,
        "max_drawdown": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
    }


def _json_default(value: Any):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
