from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np

from ..config import Settings
from ..database import Database
from ..utils.time_utils import ensure_utc
from .transformer_dataset import transformer_registry_scope
from .transformer_model import HORIZONS_MINUTES, require_torch


LOGGER = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
SUPPORTED_SCOPES = {"fast_microstructure", "minute", "news_event", "exit"}


@dataclass(slots=True)
class TransformerShadowPrediction:
    scope: str
    status: str = "unavailable"
    timestamp: datetime | None = None
    model_version: str | None = None
    predicted_direction: str | None = None
    class_probabilities: dict[str, float] = field(default_factory=dict)
    expected_returns: dict[int, float] = field(default_factory=dict)
    expected_cost: float | None = None
    expected_net_edge: float | None = None
    uncertainty: float | None = None
    inference_latency_ms: float | None = None
    cache_age_seconds: float | None = None
    reason: str | None = None
    authority_mode: str = "shadow"
    model_role: str | None = None

    def as_features(self) -> dict[str, Any]:
        prefix = f"transformer_{self.scope}"
        values: dict[str, Any] = {
            f"{prefix}_status": self.status,
            f"{prefix}_model_version": self.model_version,
            f"{prefix}_prediction": self.predicted_direction,
            f"{prefix}_uncertainty": self.uncertainty,
            f"{prefix}_expected_cost": self.expected_cost,
            f"{prefix}_expected_net_edge": self.expected_net_edge,
            f"{prefix}_inference_latency_ms": self.inference_latency_ms,
            f"{prefix}_cache_age_seconds": self.cache_age_seconds,
            f"{prefix}_shadow_only": 1.0 if self.authority_mode == "shadow" else 0.0,
            f"{prefix}_authority_mode": self.authority_mode,
            f"{prefix}_model_role": self.model_role,
        }
        for label, probability in self.class_probabilities.items():
            values[f"{prefix}_probability_{label}"] = probability
        for horizon, expected_return in self.expected_returns.items():
            values[f"{prefix}_expected_return_{horizon}m"] = expected_return
        return values


@dataclass(slots=True)
class _InferenceRequest:
    scope: str
    timestamp: datetime
    features: dict[str, Any]
    fallback_model_version: str | None


@dataclass(slots=True)
class _LoadedModel:
    version: str
    role: str
    manifest: dict[str, Any]
    module: Any
    loaded_at: float


