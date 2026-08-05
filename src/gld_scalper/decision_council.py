from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import Settings
from .utils.math_utils import clamp
from .utils.time_utils import utc_now


@dataclass(frozen=True, slots=True)
class CouncilVote:
    agent: str
    direction: str
    confidence: float
    hard_block: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "hard_block": self.hard_block,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class DecisionCouncilState:
    timestamp: datetime
    consensus: str
    confidence: float
    bullish_strength: float
    bearish_strength: float
    hard_block: bool
    block_reasons: tuple[str, ...]
    votes: tuple[CouncilVote, ...]

    def as_features(self) -> dict[str, Any]:
        payload = [vote.as_dict() for vote in self.votes]
        return {
            "decision_council_timestamp": self.timestamp,
            "decision_council_consensus": self.consensus,
            "decision_council_confidence": round(self.confidence, 4),
            "decision_council_bullish_strength": round(self.bullish_strength, 4),
            "decision_council_bearish_strength": round(self.bearish_strength, 4),
            "decision_council_hard_block": self.hard_block,
            "decision_council_block_reasons": list(self.block_reasons),
            "decision_council_votes": payload,
            "decision_council_json": json.dumps(payload, sort_keys=True),
        }


class DataHealthAgent:
    name = "DataHealthAgent"

    def assess(self, features: dict[str, Any], settings: Settings) -> CouncilVote:
        reasons: list[str] = []
        if features.get("websocket_connected") is False:
            reasons.append("websocket disconnected")
        if features.get("stream_stale") is True:
            reasons.append(str(features.get("stream_stale_reason") or "live stream stale"))
        path = str(features.get("strategy_path") or "minute")
        quote_limit = (
            settings.fast_scalp_max_quote_age_seconds
            if path == "fast"
            else settings.minute_entry_max_quote_age_seconds
        )
        trade_limit = (
            settings.fast_scalp_max_trade_age_seconds
            if path == "fast"
            else settings.minute_entry_max_trade_age_seconds
        )
        quote_age = _optional_float(features.get("quote_age_seconds"))
        trade_age = _optional_float(features.get("trade_age_seconds"))
        if quote_age is not None and quote_age > quote_limit:
            reasons.append(f"quote stale at {quote_age:.2f}s")
        if trade_age is not None and trade_age > trade_limit:
            reasons.append(f"trade stale at {trade_age:.2f}s")
        hard_block = bool(reasons)
        return CouncilVote(
            agent=self.name,
            direction="NO_TRADE" if hard_block else "NEUTRAL",
            confidence=1.0 if hard_block else 0.8,
            hard_block=hard_block,
            reasons=tuple(reasons or ["live data health acceptable"]),
        )


class MicrostructureAgent:
    name = "MicrostructureAgent"

    def assess(self, features: dict[str, Any], settings: Settings) -> CouncilVote:
        liquidity = _f(features.get("liquidity_score"), 0.5)
        spread_pct = _f(features.get("spread_pct"))
        imbalance = _f(features.get("quote_imbalance"))
        intensity = _f(features.get("trade_intensity"))
        reasons: list[str] = []
        if spread_pct > settings.max_spread_pct or str(features.get("spread_regime")) == "wide":
            reasons.append("spread is not economically tradable")
        if liquidity < settings.minimum_liquidity_score:
            reasons.append("liquidity below entry standard")
        if bool(features.get("volatility_burst")):
            reasons.append("volatility burst increases execution uncertainty")
        hard_block = bool(reasons and (spread_pct > settings.max_spread_pct or liquidity < 0.35))
        directional = clamp(imbalance * 0.65 + clamp(intensity, 0.0, 2.0) * 0.05 * _sign(imbalance), -1.0, 1.0)
        direction = "LONG" if directional >= 0.15 else "SHORT" if directional <= -0.15 else "NEUTRAL"
        if hard_block:
            direction = "NO_TRADE"
        return CouncilVote(
            agent=self.name,
            direction=direction,
            confidence=clamp(abs(directional) + liquidity * 0.25, 0.0, 1.0),
            hard_block=hard_block,
            reasons=tuple(reasons or [f"liquidity={liquidity:.2f}, imbalance={imbalance:.2f}"]),
        )


class BullCaseAgent:
    name = "BullCaseAgent"

    def assess(self, features: dict[str, Any]) -> CouncilVote:
        components = [
            _positive(features.get("indicator_agent_score")),
            _positive(features.get("pattern_agent_score")),
            _positive(features.get("trend_agent_score")),
            _positive(features.get("order_block_agent_score")),
            _f(features.get("ml_probability_long")),
            _f(features.get("transformer_minute_probability_long_good")),
        ]
        strength = clamp(sum(components) / max(len(components), 1), 0.0, 1.0)
        reasons = _directional_reasons(features, "LONG")
        return CouncilVote(self.name, "LONG", strength, False, tuple(reasons or ["limited bullish evidence"]))


