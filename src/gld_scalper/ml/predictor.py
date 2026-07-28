from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

from ..database import Database
from ..models import MLPrediction
from .dataset_builder import _flatten_features
from .model_registry import ModelRegistry
from .scopes import model_scope_from_features, scope_fallbacks


class Predictor:
    """Load the most specific approved model for each live decision context."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.registry = ModelRegistry(database)
        self._cache: dict[str, tuple[dict[str, Any], str, str]] = {}
        self._unavailable_scopes: set[str] = set()
        self._payload: dict[str, Any] | None = None
        self._model_version: str | None = None
        self._model_role = "none"
        self._model_scope = "entry:all"
        self._activate_scope("entry:all")

    @property
    def has_champion(self) -> bool:
        return self._model_role == "champion" or self.registry.get_champion() is not None

    @property
    def has_model(self) -> bool:
        if self._payload is not None or self.registry.get_champion() is not None:
            return True
        return bool(self._shadow_allowed() and self.registry.get_paper_shadow_candidates(limit=1))

    @property
    def model_role(self) -> str:
        return self._model_role

    @property
    def model_version(self) -> str | None:
        return self._model_version

    @property
    def model_scope(self) -> str:
        return self._model_scope

    def reload_champion(self) -> bool:
        previous = (self._model_version, self._model_role, self._model_scope)
        self._cache.clear()
        self._unavailable_scopes.clear()
        self._payload = None
        self._model_version = None
        self._model_role = "none"
        self._model_scope = "entry:all"
        self._activate_scope("entry:all")
        return (self._model_version, self._model_role, self._model_scope) != previous

    def prediction_features(self, prediction: MLPrediction) -> dict[str, Any]:
        rejection_reason = prediction.rejection_reason
        advice_eligible = rejection_reason in {None, "model prefers no-trade"}
        input_compatible = bool(
            self._payload
            and not any(marker in (rejection_reason or "") for marker in ("missing feature fraction", "model inference failed"))
        )
        metrics = (self._payload or {}).get("metrics") or {}
        predicted_action = {"long_good": "LONG", "short_good": "SHORT"}.get(prediction.predicted_direction)
        return {
            "ml_model_version": prediction.model_version,
            "ml_model_role": self._model_role,
            "ml_model_scope": self._model_scope,
            "ml_participating": self._payload is not None,
            "ml_predicted_direction": prediction.predicted_direction,
            "ml_probability_long": prediction.probability_long,
            "ml_probability_short": prediction.probability_short,
            "ml_probability_no_trade": prediction.probability_no_trade,
            "ml_expected_return": prediction.expected_return,
            "ml_confidence": prediction.confidence,
            "ml_rejection_reason": prediction.rejection_reason,
            "ml_rejects_trade": prediction.rejects_trade,
            "ml_advice_eligible": advice_eligible,
            "ml_input_compatible": input_compatible,
            "ml_independently_validated": self._model_role == "champion" and float(metrics.get("walk_forward_completed", 0.0) or 0.0) >= 1.0,
            "ml_validated_after_cost_profit_factor": float(metrics.get("profit_factor", 0.0) or 0.0),
            "ml_direction_aligned": predicted_action is not None,
            "ml_artifact_fingerprint": (self._payload or {}).get("artifact_fingerprint"),
        }

    def predict(self, features: dict[str, Any]) -> MLPrediction:
        requested_scope = model_scope_from_features(features)
        explicit_payload = self._payload if self._payload is not None and self._model_version and not self._cache else None
        if explicit_payload is None:
            self._payload = None
            for scope in scope_fallbacks(requested_scope):
                if self._activate_scope(scope):
                    break
        else:
            self._model_scope = requested_scope
        if self._payload is None:
            self._model_scope = requested_scope
            return MLPrediction(None, "rule_only", 0.34, 0.33, 0.33, confidence=0.0)
        columns = list(self._payload["feature_columns"])
        model = self._payload["model"]
        flat = _flatten_features(features)
        row = [[flat.get(column, 0.0) for column in columns]]
        rejection_reasons = self._input_rejection_reasons(flat, features, columns)
        try:
            classes = list(getattr(model, "classes_", ["long_good", "short_good", "no_trade"]))
            probabilities = list(model.predict_proba(row)[0])
            probability_by_class = dict(zip(classes, probabilities))
            predicted = str(classes[max(range(len(probabilities)), key=probabilities.__getitem__)])
        except Exception as exc:
            predicted = "no_trade"
            probability_by_class = {"long_good": 0.0, "short_good": 0.0, "no_trade": 1.0}
            rejection_reasons.append(f"model inference failed: {exc}")
        probability_long = float(probability_by_class.get("long_good", 0.0))
        probability_short = float(probability_by_class.get("short_good", 0.0))
        probability_no_trade = float(probability_by_class.get("no_trade", 0.0))
        confidence = max(probability_long, probability_short, probability_no_trade)
        ordered = sorted((probability_long, probability_short, probability_no_trade), reverse=True)
        margin = ordered[0] - ordered[1]
        abstention = self._payload.get("abstention") or {}
        minimum_confidence = float(abstention.get("minimum_confidence", self.database.settings.ml_min_confidence))
        minimum_margin = float(abstention.get("minimum_margin", self.database.settings.ml_min_probability_margin))
        if predicted == "no_trade":
            rejection_reasons.append("model prefers no-trade")
        elif confidence < minimum_confidence:
            predicted = "no_trade"
            rejection_reasons.append(f"model confidence {confidence:.3f} below {minimum_confidence:.3f}")
        elif margin < minimum_margin:
            predicted = "no_trade"
            rejection_reasons.append(f"model probability margin {margin:.3f} below {minimum_margin:.3f}")
        expected_by_class = self._payload.get("expected_return_by_class") or {}
        expected_return = (
            probability_long * float(expected_by_class.get("long_good", 0.0) or 0.0)
            + probability_short * float(expected_by_class.get("short_good", 0.0) or 0.0)
        )
        return MLPrediction(
            self._model_version,
            predicted,
            probability_long,
            probability_short,
            probability_no_trade,
            expected_return=expected_return,
            confidence=confidence,
            rejection_reason="; ".join(dict.fromkeys(rejection_reasons)) if rejection_reasons else None,
        )

    def _input_rejection_reasons(self, flat: dict[str, float], raw: dict[str, Any], columns: list[str]) -> list[str]:
        reasons: list[str] = []
        statistics = self._payload.get("feature_statistics") or {} if self._payload else {}
        required = [
            column
            for column in columns
            if "__" not in column and float((statistics.get(column) or {}).get("missing_fraction", 0.0)) <= 0.10
        ]
        missing_fraction = sum(column not in flat for column in required) / max(len(required), 1)
        maximum_missing = float((self._payload.get("abstention") or {}).get("max_missing_fraction", self.database.settings.ml_max_missing_feature_fraction))
        if missing_fraction > maximum_missing:
            reasons.append(f"missing feature fraction {missing_fraction:.3f} above {maximum_missing:.3f}")
        outliers = 0
        checked = 0
        for column in required:
            if column not in flat:
                continue
            stats = statistics.get(column) or {}
            standard_deviation = float(stats.get("std", 0.0) or 0.0)
            if standard_deviation <= 1e-12:
                continue
            checked += 1
            z_score = abs(flat[column] - float(stats.get("mean", 0.0) or 0.0)) / standard_deviation
            if z_score > 6.0:
                outliers += 1
        outlier_fraction = outliers / max(checked, 1)
        maximum_outliers = float((self._payload.get("abstention") or {}).get("max_outlier_fraction", self.database.settings.ml_max_outlier_feature_fraction))
        if outlier_fraction > maximum_outliers:
            reasons.append(f"feature drift fraction {outlier_fraction:.3f} above {maximum_outliers:.3f}")
        if raw.get("quote_age_seconds") is not None and float(raw["quote_age_seconds"]) > self.database.settings.quote_stale_seconds:
            reasons.append("quote data stale")
        if raw.get("data_age_seconds") is not None and float(raw["data_age_seconds"]) > self.database.settings.stale_data_seconds:
            reasons.append("market data stale")
        if raw.get("websocket_connected") is False:
            reasons.append("websocket disconnected")
        return reasons

    def _activate_scope(self, scope: str) -> bool:
        cached = self._cache.get(scope)
        if cached is not None:
            self._payload, self._model_version, self._model_role = cached
            self._model_scope = scope
            return True
        if scope in self._unavailable_scopes:
            return False
        champion = self.registry.get_champion(scope)
        candidates = [champion] if champion is not None else []
        if not candidates and self._shadow_allowed():
            candidates = self.registry.get_paper_shadow_candidates(model_scope=scope)
        for candidate in candidates:
            if candidate is None:
                continue
            path = _resolved_path(str(candidate.get("path") or candidate.get("resolved_path") or ""))
            if not path.is_file():
                continue
            try:
                payload = _load_model(path)
            except Exception:
                continue
            if not payload.get("model") or not payload.get("feature_columns"):
                continue
            payload_scope = str(payload.get("model_scope") or candidate.get("model_scope") or "entry:all")
            if payload_scope != scope:
                continue
            fingerprint = str(payload.get("artifact_fingerprint") or "")
            registered_fingerprint = str(candidate.get("artifact_fingerprint") or "")
            if registered_fingerprint and fingerprint != registered_fingerprint:
                continue
            expected_model_state = str(payload.get("model_state_fingerprint") or "")
            if expected_model_state and _model_state_fingerprint(payload["model"]) != expected_model_state:
                continue
            role = "champion" if candidate.get("status") == "champion" else "paper_shadow"
            version = str(candidate["model_version"])
            self._cache[scope] = (payload, version, role)
            self._payload, self._model_version, self._model_role = self._cache[scope]
            self._model_scope = scope
            return True
        self._unavailable_scopes.add(scope)
        return False

    def _shadow_allowed(self) -> bool:
        settings = self.database.settings
        return bool(
            settings.paper_enable_shadow_model
            and settings.paper_learning_mode
            and settings.alpaca_paper
            and settings.alpaca_paper_trade
            and settings.data_mode == "paper"
        )


def _load_model(path: Path) -> dict[str, Any]:
    try:
        import joblib

        payload = joblib.load(path)
    except Exception:
        with path.open("rb") as file_handle:
            payload = pickle.load(file_handle)
    if not isinstance(payload, dict):
        raise ValueError("model artifact must contain a dictionary payload")
    return payload


def _resolved_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _model_state_fingerprint(model: Any) -> str:
    return hashlib.sha256(pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()
