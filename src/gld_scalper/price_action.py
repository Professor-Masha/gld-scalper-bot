from __future__ import annotations

from statistics import mean
from typing import Any

from .utils.math_utils import clamp, safe_div


def analyze_price_action(bars: list[dict[str, Any]], lookback: int = 40) -> dict[str, Any]:
    """Classify Volman-style price action around the latest completed bar."""
    rows = sorted([dict(row) for row in bars if row], key=lambda row: str(row.get("timestamp")))
    if len(rows) < 8:
        return _empty("not enough bars for price-action classification")

    window = rows[-lookback:]
    latest = window[-1]
    prior = window[:-1]
    close = _f(latest.get("close"))
    high = _f(latest.get("high"))
    low = _f(latest.get("low"))
    open_ = _f(latest.get("open"))
    if close <= 0 or not prior:
        return _empty("latest bar missing usable OHLC data")

    highs = [_f(row.get("high")) for row in prior]
    lows = [_f(row.get("low")) for row in prior]
    closes = [_f(row.get("close")) for row in window]
    ranges = [max(0.0, _f(row.get("high")) - _f(row.get("low"))) for row in window]
    bodies = [abs(_f(row.get("close")) - _f(row.get("open"))) for row in window]

    resistance = max(highs)
    support = min(lows)
    range_width = max(resistance - support, 0.0)
    range_width_pct = safe_div(range_width, close)
    atr_proxy = mean(ranges[-14:]) if ranges[-14:] else range_width
    atr_pct = safe_div(atr_proxy, close)
    break_buffer = max(atr_proxy * 0.18, close * 0.00035, 0.01)

    recent_ranges = ranges[-8:]
    previous_ranges = ranges[-24:-8] or ranges[:-8] or recent_ranges
    recent_range = max(_f(row.get("high")) for row in window[-8:]) - min(_f(row.get("low")) for row in window[-8:])
    previous_avg_range = mean(previous_ranges) if previous_ranges else mean(recent_ranges)
    recent_avg_range = mean(recent_ranges) if recent_ranges else 0.0
    range_compression = bool(
        recent_avg_range <= previous_avg_range * 0.72
        or range_width_pct <= max(0.0025, atr_pct * 3.2)
    )

    body = abs(close - open_)
    candle_range = max(high - low, 0.0)
    body_to_range = safe_div(body, candle_range)
    close_location = safe_div(close - low, candle_range)
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    recent_volume = mean([_f(row.get("volume")) for row in window[-6:]])
    base_volume = mean([_f(row.get("volume")) for row in window[-24:-6] or window[:-6] or window[-6:]])
    volume_confirmation = recent_volume >= base_volume * 0.80 if base_volume else True

    near_resistance = abs(resistance - close) <= max(atr_proxy * 1.2, close * 0.0015)
    near_support = abs(close - support) <= max(atr_proxy * 1.2, close * 0.0015)
    tight_bodies = mean([safe_div(body_value, range_value) for body_value, range_value in zip(bodies[-6:], ranges[-6:])]) <= 0.55
    closes_higher = closes[-1] >= closes[-4] if len(closes) >= 4 else False
    closes_lower = closes[-1] <= closes[-4] if len(closes) >= 4 else False
    buildup_side = "resistance" if near_resistance and closes_higher else "support" if near_support and closes_lower else "none"
    buildup_score = 0.0
    buildup_score += 0.35 if buildup_side != "none" else 0.0
    buildup_score += 0.25 if range_compression else 0.0
    buildup_score += 0.20 if recent_range <= max(atr_proxy * 5.0, close * 0.0030) else 0.0
    buildup_score += 0.10 if tight_bodies else 0.0
    buildup_score += 0.10 if volume_confirmation else 0.0
    buildup_score = clamp(buildup_score, 0.0, 1.0)
    buildup_detected = buildup_score >= 0.60

    closes_above_resistance = close > resistance + break_buffer
    closes_below_support = close < support - break_buffer
    pierces_resistance = high > resistance + break_buffer * 0.45
    pierces_support = low < support - break_buffer * 0.45
    breakout_direction = "up" if closes_above_resistance else "down" if closes_below_support else "none"

    proper_break_up = bool(
        closes_above_resistance
        and body_to_range >= 0.45
        and close_location >= 0.62
        and upper_wick <= max(body * 0.90, atr_proxy * 0.30)
        and (buildup_detected or volume_confirmation)
    )
    proper_break_down = bool(
        closes_below_support
        and body_to_range >= 0.45
        and close_location <= 0.38
        and lower_wick <= max(body * 0.90, atr_proxy * 0.30)
        and (buildup_detected or volume_confirmation)
    )
    proper_break = proper_break_up or proper_break_down

    false_break_up = bool(pierces_resistance and close <= resistance and upper_wick >= max(body * 1.25, atr_proxy * 0.30))
    false_break_down = bool(pierces_support and close >= support and lower_wick >= max(body * 1.25, atr_proxy * 0.30))
    false_break = false_break_up or false_break_down

    weak_close_beyond_up = bool(pierces_resistance and not proper_break_up and close > resistance - break_buffer * 0.25)
    weak_close_beyond_down = bool(pierces_support and not proper_break_down and close < support + break_buffer * 0.25)
    tease_break = bool((weak_close_beyond_up or weak_close_beyond_down) and not false_break and not proper_break)

    pullback_direction = _pullback_direction(window, atr_proxy)
    pullback_detected = pullback_direction != "none"

    if proper_break_up:
        classification = "proper_break_up"
    elif proper_break_down:
        classification = "proper_break_down"
    elif false_break_up:
        classification = "false_break_up"
    elif false_break_down:
        classification = "false_break_down"
    elif tease_break and pierces_resistance:
        classification = "tease_break_up"
    elif tease_break and pierces_support:
        classification = "tease_break_down"
    elif pullback_detected:
        classification = f"pullback_{pullback_direction}"
    elif buildup_detected:
        classification = f"buildup_{buildup_side}"
    elif range_compression:
        classification = "range_compression"
    else:
        classification = "none"

    pattern_quality = _pattern_quality(
        classification=classification,
        buildup_score=buildup_score,
        body_to_range=body_to_range,
        close_location=close_location,
        volume_confirmation=volume_confirmation,
        range_compression=range_compression,
    )
    reason = _reason(
        classification=classification,
        buildup_score=buildup_score,
        range_width_pct=range_width_pct,
        break_buffer=break_buffer,
        volume_confirmation=volume_confirmation,
    )

    return {
        "support_level": support,
        "resistance_level": resistance,
        "range_high": resistance,
        "range_low": support,
        "range_width_pct": range_width_pct,
        "range_compression": range_compression,
        "buildup_detected": buildup_detected,
        "buildup_score": buildup_score,
        "buildup_side": buildup_side,
        "breakout_direction": breakout_direction,
        "proper_break": proper_break,
        "false_break": false_break,
        "tease_break": tease_break,
        "pullback_detected": pullback_detected,
        "pullback_direction": pullback_direction,
        "pattern_classification": classification,
        "pattern_quality": pattern_quality,
        "pattern_reason": reason,
    }


