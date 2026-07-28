from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .config import Settings
from .indicator_engine import atr, ema
from .models import MarketSignal
from .utils.math_utils import clamp
from .utils.time_utils import ensure_utc


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class EMACrossState:
    timeframe_minutes: int
    bar_timestamp: datetime
    bar_close_timestamp: datetime
    fast_ema: float
    slow_ema: float
    adx: float | None
    atr: float | None
    trend: str
    completed_bars: int

    def to_features(self) -> dict[str, Any]:
        return {
            "timeframe_minutes": self.timeframe_minutes,
            "bar_timestamp": self.bar_timestamp.isoformat(),
            "bar_close_timestamp": self.bar_close_timestamp.isoformat(),
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "adx": self.adx,
            "atr": self.atr,
            "trend": self.trend,
            "completed_bars": self.completed_bars,
        }


@dataclass(frozen=True, slots=True)
class EMACrossEvent:
    timeframe_minutes: int
    bar_timestamp: datetime
    bar_close_timestamp: datetime
    direction: str
    fast_ema: float
    slow_ema: float
    previous_fast_ema: float
    previous_slow_ema: float
    adx: float | None
    atr: float | None
    adx_passed: bool
    cooldown_passed: bool
    bars_since_last_execution: int | None
    eligible: bool
    block_reason: str
    confidence: float
    score: float

    def to_features(self) -> dict[str, Any]:
        return {
            "timeframe_minutes": self.timeframe_minutes,
            "bar_timestamp": self.bar_timestamp.isoformat(),
            "bar_close_timestamp": self.bar_close_timestamp.isoformat(),
            "direction": self.direction,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "previous_fast_ema": self.previous_fast_ema,
            "previous_slow_ema": self.previous_slow_ema,
            "adx": self.adx,
            "atr": self.atr,
            "adx_passed": self.adx_passed,
            "cooldown_passed": self.cooldown_passed,
            "bars_since_last_execution": self.bars_since_last_execution,
            "eligible": self.eligible,
            "block_reason": self.block_reason,
            "confidence": self.confidence,
            "score": self.score,
        }

    def to_record(self, *, symbol: str) -> dict[str, Any]:
        features = self.to_features()
        return {
            "timestamp": self.bar_close_timestamp,
            "bar_timestamp": self.bar_timestamp,
            "symbol": symbol,
            "timeframe": f"{self.timeframe_minutes}Min",
            "timeframe_minutes": self.timeframe_minutes,
            "strategy_name": "ema_cross_filtered",
            "decision": self.direction,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "adx": self.adx,
            "atr": self.atr,
            "adx_passed": self.adx_passed,
            "cooldown_passed": self.cooldown_passed,
            "eligible": self.eligible,
            "confidence": self.confidence,
            "score": self.score,
            "execution_status": "eligible" if self.eligible else "filtered",
            "block_reason": self.block_reason or None,
            "feature_snapshot_json": {
                **features,
                "playbook": "ema_cross_filtered",
                "playbook_direction": self.direction,
                "strategy_path": "ema_cross",
                "pine_source": "backtest_ema_cross_v6_filtered.pine",
            },
        }


