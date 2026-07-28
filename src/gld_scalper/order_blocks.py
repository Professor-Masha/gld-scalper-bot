from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from .config import Settings
from .feature_engine import resample_bars
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now


@dataclass(slots=True)
class OrderBlockZone:
    detected_at: datetime
    symbol: str
    timeframe: str
    direction: str
    zone_low: float
    zone_high: float
    origin_timestamp: datetime
    confirmed_at: datetime
    strength: float
    displacement_pct: float
    displacement_atr: float
    volume_ratio: float
    break_of_structure: bool
    fair_value_gap: bool
    retest_count: int
    mitigated: bool
    invalidated: bool
    age_bars: int
    source: str = "deterministic_order_block_v1"

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["features"] = {
            "displacement_atr": self.displacement_atr,
            "zone_width_pct": safe_div(self.zone_high - self.zone_low, self.zone_high),
        }
        return record


def analyze_order_blocks(
    bars_1m: list[dict[str, Any]],
    settings: Settings,
    *,
    now: datetime | None = None,
    symbol: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Detect confirmed multi-timeframe order blocks and summarize live context."""
    now = ensure_utc(now or utc_now())
    symbol = (symbol or settings.bot_symbol).upper()
    if not settings.enable_order_blocks or not bars_1m:
        return _empty_features("order-block engine disabled or no bars"), []

    all_zones: list[OrderBlockZone] = []
    for minutes in sorted(set(settings.order_block_timeframes)):
        if minutes <= 0:
            continue
        timeframe = f"{minutes}Min" if minutes < 60 else "1Hour"
        resampled = resample_bars(bars_1m, minutes, timeframe)
        completed = [
            row
            for row in resampled
            if ensure_utc(row["timestamp"]) + timedelta(minutes=minutes) <= now
        ]
        all_zones.extend(
            detect_order_blocks(
                completed,
                timeframe=timeframe,
                minutes=minutes,
                symbol=symbol,
                now=now,
                lookback_bars=settings.order_block_lookback_bars,
                displacement_atr=settings.order_block_displacement_atr,
                minimum_volume_ratio=settings.order_block_min_volume_ratio,
                max_age_bars=settings.order_block_max_age_bars,
                retest_tolerance_pct=settings.order_block_retest_tolerance_pct,
            )
        )

    records = [zone.as_record() for zone in all_zones]
    features = summarize_order_blocks(
        all_zones,
        bars_1m,
        now=now,
        retest_tolerance_pct=settings.order_block_retest_tolerance_pct,
    )
    return features, records


def detect_order_blocks(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    minutes: int,
    symbol: str,
    now: datetime,
    lookback_bars: int,
    displacement_atr: float,
    minimum_volume_ratio: float,
    max_age_bars: int,
    retest_tolerance_pct: float,
) -> list[OrderBlockZone]:
    ordered = sorted((dict(row) for row in bars), key=lambda row: ensure_utc(row["timestamp"]))
    if len(ordered) < 8:
        return []

    start = max(5, len(ordered) - max(lookback_bars, 8))
    zones: list[OrderBlockZone] = []
    for origin_index in range(start, len(ordered) - 2):
        origin = ordered[origin_index]
        prior = ordered[max(0, origin_index - 14) : origin_index]
        post = ordered[origin_index + 1 : min(len(ordered), origin_index + 4)]
        if len(prior) < 5 or len(post) < 2:
            continue
        atr = _average_true_range([*prior, origin])
        if atr <= 0:
            continue

        open_price = float(origin["open"])
        close_price = float(origin["close"])
        prior_high = max(float(row["high"]) for row in prior[-5:])
        prior_low = min(float(row["low"]) for row in prior[-5:])
        prior_volume = mean(float(row.get("volume") or 0.0) for row in prior[-10:])
        post_volume = mean(float(row.get("volume") or 0.0) for row in post)
        volume_ratio = safe_div(post_volume, prior_volume, default=1.0) if prior_volume > 0 else 1.0

        bullish = close_price < open_price
        bearish = close_price > open_price
        bullish_close = max(float(row["close"]) for row in post)
        bearish_close = min(float(row["close"]) for row in post)
        bullish_move = bullish_close - close_price
        bearish_move = close_price - bearish_close
        bullish_bos = bullish_close > max(float(origin["high"]), prior_high)
        bearish_bos = bearish_close < min(float(origin["low"]), prior_low)

        direction: str | None = None
        move = 0.0
        if bullish and bullish_bos and bullish_move >= atr * displacement_atr:
            direction = "bullish"
            move = bullish_move
            zone_low = float(origin["low"])
            zone_high = max(open_price, close_price)
            confirmation_index = origin_index + 1 + max(
                range(len(post)), key=lambda index: float(post[index]["close"])
            )
            fair_value_gap = len(post) >= 2 and float(post[1]["low"]) > float(origin["high"])
        elif bearish and bearish_bos and bearish_move >= atr * displacement_atr:
            direction = "bearish"
            move = bearish_move
            zone_low = min(open_price, close_price)
            zone_high = float(origin["high"])
            confirmation_index = origin_index + 1 + min(
                range(len(post)), key=lambda index: float(post[index]["close"])
            )
            fair_value_gap = len(post) >= 2 and float(post[1]["high"]) < float(origin["low"])
        else:
            continue

        if volume_ratio < minimum_volume_ratio:
            continue
        subsequent = ordered[confirmation_index + 1 :]
        invalidated = _zone_invalidated(subsequent, direction, zone_low, zone_high, retest_tolerance_pct)
        retest_count = sum(
            1 for row in subsequent if _bar_intersects_zone(row, zone_low, zone_high, retest_tolerance_pct)
        )
        age_bars = len(ordered) - confirmation_index - 1
        if age_bars > max_age_bars:
            continue
        displacement_multiple = safe_div(move, atr)
        strength = clamp(
            0.35 * clamp(displacement_multiple / max(displacement_atr * 2.0, 0.01), 0.0, 1.0)
            + 0.25 * clamp(volume_ratio / max(minimum_volume_ratio * 2.0, 0.01), 0.0, 1.0)
            + 0.25
            + (0.15 if fair_value_gap else 0.0),
            0.0,
            1.0,
        )
        confirmed_at = ensure_utc(ordered[confirmation_index]["timestamp"]) + timedelta(minutes=minutes)
        zones.append(
            OrderBlockZone(
                detected_at=now,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                zone_low=round(zone_low, 6),
                zone_high=round(zone_high, 6),
                origin_timestamp=ensure_utc(origin["timestamp"]),
                confirmed_at=confirmed_at,
                strength=round(strength, 6),
                displacement_pct=round(safe_div(move, close_price), 8),
                displacement_atr=round(displacement_multiple, 6),
                volume_ratio=round(volume_ratio, 6),
                break_of_structure=True,
                fair_value_gap=fair_value_gap,
                retest_count=retest_count,
                mitigated=retest_count > 0,
                invalidated=invalidated,
                age_bars=age_bars,
            )
        )

    deduplicated: dict[tuple[str, str, datetime], OrderBlockZone] = {}
    for zone in zones:
        deduplicated[(zone.timeframe, zone.direction, zone.origin_timestamp)] = zone
    active = sorted(deduplicated.values(), key=lambda zone: zone.confirmed_at, reverse=True)
    limited: list[OrderBlockZone] = []
    counts: dict[str, int] = {"bullish": 0, "bearish": 0}
    for zone in active:
        if counts[zone.direction] >= 3:
            continue
        limited.append(zone)
        counts[zone.direction] += 1
    return limited


def summarize_order_blocks(
    zones: list[OrderBlockZone],
    bars_1m: list[dict[str, Any]],
    *,
    now: datetime,
    retest_tolerance_pct: float,
) -> dict[str, Any]:
    if not bars_1m:
        return _empty_features("no bars available")
    latest_bar = max(bars_1m, key=lambda row: ensure_utc(row["timestamp"]))
    price = float(latest_bar["close"])
    latest_low = float(latest_bar["low"])
    latest_high = float(latest_bar["high"])
    valid = [zone for zone in zones if not zone.invalidated]
    if not valid:
        return _empty_features("no active confirmed order block")

    weighted_score = 0.0
    weight_total = 0.0
    for zone in valid:
        minutes = _timeframe_minutes(zone.timeframe)
        timeframe_weight = 1.0 + min(minutes, 60) / 120.0
        weight = max(zone.strength, 0.05) * timeframe_weight
        weighted_score += weight if zone.direction == "bullish" else -weight
        weight_total += weight
    normalized_score = clamp(safe_div(weighted_score, weight_total), -1.0, 1.0)
    bias = "bullish" if normalized_score >= 0.15 else "bearish" if normalized_score <= -0.15 else "neutral"

    def distance(zone: OrderBlockZone) -> float:
        if zone.zone_low <= price <= zone.zone_high:
            return 0.0
        boundary = zone.zone_high if price > zone.zone_high else zone.zone_low
        return abs(safe_div(price - boundary, price))

    retests = [
        zone
        for zone in valid
        if latest_low <= zone.zone_high * (1 + retest_tolerance_pct)
        and latest_high >= zone.zone_low * (1 - retest_tolerance_pct)
    ]
    selected_pool = retests or valid
    selected = min(selected_pool, key=lambda zone: (distance(zone), -zone.strength))
    same_direction = sum(1 for zone in valid if zone.direction == selected.direction)
    alignment = safe_div(same_direction, len(valid))
    bullish_near = any(zone.direction == "bullish" and distance(zone) <= 0.003 for zone in valid)
    bearish_near = any(zone.direction == "bearish" and distance(zone) <= 0.003 for zone in valid)
    detection_latency = max(0.0, (now - ensure_utc(selected.confirmed_at)).total_seconds())
    reason = (
        f"{selected.direction} {selected.timeframe} order block "
        f"{'retest active' if selected in retests else 'nearest active zone'}; "
        f"strength={selected.strength:.2f}; distance={distance(selected):.4%}"
    )
    compact = [
        {
            "timeframe": zone.timeframe,
            "direction": zone.direction,
            "zone_low": zone.zone_low,
            "zone_high": zone.zone_high,
            "strength": zone.strength,
            "retest_count": zone.retest_count,
            "origin_timestamp": zone.origin_timestamp.isoformat(),
        }
        for zone in valid
    ]
    return {
        "order_block_bias": bias,
        "order_block_score": round(normalized_score, 6),
        "order_block_strength": selected.strength,
        "order_block_timeframe": selected.timeframe,
        "order_block_direction": selected.direction,
        "order_block_zone_low": selected.zone_low,
        "order_block_zone_high": selected.zone_high,
        "order_block_distance_pct": round(distance(selected), 8),
        "order_block_retest_active": selected in retests,
        "order_block_retest_direction": selected.direction if selected in retests else None,
        "order_block_alignment": round(alignment, 6),
        "order_block_conflict": bullish_near and bearish_near,
        "order_block_zone_count": len(valid),
        "order_block_detection_latency_seconds": round(detection_latency, 3),
        "order_block_reason": reason,
        "order_block_zones_json": json.dumps(compact, sort_keys=True),
    }


def live_order_block_retest(features: dict[str, Any], price: float, tolerance_pct: float) -> tuple[bool, str | None]:
    low = _float_or_none(features.get("order_block_zone_low"))
    high = _float_or_none(features.get("order_block_zone_high"))
    direction = str(features.get("order_block_direction") or "")
    if low is None or high is None or direction not in {"bullish", "bearish"} or price <= 0:
        return False, None
    active = low * (1 - tolerance_pct) <= price <= high * (1 + tolerance_pct)
    return active, direction if active else None


def _average_true_range(bars: list[dict[str, Any]]) -> float:
    if len(bars) < 2:
        return 0.0
    ranges: list[float] = []
    for previous, current in zip(bars, bars[1:]):
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return mean(ranges[-14:]) if ranges else 0.0


def _zone_invalidated(
    bars: list[dict[str, Any]], direction: str, low: float, high: float, tolerance_pct: float
) -> bool:
    if direction == "bullish":
        return any(float(row["close"]) < low * (1 - tolerance_pct) for row in bars)
    return any(float(row["close"]) > high * (1 + tolerance_pct) for row in bars)


def _bar_intersects_zone(row: dict[str, Any], low: float, high: float, tolerance_pct: float) -> bool:
    return float(row["low"]) <= high * (1 + tolerance_pct) and float(row["high"]) >= low * (1 - tolerance_pct)


def _timeframe_minutes(timeframe: str) -> int:
    return 60 if timeframe == "1Hour" else int(timeframe.removesuffix("Min"))


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _empty_features(reason: str) -> dict[str, Any]:
    return {
        "order_block_bias": "neutral",
        "order_block_score": 0.0,
        "order_block_strength": 0.0,
        "order_block_timeframe": None,
        "order_block_direction": None,
        "order_block_zone_low": None,
        "order_block_zone_high": None,
        "order_block_distance_pct": None,
        "order_block_retest_active": False,
        "order_block_retest_direction": None,
        "order_block_alignment": 0.0,
        "order_block_conflict": False,
        "order_block_zone_count": 0,
        "order_block_detection_latency_seconds": None,
        "order_block_reason": reason,
        "order_block_zones_json": "[]",
    }
