from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .indicator_engine import compute_indicators
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_now


def resample_bars(bars: list[dict[str, Any]], minutes: int, timeframe: str) -> list[dict[str, Any]]:
    ordered = sorted([dict(row) for row in bars], key=lambda row: str(row.get("timestamp")))
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for row in ordered:
        dt = ensure_utc(row["timestamp"])
        bucket_seconds = max(minutes, 1) * 60
        bucket_epoch = int(dt.timestamp()) // bucket_seconds * bucket_seconds
        bucket_dt = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        buckets.setdefault(bucket_dt, []).append(row)
    result: list[dict[str, Any]] = []
    for bucket_dt in sorted(buckets):
        group = buckets[bucket_dt]
        result.append(
            {
                "symbol": group[0].get("symbol", "GLD"),
                "timeframe": timeframe,
                "timestamp": bucket_dt,
                "open": float(group[0]["open"]),
                "high": max(float(row["high"]) for row in group),
                "low": min(float(row["low"]) for row in group),
                "close": float(group[-1]["close"]),
                "volume": sum(float(row.get("volume") or 0) for row in group),
                "trade_count": sum(int(row.get("trade_count") or 0) for row in group),
                "vwap": _weighted_vwap(group),
                "source": "derived",
            }
        )
    return result


def build_feature_snapshot(
    *,
    bars_1m: list[dict[str, Any]],
    bars_5m: list[dict[str, Any]] | None = None,
    bars_15m: list[dict[str, Any]] | None = None,
    related_features: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not bars_1m:
        return {}
    now = now or utc_now()
    enriched_1m = compute_indicators(bars_1m)
    latest = dict(enriched_1m[-1])
    bars_5m = bars_5m or resample_bars(bars_1m, 5, "5Min")
    bars_15m = bars_15m or resample_bars(bars_1m, 15, "15Min")
    _merge_prefixed(latest, compute_indicators(bars_5m), "tf5")
    _merge_prefixed(latest, compute_indicators(bars_15m), "tf15")
    latest["latest_price"] = float(latest["close"])
    latest["data_age_seconds"] = max(
        0.0,
        (ensure_utc(now) - ensure_utc(latest["timestamp"])).total_seconds(),
    )

    if quote:
        bid = float(quote.get("bid_price") or 0)
        ask = float(quote.get("ask_price") or 0)
        spread = float(quote.get("spread") if quote.get("spread") is not None else ask - bid)
        midpoint = (bid + ask) / 2 if bid and ask else float(latest["close"])
        latest["bid_price"] = bid
        latest["ask_price"] = ask
        latest["spread"] = spread
        latest["spread_pct"] = float(quote.get("spread_pct") if quote.get("spread_pct") is not None else safe_div(spread, midpoint))
        if quote.get("timestamp") is not None:
            latest["quote_timestamp"] = quote["timestamp"]
            latest["quote_age_seconds"] = max(
                0.0,
                (ensure_utc(now) - ensure_utc(quote["timestamp"])).total_seconds(),
            )
        bid_size = float(quote.get("bid_size") or 0)
        ask_size = float(quote.get("ask_size") or 0)
        latest["bid_size"] = bid_size
        latest["ask_size"] = ask_size
        latest["quote_missing"] = False
        latest["quote_imbalance"] = float(quote.get("quote_imbalance") if quote.get("quote_imbalance") is not None else safe_div(bid_size - ask_size, bid_size + ask_size))
    else:
        latest.setdefault("spread", float(latest["close"]) * 0.0004)
        latest.setdefault("spread_pct", 0.0004)
        latest.setdefault("quote_imbalance", 0.0)
        latest.setdefault("bid_size", 0.0)
        latest.setdefault("ask_size", 0.0)
        latest["quote_missing"] = True

    for key, value in (related_features or {}).items():
        latest[key] = value
    return latest


def build_archive_compatible_features(bars_1m: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the exact bar features used by the historical ML archive."""
    if len(bars_1m) < 60:
        return {}
    bars = sorted([dict(row) for row in bars_1m], key=lambda row: str(row.get("timestamp")))
    current = bars[-1]
    previous = bars[-2]
    close = float(current["close"])
    window_5 = bars[-5:]
    window_20 = bars[-20:]
    window_60 = bars[-60:]
    closes_20 = [float(row["close"]) for row in window_20]
    mean_20 = sum(closes_20) / len(closes_20)
    variance_20 = sum((value - mean_20) ** 2 for value in closes_20) / len(closes_20)
    volume_20 = sum(float(row.get("volume") or 0.0) for row in window_20) / len(window_20)
    timestamp = ensure_utc(current["timestamp"])
    minute_of_day = timestamp.hour * 60 + timestamp.minute
    angle = 2 * math.pi * minute_of_day / 1440
    return {
        "log_volume": math.log1p(float(current.get("volume") or 0.0)),
        "log_trade_count": math.log1p(float(current.get("trade_count") or 0.0)),
        "return_1m": safe_div(close - float(previous["close"]), float(previous["close"])),
        "return_5m": safe_div(close - float(bars[-6]["close"]), float(bars[-6]["close"])),
        "return_15m": safe_div(close - float(bars[-16]["close"]), float(bars[-16]["close"])),
        "range_1m_pct": safe_div(float(current["high"]) - float(current["low"]), close),
        "range_5m_pct": safe_div(
            max(float(row["high"]) for row in window_5) - min(float(row["low"]) for row in window_5),
            close,
        ),
        "range_20m_pct": safe_div(
            max(float(row["high"]) for row in window_20) - min(float(row["low"]) for row in window_20),
            close,
        ),
        "distance_sma20_pct": safe_div(close - mean_20, mean_20),
        "realized_volatility_20": math.sqrt(variance_20) / max(mean_20, 1e-12),
        "volume_ratio_20": safe_div(float(current.get("volume") or 0.0), volume_20, default=1.0),
        "vwap_distance_pct": safe_div(close - float(current.get("vwap") or close), close),
        "candle_body_pct": safe_div(abs(close - float(current["open"])), close),
        "breakout_20_high": close >= max(float(row["high"]) for row in window_20[:-1]),
        "breakout_20_low": close <= min(float(row["low"]) for row in window_20[:-1]),
        "compression_20": safe_div(
            max(float(row["high"]) for row in window_20) - min(float(row["low"]) for row in window_20),
            max(float(row["high"]) for row in window_60) - min(float(row["low"]) for row in window_60),
            default=1.0,
        ),
        "minute_sin": math.sin(angle),
        "minute_cos": math.cos(angle),
        "weekday": float(timestamp.weekday()),
    }


def _merge_prefixed(target: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    if not rows:
        return
    latest = rows[-1]
    for key, value in latest.items():
        if key in {"symbol", "timeframe", "timestamp", "source", "created_at"}:
            continue
        target[f"{prefix}_{key}"] = value


def _weighted_vwap(group: list[dict[str, Any]]) -> float:
    total_volume = sum(float(row.get("volume") or 0) for row in group)
    if total_volume == 0:
        return float(group[-1]["close"])
    total = 0.0
    for row in group:
        typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3
        total += typical * float(row.get("volume") or 0)
    return total / total_volume
