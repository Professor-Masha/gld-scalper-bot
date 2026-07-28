from __future__ import annotations

from typing import Any


AVOID_REGIMES = {"sideways_chop", "poor_liquidity", "high_volatility", "low_volatility", "unclear"}


def detect_regime(features: dict[str, Any]) -> str:
    close = _f(features.get("close", features.get("latest_price")))
    ema_9 = _f(features.get("ema_9"))
    ema_21 = _f(features.get("ema_21"))
    ema_50 = _f(features.get("ema_50"))
    vwap = _f(features.get("vwap"))
    adx = _f(features.get("adx"))
    atr_pct = _f(features.get("atr_pct"))
    bandwidth = _f(features.get("bollinger_bandwidth"))
    realized_vol = _f(features.get("realized_volatility"))
    spread_pct = _f(features.get("spread_pct"))
    relative_volume = _f(features.get("relative_volume"), 1.0)
    liquidity_score = _f(features.get("liquidity_score"), 1.0)
    spread_regime = str(features.get("spread_regime") or "")
    volatility_burst = bool(features.get("volatility_burst"))
    pattern = str(features.get("pattern_classification") or "")
    breakout = bool(features.get("breakout_candle"))
    breakdown = bool(features.get("breakdown_candle"))

    if spread_pct > 0.002 or relative_volume < 0.45 or liquidity_score < 0.30 or spread_regime == "wide":
        return "poor_liquidity"
    if atr_pct > 0.012 or realized_vol > 0.08 or volatility_burst:
        return "high_volatility"
    if atr_pct and atr_pct < 0.00035:
        return "low_volatility"
    if (breakout or pattern == "proper_break_up") and close > vwap and ema_9 >= ema_21:
        return "breakout_up"
    if (breakdown or pattern == "proper_break_down") and close < vwap and ema_9 <= ema_21:
        return "breakout_down"
    if ema_9 > ema_21 > ema_50 and close > vwap and adx >= 18:
        return "bullish_trend"
    if ema_9 < ema_21 < ema_50 and close < vwap and adx >= 18:
        return "bearish_trend"
    if bandwidth < 0.005 or abs(close - vwap) / close < 0.0008 or adx < 13:
        return "sideways_chop"
    return "unclear"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
