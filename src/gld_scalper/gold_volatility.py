from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from .utils.math_utils import safe_div
from .utils.time_utils import EASTERN, ensure_utc, utc_now


def build_gold_volatility_features(bars: list[dict[str, Any]], now=None) -> dict[str, Any]:
    now = ensure_utc(now or utc_now())
    segment, expected_profile, baseline = _intraday_profile(now)
    rows = sorted([dict(row) for row in bars if row], key=lambda row: str(row.get("timestamp")))
    closes = [_f(row.get("close")) for row in rows if _f(row.get("close")) > 0]
    ranges = [
        max(0.0, _f(row.get("high")) - _f(row.get("low")))
        for row in rows
        if _f(row.get("high")) > 0 and _f(row.get("low")) > 0
    ]
    latest_close = closes[-1] if closes else 0.0
    recent_range_pct = safe_div(mean(ranges[-12:]) if ranges[-12:] else 0.0, latest_close)
    realized_vol = _realized_volatility(closes[-30:])
    phase, cycle_state = _cycle_phase(closes)
    observed = max(recent_range_pct, realized_vol / 100.0)
    regime = _volatility_regime(observed, baseline, expected_profile)
    reason = (
        f"segment={segment}; expected={expected_profile}; observed={observed:.5f}; "
        f"baseline={baseline:.5f}; cycle={cycle_state}"
    )
    return {
        "gold_intraday_segment": segment,
        "expected_volatility_profile": expected_profile,
        "gold_expected_volatility_baseline": baseline,
        "gold_recent_range_pct": recent_range_pct,
        "gold_realized_volatility": realized_vol,
        "hilbert_cycle_phase": phase,
        "cycle_state": cycle_state,
        "gold_volatility_regime": regime,
        "gold_volatility_reason": reason,
    }


def _intraday_profile(now) -> tuple[str, str, float]:
    eastern = ensure_utc(now).astimezone(EASTERN)
    minute = eastern.hour * 60 + eastern.minute
    weekday_closed = eastern.weekday() >= 5
    if weekday_closed:
        return "closed", "closed", 0.0
    if 4 * 60 <= minute < 9 * 60 + 30:
        return "premarket", "normal", 0.00075
    if 9 * 60 + 30 <= minute < 10 * 60 + 30:
        return "opening_drive", "high", 0.00135
    if 10 * 60 + 30 <= minute < 12 * 60:
        return "late_morning", "normal", 0.00090
    if 12 * 60 <= minute < 14 * 60:
        return "midday", "low", 0.00055
    if 14 * 60 <= minute < 15 * 60:
        return "afternoon", "normal", 0.00085
    if 15 * 60 <= minute < 16 * 60:
        return "power_hour", "high", 0.00120
    if 16 * 60 <= minute < 20 * 60:
        return "afterhours", "low", 0.00045
    return "closed", "closed", 0.0


def _realized_volatility(closes: list[float]) -> float:
    if len(closes) < 3:
        return 0.0
    returns = [safe_div(closes[idx] - closes[idx - 1], closes[idx - 1]) for idx in range(1, len(closes))]
    return pstdev(returns) * math.sqrt(390) if len(returns) > 1 else 0.0


def _cycle_phase(closes: list[float]) -> tuple[float | None, str]:
    if len(closes) < 12:
        return None, "unknown"
    window = closes[-32:]
    center = mean(window)
    detrended = [value - center for value in window]
    in_phase = detrended[-1]
    quadrature_lag = 3 if len(detrended) > 6 else 1
    quadrature = detrended[-quadrature_lag]
    amplitude = math.hypot(in_phase, quadrature)
    if amplitude <= 1e-9:
        return 0.0, "flat"
    phase = (math.atan2(quadrature, in_phase) + math.pi) / (2 * math.pi)
    slope = closes[-1] - closes[-4]
    prior_slope = closes[-4] - closes[-8]
    if abs(slope) <= max(abs(closes[-1]) * 0.00025, 0.01):
        state = "flat"
    elif slope > 0 and prior_slope <= 0:
        state = "turning_up"
    elif slope < 0 and prior_slope >= 0:
        state = "turning_down"
    elif slope > 0:
        state = "rising"
    else:
        state = "falling"
    return round(phase, 4), state


def _volatility_regime(observed: float, baseline: float, expected_profile: str) -> str:
    if expected_profile == "closed":
        return "closed"
    if baseline <= 0:
        return "unknown"
    ratio = safe_div(observed, baseline, 1.0)
    if ratio >= 1.75:
        return "high"
    if ratio <= 0.55:
        return "low"
    return "normal"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