def _pullback_direction(window: list[dict[str, Any]], atr_proxy: float) -> str:
    if len(window) < 14:
        return "none"
    closes = [_f(row.get("close")) for row in window]
    highs = [_f(row.get("high")) for row in window]
    lows = [_f(row.get("low")) for row in window]
    latest_close = closes[-1]
    prior_close = closes[-8]
    recent_high = max(highs[-8:-2])
    recent_low = min(lows[-8:-2])
    previous_high = max(highs[-18:-8] or highs[:-8])
    previous_low = min(lows[-18:-8] or lows[:-8])
    shallow_retrace = max(atr_proxy * 0.35, latest_close * 0.0007)
    uptrend_pullback = (
        recent_high > previous_high
        and latest_close < recent_high
        and latest_close >= recent_high - max(atr_proxy * 2.4, latest_close * 0.003)
        and latest_close > previous_low + shallow_retrace
        and latest_close >= prior_close - max(atr_proxy * 2.0, latest_close * 0.0025)
    )
    downtrend_pullback = (
        recent_low < previous_low
        and latest_close > recent_low
        and latest_close <= recent_low + max(atr_proxy * 2.4, latest_close * 0.003)
        and latest_close < previous_high - shallow_retrace
        and latest_close <= prior_close + max(atr_proxy * 2.0, latest_close * 0.0025)
    )
    if uptrend_pullback:
        return "up"
    if downtrend_pullback:
        return "down"
    return "none"


def _pattern_quality(
    *,
    classification: str,
    buildup_score: float,
    body_to_range: float,
    close_location: float,
    volume_confirmation: bool,
    range_compression: bool,
) -> float:
    if classification.startswith("proper_break"):
        directional_close = max(close_location, 1.0 - close_location)
        quality = 0.45 + buildup_score * 0.30 + body_to_range * 0.15 + directional_close * 0.10
    elif classification.startswith("pullback"):
        quality = 0.50 + buildup_score * 0.20 + (0.10 if range_compression else 0.0)
    elif classification.startswith("buildup"):
        quality = 0.35 + buildup_score * 0.45
    elif classification == "range_compression":
        quality = 0.42
    elif classification.startswith("false_break"):
        quality = 0.20
    elif classification.startswith("tease_break"):
        quality = 0.25
    else:
        quality = 0.0
    if volume_confirmation:
        quality += 0.05
    return round(clamp(quality, 0.0, 1.0), 4)


def _reason(
    *,
    classification: str,
    buildup_score: float,
    range_width_pct: float,
    break_buffer: float,
    volume_confirmation: bool,
) -> str:
    parts = [f"classified {classification}"]
    parts.append(f"buildup_score={buildup_score:.2f}")
    parts.append(f"range_width_pct={range_width_pct:.4f}")
    parts.append(f"break_buffer={break_buffer:.4f}")
    parts.append("volume confirms" if volume_confirmation else "volume confirmation weak")
    return "; ".join(parts)


def _empty(reason: str) -> dict[str, Any]:
    return {
        "support_level": None,
        "resistance_level": None,
        "range_high": None,
        "range_low": None,
        "range_width_pct": None,
        "range_compression": False,
        "buildup_detected": False,
        "buildup_score": 0.0,
        "buildup_side": "none",
        "breakout_direction": "none",
        "proper_break": False,
        "false_break": False,
        "tease_break": False,
        "pullback_detected": False,
        "pullback_direction": "none",
        "pattern_classification": "none",
        "pattern_quality": 0.0,
        "pattern_reason": reason,
    }


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