class AsyncTransformerShadowRuntime:
    """Asynchronous, read-only model adviser with no broker-order capability."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._queue: queue.Queue[_InferenceRequest | None] = queue.Queue(maxsize=settings.transformer_queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._latest: dict[str, TransformerShadowPrediction] = {}
        self._history: dict[str, deque[tuple[datetime, dict[str, float]]]] = {
            scope: deque(maxlen=256) for scope in SUPPORTED_SCOPES
        }
        self._models: dict[str, _LoadedModel | None] = {}
        self._last_model_check: dict[str, float] = {}

    def start(self) -> None:
        if not self.settings.enable_transformer_shadow or self._thread is not None:
            return
        try:
            require_torch()
        except RuntimeError as exc:
            LOGGER.warning("Transformer shadow disabled: %s", exc)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="transformer-shadow", daemon=True)
        self._thread.start()
        LOGGER.info(
            "Transformer runtime started mode=%s; inference is asynchronous and has no broker access",
            self.settings.transformer_trading_mode,
        )

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def submit(
        self,
        scope: str,
        timestamp: datetime,
        features: Mapping[str, Any],
        *,
        fallback_model_version: str | None = None,
    ) -> TransformerShadowPrediction:
        scope = _normalize_scope(scope)
        request = _InferenceRequest(
            scope=scope,
            timestamp=ensure_utc(timestamp),
            features=dict(features),
            fallback_model_version=fallback_model_version,
        )
        if self._thread is not None:
            try:
                self._queue.put_nowait(request)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(request)
                except queue.Full:
                    pass
        with self._lock:
            cached = self._latest.get(scope)
            if cached is None or cached.timestamp is None:
                return TransformerShadowPrediction(
                    scope=scope,
                    reason="no completed Transformer prediction",
                    authority_mode=self.settings.transformer_trading_mode,
                )
            age = max(0.0, (ensure_utc(timestamp) - ensure_utc(cached.timestamp)).total_seconds())
            if age > self.settings.transformer_cache_max_age_seconds:
                return TransformerShadowPrediction(
                    scope=scope,
                    status="stale",
                    timestamp=cached.timestamp,
                    model_version=cached.model_version,
                    cache_age_seconds=age,
                    reason="cached Transformer prediction is stale; random forest remains authoritative",
                    authority_mode=self.settings.transformer_trading_mode,
                    model_role=cached.model_role,
                )
            result = TransformerShadowPrediction(
                scope=cached.scope,
                status=cached.status,
                timestamp=cached.timestamp,
                model_version=cached.model_version,
                predicted_direction=cached.predicted_direction,
                class_probabilities=dict(cached.class_probabilities),
                expected_returns=dict(cached.expected_returns),
                expected_cost=cached.expected_cost,
                expected_net_edge=cached.expected_net_edge,
                uncertainty=cached.uncertainty,
                inference_latency_ms=cached.inference_latency_ms,
                cache_age_seconds=age,
                reason=cached.reason,
                authority_mode=cached.authority_mode,
                model_role=cached.model_role,
            )
            return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.settings.enable_transformer_shadow,
                "authority_mode": self.settings.transformer_trading_mode,
                "running": self._thread is not None and self._thread.is_alive(),
                "queue_depth": self._queue.qsize(),
                "loaded_models": {scope: model.version if model else None for scope, model in self._models.items()},
                "latest": {scope: prediction.as_features() for scope, prediction in self._latest.items()},
            }

    def _run(self) -> None:
        database = Database(settings=self.settings)
        try:
            database.init_db()
            torch = require_torch()
            torch.set_num_threads(self.settings.transformer_torch_threads)
            while not self._stop.is_set():
                try:
                    request = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if request is None:
                    break
                try:
                    self._process(database, torch, request)
                except Exception as exc:  # shadow failures must not affect order handling
                    LOGGER.exception("Transformer shadow inference failed scope=%s", request.scope)
                    prediction = TransformerShadowPrediction(
                        scope=request.scope,
                        status="error",
                        timestamp=request.timestamp,
                        reason=str(exc),
                        authority_mode=self.settings.transformer_trading_mode,
                    )
                    with self._lock:
                        self._latest[request.scope] = prediction
        finally:
            database.close()

    def _process(self, database: Database, torch: Any, request: _InferenceRequest) -> None:
        flat = _flatten_features(request.features)
        history = self._history[request.scope]
        history.append((request.timestamp, flat))
        loaded = self._load_model(database, torch, request.scope)
        if loaded is None:
            with self._lock:
                self._latest[request.scope] = TransformerShadowPrediction(
                    scope=request.scope,
                    status="no_model",
                    timestamp=request.timestamp,
                    reason="no Transformer candidate exists for this independent scope",
                    authority_mode=self.settings.transformer_trading_mode,
                )
            return

        manifest = loaded.manifest
        columns = list(manifest["feature_columns"])
        sequence_length = int(manifest["config"]["sequence_length"])
        mean = np.asarray(manifest["mean"], dtype=np.float32)
        std = np.asarray(manifest["std"], dtype=np.float32)
        window_seconds = float(manifest.get("window_seconds") or manifest.get("config", {}).get("sequence_length", 120))
        cutoff = request.timestamp.timestamp() - window_seconds
        points = [item for item in history if item[0].timestamp() >= cutoff and _session_key(item[0]) == _session_key(request.timestamp)]
        points = points[-sequence_length:]
        raw = np.full((sequence_length, len(columns)), np.nan, dtype=np.float32)
        valid = np.zeros(sequence_length, dtype=np.bool_)
        session = np.zeros(sequence_length, dtype=np.bool_)
        current_session = _session_key(request.timestamp)
        for row_index, (timestamp, values) in enumerate(points):
            valid[row_index] = True
            session[row_index] = _session_key(timestamp) == current_session
            for column_index, column in enumerate(columns):
                value = values.get(column)
                if value is not None and math.isfinite(value):
                    raw[row_index, column_index] = value
        missing = ~np.isfinite(raw)
        normalized = (np.where(missing, mean, raw) - mean) / np.where(std > 1e-8, std, 1.0)
        inputs = (
            torch.from_numpy(normalized[None, ...]),
            torch.from_numpy(missing[None, ...]),
            torch.from_numpy(valid[None, ...]),
            torch.from_numpy(session[None, ...]),
        )
        started = time.perf_counter()
        with torch.inference_mode():
            logits, returns, cost, log_variance = loaded.module(*inputs)
            probabilities = torch.softmax(logits, dim=-1)[0].cpu().numpy()
        latency_ms = (time.perf_counter() - started) * 1_000.0
        classes = list(manifest["classes"])
        class_probabilities = {label: float(probabilities[index]) for index, label in enumerate(classes)}
        ranked = sorted(class_probabilities.items(), key=lambda item: item[1], reverse=True)
        predicted = ranked[0][0]
        abstention = manifest.get("abstention", {})
        if len(ranked) > 1 and (
            ranked[0][1] < float(abstention.get("minimum_confidence", 0.58))
            or ranked[0][1] - ranked[1][1] < float(abstention.get("minimum_margin", 0.08))
        ):
            predicted = "no_trade" if "no_trade" in classes else "hold"
        entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities) / math.log(max(len(probabilities), 2))
        return_values = returns[0].cpu().numpy()
        variance_values = np.exp(log_variance[0].cpu().numpy())
        horizon = 1 if request.scope == "fast_microstructure" else 5
        horizon_index = list(HORIZONS_MINUTES).index(horizon)
        direction = 1.0 if predicted == "long_good" else -1.0 if predicted == "short_good" else 0.0
        expected_net_edge = (
            direction * float(return_values[horizon_index])
            - float(cost.reshape(-1)[0].cpu())
            - float(abstention.get("uncertainty_multiplier", 0.0) or 0.0)
            * float(np.sqrt(variance_values[horizon_index]))
        )
        if expected_net_edge <= float(abstention.get("minimum_expected_edge", 0.0) or 0.0):
            predicted = "no_trade" if "no_trade" in classes else "hold"
        uncertainty = max(float(entropy), min(1.0, float(np.sqrt(variance_values).mean()) / 0.01))
        prediction = TransformerShadowPrediction(
            scope=request.scope,
            status=self.settings.transformer_trading_mode,
            timestamp=request.timestamp,
            model_version=loaded.version,
            predicted_direction=predicted,
            class_probabilities=class_probabilities,
            expected_returns={horizon: float(return_values[index]) for index, horizon in enumerate(HORIZONS_MINUTES)},
            expected_cost=float(cost.reshape(-1)[0].cpu()),
            expected_net_edge=expected_net_edge,
            uncertainty=uncertainty,
            inference_latency_ms=latency_ms,
            cache_age_seconds=0.0,
            authority_mode=self.settings.transformer_trading_mode,
            model_role=loaded.role,
        )
        with self._lock:
            self._latest[request.scope] = prediction
        database.insert_transformer_prediction(
            {
                "timestamp": request.timestamp,
                "symbol": self.settings.bot_symbol,
                "model_version": loaded.version,
                "model_scope": transformer_registry_scope(request.scope),
                "model_role": loaded.role,
                "predicted_direction": predicted,
                "probability_long": class_probabilities.get("long_good"),
                "probability_short": class_probabilities.get("short_good"),
                "probability_no_trade": class_probabilities.get("no_trade"),
                "expected_return_1m": prediction.expected_returns[1],
                "expected_return_3m": prediction.expected_returns[3],
                "expected_return_5m": prediction.expected_returns[5],
                "expected_return_15m": prediction.expected_returns[15],
                "expected_cost": prediction.expected_cost,
                "expected_net_edge": prediction.expected_net_edge,
                "uncertainty": prediction.uncertainty,
                "inference_latency_ms": latency_ms,
                "cache_age_seconds": 0.0,
                "status": self.settings.transformer_trading_mode,
                "fallback_model_version": request.fallback_model_version,
                "features": {"input": flat, "class_probabilities": class_probabilities},
            }
        )

    def _load_model(self, database: Database, torch: Any, scope: str) -> _LoadedModel | None:
        now = time.monotonic()
        if now - self._last_model_check.get(scope, 0.0) < self.settings.transformer_model_refresh_seconds:
            return self._models.get(scope)
        self._last_model_check[scope] = now
        registry_scope = transformer_registry_scope(scope)
        required_status = "champion" if self.settings.transformer_trading_mode == "paper_champion" else None
        status_clause = "AND status = 'champion'" if required_status else "AND status IN ('champion', 'candidate')"
        row = database.conn.execute(
            f"""
            SELECT * FROM model_versions
            WHERE model_scope = ? {status_clause}
              AND model_type LIKE 'causal_transformer_%'
            ORDER BY CASE status WHEN 'champion' THEN 0 ELSE 1 END, datetime(created_at) DESC, id DESC
            LIMIT 1
            """,
            (registry_scope,),
        ).fetchone()
        if row is None:
            self._models[scope] = None
            return None
        candidate = dict(row)
        if self._models.get(scope) is not None and self._models[scope].version == candidate["model_version"]:
            return self._models[scope]
        manifest = json.loads(Path(candidate["path"]).read_text(encoding="utf-8"))
        module = torch.jit.load(manifest["torchscript_path"], map_location="cpu")
        module.eval()
        loaded = _LoadedModel(
            version=str(candidate["model_version"]),
            role=(
                "paper_champion"
                if self.settings.transformer_trading_mode == "paper_champion" and candidate["status"] == "champion"
                else "bounded_champion"
                if self.settings.transformer_trading_mode == "bounded_adviser" and candidate["status"] == "champion"
                else "bounded_candidate"
                if self.settings.transformer_trading_mode == "bounded_adviser"
                else "champion_shadow"
                if candidate["status"] == "champion"
                else "candidate_shadow"
            ),
            manifest=manifest,
            module=module,
            loaded_at=now,
        )
        self._models[scope] = loaded
        LOGGER.info(
            "loaded Transformer scope=%s version=%s authority=%s role=%s",
            scope,
            loaded.version,
            self.settings.transformer_trading_mode,
            loaded.role,
        )
        return loaded


def _normalize_scope(scope: str) -> str:
    value = str(scope).strip().lower().replace("-", "_")
    aliases = {"fast": "fast_microstructure", "minute_setup": "minute", "news": "news_event"}
    value = aliases.get(value, value)
    if value not in SUPPORTED_SCOPES:
        raise ValueError(f"Unsupported Transformer scope: {scope}")
    return value


def _flatten_features(values: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_key, raw_value in values.items():
        key = f"{prefix}_{raw_key}" if prefix else str(raw_key)
        if isinstance(raw_value, Mapping):
            result.update(_flatten_features(raw_value, key))
        elif isinstance(raw_value, bool):
            result[key] = float(raw_value)
        elif isinstance(raw_value, (int, float)):
            value = float(raw_value)
            if math.isfinite(value):
                result[key] = value
        elif isinstance(raw_value, str) and raw_value:
            try:
                parsed = json.loads(raw_value)
            except (ValueError, TypeError):
                result[f"{key}__{raw_value.strip().lower()}"] = 1.0
            else:
                if isinstance(parsed, Mapping):
                    result.update(_flatten_features(parsed, key))
    return result


def _session_key(timestamp: datetime) -> str:
    return ensure_utc(timestamp).astimezone(NY).date().isoformat()
