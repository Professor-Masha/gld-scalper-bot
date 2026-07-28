from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, market_session, minutes_before_close, minutes_since_open


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def sma(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = []
    for idx in range(len(values)):
        if idx + 1 < period:
            output.append(None)
        else:
            output.append(mean(values[idx + 1 - period : idx + 1]))
    return output


def ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    output: list[float | None] = []
    current = values[0]
    for value in values:
        current = (value * alpha) + (current * (1 - alpha))
        output.append(current)
    return output


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    if not values:
        return []
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, period + 1):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    output[period] = _rsi_from_avgs(avg_gain, avg_loss)
    for idx in range(period + 1, len(values)):
        change = values[idx] - values[idx - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        output[idx] = _rsi_from_avgs(avg_gain, avg_loss)
    return output


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    ranges: list[float] = []
    for idx, high in enumerate(highs):
        low = lows[idx]
        if idx == 0:
            ranges.append(high - low)
        else:
            prev_close = closes[idx - 1]
            ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return ranges


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    ranges = true_range(highs, lows, closes)
    output: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return output
    current = mean(ranges[:period])
    output[period - 1] = current
    for idx in range(period, len(ranges)):
        current = ((current * (period - 1)) + ranges[idx]) / period
        output[idx] = current
    return output


def rolling_max(values: list[float], period: int) -> list[float | None]:
    return [None if idx + 1 < period else max(values[idx + 1 - period : idx + 1]) for idx in range(len(values))]


def rolling_min(values: list[float], period: int) -> list[float | None]:
    return [None if idx + 1 < period else min(values[idx + 1 - period : idx + 1]) for idx in range(len(values))]


def bollinger_bands(values: list[float], period: int = 20, deviations: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    upper: list[float | None] = []
    middle = sma(values, period)
    lower: list[float | None] = []
    for idx, mid in enumerate(middle):
        if mid is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[idx + 1 - period : idx + 1]
        std = pstdev(window)
        upper.append(mid + deviations * std)
        lower.append(mid - deviations * std)
    return upper, middle, lower


def macd(values: list[float]) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(ema12, ema26)]
    filled = [value if value is not None else 0.0 for value in line]
    signal = ema(filled, 9)
    histogram = [(m - s) if m is not None and s is not None else None for m, s in zip(line, signal)]
    return line, signal, histogram


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    output: list[float] = []
    current = 0.0
    for idx, close in enumerate(closes):
        if idx == 0:
            output.append(current)
            continue
        if close > closes[idx - 1]:
            current += volumes[idx]
        elif close < closes[idx - 1]:
            current -= volumes[idx]
        output.append(current)
    return output


def money_flow_index(highs: list[float], lows: list[float], closes: list[float], volumes: list[float], period: int = 14) -> list[float | None]:
    typical = [(high + low + close) / 3 for high, low, close in zip(highs, lows, closes)]
    positive: list[float] = [0.0]
    negative: list[float] = [0.0]
    for idx in range(1, len(typical)):
        flow = typical[idx] * volumes[idx]
        if typical[idx] > typical[idx - 1]:
            positive.append(flow)
            negative.append(0.0)
        elif typical[idx] < typical[idx - 1]:
            positive.append(0.0)
            negative.append(flow)
        else:
            positive.append(0.0)
            negative.append(0.0)
    output: list[float | None] = []
    for idx in range(len(typical)):
        if idx + 1 < period:
            output.append(None)
            continue
        pos = sum(positive[idx + 1 - period : idx + 1])
        neg = sum(negative[idx + 1 - period : idx + 1])
        output.append(100.0 if neg == 0 else 100 - (100 / (1 + pos / neg)))
    return output


def accumulation_distribution(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> list[float]:
    output: list[float] = []
    current = 0.0
    for high, low, close, volume in zip(highs, lows, closes, volumes):
        mfm = safe_div((close - low) - (high - close), high - low)
        current += mfm * volume
        output.append(current)
    return output


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    tr = true_range(highs, lows, closes)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for idx in range(1, len(closes)):
        up_move = highs[idx] - highs[idx - 1]
        down_move = lows[idx - 1] - lows[idx]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    output: list[float | None] = [None] * len(closes)
    dx_values: list[float] = []
    for idx in range(period, len(closes)):
        tr_sum = sum(tr[idx + 1 - period : idx + 1])
        plus_di = 100 * safe_div(sum(plus_dm[idx + 1 - period : idx + 1]), tr_sum)
        minus_di = 100 * safe_div(sum(minus_dm[idx + 1 - period : idx + 1]), tr_sum)
        dx = 100 * safe_div(abs(plus_di - minus_di), plus_di + minus_di)
        dx_values.append(dx)
        if len(dx_values) >= period:
            output[idx] = mean(dx_values[-period:])
    return output


def supertrend(highs: list[float], lows: list[float], closes: list[float], period: int = 10, multiplier: float = 3.0) -> tuple[list[float | None], list[int]]:
    atr_values = atr(highs, lows, closes, period)
    trend: list[float | None] = [None] * len(closes)
    direction: list[int] = [0] * len(closes)
    final_upper: list[float | None] = [None] * len(closes)
    final_lower: list[float | None] = [None] * len(closes)
    for idx in range(len(closes)):
        atr_value = atr_values[idx]
        if atr_value is None:
            continue
        hl2 = (highs[idx] + lows[idx]) / 2
        basic_upper = hl2 + multiplier * atr_value
        basic_lower = hl2 - multiplier * atr_value
        if idx == 0 or final_upper[idx - 1] is None:
            final_upper[idx] = basic_upper
            final_lower[idx] = basic_lower
            direction[idx] = 1
        else:
            prev_upper = final_upper[idx - 1] or basic_upper
            prev_lower = final_lower[idx - 1] or basic_lower
            final_upper[idx] = basic_upper if basic_upper < prev_upper or closes[idx - 1] > prev_upper else prev_upper
            final_lower[idx] = basic_lower if basic_lower > prev_lower or closes[idx - 1] < prev_lower else prev_lower
            if closes[idx] > prev_upper:
                direction[idx] = 1
            elif closes[idx] < prev_lower:
                direction[idx] = -1
            else:
                direction[idx] = direction[idx - 1]
        trend[idx] = final_lower[idx] if direction[idx] == 1 else final_upper[idx]
    return trend, direction


def parabolic_sar(highs: list[float], lows: list[float], step: float = 0.02, maximum: float = 0.2) -> list[float | None]:
    if not highs:
        return []
    sar: list[float | None] = [lows[0]]
    long = True
    ep = highs[0]
    af = step
    for idx in range(1, len(highs)):
        previous = sar[-1] if sar[-1] is not None else lows[idx - 1]
        current = previous + af * (ep - previous)
        if long:
            if lows[idx] < current:
                long = False
                current = ep
                ep = lows[idx]
                af = step
            elif highs[idx] > ep:
                ep = highs[idx]
                af = min(maximum, af + step)
        else:
            if highs[idx] > current:
                long = True
                current = ep
                ep = highs[idx]
                af = step
            elif lows[idx] < ep:
                ep = lows[idx]
                af = min(maximum, af + step)
        sar.append(current)
    return sar


def compute_indicators(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted([dict(row) for row in bars], key=lambda row: str(row.get("timestamp")))
    if not rows:
        return []
    opens = [_float(row.get("open")) for row in rows]
    highs = [_float(row.get("high")) for row in rows]
    lows = [_float(row.get("low")) for row in rows]
    closes = [_float(row.get("close")) for row in rows]
    volumes = [_float(row.get("volume")) for row in rows]

    ema_3 = ema(closes, 3)
    ema_5 = ema(closes, 5)
    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    ema_50 = ema(closes, 50)
    sma_20 = sma(closes, 20)
    sma_50 = sma(closes, 50)
    rsi_7 = rsi(closes, 7)
    rsi_14 = rsi(closes, 14)
    atr_7 = atr(highs, lows, closes, 7)
    atr_14 = atr(highs, lows, closes, 14)
    macd_line, macd_signal, macd_histogram = macd(closes)
    bollinger_upper, bollinger_middle, bollinger_lower = bollinger_bands(closes)
    high_20 = rolling_max(highs, 20)
    low_20 = rolling_min(lows, 20)
    volume_ma = sma(volumes, 20)
    ranges = [high - low for high, low in zip(highs, lows)]
    avg_range = sma(ranges, 20)
    obv_values = obv(closes, volumes)
    mfi_values = money_flow_index(highs, lows, closes, volumes)
    ad_values = accumulation_distribution(highs, lows, closes, volumes)
    adx_values = adx(highs, lows, closes)
    supertrend_values, supertrend_direction = supertrend(highs, lows, closes)
    psar = parabolic_sar(highs, lows)
    ema_20 = ema(closes, 20)

    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    consecutive_green = 0
    consecutive_red = 0
    output: list[dict[str, Any]] = []

    returns: list[float] = [0.0]
    for idx in range(1, len(closes)):
        returns.append(safe_div(closes[idx] - closes[idx - 1], closes[idx - 1]))

    for idx, row in enumerate(rows):
        typical = (highs[idx] + lows[idx] + closes[idx]) / 3
        cumulative_price_volume += typical * volumes[idx]
        cumulative_volume += volumes[idx]
        vwap = row.get("vwap")
        if vwap is None:
            vwap = safe_div(cumulative_price_volume, cumulative_volume, closes[idx])
        candle_range = ranges[idx]
        body = closes[idx] - opens[idx]
        abs_body = abs(body)
        upper_wick = highs[idx] - max(opens[idx], closes[idx])
        lower_wick = min(opens[idx], closes[idx]) - lows[idx]
        if closes[idx] > opens[idx]:
            consecutive_green += 1
            consecutive_red = 0
        elif closes[idx] < opens[idx]:
            consecutive_red += 1
            consecutive_green = 0
        else:
            consecutive_green = 0
            consecutive_red = 0

        high_window = high_20[idx]
        low_window = low_20[idx]
        breakout = bool(idx > 0 and high_window is not None and closes[idx] >= max(highs[max(0, idx - 20) : idx] or [closes[idx]]))
        breakdown = bool(idx > 0 and low_window is not None and closes[idx] <= min(lows[max(0, idx - 20) : idx] or [closes[idx]]))
        reversal = bool(abs_body > 0 and (upper_wick > abs_body * 1.5 or lower_wick > abs_body * 1.5))
        recent_returns = returns[max(0, idx - 19) : idx + 1]
        realized_volatility = pstdev(recent_returns) * math.sqrt(390) if len(recent_returns) > 1 else 0.0
        boll_width = safe_div((bollinger_upper[idx] or 0) - (bollinger_lower[idx] or 0), bollinger_middle[idx] or 0)
        atr_pct = safe_div(atr_14[idx] or 0, closes[idx])
        dt = ensure_utc(row["timestamp"])

        enriched = dict(row)
        enriched.update(
            {
                "ema_3": ema_3[idx],
                "ema_5": ema_5[idx],
                "ema_9": ema_9[idx],
                "ema_21": ema_21[idx],
                "ema_50": ema_50[idx],
                "sma_20": sma_20[idx],
                "sma_50": sma_50[idx],
                "vwap": vwap,
                "supertrend": supertrend_values[idx],
                "supertrend_direction": supertrend_direction[idx],
                "adx": adx_values[idx],
                "parabolic_sar": psar[idx],
                "rsi_7": rsi_7[idx],
                "rsi_14": rsi_14[idx],
                "macd": macd_line[idx],
                "macd_signal": macd_signal[idx],
                "macd_histogram": macd_histogram[idx],
                "macd_histogram_slope": (macd_histogram[idx] or 0) - (macd_histogram[idx - 1] or 0) if idx > 0 else 0.0,
                "stoch_rsi": _stoch_value(rsi_14, idx, 14),
                "roc": returns[idx],
                "momentum": closes[idx] - closes[idx - 10] if idx >= 10 else None,
                "cci": _cci(highs, lows, closes, idx, 20),
                "williams_r": _williams_r(highs, lows, closes, idx, 14),
                "atr_7": atr_7[idx],
                "atr_14": atr_14[idx],
                "atr_pct": atr_pct,
                "bollinger_upper": bollinger_upper[idx],
                "bollinger_middle": bollinger_middle[idx],
                "bollinger_lower": bollinger_lower[idx],
                "bollinger_bandwidth": boll_width,
                "keltner_middle": ema_20[idx],
                "keltner_upper": (ema_20[idx] + 2 * (atr_14[idx] or 0)) if ema_20[idx] is not None else None,
                "keltner_lower": (ema_20[idx] - 2 * (atr_14[idx] or 0)) if ema_20[idx] is not None else None,
                "donchian_upper": high_window,
                "donchian_lower": low_window,
                "realized_volatility": realized_volatility,
                "candle_range": candle_range,
                "average_candle_range": avg_range[idx],
                "volume_ma": volume_ma[idx],
                "relative_volume": safe_div(volumes[idx], volume_ma[idx] or 0, 1.0),
                "obv": obv_values[idx],
                "money_flow_index": mfi_values[idx],
                "accumulation_distribution": ad_values[idx],
                "vwap_deviation": safe_div(closes[idx] - _float(vwap), _float(vwap)),
                "candle_body_size": abs_body,
                "upper_wick": upper_wick,
                "lower_wick": lower_wick,
                "body_to_range_ratio": safe_div(abs_body, candle_range),
                "close_location_value": safe_div(closes[idx] - lows[idx], candle_range),
                "consecutive_green_candles": consecutive_green,
                "consecutive_red_candles": consecutive_red,
                "breakout_candle": breakout,
                "breakdown_candle": breakdown,
                "reversal_candle": reversal,
                "recent_high_20": high_window,
                "recent_low_20": low_window,
                "minute_of_day": dt.hour * 60 + dt.minute,
                "session": market_session(dt, extended_hours=True),
                "minutes_since_open": minutes_since_open(dt),
                "minutes_before_close": minutes_before_close(dt),
                "day_of_week": dt.weekday(),
                "first_30_minutes": 0 <= minutes_since_open(dt) < 30,
                "lunch_period": 120 <= minutes_since_open(dt) <= 240,
                "power_hour": 0 <= minutes_before_close(dt) <= 60,
            }
        )
        output.append(enriched)
    return output


def _stoch_value(values: list[float | None], idx: int, period: int) -> float | None:
    if idx + 1 < period:
        return None
    window = [value for value in values[idx + 1 - period : idx + 1] if value is not None]
    if len(window) < 2:
        return None
    low = min(window)
    high = max(window)
    return safe_div((values[idx] or low) - low, high - low)


def _cci(highs: list[float], lows: list[float], closes: list[float], idx: int, period: int) -> float | None:
    if idx + 1 < period:
        return None
    typical = [
        (high + low + close) / 3
        for high, low, close in zip(
            highs[idx + 1 - period : idx + 1],
            lows[idx + 1 - period : idx + 1],
            closes[idx + 1 - period : idx + 1],
        )
    ]
    typical_mean = mean(typical)
    mean_deviation = mean([abs(value - typical_mean) for value in typical])
    return safe_div(typical[-1] - typical_mean, 0.015 * mean_deviation)


def _williams_r(highs: list[float], lows: list[float], closes: list[float], idx: int, period: int) -> float | None:
    if idx + 1 < period:
        return None
    high = max(highs[idx + 1 - period : idx + 1])
    low = min(lows[idx + 1 - period : idx + 1])
    return -100 * safe_div(high - closes[idx], high - low)
