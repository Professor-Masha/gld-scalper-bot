from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .utils.math_utils import clamp


@dataclass(slots=True)
class AgentAssessment:
    name: str
    score: float
    bias: str
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "bias": self.bias,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


class IndicatorAgent:
    name = "IndicatorAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        score = 0.0
        reasons: list[str] = []
        score = _add(score, reasons, _gt(features, "ema_9", "ema_21"), 0.25, "1m EMA bullish")
        score = _add(score, reasons, _lt(features, "ema_9", "ema_21"), -0.25, "1m EMA bearish")
        score = _add(score, reasons, _gt(features, "sma_20", "sma_50"), 0.20, "SMA trend bullish")
        score = _add(score, reasons, _lt(features, "sma_20", "sma_50"), -0.20, "SMA trend bearish")
        score = _add(score, reasons, _gt(features, "tf5_ema_9", "tf5_ema_21"), 0.20, "5m EMA bullish")
        score = _add(score, reasons, _lt(features, "tf5_ema_9", "tf5_ema_21"), -0.20, "5m EMA bearish")
        score = _add(score, reasons, _gt(features, "close", "vwap"), 0.15, "above VWAP")
        score = _add(score, reasons, _lt(features, "close", "vwap"), -0.15, "below VWAP")
        macd = _f(features.get("macd_histogram"))
        macd_slope = _f(features.get("macd_histogram_slope"))
        score = _add(score, reasons, macd >= 0 and macd_slope > 0, 0.15, "MACD improving")
        score = _add(score, reasons, macd <= 0 and macd_slope < 0, -0.15, "MACD weakening")
        rsi = _f(features.get("rsi_14"), 50.0)
        score = _add(score, reasons, 52 <= rsi <= 72, 0.12, "RSI constructive")
        score = _add(score, reasons, 28 <= rsi <= 48, -0.12, "RSI soft")
        return _assessment(self.name, score, reasons or ["mixed indicators"])


class PatternAgent:
    name = "PatternAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        classification = str(features.get("pattern_classification") or "none")
        quality = _f(features.get("pattern_quality"))
        score = 0.0
        reasons = [classification]
        if classification == "proper_break_up":
            score = 0.75 + quality * 0.25
        elif classification == "proper_break_down":
            score = -0.75 - quality * 0.25
        elif classification == "pullback_up":
            score = 0.45 + quality * 0.20
        elif classification == "pullback_down":
            score = -0.45 - quality * 0.20
        elif classification == "buildup_resistance":
            score = 0.25 + quality * 0.15
        elif classification == "buildup_support":
            score = -0.25 - quality * 0.15
        elif classification.startswith("false_break") or classification.startswith("tease_break"):
            score = 0.0
            reasons.append("break quality rejected")
        elif classification == "range_compression":
            reasons.append("compression needs a proper break")
        return _assessment(self.name, score, reasons)


class TrendAgent:
    name = "TrendAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        score = 0.0
        reasons: list[str] = []
        adx = _f(features.get("adx"))
        score = _add(score, reasons, _gt(features, "ema_9", "ema_21") and _gt(features, "ema_21", "ema_50"), 0.35, "1m trend up")
        score = _add(score, reasons, _lt(features, "ema_9", "ema_21") and _lt(features, "ema_21", "ema_50"), -0.35, "1m trend down")
        score = _add(score, reasons, _gt(features, "tf15_ema_9", "tf15_ema_21"), 0.20, "15m trend up")
        score = _add(score, reasons, _lt(features, "tf15_ema_9", "tf15_ema_21"), -0.20, "15m trend down")
        score = _add(score, reasons, adx >= 18 and score > 0, 0.15, "trend strength supports long")
        score = _add(score, reasons, adx >= 18 and score < 0, -0.15, "trend strength supports short")
        cycle_state = str(features.get("cycle_state") or "")
        score = _add(score, reasons, cycle_state in {"rising", "turning_up"}, 0.10, f"cycle {cycle_state}")
        score = _add(score, reasons, cycle_state in {"falling", "turning_down"}, -0.10, f"cycle {cycle_state}")
        return _assessment(self.name, score, reasons or ["trend unclear"])


class OrderBlockAgent:
    name = "OrderBlockAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        score = _f(features.get("order_block_score")) * _f(features.get("order_block_strength"), 0.0)
        reasons = [str(features.get("order_block_reason") or "no active order block")]
        if features.get("order_block_retest_active"):
            direction = str(features.get("order_block_retest_direction") or "")
            score += 0.25 if direction == "bullish" else -0.25 if direction == "bearish" else 0.0
            reasons.append(f"live {direction} retest")
        if features.get("order_block_conflict"):
            score *= 0.50
            reasons.append("nearby bullish and bearish zones conflict")
        return _assessment(self.name, score, reasons)


