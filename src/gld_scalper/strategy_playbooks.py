from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .utils.math_utils import clamp


@dataclass(slots=True)
class PlaybookDecision:
    playbook: str
    direction: str
    score: float
    allowed: bool
    block_reason: str
    expected_hold_minutes: int
    target_r_multiple: float
    confirmations: tuple[str, ...] = ()
    required_confirmations: int = 0

    def to_features(self) -> dict[str, Any]:
        return {
            "playbook": self.playbook,
            "playbook_direction": self.direction,
            "playbook_score": self.score,
            "playbook_allowed": self.allowed,
            "playbook_block_reason": self.block_reason,
            "playbook_expected_hold_minutes": self.expected_hold_minutes,
            "playbook_target_r_multiple": self.target_r_multiple,
            "playbook_confirmations": list(self.confirmations),
            "playbook_confirmation_count": len(self.confirmations),
            "playbook_required_confirmations": self.required_confirmations,
        }


def evaluate_playbooks(features: dict[str, Any], settings: Settings) -> PlaybookDecision:
    ema_cross = _ema_cross_filtered(features, settings)
    candidates = [
        _proper_breakout(features),
        _buildup_break(features),
        _pullback_continuation(features),
        _trend_continuation(features),
        _false_break_reversal(features),
        _compression_breakout(features),
        _news_event(features),
    ]
    best = (
        ema_cross
        if ema_cross.direction in {"LONG", "SHORT"}
        else max(candidates, key=lambda item: item.score)
    )
    blocks = _strict_blocks(best, features, settings)
    if blocks:
        return PlaybookDecision(
            best.playbook,
            best.direction,
            best.score,
            False,
            "; ".join(blocks),
            best.expected_hold_minutes,
            best.target_r_multiple,
            best.confirmations,
            best.required_confirmations,
        )
    return best


def _ema_cross_filtered(f: dict[str, Any], settings: Settings) -> PlaybookDecision:
    active = bool(f.get("ema_cross_signal_active"))
    direction = str(f.get("ema_cross_direction") or "NO_TRADE") if active else "NO_TRADE"
    if direction not in {"LONG", "SHORT"}:
        direction = "NO_TRADE"
    score = _num(f.get("ema_cross_score")) if direction != "NO_TRADE" else 0.0
    timeframe = int(_num(f.get("ema_cross_timeframe_minutes"), 1.0))
    adx_value = f.get("ema_cross_adx")
    adx_ok = bool(
        not settings.ema_cross_use_adx_filter
        or adx_value is not None
        and _num(adx_value) >= settings.ema_cross_adx_threshold
    )
    confirmations = _confirmations(
        ("completed_bar_ema_cross", bool(f.get("ema_cross_closed_bar_confirmed"))),
        ("adx_trend_filter", adx_ok),
        ("persistent_signal_deduplication", bool(f.get("ema_cross_signal_ids"))),
    )
    expected_hold = max(5, min(90, timeframe * 2))
    target_r = (
        settings.ema_cross_take_profit_atr_multiple
        / settings.ema_cross_stop_loss_atr_multiple
    )
    return PlaybookDecision(
        "ema_cross_filtered",
        direction,
        round(clamp(score, 0, 100), 2),
        direction != "NO_TRADE" and adx_ok,
        "" if adx_ok else "EMA cross ADX filter failed",
        expected_hold,
        target_r,
        confirmations,
        3,
    )


def _proper_breakout(f: dict[str, Any]) -> PlaybookDecision:
    pattern = str(f.get("pattern_classification") or "")
    direction = "LONG" if pattern == "proper_break_up" else "SHORT" if pattern == "proper_break_down" else "NO_TRADE"
    quality = _num(f.get("pattern_quality"))
    liquidity = _num(f.get("liquidity_score"), 0.5)
    volume = _num(f.get("relative_volume"), 1.0)
    score = 30 + quality * 35 + liquidity * 20 + min(volume, 2.0) * 7.5 if direction != "NO_TRADE" else 0.0
    confirmations = _confirmations(
        ("proper_break", bool(f.get("proper_break"))),
        ("pattern_quality", quality >= 0.58),
        ("volume", volume >= 1.0),
        ("buildup_or_compression", bool(f.get("buildup_detected") or f.get("range_compression"))),
    )
    return PlaybookDecision("proper_breakout", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 10, 1.4, confirmations, 3)