@dataclass(frozen=True, slots=True)
class EMACrossSelection:
    selected: EMACrossEvent | None
    merged: tuple[EMACrossEvent, ...] = ()
    conflicts: tuple[EMACrossEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class EMACrossEvaluation:
    states: tuple[EMACrossState, ...]
    events: tuple[EMACrossEvent, ...]

    def to_features(
        self,
        selection: EMACrossSelection | None = None,
        *,
        selected_ids: Sequence[int] = (),
    ) -> dict[str, Any]:
        selection = selection or EMACrossSelection(None)
        selected = selection.selected
        features: dict[str, Any] = {
            "ema_cross_enabled": True,
            "ema_cross_strategy_name": "ema_cross_filtered",
            "ema_cross_signal_active": selected is not None,
            "ema_cross_state_count": len(self.states),
            "ema_cross_raw_event_count": len(self.events),
            "ema_cross_states": [state.to_features() for state in self.states],
            "ema_cross_events": [event.to_features() for event in self.events],
            "ema_cross_signal_ids": [int(item) for item in selected_ids],
            "ema_cross_merged_timeframes": [event.timeframe_minutes for event in selection.merged],
            "ema_cross_conflicting_timeframes": [event.timeframe_minutes for event in selection.conflicts],
        }
        if selected is None:
            return features
        features.update(
            {
                "ema_cross_direction": selected.direction,
                "ema_cross_timeframe": f"{selected.timeframe_minutes}Min",
                "ema_cross_timeframe_minutes": selected.timeframe_minutes,
                "ema_cross_bar_timestamp": selected.bar_timestamp.isoformat(),
                "ema_cross_bar_close_timestamp": selected.bar_close_timestamp.isoformat(),
                "ema_cross_fast_ema": selected.fast_ema,
                "ema_cross_slow_ema": selected.slow_ema,
                "ema_cross_adx": selected.adx,
                "ema_cross_atr": selected.atr,
                "ema_cross_confidence": selected.confidence,
                "ema_cross_score": selected.score,
                "ema_cross_closed_bar_confirmed": True,
            }
        )
        return features


def evaluate_ema_cross_strategy(
    bars_1m: list[dict[str, Any]],
    settings: Settings,
    *,
    last_executed_bars: Mapping[int, datetime | str] | None = None,
) -> EMACrossEvaluation:
    if not settings.enable_ema_cross_strategy or not bars_1m:
        return EMACrossEvaluation((), ())
    previous_executions = {
        int(timeframe): ensure_utc(timestamp)
        for timeframe, timestamp in (last_executed_bars or {}).items()
        if timestamp is not None
    }
    states: list[EMACrossState] = []
    events: list[EMACrossEvent] = []
    for timeframe in sorted(set(settings.ema_cross_timeframes)):
        completed = resample_completed_session_bars(bars_1m, timeframe)
        minimum_bars = max(
            settings.ema_cross_slow_period + 2,
            settings.ema_cross_adx_period * 2 + 2,
            settings.ema_cross_atr_period + 2,
        )
        if len(completed) < minimum_bars:
            continue
        closes = [float(row["close"]) for row in completed]
        highs = [float(row["high"]) for row in completed]
        lows = [float(row["low"]) for row in completed]
        fast_values = ema(closes, settings.ema_cross_fast_period)
        slow_values = ema(closes, settings.ema_cross_slow_period)
        adx_values = pine_dmi_adx(
            highs,
            lows,
            closes,
            di_length=settings.ema_cross_adx_period,
            adx_smoothing=settings.ema_cross_adx_period,
        )
        atr_values = atr(highs, lows, closes, settings.ema_cross_atr_period)
        current = completed[-1]
        fast = float(fast_values[-1] or 0.0)
        slow = float(slow_values[-1] or 0.0)
        previous_fast = float(fast_values[-2] or 0.0)
        previous_slow = float(slow_values[-2] or 0.0)
        current_adx = adx_values[-1]
        current_atr = atr_values[-1]
        state = EMACrossState(
            timeframe_minutes=timeframe,
            bar_timestamp=ensure_utc(current["timestamp"]),
            bar_close_timestamp=ensure_utc(current["bar_close_timestamp"]),
            fast_ema=fast,
            slow_ema=slow,
            adx=current_adx,
            atr=current_atr,
            trend="LONG" if fast > slow else "SHORT" if fast < slow else "FLAT",
            completed_bars=len(completed),
        )
        states.append(state)
        direction = _cross_direction(previous_fast, previous_slow, fast, slow)
        if direction is None:
            continue
        adx_passed = bool(
            not settings.ema_cross_use_adx_filter
            or current_adx is not None
            and current_adx >= settings.ema_cross_adx_threshold
        )
        bars_since = _bars_since_execution(completed, previous_executions.get(timeframe))
        cooldown_passed = bool(
            not settings.ema_cross_use_cooldown
            or bars_since is None
            or bars_since >= settings.ema_cross_cooldown_bars
        )
        blocks: list[str] = []
        if not adx_passed:
            value = "unavailable" if current_adx is None else f"{current_adx:.2f}"
            blocks.append(f"ADX {value} below {settings.ema_cross_adx_threshold:.2f}")
        if not cooldown_passed:
            blocks.append(
                f"cooldown {bars_since}/{settings.ema_cross_cooldown_bars} completed bars"
            )
        eligible = not blocks
        adx_strength = 0.0 if current_adx is None else clamp(
            (current_adx - settings.ema_cross_adx_threshold) / 30.0,
            0.0,
            1.0,
        )
        timeframe_strength = clamp(timeframe / max(settings.ema_cross_timeframes), 0.0, 1.0)
        confidence = clamp(0.58 + adx_strength * 0.25 + timeframe_strength * 0.07, 0.50, 0.95)
        score = clamp(72.0 + adx_strength * 20.0 + timeframe_strength * 8.0, 0.0, 100.0)
        events.append(
            EMACrossEvent(
                timeframe_minutes=timeframe,
                bar_timestamp=state.bar_timestamp,
                bar_close_timestamp=state.bar_close_timestamp,
                direction=direction,
                fast_ema=fast,
                slow_ema=slow,
                previous_fast_ema=previous_fast,
                previous_slow_ema=previous_slow,
                adx=current_adx,
                atr=current_atr,
                adx_passed=adx_passed,
                cooldown_passed=cooldown_passed,
                bars_since_last_execution=bars_since,
                eligible=eligible,
                block_reason="; ".join(blocks),
                confidence=round(confidence, 4),
                score=round(score, 2),
            )
        )
    return EMACrossEvaluation(tuple(states), tuple(events))


def select_ema_cross_events(events: Sequence[EMACrossEvent]) -> EMACrossSelection:
    eligible = [event for event in events if event.eligible]
    if not eligible:
        return EMACrossSelection(None)
    selected = max(
        eligible,
        key=lambda event: (
            event.timeframe_minutes,
            event.score,
            event.bar_close_timestamp,
        ),
    )
    merged = tuple(event for event in eligible if event.direction == selected.direction)
    conflicts = tuple(event for event in eligible if event.direction != selected.direction)
    return EMACrossSelection(selected, merged, conflicts)


def apply_ema_cross_paper_authority(
    signal: MarketSignal,
    features: dict[str, Any],
    settings: Settings,
) -> MarketSignal:
    direction = str(features.get("ema_cross_direction") or "")
    active = bool(
        features.get("ema_cross_signal_active")
        and direction in {"LONG", "SHORT"}
        and features.get("playbook") == "ema_cross_filtered"
        and features.get("playbook_allowed") is not False
    )
    if not active:
        return signal
    if not (
        settings.ema_cross_paper_signal_authority
        and settings.paper_learning_mode
        and settings.alpaca_paper
        and settings.alpaca_paper_trade
        and settings.data_mode == "paper"
    ):
        return signal
    score = float(features.get("ema_cross_score") or 0.0)
    confidence = float(features.get("ema_cross_confidence") or 0.0)
    timeframe = str(features.get("ema_cross_timeframe") or "")
    adx_value = features.get("ema_cross_adx")
    reason = (
        f"filtered EMA {settings.ema_cross_fast_period}/{settings.ema_cross_slow_period} "
        f"{direction.lower()} cross on completed {timeframe} bar; "
        f"ADX={float(adx_value):.2f}"
        if adx_value is not None
        else (
            f"filtered EMA {settings.ema_cross_fast_period}/{settings.ema_cross_slow_period} "
            f"{direction.lower()} cross on completed {timeframe} bar"
        )
    )
    features.update(
        {
            "strategy_path": "ema_cross",
            "playbook": "ema_cross_filtered",
            "playbook_direction": direction,
            "playbook_score": score,
            "playbook_expected_hold_minutes": _expected_hold_minutes(
                int(features.get("ema_cross_timeframe_minutes") or 1)
            ),
            "playbook_target_r_multiple": settings.ema_cross_take_profit_atr_multiple
            / settings.ema_cross_stop_loss_atr_multiple,
            "playbook_confirmations": [
                "completed_bar_ema_cross",
                "adx_trend_filter",
                "persistent_signal_deduplication",
            ],
            "playbook_confirmation_count": 3,
            "playbook_required_confirmations": 3,
            "paper_exploration": True,
            "paper_exploration_direction": direction,
            "paper_exploration_source": "ema_cross_filtered",
            "controlled_exploration_selected": True,
            "controlled_exploration_classification": "pine_strategy_signal",
        }
    )
    bullish = max(signal.bullish_score, score) if direction == "LONG" else signal.bullish_score
    bearish = max(signal.bearish_score, score) if direction == "SHORT" else signal.bearish_score
    return MarketSignal(
        timestamp=signal.timestamp,
        symbol=signal.symbol,
        decision=direction,
        bullish_score=round(bullish, 2),
        bearish_score=round(bearish, 2),
        no_trade_score=min(signal.no_trade_score, 20.0),
        regime=signal.regime,
        confidence=round(confidence, 4),
        reason=reason,
        features=dict(features),
        model_version=signal.model_version,
    )


def resample_completed_session_bars(
    bars_1m: list[dict[str, Any]],
    timeframe_minutes: int,
) -> list[dict[str, Any]]:
    if timeframe_minutes < 1:
        raise ValueError("timeframe_minutes must be positive")
    ordered = sorted(
        (dict(row) for row in bars_1m),
        key=lambda row: ensure_utc(row["timestamp"]),
    )
    if not ordered:
        return []
    latest_minute_close = ensure_utc(ordered[-1]["timestamp"]) + timedelta(minutes=1)
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    bucket_closes: dict[datetime, datetime] = {}
    for row in ordered:
        timestamp = ensure_utc(row["timestamp"])
        local = timestamp.astimezone(NEW_YORK)
        session_open = datetime.combine(local.date(), time(9, 30), tzinfo=NEW_YORK)
        session_close = datetime.combine(local.date(), time(16, 0), tzinfo=NEW_YORK)
        if not session_open <= local < session_close:
            continue
        elapsed_minutes = int((local - session_open).total_seconds() // 60)
        bucket_index = elapsed_minutes // timeframe_minutes
        bucket_start_local = session_open + timedelta(minutes=bucket_index * timeframe_minutes)
        bucket_close_local = min(
            bucket_start_local + timedelta(minutes=timeframe_minutes),
            session_close,
        )
        bucket_start = bucket_start_local.astimezone(NEW_YORK).astimezone(
            ensure_utc(timestamp).tzinfo
        )
        bucket_close = bucket_close_local.astimezone(NEW_YORK).astimezone(
            ensure_utc(timestamp).tzinfo
        )
        buckets.setdefault(bucket_start, []).append(row)
        bucket_closes[bucket_start] = bucket_close
    completed: list[dict[str, Any]] = []
    for bucket_start in sorted(buckets):
        bucket_close = bucket_closes[bucket_start]
        if bucket_close > latest_minute_close:
            continue
        group = buckets[bucket_start]
        volume = sum(float(item.get("volume") or 0.0) for item in group)
        typical_value = sum(
            (
                float(item["high"])
                + float(item["low"])
                + float(item["close"])
            )
            / 3.0
            * float(item.get("volume") or 0.0)
            for item in group
        )
        completed.append(
            {
                "symbol": str(group[0].get("symbol") or "GLD").upper(),
                "timeframe": f"{timeframe_minutes}Min",
                "timestamp": bucket_start,
                "bar_close_timestamp": bucket_close,
                "open": float(group[0]["open"]),
                "high": max(float(item["high"]) for item in group),
                "low": min(float(item["low"]) for item in group),
                "close": float(group[-1]["close"]),
                "volume": volume,
                "trade_count": sum(int(item.get("trade_count") or 0) for item in group),
                "vwap": typical_value / volume if volume > 0 else float(group[-1]["close"]),
                "source": "derived_session_aligned",
            }
        )
    return completed


def pine_dmi_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    di_length: int,
    adx_smoothing: int,
) -> list[float | None]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high, low, and close arrays must be the same length")
    if not closes:
        return []
    true_ranges = [max(highs[0] - lows[0], 0.0)]
    plus_dm = [0.0]
    minus_dm = [0.0]
    for index in range(1, len(closes)):
        up_move = highs[index] - highs[index - 1]
        down_move = lows[index - 1] - lows[index]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    smoothed_tr = _wilder_rma(true_ranges, di_length)
    smoothed_plus = _wilder_rma(plus_dm, di_length)
    smoothed_minus = _wilder_rma(minus_dm, di_length)
    dx_values: list[float | None] = []
    for tr_value, plus_value, minus_value in zip(
        smoothed_tr,
        smoothed_plus,
        smoothed_minus,
    ):
        if tr_value is None or plus_value is None or minus_value is None or tr_value <= 0:
            dx_values.append(None)
            continue
        plus_di = 100.0 * plus_value / tr_value
        minus_di = 100.0 * minus_value / tr_value
        denominator = plus_di + minus_di
        dx_values.append(
            0.0 if denominator <= 0 else 100.0 * abs(plus_di - minus_di) / denominator
        )
    return _wilder_rma_optional(dx_values, adx_smoothing)


def _wilder_rma(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be positive")
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return output
    current = sum(values[:period]) / period
    output[period - 1] = current
    for index in range(period, len(values)):
        current = (current * (period - 1) + values[index]) / period
        output[index] = current
    return output


def _wilder_rma_optional(
    values: list[float | None],
    period: int,
) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    valid: list[tuple[int, float]] = [
        (index, float(value))
        for index, value in enumerate(values)
        if value is not None
    ]
    if len(valid) < period:
        return output
    seed = valid[:period]
    current = sum(value for _, value in seed) / period
    output[seed[-1][0]] = current
    for index, value in valid[period:]:
        current = (current * (period - 1) + value) / period
        output[index] = current
    return output


def _cross_direction(
    previous_fast: float,
    previous_slow: float,
    fast: float,
    slow: float,
) -> str | None:
    if fast > slow and previous_fast <= previous_slow:
        return "LONG"
    if fast < slow and previous_fast >= previous_slow:
        return "SHORT"
    return None


def _bars_since_execution(
    completed_bars: list[dict[str, Any]],
    last_execution: datetime | None,
) -> int | None:
    if last_execution is None:
        return None
    timestamps = [ensure_utc(row["timestamp"]) for row in completed_bars]
    previous = ensure_utc(last_execution)
    return sum(1 for timestamp in timestamps if timestamp > previous)


def _expected_hold_minutes(timeframe_minutes: int) -> int:
    return max(5, min(90, timeframe_minutes * 2))
