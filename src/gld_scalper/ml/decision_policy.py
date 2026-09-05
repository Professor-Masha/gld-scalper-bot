from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(slots=True, frozen=True)
class ExpectedEdgeDecision:
    action: str
    expected_gross_return: float
    expected_cost: float
    uncertainty_penalty: float
    expected_net_edge: float
    accepted: bool
    reason: str

    def as_dict(self) -> dict[str, float | str | bool]:
        return asdict(self)


def expected_edge_decision(
    *,
    probabilities: Mapping[str, float],
    expected_returns: Mapping[str, float],
    expected_cost: float,
    uncertainty: float = 0.0,
    uncertainty_multiplier: float = 1.0,
    minimum_edge: float = 0.0001,
    minimum_confidence: float = 0.0,
    minimum_margin: float = 0.0,
) -> ExpectedEdgeDecision:
    """Choose LONG/SHORT only when calibrated, after-cost utility is positive."""

    long_probability = _finite(probabilities.get("long_good"))
    short_probability = _finite(probabilities.get("short_good"))
    no_trade_probability = _finite(probabilities.get("no_trade"))
    ranked = sorted(
        (("LONG", long_probability), ("SHORT", short_probability), ("NO_TRADE", no_trade_probability)),
        key=lambda item: item[1],
        reverse=True,
    )
    action, confidence = ranked[0]
    margin = confidence - ranked[1][1]
    gross = _finite(expected_returns.get("long_good" if action == "LONG" else "short_good")) if action != "NO_TRADE" else 0.0
    cost = max(_finite(expected_cost), 0.0)
    penalty = max(_finite(uncertainty), 0.0) * max(_finite(uncertainty_multiplier), 0.0)
    edge = gross - cost - penalty
    blocks: list[str] = []
    if action == "NO_TRADE":
        blocks.append("calibrated model prefers no-trade")
    if confidence < minimum_confidence:
        blocks.append(f"confidence {confidence:.3f} below {minimum_confidence:.3f}")
    if margin < minimum_margin:
        blocks.append(f"probability margin {margin:.3f} below {minimum_margin:.3f}")
    if edge <= minimum_edge:
        blocks.append(f"expected net edge {edge:.6f} does not exceed {minimum_edge:.6f}")
    return ExpectedEdgeDecision(
        action=action if not blocks else "NO_TRADE",
        expected_gross_return=gross,
        expected_cost=cost,
        uncertainty_penalty=penalty,
        expected_net_edge=edge,
        accepted=not blocks,
        reason="accepted calibrated after-cost edge" if not blocks else "; ".join(blocks),
    )


def _finite(value: object, default: float = 0.0) -> float:
    try:
        result = float(value) if value is not None else default
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default