def _buildup_break(f: dict[str, Any]) -> PlaybookDecision:
    side = str(f.get("buildup_side") or "")
    breakout_direction = str(f.get("breakout_direction") or "")
    direction = "LONG" if side == "resistance" and breakout_direction == "up" else "SHORT" if side == "support" and breakout_direction == "down" else "NO_TRADE"
    buildup = _num(f.get("buildup_score"))
    compression = 1.0 if f.get("range_compression") else 0.0
    liquidity = _num(f.get("liquidity_score"), 0.5)
    score = 25 + buildup * 35 + compression * 15 + liquidity * 25 if direction != "NO_TRADE" else 0.0
    confirmations = _confirmations(
        ("buildup", bool(f.get("buildup_detected"))),
        ("compression", bool(f.get("range_compression"))),
        ("proper_break", bool(f.get("proper_break"))),
        ("volume", _num(f.get("relative_volume"), 1.0) >= 1.0),
    )
    return PlaybookDecision("buildup_break", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 12, 1.5, confirmations, 4)


def _pullback_continuation(f: dict[str, Any]) -> PlaybookDecision:
    pattern = str(f.get("pattern_classification") or "")
    direction = "LONG" if pattern == "pullback_up" else "SHORT" if pattern == "pullback_down" else "NO_TRADE"
    trend_ok = (
        _num(f.get("ema_9")) > _num(f.get("ema_21")) and direction == "LONG"
        or _num(f.get("ema_9")) < _num(f.get("ema_21")) and direction == "SHORT"
    )
    tf_ok = (
        _num(f.get("tf5_ema_9")) > _num(f.get("tf5_ema_21")) and direction == "LONG"
        or _num(f.get("tf5_ema_9")) < _num(f.get("tf5_ema_21")) and direction == "SHORT"
    )
    quality = _num(f.get("pattern_quality"))
    score = 30 + quality * 30 + (20 if trend_ok else 0) + (20 if tf_ok else 0) if direction != "NO_TRADE" else 0.0
    reclaim = (
        direction == "LONG" and _num(f.get("close")) >= max(_num(f.get("ema_9")), _num(f.get("vwap")))
        or direction == "SHORT" and _num(f.get("close")) <= min(_num(f.get("ema_9")), _num(f.get("vwap")))
    )
    confirmations = _confirmations(
        ("pullback_pattern", direction != "NO_TRADE"),
        ("one_minute_trend", trend_ok),
        ("five_minute_trend", tf_ok),
        ("ema_vwap_reclaim", reclaim),
    )
    return PlaybookDecision("pullback_continuation", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 15, 1.25, confirmations, 4)


def _trend_continuation(f: dict[str, Any]) -> PlaybookDecision:
    long_ok = _num(f.get("ema_9")) > _num(f.get("ema_21")) and _num(f.get("close")) > _num(f.get("vwap")) and _num(f.get("close")) > _num(f.get("ema_50"))
    short_ok = _num(f.get("ema_9")) < _num(f.get("ema_21")) and _num(f.get("close")) < _num(f.get("vwap")) and _num(f.get("close")) < _num(f.get("ema_50"))
    tf5_long = _num(f.get("tf5_ema_9")) >= _num(f.get("tf5_ema_21"))
    tf5_short = _num(f.get("tf5_ema_9")) <= _num(f.get("tf5_ema_21"))
    direction = "LONG" if long_ok and tf5_long else "SHORT" if short_ok and tf5_short else "NO_TRADE"
    adx_score = clamp(_num(f.get("adx")) / 28.0, 0.0, 1.0)
    volume_score = clamp(_num(f.get("relative_volume"), 1.0) / 1.4, 0.0, 1.0)
    liquidity = _num(f.get("liquidity_score"), 0.55)
    score = 42 + adx_score * 20 + volume_score * 18 + liquidity * 20 if direction != "NO_TRADE" else 0.0
    confirmations = _confirmations(
        ("trend_stack", direction != "NO_TRADE"),
        ("five_minute_alignment", tf5_long if direction == "LONG" else tf5_short),
        ("trend_strength", _num(f.get("adx")) >= 18),
        ("volume", _num(f.get("relative_volume"), 1.0) >= 0.9),
    )
    return PlaybookDecision("trend_continuation", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 10, 1.18, confirmations, 3)


