from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc


FIB_RETRACEMENTS = (0.0, 0.236, 0.382, 0.500, 0.618, 0.650, 0.786, 1.0)
FIB_EXTENSIONS = (1.272, 1.414, 1.618, 1.650, 2.618, 3.618, 4.236)


@dataclass(frozen=True, slots=True)
class FairValueGap:
    gap_key: str
    symbol: str
    timeframe: str
    direction: str
    detected_at: datetime
    zone_low: float
    zone_high: float
    midpoint: float
    status: str
    fill_fraction: float
    age_bars: int
    invalidated: bool
    last_touched_at: datetime | None

    def as_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["features"] = {
            "midpoint": self.midpoint,
            "fill_fraction": self.fill_fraction,
            "age_bars": self.age_bars,
        }
        return value


@dataclass(frozen=True, slots=True)
class TechnicalSetup:
    route: str
    direction: str
    quality: float
    entry_zone_low: float | None
    entry_zone_high: float | None
    invalidation_price: float | None
    stop_price: float | None
    target_1: float | None
    target_2: float | None
    reward_risk: float
    confidence: float
    reasons: tuple[str, ...]
    abstain_reason: str | None = None

    def as_features(self) -> dict[str, Any]:
        return {
            "technical_route": self.route,
            "technical_direction": self.direction,
            "technical_quality": round(self.quality, 6),
            "technical_entry_zone_low": self.entry_zone_low,
            "technical_entry_zone_high": self.entry_zone_high,
            "technical_invalidation_price": self.invalidation_price,
            "technical_stop_price": self.stop_price,
            "technical_target_1": self.target_1,
            "technical_target_2": self.target_2,
            "technical_reward_risk": round(self.reward_risk, 6),
            "technical_confidence": round(self.confidence, 6),
            "technical_reasons_json": json.dumps(self.reasons),
            "technical_abstain_reason": self.abstain_reason,
        }


