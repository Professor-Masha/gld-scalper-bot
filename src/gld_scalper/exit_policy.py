from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .models import MarketSignal, RiskState
from .utils.math_utils import clamp, safe_div


@dataclass(frozen=True, slots=True)
class ExitGeometry:
    stop_price: float
    target_price: float
    stop_distance: float
    target_distance: float
    stop_distance_pct: float
    target_distance_pct: float
    economic_breakeven_pct: float
    playbook: str
    method: str
    rejected_reason: str | None = None


def economic_breakeven_pct(
    settings: Settings,
    *,
    spread_pct: float,
    fee_pct: float = 0.0,
) -> float:
    """Estimated round-trip move required before a position is economically profitable."""
    return max(0.0, spread_pct) + settings.estimated_round_trip_slippage_pct + max(0.0, fee_pct) + settings.economic_breakeven_safety_buffer_pct


def build_exit_geometry(
    settings: Settings,
    signal: MarketSignal,
    state: RiskState,
    entry_price: float,
) -> ExitGeometry:
    features = signal.features
    playbook = str(features.get("playbook") or "unclassified")
    strategy_atr = (
        float(features.get("ema_cross_atr") or 0.0)
        if playbook == "ema_cross_filtered"
        else 0.0
    )
    atr = max(strategy_atr, float(state.atr or 0.0), entry_price * 0.00025)
    fee_pct = safe_div(settings.estimated_fee_per_share * 2.0, entry_price)
    breakeven_pct = economic_breakeven_pct(settings, spread_pct=state.spread_pct, fee_pct=fee_pct)
    buffer = atr * settings.position_structure_buffer_atr

    raw_distance, method = _setup_stop_distance(
        signal.decision,
        playbook,
        features,
        entry_price,
        atr,
        buffer,
    )
    minimum_distance = entry_price * max(breakeven_pct * 1.20, 0.00035)
    stop_distance = max(raw_distance, minimum_distance, 0.02)
    maximum_distance = entry_price * settings.position_max_normal_stop_pct
    rejected_reason = None
    if stop_distance > maximum_distance + 1e-9:
        if playbook == "ema_cross_filtered":
            stop_distance = maximum_distance
            method = f"{method}+normal_risk_cap"
        else:
            rejected_reason = (
                f"{playbook} structural stop {safe_div(stop_distance, entry_price):.6f} exceeds "
                f"maximum normal stop {settings.position_max_normal_stop_pct:.6f}"
            )

    target_r = clamp(float(features.get("playbook_target_r_multiple") or 1.20), settings.position_min_reward_risk, 2.50)
    if playbook == "false_break_reversal":
        target_r = max(settings.position_min_reward_risk, min(target_r, 1.35))
    elif playbook in {"proper_breakout", "compression_breakout", "trend_continuation"}:
        target_r = max(target_r, 1.40)
    target_distance = max(
        stop_distance * target_r,
        entry_price * breakeven_pct * 1.50,
        0.03,
    )
    technical_target = (
        0.0
        if playbook == "ema_cross_filtered"
        else _positive(features.get("technical_target_1"))
    )
    if technical_target:
        directional_distance = (
            technical_target - entry_price
            if signal.decision == "LONG"
            else entry_price - technical_target
        )
        if directional_distance > minimum_distance:
            target_distance = max(directional_distance, minimum_distance)
            method = f"{method}+technical_target"
    if signal.decision == "LONG":
        stop_price = entry_price - stop_distance
        target_price = entry_price + target_distance
    else:
        stop_price = entry_price + stop_distance
        target_price = entry_price - target_distance
    return ExitGeometry(
        stop_price=max(0.01, stop_price),
        target_price=max(0.01, target_price),
        stop_distance=stop_distance,
        target_distance=target_distance,
        stop_distance_pct=safe_div(stop_distance, entry_price),
        target_distance_pct=safe_div(target_distance, entry_price),
        economic_breakeven_pct=breakeven_pct,
        playbook=playbook,
        method=method,
        rejected_reason=rejected_reason,
    )


def _setup_stop_distance(
    direction: str,
    playbook: str,
    features: dict[str, Any],
    entry_price: float,
    atr: float,
    buffer: float,
) -> tuple[float, str]:
    support = _positive(features.get("support_level") or features.get("range_low"))
    resistance = _positive(features.get("resistance_level") or features.get("range_high"))
    multipliers = {
        "spread_capture": 0.45,
        "false_break_reversal": 0.65,
        "proper_breakout": 0.75,
        "compression_breakout": 0.75,
        "buildup_break": 0.80,
        "pullback_continuation": 0.85,
        "trend_continuation": 0.90,
        "news_event": 1.00,
        "ema_cross_filtered": 1.50,
    }
    baseline = atr * multipliers.get(playbook, 0.80)
    if playbook == "ema_cross_filtered":
        return baseline, "pine_atr_stop"

    technical_stop = _positive(features.get("technical_stop_price"))
    technical_direction = str(features.get("technical_direction") or "NO_TRADE")
    if technical_direction == direction:
        distance = entry_price - technical_stop if direction == "LONG" else technical_stop - entry_price
        if 0 < distance <= baseline * 2.25:
            return max(distance, baseline * 0.60), "technical_structure_stop"

    if direction == "LONG":
        structure = resistance - buffer if playbook in {"proper_breakout", "compression_breakout", "buildup_break"} else support - buffer
        if playbook == "false_break_reversal":
            structure = support - buffer
        distance = entry_price - structure if structure > 0 and structure < entry_price else baseline
    else:
        structure = support + buffer if playbook in {"proper_breakout", "compression_breakout", "buildup_break"} else resistance + buffer
        if playbook == "false_break_reversal":
            structure = resistance + buffer
        distance = structure - entry_price if structure > entry_price else baseline
    # A session-wide support/resistance level can be far outside a scalp's useful
    # risk envelope. In that case it is context, not a valid protective stop.
    if distance > baseline * 2.25:
        return baseline, "playbook_atr_structure_out_of_range"
    method = "structure_plus_atr_buffer" if abs(distance - baseline) > 1e-9 else "playbook_atr"
    return max(distance, baseline * 0.60), method


def _positive(value: Any) -> float:
    try:
        result = float(value or 0.0)
        return result if result > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