class BearCaseAgent:
    name = "BearCaseAgent"

    def assess(self, features: dict[str, Any]) -> CouncilVote:
        components = [
            _negative(features.get("indicator_agent_score")),
            _negative(features.get("pattern_agent_score")),
            _negative(features.get("trend_agent_score")),
            _negative(features.get("order_block_agent_score")),
            _f(features.get("ml_probability_short")),
            _f(features.get("transformer_minute_probability_short_good")),
        ]
        strength = clamp(sum(components) / max(len(components), 1), 0.0, 1.0)
        reasons = _directional_reasons(features, "SHORT")
        return CouncilVote(self.name, "SHORT", strength, False, tuple(reasons or ["limited bearish evidence"]))


class RiskCouncil:
    name = "RiskCouncil"

    def assess(self, features: dict[str, Any], bull: CouncilVote, bear: CouncilVote) -> CouncilVote:
        reasons: list[str] = []
        if features.get("event_risk_active"):
            reasons.append(str(features.get("event_risk_reason") or "scheduled event risk"))
        if _f(features.get("headline_event_risk")) >= 0.80:
            reasons.append("headline event risk elevated")
        if bool(features.get("order_block_conflict")):
            reasons.append("order-block directions conflict")
        if features.get("playbook_allowed") is False:
            reasons.append(str(features.get("playbook_block_reason") or "playbook rejected"))
        edge = abs(bull.confidence - bear.confidence)
        if edge < 0.12:
            reasons.append("bull and bear evidence remain too close")
        hard_block = bool(features.get("event_risk_active") or features.get("playbook_allowed") is False)
        direction = "NO_TRADE" if reasons else ("LONG" if bull.confidence > bear.confidence else "SHORT")
        confidence = clamp(max(bull.confidence, bear.confidence) - min(bull.confidence, bear.confidence), 0.0, 1.0)
        return CouncilVote(self.name, direction, confidence, hard_block, tuple(reasons or ["risk council accepts the stronger case"]))


def run_decision_council(
    features: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> DecisionCouncilState:
    now = now or utc_now()
    health = DataHealthAgent().assess(features, settings)
    microstructure = MicrostructureAgent().assess(features, settings)
    bull = BullCaseAgent().assess(features)
    bear = BearCaseAgent().assess(features)
    risk = RiskCouncil().assess(features, bull, bear)
    votes = (health, microstructure, bull, bear, risk)
    blockers = tuple(reason for vote in votes if vote.hard_block for reason in vote.reasons)
    if blockers:
        consensus = "NO_TRADE"
        confidence = 1.0
    else:
        margin = bull.confidence - bear.confidence
        micro_confirms = microstructure.direction in {"NEUTRAL", "LONG" if margin > 0 else "SHORT"}
        if abs(margin) >= 0.12 and risk.direction != "NO_TRADE" and micro_confirms:
            consensus = "LONG" if margin > 0 else "SHORT"
            confidence = clamp(abs(margin) + max(risk.confidence, 0.0) * 0.25, 0.0, 1.0)
        else:
            consensus = "NO_TRADE"
            confidence = clamp(1.0 - abs(margin), 0.0, 1.0)
    return DecisionCouncilState(
        timestamp=now,
        consensus=consensus,
        confidence=confidence,
        bullish_strength=bull.confidence,
        bearish_strength=bear.confidence,
        hard_block=bool(blockers),
        block_reasons=blockers,
        votes=votes,
    )


def _directional_reasons(features: dict[str, Any], direction: str) -> list[str]:
    reasons: list[str] = []
    if features.get("agent_consensus") == direction:
        reasons.append("native reasoning agents align")
    if features.get("playbook_direction") == direction:
        reasons.append(f"{features.get('playbook')} playbook aligns")
    if features.get("technical_direction") == direction:
        reasons.append("technical confluence aligns")
    predicted = "long_good" if direction == "LONG" else "short_good"
    if features.get("ml_predicted_direction") == predicted:
        reasons.append("ML direction aligns")
    return reasons


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _f(value: Any, default: float = 0.0) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _positive(value: Any) -> float:
    return clamp(_f(value), 0.0, 1.0)


def _negative(value: Any) -> float:
    return clamp(-_f(value), 0.0, 1.0)


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0
