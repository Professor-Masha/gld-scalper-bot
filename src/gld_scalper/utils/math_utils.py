from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or denominator is None:
        return default
    return numerator / denominator


def pct_change(previous: float, current: float, default: float = 0.0) -> float:
    return safe_div(current - previous, previous, default)
