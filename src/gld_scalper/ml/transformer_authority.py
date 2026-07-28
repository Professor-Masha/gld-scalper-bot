from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..config import Settings
from ..models import MarketSignal
from ..utils.math_utils import clamp
from .transformer_runtime import TransformerShadowPrediction


LABEL_TO_ACTION = {"long_good": "LONG", "short_good": "SHORT", "no_trade": "NO_TRADE"}


def apply_transformer_to_signal(
    signal: MarketSignal,
    prediction: TransformerShadowPrediction,
    features: dict[str, Any],
    settings: Settings,
) -> MarketSignal:
    """Apply paper-only Transformer authority without touching broker methods."""

    mode = settings.transformer_trading_mode
    action, confidence, edge, eligible, reason = _advice(prediction, features, settings)
    features.update(
        {
            "transformer_authority_mode": mode,
            "transformer_authority_action": action,
            "transformer_authority_confidence": confidence,
            "transformer_authority_edge": edge,
            "transformer_authority_eligible": eligible,
            "transformer_authority_reason": reason,
            "transformer_original_decision": signal.decision,
        }
    )
    if mode == "shadow" or not eligible:
        return replace(signal, features=dict(features))

    if mode == "bounded_adviser":
        if signal.decision == "NO_TRADE":
            features["transformer_authority_effect"] = "observed_only_cannot_originate"
            return replace(signal, features=dict(features))
        if action == "NO_TRADE" or action not in {signal.decision, "NO_TRADE"}:
            features["transformer_authority_effect"] = "veto"
            return replace(
                signal,
                decision="NO_TRADE",
                no_trade_score=max(signal.no_trade_score, 80.0),
                confidence=max(signal.confidence, confidence),
                reason=f"bounded Transformer veto: {reason}",
                features=dict(features),
            )
        adjustment = clamp(
            confidence * settings.transformer_bounded_max_score_adjustment,
            0.0,
            settings.transformer_bounded_max_score_adjustment,
        )
        features["transformer_authority_effect"] = "aligned_score_adjustment"
        return replace(
            signal,
            bullish_score=signal.bullish_score + adjustment if action == "LONG" else signal.bullish_score,
            bearish_score=signal.bearish_score + adjustment if action == "SHORT" else signal.bearish_score,
            confidence=clamp(signal.confidence + adjustment / 100.0, 0.0, 1.0),
            reason=f"{signal.reason}; bounded Transformer aligned",
            features=dict(features),
        )

    # paper_champion may recommend an action, but only a promoted champion is
    # loaded and every normal risk, entry-quality, shortability, and execution
    # gate runs after this function.
    if prediction.model_role != "paper_champion":
        features["transformer_authority_effect"] = "fallback_not_champion"
        return replace(signal, features=dict(features))
    if action == "NO_TRADE":
        features["transformer_authority_effect"] = "champion_abstained"
        return replace(
            signal,
            decision="NO_TRADE",
            no_trade_score=max(signal.no_trade_score, 80.0),
            reason=f"paper Transformer champion abstained: {reason}",
            features=dict(features),
        )
    if signal.decision not in {"NO_TRADE", action}:
        features["transformer_authority_effect"] = "champion_vetoed_conflict"
        return replace(
            signal,
            decision="NO_TRADE",
            no_trade_score=max(signal.no_trade_score, 85.0),
            reason=f"paper Transformer champion conflicts with deterministic route: {reason}",
            features=dict(features),
        )
    if signal.decision == "NO_TRADE" and not _may_originate(action, features):
        features["transformer_authority_effect"] = "champion_origin_blocked_by_confluence"
        return replace(signal, features=dict(features))
    features["transformer_authority_effect"] = "champion_recommendation"
    score = max(76.0, signal.bullish_score if action == "LONG" else signal.bearish_score)
    return replace(
        signal,
        decision=action,
        bullish_score=score if action == "LONG" else signal.bullish_score,
        bearish_score=score if action == "SHORT" else signal.bearish_score,
        no_trade_score=min(signal.no_trade_score, 55.0),
        confidence=max(signal.confidence, confidence),
        reason=f"paper Transformer champion recommendation: {reason}",
        features=dict(features),
    )


