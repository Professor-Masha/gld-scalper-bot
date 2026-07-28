from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any

from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now


def build_microstructure_features(
    *,
    bars: list[dict[str, Any]],
    quote: dict[str, Any] | None,
    recent_quotes: list[dict[str, Any]] | None = None,
    recent_trades: list[dict[str, Any]] | None = None,
    now=None,
) -> dict[str, Any]:
    now = ensure_utc(now or utc_now())
    recent_trades = recent_trades or []
    recent_quotes = recent_quotes or ([] if quote is None else [quote])
    spread, spread_pct, quote_imbalance, quote_size = _quote_metrics(quote)
    spread_regime = _spread_regime(spread_pct, quote)
    spread_expansion_ratio, spread_stability_score = _spread_stability(spread_pct, recent_quotes)
    trade_counts = _trade_counts(recent_trades, now)
    volatility = _volatility_metrics(bars)
    volatility_burst_score = _volatility_burst_score(volatility)
    volatility_burst = volatility_burst_score >= 0.65
    liquidity_score = _liquidity_score(
        spread_pct=spread_pct,
        spread_regime=spread_regime,
        quote_size=quote_size,
        trade_count_60s=trade_counts["trade_count_60s"],
        trade_count_5m=trade_counts["trade_count_5m"],
        volatility_burst_score=volatility_burst_score,
    )

    reason = (
        f"spread={spread_regime}; quote_imbalance={quote_imbalance:.2f}; "
        f"trades_60s={trade_counts['trade_count_60s']}; "
        f"vol_burst_score={volatility_burst_score:.2f}; liquidity={liquidity_score:.2f}"
    )
    return {
        "micro_spread": spread,
        "micro_spread_pct": spread_pct,
        "spread_regime": spread_regime,
        "spread_expansion_ratio": spread_expansion_ratio,
        "spread_stability_score": spread_stability_score,
        "quote_imbalance": quote_imbalance,
        "quote_depth": quote_size,
        "trade_count_60s": trade_counts["trade_count_60s"],
        "trade_count_5m": trade_counts["trade_count_5m"],
        "trade_intensity_60s": trade_counts["trade_intensity_60s"],
        "trade_intensity_5m": trade_counts["trade_intensity_5m"],
        "trade_volume_60s": trade_counts["trade_volume_60s"],
        "realized_volatility_short": volatility["realized_volatility_short"],
        "realized_volatility_long": volatility["realized_volatility_long"],
        "range_expansion_ratio": volatility["range_expansion_ratio"],
        "volatility_burst_score": volatility_burst_score,
        "volatility_burst": volatility_burst,
        "liquidity_score": liquidity_score,
        "microstructure_reason": reason,
    }


def _quote_metrics(quote: dict[str, Any] | None) -> tuple[float, float, float, float]:
    if not quote:
        return 0.0, 0.0004, 0.0, 0.0
    bid = _f(quote.get("bid_price"))
    ask = _f(quote.get("ask_price"))
    bid_size = _f(quote.get("bid_size"))
    ask_size = _f(quote.get("ask_size"))
    spread = _f(quote.get("spread"), ask - bid if ask and bid else 0.0)
    midpoint = (bid + ask) / 2 if bid and ask else 0.0
    spread_pct = _f(quote.get("spread_pct"), safe_div(spread, midpoint, 0.0004))
    imbalance = _f(quote.get("quote_imbalance"), safe_div(bid_size - ask_size, bid_size + ask_size))
    return spread, spread_pct, clamp(imbalance, -1.0, 1.0), bid_size + ask_size


def _spread_regime(spread_pct: float, quote: dict[str, Any] | None) -> str:
    if not quote:
        return "unknown"
    if spread_pct <= 0.00045:
        return "tight"
    if spread_pct <= 0.0015:
        return "normal"
    return "wide"


def _spread_stability(spread_pct: float, quotes: list[dict[str, Any]]) -> tuple[float, float]:
    spreads = [_f(row.get("spread_pct")) for row in quotes if _f(row.get("spread_pct")) > 0]
    if len(spreads) < 3 or spread_pct <= 0:
        return 1.0, 0.0
    baseline = median(spreads[:-1] or spreads)
    expansion = safe_div(spread_pct, baseline, 1.0)
    dispersion = safe_div(pstdev(spreads), max(mean(spreads), 0.000001)) if len(spreads) > 1 else 0.0
    stability = clamp(1.0 - abs(expansion - 1.0) * 0.6 - dispersion * 0.4, 0.0, 1.0)
    return expansion, stability


