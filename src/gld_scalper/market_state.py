from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .utils.time_utils import ensure_utc


MARKET_OPEN = "MARKET_OPEN"
MARKET_CLOSED = "MARKET_CLOSED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
POOR_LIQUIDITY = "POOR_LIQUIDITY"


@dataclass(frozen=True, slots=True)
class MarketGateResult:
    state: str
    checked_at: datetime
    reason: str
    market_open: bool
    next_open: datetime | None = None
    next_close: datetime | None = None
    aligned: bool | None = None
    maximum_skew_seconds: float | None = None

    @property
    def evaluation_allowed(self) -> bool:
        return self.state == MARKET_OPEN and self.aligned is not False

    def as_features(self) -> dict[str, Any]:
        return {
            "market_state": self.state,
            "market_clock_checked_at": self.checked_at,
            "market_open": self.market_open,
            "market_state_reason": self.reason,
            "market_next_open": self.next_open,
            "market_next_close": self.next_close,
            "market_data_aligned": self.aligned,
            "market_data_maximum_skew_seconds": self.maximum_skew_seconds,
        }


class MarketStateHeartbeat:
    """Limits repeated non-trading state records while preserving state changes."""

    def __init__(self, interval_seconds: int) -> None:
        self.interval = timedelta(seconds=max(1, interval_seconds))
        self._last_key: tuple[str, str] | None = None
        self._last_emitted_at: datetime | None = None

    def should_emit(self, result: MarketGateResult) -> bool:
        normalized_reason = re.sub(r"\d+(?:\.\d+)?", "#", result.reason)
        key = (result.state, normalized_reason)
        changed = key != self._last_key
        due = self._last_emitted_at is None or result.checked_at >= self._last_emitted_at + self.interval
        if changed or due:
            self._last_key = key
            self._last_emitted_at = result.checked_at
            return True
        return False


def inspect_market_clock(clock: Any, *, now: datetime) -> MarketGateResult:
    checked_at = ensure_utc(now)
    if clock is None:
        return MarketGateResult(
            state=DATA_UNAVAILABLE,
            checked_at=checked_at,
            reason="Alpaca market clock is unavailable; market evaluation is suspended.",
            market_open=False,
        )
    market_open = bool(_field(clock, "is_open", False))
    next_open = _datetime_field(clock, "next_open")
    next_close = _datetime_field(clock, "next_close")
    if not market_open:
        detail = f" Next regular open: {next_open.isoformat()}." if next_open else ""
        return MarketGateResult(
            state=MARKET_CLOSED,
            checked_at=checked_at,
            reason=f"Alpaca reports that the U.S. equity market is closed.{detail}",
            market_open=False,
            next_open=next_open,
            next_close=next_close,
        )
    return MarketGateResult(
        state=MARKET_OPEN,
        checked_at=checked_at,
        reason="Alpaca reports that the U.S. equity market is open.",
        market_open=True,
        next_open=next_open,
        next_close=next_close,
        aligned=None,
    )


def inspect_market_snapshot(
    *,
    now: datetime,
    bars: Sequence[Mapping[str, Any]],
    quote: Mapping[str, Any] | None,
    trade: Mapping[str, Any] | None,
    stream_health: Mapping[str, Any] | None,
    bar_max_age_seconds: float,
    quote_max_age_seconds: float,
    trade_max_age_seconds: float,
    alignment_tolerance_seconds: float,
) -> MarketGateResult:
    checked_at = ensure_utc(now)
    health = stream_health or {}
    reasons: list[str] = []
    timestamps: dict[str, datetime] = {}
    if not bars or bars[-1].get("timestamp") is None:
        reasons.append("completed GLD bar is unavailable")
    else:
        timestamps["bar"] = ensure_utc(bars[-1]["timestamp"])
    if not quote or quote.get("timestamp") is None:
        reasons.append("live GLD quote is unavailable")
    else:
        timestamps["quote"] = ensure_utc(quote["timestamp"])
    if not trade or trade.get("timestamp") is None:
        reasons.append("live GLD trade is unavailable")
    else:
        timestamps["trade"] = ensure_utc(trade["timestamp"])
    if health.get("websocket_connected") is False:
        reasons.append("live market stream is not connected")
    if health.get("stream_stale") is True:
        reasons.append(str(health.get("stream_stale_reason") or "live market stream is stale"))

    limits = {
        "bar": max(0.0, float(bar_max_age_seconds)),
        "quote": max(0.0, float(quote_max_age_seconds)),
        "trade": max(0.0, float(trade_max_age_seconds)),
    }
    for name, timestamp in timestamps.items():
        age = max(0.0, (checked_at - timestamp).total_seconds())
        if age > limits[name]:
            reasons.append(f"{name} age {age:.1f}s exceeds {limits[name]:.1f}s")

    maximum_skew: float | None = None
    if len(timestamps) == 3:
        epochs = [value.timestamp() for value in timestamps.values()]
        maximum_skew = max(epochs) - min(epochs)
        if maximum_skew > max(0.0, float(alignment_tolerance_seconds)):
            reasons.append(
                f"bar, quote and trade timestamps differ by {maximum_skew:.1f}s, "
                f"above the {alignment_tolerance_seconds:.1f}s alignment limit"
            )
    if reasons:
        return MarketGateResult(
            state=DATA_UNAVAILABLE,
            checked_at=checked_at,
            reason="; ".join(dict.fromkeys(reasons)),
            market_open=True,
            aligned=False,
            maximum_skew_seconds=maximum_skew,
        )
    return MarketGateResult(
        state=MARKET_OPEN,
        checked_at=checked_at,
        reason="Live GLD bar, quote and trade inputs are fresh and time-aligned.",
        market_open=True,
        aligned=True,
        maximum_skew_seconds=maximum_skew,
    )


def model_inference_block_reason(features: Mapping[str, Any]) -> str | None:
    state = str(features.get("market_state") or "").upper()
    if state in {MARKET_CLOSED, DATA_UNAVAILABLE}:
        return f"model inference skipped: {state.lower()}"
    if features.get("market_open") is False:
        return "model inference skipped: market closed"
    if features.get("market_data_aligned") is False:
        return "model inference skipped: market inputs are not time-aligned"
    if features.get("websocket_connected") is False or features.get("stream_stale") is True:
        return "model inference skipped: live stream is unavailable or stale"
    return None


def plausible_expected_cost(value: Any, maximum: float) -> bool:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(cost) and 0.0 <= cost <= maximum


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _datetime_field(value: Any, name: str) -> datetime | None:
    raw = _field(value, name)
    return ensure_utc(raw) if raw is not None else None
