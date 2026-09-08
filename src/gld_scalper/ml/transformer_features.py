from __future__ import annotations

import json
import math
from typing import Any, Mapping


ALLOWED_CATEGORICAL_KEYS = {
    "decision",
    "executed_action",
    "gold_volatility_regime",
    "market_state",
    "model_scope",
    "order_block_direction",
    "options_bias",
    "pattern_classification",
    "playbook",
    "regime",
    "session",
    "session_phase",
    "spread_regime",
    "strategy_path",
    "technical_family",
    "technical_route",
    "time_of_day_profile",
}

# Semantic allowlist for stable numeric observations. A feature must describe
# market state, model evidence, execution economics, or position state; merely
# being numeric is not enough (for example, encoded identifiers stay out).
ALLOWED_NUMERIC_KEY_TERMS = (
    "return", "price", "open", "high", "low", "close", "midpoint", "vwap",
    "bid", "ask", "spread", "size", "volume", "trade_count", "imbalance",
    "signed_volume", "trade_intensity", "liquidity", "volatility", "atr", "adx",
    "rsi", "ema", "sma", "macd", "bollinger", "momentum", "slope", "distance",
    "age_seconds", "fresh", "stale", "connected", "aligned", "skew", "gap",
    "break", "pullback", "compression", "support", "resistance", "order_block",
    "pattern", "fibonacci", "fib_", "fvg", "wick", "candle", "cycle", "hilbert",
    "sentiment", "event_risk", "macro", "yield", "dollar", "options", "delta",
    "gamma", "theta", "vega", "open_interest", "pnl", "profit", "loss", "cost",
    "slippage", "fee", "excursion", "drawdown", "stop", "target", "qty",
    "exposure", "confidence", "probability", "uncertainty", "score", "strength",
    "duration", "holding", "session_id", "regime_id", "hour_sin", "hour_cos",
)

BLOCKED_KEY_PARTS = (
    "author",
    "client_order_id",
    "created_at",
    "error",
    "headline",
    "message",
    "raw_json",
    "reason",
    "received_at",
    "summary",
    "timestamp",
    "url",
)


def flatten_transformer_features(values: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    """Flatten only stable numeric and explicitly controlled categorical features."""

    result: dict[str, float] = {}
    for raw_key, raw_value in values.items():
        key = f"{prefix}_{raw_key}" if prefix else str(raw_key)
        if _blocked(key):
            continue
        if isinstance(raw_value, Mapping):
            result.update(flatten_transformer_features(raw_value, key))
        elif isinstance(raw_value, bool):
            if transformer_feature_allowed(key):
                result[key] = float(raw_value)
        elif isinstance(raw_value, (int, float)):
            number = float(raw_value)
            if math.isfinite(number) and transformer_feature_allowed(key):
                result[key] = number
        elif isinstance(raw_value, str) and raw_value:
            try:
                parsed = json.loads(raw_value)
            except (ValueError, TypeError):
                base = key.rsplit("_", 1)[-1] if prefix else key
                if base in ALLOWED_CATEGORICAL_KEYS or key in ALLOWED_CATEGORICAL_KEYS:
                    token = _category_token(raw_value)
                    if token:
                        result[f"{key}__{token}"] = 1.0
            else:
                if isinstance(parsed, Mapping):
                    result.update(flatten_transformer_features(parsed, key))
    return result


def transformer_feature_allowed(column: str) -> bool:
    if not column or _blocked(column):
        return False
    lowered = column.lower()
    categorical = lowered.split("__", 1)[0]
    return (
        any(categorical == key or categorical.endswith(f"_{key}") for key in ALLOWED_CATEGORICAL_KEYS)
        or any(term in lowered for term in ALLOWED_NUMERIC_KEY_TERMS)
    )


def rejected_transformer_columns(columns: list[str]) -> list[str]:
    return [column for column in columns if not transformer_feature_allowed(str(column))]


def _blocked(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in BLOCKED_KEY_PARTS)


def _category_token(value: str) -> str:
    token = "_".join(part for part in value.strip().lower().replace("-", "_").split() if part)
    if not token or len(token) > 48:
        return ""
    return "".join(character for character in token if character.isalnum() or character == "_")