def _trade_counts(recent_trades: list[dict[str, Any]], now) -> dict[str, float]:
    count_60 = 0
    count_5m = 0
    volume_60 = 0.0
    for trade in recent_trades:
        ts = trade.get("timestamp")
        if ts is None:
            continue
        age = (now - ensure_utc(ts)).total_seconds()
        if age < 0:
            continue
        if age <= 300:
            count_5m += 1
        if age <= 60:
            count_60 += 1
            volume_60 += _f(trade.get("size"))
    return {
        "trade_count_60s": float(count_60),
        "trade_count_5m": float(count_5m),
        "trade_intensity_60s": float(count_60),
        "trade_intensity_5m": count_5m / 5.0,
        "trade_volume_60s": volume_60,
    }


def _volatility_metrics(bars: list[dict[str, Any]]) -> dict[str, float]:
    rows = sorted([dict(row) for row in bars if row], key=lambda row: str(row.get("timestamp")))
    closes = [_f(row.get("close")) for row in rows if _f(row.get("close")) > 0]
    ranges = [
        max(0.0, _f(row.get("high")) - _f(row.get("low")))
        for row in rows
        if _f(row.get("high")) > 0 and _f(row.get("low")) > 0
    ]
    short_returns = _returns(closes[-8:])
    long_returns = _returns(closes[-40:])
    short_vol = pstdev(short_returns) * math.sqrt(390) if len(short_returns) > 1 else 0.0
    long_vol = pstdev(long_returns) * math.sqrt(390) if len(long_returns) > 1 else 0.0
    short_range = mean(ranges[-5:]) if ranges[-5:] else 0.0
    long_range = mean(ranges[-30:]) if ranges[-30:] else short_range
    return {
        "realized_volatility_short": short_vol,
        "realized_volatility_long": long_vol,
        "range_expansion_ratio": safe_div(short_range, long_range, 1.0),
    }


def _volatility_burst_score(volatility: dict[str, float]) -> float:
    long_vol = volatility["realized_volatility_long"]
    short_vol = volatility["realized_volatility_short"]
    vol_ratio = safe_div(short_vol, long_vol, 1.0)
    range_ratio = volatility["range_expansion_ratio"]
    score = 0.0
    score += clamp((vol_ratio - 1.0) / 1.4, 0.0, 0.60)
    score += clamp((range_ratio - 1.0) / 2.0, 0.0, 0.40)
    if short_vol > 0.055:
        score += 0.15
    return round(clamp(score, 0.0, 1.0), 4)


def _liquidity_score(
    *,
    spread_pct: float,
    spread_regime: str,
    quote_size: float,
    trade_count_60s: float,
    trade_count_5m: float,
    volatility_burst_score: float,
) -> float:
    if spread_regime == "unknown":
        spread_score = 0.45
    elif spread_regime == "tight":
        spread_score = 1.0
    elif spread_regime == "normal":
        spread_score = 0.72
    else:
        spread_score = 0.18
    if spread_pct > 0.0:
        spread_score = min(spread_score, clamp(1.0 - (spread_pct / 0.003), 0.05, 1.0))
    depth_score = clamp(quote_size / 400.0, 0.0, 1.0) if quote_size else 0.35
    intensity_score = max(clamp(trade_count_60s / 15.0, 0.0, 1.0), clamp(trade_count_5m / 50.0, 0.0, 1.0))
    burst_penalty = clamp(1.0 - volatility_burst_score * 0.55, 0.35, 1.0)
    score = (spread_score * 0.42 + depth_score * 0.22 + intensity_score * 0.26 + 0.10) * burst_penalty
    return round(clamp(score, 0.0, 1.0), 4)


def _returns(closes: list[float]) -> list[float]:
    output: list[float] = []
    for idx in range(1, len(closes)):
        output.append(safe_div(closes[idx] - closes[idx - 1], closes[idx - 1]))
    return output


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