def apply_transformer_to_fast_decision(
    decision: Any,
    prediction: TransformerShadowPrediction,
    settings: Settings,
) -> None:
    action, confidence, edge, eligible, reason = _advice(prediction, decision.features, settings)
    decision.features.update(
        {
            "transformer_authority_mode": settings.transformer_trading_mode,
            "transformer_authority_action": action,
            "transformer_authority_confidence": confidence,
            "transformer_authority_edge": edge,
            "transformer_authority_eligible": eligible,
            "transformer_authority_reason": reason,
            "transformer_original_decision": decision.decision,
        }
    )
    if settings.transformer_trading_mode == "shadow" or not eligible:
        return
    if settings.transformer_trading_mode == "bounded_adviser":
        if decision.is_trade and action not in {decision.decision}:
            decision.allowed = False
            decision.decision = "NO_TRADE"
            decision.reason = f"bounded Transformer veto: {reason}"
        elif decision.is_trade and action == decision.decision:
            decision.score += min(
                settings.transformer_bounded_max_score_adjustment,
                confidence * settings.transformer_bounded_max_score_adjustment,
            )
            decision.confidence = clamp(decision.confidence + 0.03, 0.0, 1.0)
        return
    if prediction.model_role != "paper_champion":
        return
    if action == "NO_TRADE" or decision.is_trade and action != decision.decision:
        decision.allowed = False
        decision.decision = "NO_TRADE"
        decision.reason = f"paper Transformer champion abstained or vetoed: {reason}"
    elif not decision.is_trade and action in {"LONG", "SHORT"} and _may_originate(action, decision.features):
        decision.decision = action
        decision.allowed = True
        decision.confidence = confidence
        decision.score = max(decision.score, 80.0)
        decision.reason = f"paper Transformer champion recommendation: {reason}"


def _advice(
    prediction: TransformerShadowPrediction,
    features: dict[str, Any],
    settings: Settings,
) -> tuple[str, float, float, bool, str]:
    action = LABEL_TO_ACTION.get(str(prediction.predicted_direction or ""), "NO_TRADE")
    confidence = max(prediction.class_probabilities.values(), default=0.0)
    horizon = 1 if prediction.scope == "fast_microstructure" else 5
    expected_return = float(prediction.expected_returns.get(horizon, 0.0) or 0.0)
    expected_cost = max(0.0, float(prediction.expected_cost or 0.0))
    uncertainty = 1.0 if prediction.uncertainty is None else float(prediction.uncertainty)
    directional_return = expected_return if action == "LONG" else -expected_return if action == "SHORT" else 0.0
    edge = directional_return - expected_cost
    blocks = _hard_blocks(features)
    if prediction.status in {"unavailable", "no_model", "stale", "error"}:
        blocks.append(prediction.reason or prediction.status)
    if confidence < settings.transformer_adviser_min_confidence:
        blocks.append("confidence below adviser threshold")
    if uncertainty > settings.transformer_adviser_max_uncertainty:
        blocks.append("uncertainty above adviser threshold")
    if action != "NO_TRADE" and edge < settings.transformer_champion_min_expected_edge_pct:
        blocks.append("expected return does not clear estimated cost and edge buffer")
    reason = (
        f"model={prediction.model_version}; action={action}; confidence={confidence:.3f}; "
        f"edge={edge:.6f}; uncertainty={uncertainty:.3f}"
    )
    if blocks:
        reason += "; blocked: " + "; ".join(blocks)
    return action, confidence, edge, not blocks, reason


def _may_originate(action: str, features: dict[str, Any]) -> bool:
    return (
        features.get("technical_direction") == action
        and float(features.get("technical_quality") or 0.0) >= 0.60
        and bool(features.get("technical_big3_aligned") or features.get("event_gold_confirmed"))
        and features.get("playbook_allowed") is not False
    )


def _hard_blocks(features: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if features.get("stream_stale") or features.get("websocket_connected") is False:
        blocks.append("live data unhealthy")
    if str(features.get("spread_regime") or "") == "wide":
        blocks.append("wide spread")
    if float(features.get("liquidity_score") or 0.0) < 0.40:
        blocks.append("poor liquidity")
    if features.get("event_risk_active") and not features.get("event_post_release"):
        blocks.append("pre-release event window")
    if features.get("entry_freeze_active"):
        blocks.append("entry freeze active")
    return blocks