def _false_break_reversal(f: dict[str, Any]) -> PlaybookDecision:
    pattern = str(f.get("pattern_classification") or "")
    direction = "SHORT" if pattern == "false_break_up" else "LONG" if pattern == "false_break_down" else "NO_TRADE"
    wick_rejection = max(_num(f.get("upper_wick")), _num(f.get("lower_wick")))
    candle_range = _num(f.get("candle_range"), 1.0)
    rejection_strength = clamp(wick_rejection / max(candle_range, 0.01), 0.0, 1.0)
    score = 35 + rejection_strength * 35 + _num(f.get("liquidity_score"), 0.5) * 20 if direction != "NO_TRADE" else 0.0
    returned_to_range = bool(f.get("false_break")) or (
        pattern == "false_break_up" and _num(f.get("close")) <= _num(f.get("recent_high_20"), _num(f.get("resistance")))
        or pattern == "false_break_down" and _num(f.get("close")) >= _num(f.get("recent_low_20"), _num(f.get("support")))
    )
    confirmations = _confirmations(
        ("false_break_pattern", direction != "NO_TRADE"),
        ("wick_rejection", rejection_strength >= 0.30),
        ("returned_inside_range", returned_to_range),
        ("not_volatility_burst", not bool(f.get("volatility_burst"))),
    )
    return PlaybookDecision("false_break_reversal", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 8, 1.1, confirmations, 3)


def _compression_breakout(f: dict[str, Any]) -> PlaybookDecision:
    direction_text = str(f.get("breakout_direction") or "")
    direction = "LONG" if direction_text == "up" else "SHORT" if direction_text == "down" else "NO_TRADE"
    compression = bool(f.get("range_compression"))
    proper = bool(f.get("proper_break"))
    volume = _num(f.get("relative_volume"), 1.0)
    quality = _num(f.get("pattern_quality"))
    score = 30 + 20 * compression + 20 * proper + min(volume, 2.0) * 10 + quality * 10 if direction != "NO_TRADE" else 0
    confirmations = _confirmations(
        ("compression", compression),
        ("proper_break", proper),
        ("volume_expansion", volume >= 1.05),
        ("pattern_quality", quality >= 0.58),
    )
    return PlaybookDecision("compression_breakout", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 10, 1.4, confirmations, 4)


def _news_event(f: dict[str, Any]) -> PlaybookDecision:
    post_release = bool(f.get("event_post_release") or f.get("news_event_post_release"))
    pressure = _num(f.get("micro_price_pressure")) + _num(f.get("midpoint_move_pct")) * 100
    direction = "LONG" if pressure > 0.25 else "SHORT" if pressure < -0.25 else "NO_TRADE"
    score = 40 + min(abs(pressure), 1.0) * 25 + (20 if f.get("proper_break") else 0) + _num(f.get("liquidity_score"), 0.5) * 15 if direction != "NO_TRADE" else 0
    confirmations = _confirmations(
        ("post_release", post_release),
        ("volatility_burst", bool(f.get("volatility_burst"))),
        ("proper_break", bool(f.get("proper_break"))),
        ("directional_pressure", direction != "NO_TRADE"),
    )
    return PlaybookDecision("news_event", direction, round(clamp(score, 0, 100), 2), direction != "NO_TRADE", "", 5, 1.2, confirmations, 4)


def _strict_blocks(decision: PlaybookDecision, f: dict[str, Any], settings: Settings) -> list[str]:
    blocks: list[str] = []
    if decision.direction == "NO_TRADE":
        blocks.append("no valid playbook")
    if decision.score < settings.minimum_playbook_score:
        blocks.append(f"playbook score {decision.score:.1f} below {settings.minimum_playbook_score:.1f}")
    if decision.playbook in {"proper_breakout", "buildup_break", "pullback_continuation"} and _num(f.get("pattern_quality")) < settings.minimum_pattern_quality:
        blocks.append("pattern quality below minimum")
    if str(f.get("spread_regime") or "") != "unknown" and _num(f.get("liquidity_score"), 1.0) < settings.minimum_liquidity_score:
        blocks.append("liquidity score below minimum")
    if len(decision.confirmations) < decision.required_confirmations:
        blocks.append(
            f"{decision.playbook} confirmations {len(decision.confirmations)}/{decision.required_confirmations}"
        )
    if settings.require_proper_break_for_breakout and decision.playbook in {"proper_breakout", "buildup_break", "compression_breakout"} and not f.get("proper_break"):
        blocks.append("breakout playbook requires proper break")
    if settings.avoid_midday_chop and f.get("lunch_period") and not f.get("proper_break"):
        blocks.append("midday chop requires a proper break")
    if settings.avoid_event_risk_trading and f.get("event_risk_active") and decision.playbook != "news_event":
        blocks.append("scheduled event risk window active")
    if f.get("stream_stale") or f.get("websocket_connected") is False:
        blocks.append("live stream freshness block")
    if str(f.get("spread_regime") or "") == "wide":
        blocks.append("wide spread regime")
    if f.get("volatility_burst") and not f.get("proper_break"):
        blocks.append("volatility burst without proper break")
    return blocks


def _confirmations(*conditions: tuple[str, bool]) -> tuple[str, ...]:
    return tuple(name for name, confirmed in conditions if confirmed)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