def analyze_technical_market(
    bars: Iterable[dict[str, Any]],
    features: dict[str, Any],
    *,
    now: datetime,
    symbol: str = "GLD",
    timeframe: str = "1Min",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build causal Fibonacci/FVG state and one grouped-confluence setup.

    Only bars at or before ``now`` are considered. The function is deterministic,
    has no broker access, and returns NO_TRADE whenever structure, momentum, and
    trend do not provide a sufficiently coherent setup.
    """

    ordered = sorted(
        (dict(row) for row in bars if ensure_utc(row["timestamp"]) <= ensure_utc(now)),
        key=lambda row: ensure_utc(row["timestamp"]),
    )
    if len(ordered) < 5:
        empty = _empty_features("fewer than five causal bars")
        return empty, []
    fibonacci = build_fibonacci_features(ordered)
    rsi_divergence = build_rsi_divergence_features(ordered)
    gaps = detect_fair_value_gaps(ordered, symbol=symbol, timeframe=timeframe)
    gap_features = summarize_fair_value_gaps(gaps, float(ordered[-1]["close"]))
    combined = {**features, **fibonacci, **rsi_divergence, **gap_features}
    families = grouped_indicator_families(combined)
    setup = select_technical_setup(combined, families)
    output = {
        **fibonacci,
        **rsi_divergence,
        **gap_features,
        **{f"technical_family_{name}": round(value, 6) for name, value in families.items()},
        **setup.as_features(),
        "technical_big3_aligned": _big_three_aligned(families),
        "technical_family_scores_json": json.dumps(families, sort_keys=True),
    }
    return output, [gap.as_record() for gap in gaps]


def build_fibonacci_features(
    bars: list[dict[str, Any]],
    lookback: int = 180,
    depth: int = 10,
    deviation_atr_multiplier: float = 3.0,
) -> dict[str, Any]:
    window = bars[-max(20, lookback) :]
    pivots = _confirmed_zigzag_pivots(
        window,
        depth=max(2, depth),
        deviation_atr_multiplier=max(0.0, deviation_atr_multiplier),
    )
    if len(pivots) >= 2:
        first_index, first_kind, first_price = pivots[-2]
        second_index, second_kind, second_price = pivots[-1]
        if first_kind == second_kind:
            pivots = pivots[:-1]
            if len(pivots) >= 2:
                first_index, first_kind, first_price = pivots[-2]
                second_index, second_kind, second_price = pivots[-1]
        low_index = first_index if first_kind == "low" else second_index
        high_index = first_index if first_kind == "high" else second_index
        swing_low = first_price if first_kind == "low" else second_price
        swing_high = first_price if first_kind == "high" else second_price
        pivot_method = "atr_zigzag_confirmed"
    else:
        highs = [float(row["high"]) for row in window]
        lows = [float(row["low"]) for row in window]
        high_index = max(range(len(window)), key=highs.__getitem__)
        low_index = min(range(len(window)), key=lows.__getitem__)
        swing_high, swing_low = highs[high_index], lows[low_index]
        first_kind, second_kind = ("low", "high") if low_index < high_index else ("high", "low")
        pivot_method = "window_extrema_fallback"
    span = abs(swing_high - swing_low)
    if span <= 0:
        return {"fib_direction": "none", "fib_valid": False}
    bullish_impulse = second_kind == "high"
    direction = "LONG" if bullish_impulse else "SHORT"
    levels: dict[str, float] = {}
    for ratio in FIB_RETRACEMENTS:
        price = swing_high - span * ratio if bullish_impulse else swing_low + span * ratio
        levels[f"fib_{str(ratio).replace('.', '_')}"] = round(price, 6)
    for ratio in FIB_EXTENSIONS:
        price = swing_high + span * (ratio - 1.0) if bullish_impulse else swing_low - span * (ratio - 1.0)
        levels[f"fib_{str(ratio).replace('.', '_')}"] = round(price, 6)
    current = float(window[-1]["close"])
    retracements = [levels[f"fib_{str(ratio).replace('.', '_')}"] for ratio in FIB_RETRACEMENTS]
    nearest = min(retracements, key=lambda value: abs(value - current))
    in_golden_pocket = min(levels["fib_0_618"], levels["fib_0_786"]) <= current <= max(
        levels["fib_0_618"], levels["fib_0_786"]
    )
    return {
        "fib_valid": True,
        "fib_direction": direction,
        "fib_pivot_method": pivot_method,
        "fib_depth": depth,
        "fib_deviation_atr_multiplier": deviation_atr_multiplier,
        "fib_swing_high": round(swing_high, 6),
        "fib_swing_low": round(swing_low, 6),
        "fib_swing_high_at": ensure_utc(window[high_index]["timestamp"]).isoformat(),
        "fib_swing_low_at": ensure_utc(window[low_index]["timestamp"]).isoformat(),
        "fib_nearest_level": nearest,
        "fib_distance_pct": round(abs(safe_div(current - nearest, current)), 8),
        "fib_golden_pocket": in_golden_pocket,
        **levels,
    }


def _confirmed_zigzag_pivots(
    bars: list[dict[str, Any]],
    *,
    depth: int,
    deviation_atr_multiplier: float,
) -> list[tuple[int, str, float]]:
    """Return alternating, confirmed pivots using depth and ATR deviation.

    A pivot is confirmed only after ``depth`` subsequent bars exist, so live
    calculations do not look beyond the latest available bar.
    """

    if len(bars) < depth * 2 + 3:
        return []
    atr = _average_true_range(bars, period=10)
    minimum_move = atr * deviation_atr_multiplier
    raw: list[tuple[int, str, float]] = []
    for index in range(depth, len(bars) - depth):
        segment = bars[index - depth : index + depth + 1]
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        if high >= max(float(row["high"]) for row in segment):
            raw.append((index, "high", high))
        if low <= min(float(row["low"]) for row in segment):
            raw.append((index, "low", low))
    raw.sort(key=lambda item: (item[0], item[1]))
    confirmed: list[tuple[int, str, float]] = []
    for pivot in raw:
        if not confirmed:
            confirmed.append(pivot)
            continue
        previous = confirmed[-1]
        if pivot[1] == previous[1]:
            more_extreme = pivot[2] > previous[2] if pivot[1] == "high" else pivot[2] < previous[2]
            if more_extreme:
                confirmed[-1] = pivot
            continue
        if abs(pivot[2] - previous[2]) >= minimum_move:
            confirmed.append(pivot)
    return confirmed


def _average_true_range(bars: list[dict[str, Any]], period: int) -> float:
    sample = bars[-max(period + 1, 2) :]
    ranges: list[float] = []
    for index, row in enumerate(sample):
        high, low = float(row["high"]), float(row["low"])
        previous_close = float(sample[index - 1]["close"]) if index else float(row["open"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges[-period:]) / max(len(ranges[-period:]), 1)


def detect_fair_value_gaps(
    bars: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    lookback: int = 180,
    maximum_age_bars: int = 120,
) -> list[FairValueGap]:
    window = bars[-max(5, lookback) :]
    gaps: list[FairValueGap] = []
    for index in range(2, len(window)):
        first, third = window[index - 2], window[index]
        first_high, first_low = float(first["high"]), float(first["low"])
        third_high, third_low = float(third["high"]), float(third["low"])
        direction = "bullish" if third_low > first_high else "bearish" if third_high < first_low else None
        if direction is None:
            continue
        zone_low, zone_high = (
            (first_high, third_low) if direction == "bullish" else (third_high, first_low)
        )
        detected_at = ensure_utc(third["timestamp"])
        later = window[index + 1 :]
        minimum_low = min([float(row["low"]) for row in later] or [zone_high])
        maximum_high = max([float(row["high"]) for row in later] or [zone_low])
        last_touch: datetime | None = None
        if direction == "bullish":
            fill_fraction = clamp(safe_div(zone_high - minimum_low, zone_high - zone_low), 0.0, 1.0)
            touched = [row for row in later if float(row["low"]) <= zone_high]
            invalidated = any(float(row["close"]) < zone_low for row in later)
        else:
            fill_fraction = clamp(safe_div(maximum_high - zone_low, zone_high - zone_low), 0.0, 1.0)
            touched = [row for row in later if float(row["high"]) >= zone_low]
            invalidated = any(float(row["close"]) > zone_high for row in later)
        if touched:
            last_touch = ensure_utc(touched[-1]["timestamp"])
        age = len(window) - index - 1
        if invalidated:
            status = "invalidated"
        elif fill_fraction >= 0.999:
            status = "filled"
        elif fill_fraction > 0:
            status = "partially_filled"
        else:
            status = "open"
        key = f"{symbol}:{timeframe}:{direction}:{detected_at.isoformat()}"
        gaps.append(
            FairValueGap(
                gap_key=key,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                detected_at=detected_at,
                zone_low=round(zone_low, 6),
                zone_high=round(zone_high, 6),
                midpoint=round((zone_low + zone_high) / 2.0, 6),
                status=status,
                fill_fraction=round(fill_fraction, 6),
                age_bars=age,
                invalidated=invalidated or age > maximum_age_bars,
                last_touched_at=last_touch,
            )
        )
    return gaps[-40:]


def build_rsi_divergence_features(
    bars: list[dict[str, Any]],
    *,
    period: int = 14,
    lookback: int = 90,
    pivot_right_bars: int = 2,
) -> dict[str, Any]:
    window = bars[-max(lookback + period + pivot_right_bars, 30) :]
    closes = [float(row["close"]) for row in window]
    rsi_values = _wilder_rsi(closes, period)
    usable = [index for index, value in enumerate(rsi_values) if value is not None]
    if len(usable) < 8:
        return {
            "rsi_divergence": "none",
            "rsi_divergence_strength": 0.0,
            "rsi_pivot_high": False,
            "rsi_pivot_low": False,
        }
    left = pivot_right_bars
    highs: list[int] = []
    lows: list[int] = []
    # The last candidate must have right-side bars already present. This creates
    # the same deliberate confirmation delay as the supplied Pine indicator.
    for index in range(left, len(window) - pivot_right_bars):
        if rsi_values[index] is None:
            continue
        segment = [value for value in rsi_values[index - left : index + pivot_right_bars + 1] if value is not None]
        if float(rsi_values[index]) >= max(float(value) for value in segment):
            highs.append(index)
        if float(rsi_values[index]) <= min(float(value) for value in segment):
            lows.append(index)
    direction = "none"
    strength = 0.0
    first_at = second_at = None
    if len(highs) >= 2:
        prior, latest = highs[-2], highs[-1]
        price_change = safe_div(closes[latest] - closes[prior], closes[prior])
        oscillator_change = float(rsi_values[latest]) - float(rsi_values[prior])
        if price_change > 0 and oscillator_change < 0:
            direction = "bearish"
            strength = clamp(abs(price_change) * 100 + abs(oscillator_change) / 20.0, 0.0, 1.0)
            first_at, second_at = window[prior]["timestamp"], window[latest]["timestamp"]
    if len(lows) >= 2:
        prior, latest = lows[-2], lows[-1]
        price_change = safe_div(closes[latest] - closes[prior], closes[prior])
        oscillator_change = float(rsi_values[latest]) - float(rsi_values[prior])
        bullish_strength = clamp(abs(price_change) * 100 + abs(oscillator_change) / 20.0, 0.0, 1.0)
        if price_change < 0 and oscillator_change > 0 and bullish_strength >= strength:
            direction = "bullish"
            strength = bullish_strength
            first_at, second_at = window[prior]["timestamp"], window[latest]["timestamp"]
    latest_rsi = float(next(value for value in reversed(rsi_values) if value is not None))
    return {
        "rsi_divergence": direction,
        "rsi_divergence_strength": round(strength, 6),
        "rsi_divergence_first_at": ensure_utc(first_at).isoformat() if first_at else None,
        "rsi_divergence_second_at": ensure_utc(second_at).isoformat() if second_at else None,
        "rsi_pivot_high": bool(highs and highs[-1] == len(window) - pivot_right_bars - 1),
        "rsi_pivot_low": bool(lows and lows[-1] == len(window) - pivot_right_bars - 1),
        "rsi_divergence_latest_rsi": round(latest_rsi, 6),
        "rsi_overbought": latest_rsi >= 70.0,
        "rsi_oversold": latest_rsi <= 30.0,
    }


def _wilder_rsi(closes: list[float], period: int) -> list[float | None]:
    values: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return values
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    values[period] = 100.0 if average_loss == 0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    for index in range(period + 1, len(closes)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        values[index] = 100.0 if average_loss == 0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return values


def summarize_fair_value_gaps(gaps: list[FairValueGap], price: float) -> dict[str, Any]:
    active = [gap for gap in gaps if not gap.invalidated and gap.status != "filled"]
    if not active:
        return {
            "fvg_active": False,
            "fvg_direction": "none",
            "fvg_status": "none",
            "fvg_zone_count": 0,
        }
    selected = min(
        active,
        key=lambda gap: 0.0 if gap.zone_low <= price <= gap.zone_high else min(abs(price - gap.zone_low), abs(price - gap.zone_high)),
    )
    inside = selected.zone_low <= price <= selected.zone_high
    return {
        "fvg_active": True,
        "fvg_direction": selected.direction,
        "fvg_status": selected.status,
        "fvg_zone_low": selected.zone_low,
        "fvg_zone_high": selected.zone_high,
        "fvg_midpoint": selected.midpoint,
        "fvg_fill_fraction": selected.fill_fraction,
        "fvg_age_bars": selected.age_bars,
        "fvg_retest_active": inside,
        "fvg_distance_pct": round(
            0.0 if inside else min(abs(price - selected.zone_low), abs(price - selected.zone_high)) / max(price, 1e-9),
            8,
        ),
        "fvg_zone_count": len(active),
    }


def grouped_indicator_families(features: dict[str, Any]) -> dict[str, float]:
    price = _f(features.get("close"))
    trend_votes = [
        _compare(features, "ema_9", "ema_21"),
        _compare(features, "sma_20", "sma_50"),
        _compare_value(price, _f(features.get("vwap"))),
        _compare(features, "tf5_ema_9", "tf5_ema_21"),
        _compare(features, "tf15_ema_9", "tf15_ema_21"),
    ]
    rsi = _f(features.get("rsi_14"), 50.0)
    momentum_votes = [
        1.0 if rsi >= 55 else -1.0 if rsi <= 45 else 0.0,
        _sign(_f(features.get("macd_histogram"))),
        _sign(_f(features.get("macd_histogram_slope"))),
        _sign(_f(features.get("roc_5"), _f(features.get("roc")))),
        1.0 if features.get("rsi_divergence") == "bullish" else -1.0 if features.get("rsi_divergence") == "bearish" else 0.0,
    ]
    structure_votes = [
        _direction_vote(str(features.get("pattern_classification") or "")),
        1.0 if features.get("fvg_direction") == "bullish" else -1.0 if features.get("fvg_direction") == "bearish" else 0.0,
        1.0 if features.get("order_block_bias") == "bullish" else -1.0 if features.get("order_block_bias") == "bearish" else 0.0,
        1.0 if features.get("fib_direction") == "LONG" else -1.0 if features.get("fib_direction") == "SHORT" else 0.0,
        1.0 if features.get("rsi_divergence") == "bullish" else -1.0 if features.get("rsi_divergence") == "bearish" else 0.0,
    ]
    micro_votes = [
        _sign(_f(features.get("quote_imbalance"))),
        _sign(_f(features.get("signed_volume"))),
        _compare_value(_f(features.get("aggressive_buy_volume")), _f(features.get("aggressive_sell_volume"))),
    ]
    volume = clamp((_f(features.get("relative_volume"), 1.0) - 0.7) / 0.8, -1.0, 1.0)
    volatility = -1.0 if features.get("volatility_burst") and not features.get("proper_break") else 0.4 if 0.0004 <= _f(features.get("atr_pct")) <= 0.01 else -0.4
    event = 1.0 if features.get("event_gold_direction") == "LONG" else -1.0 if features.get("event_gold_direction") == "SHORT" else 0.0
    return {
        "trend": _average_votes(trend_votes),
        "momentum": _average_votes(momentum_votes),
        "structure": _average_votes(structure_votes),
        "volume": volume,
        "volatility": volatility,
        "microstructure": _average_votes(micro_votes),
        "events": event,
    }


def select_technical_setup(features: dict[str, Any], families: dict[str, float]) -> TechnicalSetup:
    trend, structure, momentum = (families[name] for name in ("trend", "structure", "momentum"))
    big_three = _big_three_aligned(families)
    direction = "LONG" if trend + structure + momentum > 0 else "SHORT"
    sign = 1.0 if direction == "LONG" else -1.0
    pattern = str(features.get("pattern_classification") or "")
    event_direction = str(features.get("event_gold_direction") or "NO_TRADE")
    route = "none"
    if features.get("event_post_release") and event_direction in {"LONG", "SHORT"}:
        route, direction = "news_momentum", event_direction
        sign = 1.0 if direction == "LONG" else -1.0
    elif features.get("fvg_retest_active") and sign * families["structure"] > 0:
        route = "fvg_retest"
    elif pattern in {"proper_break_up", "proper_break_down"} and features.get("pullback_detected"):
        route = "breakout_retest"
    elif pattern in {"proper_break_up", "proper_break_down"}:
        route = "trend_continuation"
    elif pattern in {"pullback_up", "pullback_down"} and big_three:
        route = "trend_continuation"
    elif pattern.startswith("false_break") and abs(structure) >= 0.20:
        route = "structure_reversal"
        direction = "SHORT" if pattern.endswith("up") else "LONG"
        sign = 1.0 if direction == "LONG" else -1.0
    elif features.get("fib_golden_pocket") and abs(momentum) >= 0.20:
        route = "mean_reversion"

    quality = clamp(
        (abs(trend) + abs(structure) + abs(momentum)) / 3.0 * 0.60
        + max(0.0, sign * families["microstructure"]) * 0.15
        + max(0.0, families["volume"]) * 0.10
        + max(0.0, families["volatility"]) * 0.10
        + (0.05 if route != "none" else 0.0),
        0.0,
        1.0,
    )
    blocks: list[str] = []
    if route == "none":
        blocks.append("no recognized technical route")
    if route not in {"news_momentum", "structure_reversal", "mean_reversion"} and not big_three:
        blocks.append("Big 3 trend, structure, and momentum are not aligned")
    if _f(features.get("liquidity_score"), 0.5) < 0.40:
        blocks.append("liquidity below technical-route floor")
    if str(features.get("spread_regime") or "") == "wide":
        blocks.append("spread regime is wide")
    if features.get("stream_stale") or features.get("websocket_connected") is False:
        blocks.append("market data is stale or disconnected")
    if quality < 0.52:
        blocks.append("grouped confluence quality below 0.52")
    reasons = (
        f"trend={trend:.2f}",
        f"structure={structure:.2f}",
        f"momentum={momentum:.2f}",
        f"microstructure={families['microstructure']:.2f}",
        f"route={route}",
    )
    price = _f(features.get("close"))
    atr = max(_f(features.get("atr_14"), _f(features.get("atr"))), price * 0.00025)
    geometry = _technical_geometry(direction, price, atr, features)
    if blocks:
        return TechnicalSetup(
            route=route,
            direction="NO_TRADE",
            quality=quality,
            entry_zone_low=geometry[0],
            entry_zone_high=geometry[1],
            invalidation_price=geometry[2],
            stop_price=geometry[3],
            target_1=geometry[4],
            target_2=geometry[5],
            reward_risk=geometry[6],
            confidence=quality * 0.50,
            reasons=reasons,
            abstain_reason="; ".join(blocks),
        )
    return TechnicalSetup(
        route=route,
        direction=direction,
        quality=quality,
        entry_zone_low=geometry[0],
        entry_zone_high=geometry[1],
        invalidation_price=geometry[2],
        stop_price=geometry[3],
        target_1=geometry[4],
        target_2=geometry[5],
        reward_risk=geometry[6],
        confidence=clamp(quality + 0.05, 0.0, 1.0),
        reasons=reasons,
    )


def _technical_geometry(direction: str, price: float, atr: float, f: dict[str, Any]) -> tuple[float, ...]:
    support = _positive(f.get("support_level") or f.get("range_low") or f.get("fib_0_618"))
    resistance = _positive(f.get("resistance_level") or f.get("range_high") or f.get("fib_0_618"))
    fvg_low, fvg_high = _positive(f.get("fvg_zone_low")), _positive(f.get("fvg_zone_high"))
    entry_low = fvg_low or max(0.01, price - atr * 0.20)
    entry_high = fvg_high or price + atr * 0.20
    if direction == "LONG":
        structural = max(value for value in (support, fvg_low) if 0 < value < price) if any(0 < value < price for value in (support, fvg_low)) else price - atr * 0.80
        stop = structural - atr * 0.15
        target_1 = max(resistance, _positive(f.get("fib_1_272")), price + atr)
        target_2 = max(_positive(f.get("fib_1_618")), price + atr * 1.60)
    else:
        candidates = [value for value in (resistance, fvg_high) if value > price]
        structural = min(candidates) if candidates else price + atr * 0.80
        stop = structural + atr * 0.15
        targets = [value for value in (support, _positive(f.get("fib_1_272"))) if 0 < value < price]
        target_1 = max(targets) if targets else price - atr
        extension = _positive(f.get("fib_1_618"))
        target_2 = extension if 0 < extension < price else price - atr * 1.60
    risk = abs(price - stop)
    reward = abs(target_1 - price)
    return (
        round(entry_low, 6),
        round(entry_high, 6),
        round(structural, 6),
        round(max(0.01, stop), 6),
        round(max(0.01, target_1), 6),
        round(max(0.01, target_2), 6),
        round(safe_div(reward, risk), 6),
    )


def _big_three_aligned(families: dict[str, float]) -> bool:
    values = [families[name] for name in ("trend", "structure", "momentum")]
    return all(value >= 0.15 for value in values) or all(value <= -0.15 for value in values)


def _empty_features(reason: str) -> dict[str, Any]:
    return {
        "fib_valid": False,
        "fib_direction": "none",
        "fvg_active": False,
        "fvg_direction": "none",
        "technical_route": "none",
        "technical_direction": "NO_TRADE",
        "technical_quality": 0.0,
        "technical_confidence": 0.0,
        "technical_abstain_reason": reason,
        "technical_big3_aligned": False,
    }


def _direction_vote(classification: str) -> float:
    if classification.endswith("_up") or classification.endswith("resistance"):
        return 1.0
    if classification.endswith("_down") or classification.endswith("support"):
        return -1.0
    return 0.0


def _compare(features: dict[str, Any], left: str, right: str) -> float:
    return _compare_value(_f(features.get(left)), _f(features.get(right)))


def _compare_value(left: float, right: float) -> float:
    if left <= 0 or right <= 0:
        return 0.0
    return 1.0 if left > right else -1.0 if left < right else 0.0


def _average_votes(values: list[float]) -> float:
    usable = [value for value in values if value != 0.0]
    return sum(usable) / len(usable) if usable else 0.0


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def _positive(value: Any) -> float:
    result = _f(value)
    return result if result > 0 else 0.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default
