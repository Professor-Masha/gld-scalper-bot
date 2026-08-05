from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import MarketSignal
from .utils.math_utils import clamp


@dataclass(slots=True)
class TargetExposure:
    symbol: str
    desired_exposure_pct: float
    direction: str
    confidence: float
    reason: str

    def as_features(self) -> dict[str, Any]:
        return {
            "target_symbol": self.symbol,
            "target_exposure_pct": self.desired_exposure_pct,
            "target_direction": self.direction,
            "target_confidence": self.confidence,
            "target_reason": self.reason,
        }


def target_exposure_from_signal(signal: MarketSignal, features: dict[str, Any]) -> TargetExposure:
    if signal.decision == "NO_TRADE":
        return TargetExposure(signal.symbol, 0.0, "FLAT", signal.confidence, "no trade target exposure")
    base = 0.03 if signal.confidence < 0.80 else 0.05
    quality = _f(features.get("pattern_quality"))
    liquidity = _f(features.get("liquidity_score"), 0.5)
    macro_alignment = _macro_alignment(signal.decision, features)
    multiplier = 0.75 + quality * 0.35 + liquidity * 0.25 + macro_alignment * 0.10
    if bool(features.get("volatility_burst")) or _f(features.get("headline_event_risk")) > 0.65:
        multiplier *= 0.75
    advisory_bias = str(features.get("tradingagents_advisory_bias") or "neutral")
    advisory_aligned = (
        (signal.decision == "LONG" and advisory_bias == "bullish")
        or (signal.decision == "SHORT" and advisory_bias == "bearish")
    )
    advisory_multiplier = _f(features.get("tradingagents_advisory_size_multiplier"), 1.0)
    if not features.get("tradingagents_advisory_stale", True):
        multiplier *= advisory_multiplier if advisory_aligned else min(advisory_multiplier, 1.0)
    exposure = clamp(base * multiplier, 0.0, 0.07)
    if signal.decision == "SHORT":
        exposure *= -1.0
    return TargetExposure(
        symbol=signal.symbol,
        desired_exposure_pct=round(exposure, 5),
        direction=signal.decision,
        confidence=signal.confidence,
        reason="target exposure derived from signal confidence, pattern quality, liquidity, and macro alignment",
    )


def _macro_alignment(direction: str, features: dict[str, Any]) -> float:
    bias = str(features.get("macro_bias") or "")
    confidence = _f(features.get("macro_confidence"))
    if direction == "LONG" and bias == "bullish_gold_environment":
        return confidence
    if direction == "SHORT" and bias == "bearish_gold_environment":
        return confidence
    if bias == "event_risk_environment":
        return -confidence
    return 0.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