class OptionsAgent:
    name = "OptionsAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        if features.get("options_stale"):
            return _assessment(self.name, 0.0, [str(features.get("options_reason") or "options data stale")])
        raw_score = _f(features.get("options_raw_score"))
        confidence = _f(features.get("options_confidence"))
        score = clamp(raw_score * confidence, -0.50, 0.50)
        return _assessment(self.name, score, [str(features.get("options_reason") or "neutral options context")])


class RiskAgent:
    name = "RiskAgent"

    def assess(self, features: dict[str, Any]) -> AgentAssessment:
        risk_score = 0.0
        reasons: list[str] = []
        liquidity = _f(features.get("liquidity_score"), 0.5)
        spread_regime = str(features.get("spread_regime") or "unknown")
        vol_regime = str(features.get("gold_volatility_regime") or "unknown")
        if liquidity < 0.35:
            risk_score -= 0.55
            reasons.append("liquidity weak")
        elif liquidity >= 0.70:
            risk_score += 0.20
            reasons.append("liquidity healthy")
        if spread_regime == "wide":
            risk_score -= 0.45
            reasons.append("spread wide")
        elif spread_regime == "tight":
            risk_score += 0.15
            reasons.append("spread tight")
        if bool(features.get("volatility_burst")):
            risk_score -= 0.35
            reasons.append("volatility burst")
        if vol_regime in {"closed", "high", "low"}:
            risk_score -= 0.20
            reasons.append(f"gold volatility {vol_regime}")
        if bool(features.get("stream_stale")) or features.get("websocket_connected") is False:
            risk_score -= 0.60
            reasons.append("live data unhealthy")
        if bool(features.get("order_block_conflict")):
            risk_score -= 0.15
            reasons.append("order-block conflict")
        if not features.get("options_stale") and _f(features.get("options_event_risk")) >= 0.80:
            risk_score -= 0.20
            reasons.append("options-implied event risk elevated")
        return _assessment(self.name, risk_score, reasons or ["risk acceptable"])


def combine_reasoning(features: dict[str, Any]) -> dict[str, Any]:
    indicator = IndicatorAgent().assess(features)
    pattern = PatternAgent().assess(features)
    trend = TrendAgent().assess(features)
    order_block = OrderBlockAgent().assess(features)
    options = OptionsAgent().assess(features)
    risk_assessment = RiskAgent().assess(features)
    assessments = [indicator, pattern, trend, order_block, options, risk_assessment]
    directional = (
        indicator.score * 0.20
        + pattern.score * 0.28
        + trend.score * 0.20
        + order_block.score * 0.24
        + options.score * 0.08
    )
    risk = risk_assessment.score
    combined_score = clamp(directional + min(risk, 0.20), -1.0, 1.0)
    if risk <= -0.55:
        consensus = "NO_TRADE"
    elif combined_score >= 0.25:
        consensus = "LONG"
    elif combined_score <= -0.25:
        consensus = "SHORT"
    else:
        consensus = "NO_TRADE"
    confidence = clamp(abs(combined_score) + max(risk, 0.0) * 0.20, 0.0, 1.0)
    payload = [assessment.as_dict() for assessment in assessments]
    return {
        "indicator_agent_score": round(indicator.score, 4),
        "pattern_agent_score": round(pattern.score, 4),
        "trend_agent_score": round(trend.score, 4),
        "order_block_agent_score": round(order_block.score, 4),
        "options_agent_score": round(options.score, 4),
        "risk_agent_score": round(risk_assessment.score, 4),
        "agent_directional_score": round(directional, 4),
        "agent_combined_score": round(combined_score, 4),
        "agent_consensus": consensus,
        "agent_confidence": round(confidence, 4),
        "agent_reasoning_json": json.dumps(payload, sort_keys=True),
        "agent_reasoning_summary": " | ".join(f"{item.name}:{item.bias}:{item.reason}" for item in assessments),
    }


def _assessment(name: str, score: float, reasons: list[str]) -> AgentAssessment:
    score = clamp(score, -1.0, 1.0)
    if score >= 0.20:
        bias = "bullish"
    elif score <= -0.20:
        bias = "bearish"
    else:
        bias = "neutral"
    return AgentAssessment(name=name, score=score, bias=bias, confidence=abs(score), reason="; ".join(reasons))


def _add(score: float, reasons: list[str], condition: bool, points: float, reason: str) -> float:
    if condition:
        reasons.append(reason)
        return score + points
    return score


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _gt(features: dict[str, Any], left: str, right: str) -> bool:
    return _f(features.get(left)) > _f(features.get(right))


def _lt(features: dict[str, Any], left: str, right: str) -> bool:
    return _f(features.get(left)) < _f(features.get(right))
