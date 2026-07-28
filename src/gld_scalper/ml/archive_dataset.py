from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.math_utils import safe_div
from ..utils.time_utils import ensure_utc, utc_now
from .dataset_builder import _flatten_features


@dataclass(slots=True)
class ArchiveDatasetResult:
    path: Path
    rows: int
    start: str | None
    end: str | None
    class_counts: dict[str, int]
    horizon_minutes: int
    horizons: list[int]
    stride_minutes: int
    fingerprint: str


def build_archive_training_records(
    database: Database,
    settings: Settings,
    *,
    start: datetime | str,
    end: datetime | str,
    stride_minutes: int = 5,
    horizon_minutes: int = 5,
    horizons: Sequence[int] | None = None,
    max_samples: int | None = None,
    slippage_pct: float = 0.0001,
    minimum_edge_pct: float = 0.0002,
) -> list[dict[str, Any]]:
    start_dt = ensure_utc(start)
    end_dt = ensure_utc(end)
    if end_dt <= start_dt:
        raise ValueError("end must be later than start")
    stride_minutes = max(1, int(stride_minutes))
    horizon_minutes = max(1, int(horizon_minutes))
    horizon_values = [*(horizons or (1, 3, 5, 15)), horizon_minutes]
    selected_horizons = sorted({max(1, int(value)) for value in horizon_values})
    maximum_horizon = max(selected_horizons)
    bars = [
        dict(row)
        for row in database.conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume, trade_count, vwap
            FROM bars
            WHERE symbol = ? AND timeframe = '1Min'
              AND julianday(timestamp) >= julianday(?)
              AND julianday(timestamp) < julianday(?)
            ORDER BY julianday(timestamp), id
            """,
            (settings.bot_symbol, start_dt.isoformat(), end_dt.isoformat()),
        ).fetchall()
    ]
    if len(bars) < 80 + maximum_horizon:
        raise RuntimeError("Not enough GLD 1-minute bars in the requested archive window.")
    for bar in bars:
        bar["dt"] = ensure_utc(bar["timestamp"])
    related = _load_related_closes(database, settings, start_dt, end_dt)
    news = _load_news_context(database, start_dt, end_dt)
    events = _load_event_context(database, start_dt, end_dt)
    macro = _load_macro_context(database)
    market_sessions = _load_market_sessions(database, start_dt, end_dt)
    records: list[dict[str, Any]] = []
    for index in range(60, len(bars) - maximum_horizon, stride_minutes):
        if max_samples is not None and len(records) >= max_samples:
            break
        current = bars[index]
        timestamp = current["dt"]
        if not _regular_session(timestamp, market_sessions):
            continue
        quote = _quote_at_or_before(database, settings.bot_symbol, timestamp, timestamp + timedelta(minutes=1))
        features = _bar_features(bars, index)
        features.update(_quote_features(quote, float(current["close"]), timestamp))
        features.update(_related_features(related, timestamp))
        features.update(_news_features(news, timestamp))
        features.update(_event_features(events, timestamp))
        features.update(_macro_features(macro, timestamp))
        features.update(_price_action_playbook_features(bars, index, features))
        playbook = _classify_playbook(features)
        features["playbook"] = playbook
        outcomes_by_horizon = {
            minutes: _outcome(
                current,
                bars[index + 1 : index + minutes + 1],
                spread_pct=float(features.get("spread_pct") or 0.0004),
                slippage_pct=slippage_pct,
                minimum_edge_pct=minimum_edge_pct,
            )
            for minutes in selected_horizons
        }
        outcome = outcomes_by_horizon[horizon_minutes]
        records.append(
            {
                "timestamp": timestamp,
                "features": _flatten_features(features),
                "label": outcome["label"],
                "outcome": outcome,
                "outcomes_by_horizon": outcomes_by_horizon,
                "horizon_minutes": horizon_minutes,
                "playbook": playbook,
                "strategy_path": "minute",
                "regime": _archive_regime(features),
                "executed_action": "COUNTERFACTUAL",
                "source": "historical_archive",
            }
        )
    return records


def save_archive_training_artifact(
    records: list[dict[str, Any]],
    settings: Settings,
    *,
    name: str,
    horizon_minutes: int,
    stride_minutes: int,
) -> ArchiveDatasetResult:
    if not records:
        raise RuntimeError("Archive dataset builder produced no eligible training rows.")
    output_dir = PROJECT_ROOT / "data" / settings.data_mode / "ml_training"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.joblib"
    columns = sorted({key for record in records for key in record["features"]})
    horizons = sorted({int(key) for record in records for key in (record.get("outcomes_by_horizon") or {horizon_minutes: {}})})
    fingerprint_source = {
        "rows": len(records),
        "start": records[0]["timestamp"].isoformat(),
        "end": records[-1]["timestamp"].isoformat(),
        "columns": columns,
        "horizons": horizons,
        "stride_minutes": stride_minutes,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_source, sort_keys=True).encode("utf-8")).hexdigest()
    payload = {
        "format_version": 3,
        "created_at": utc_now().isoformat(),
        "horizon_minutes": horizon_minutes,
        "horizons": horizons,
        "stride_minutes": stride_minutes,
        "fingerprint": fingerprint,
        "feature_columns": columns,
        "records": records,
    }
    import joblib

    joblib.dump(payload, path, compress=3)
    class_counts: dict[str, int] = defaultdict(int)
    for record in records:
        class_counts[str(record["label"])] += 1
    return ArchiveDatasetResult(
        path=path,
        rows=len(records),
        start=records[0]["timestamp"].isoformat(),
        end=records[-1]["timestamp"].isoformat(),
        class_counts=dict(class_counts),
        horizon_minutes=horizon_minutes,
        horizons=horizons,
        stride_minutes=stride_minutes,
        fingerprint=fingerprint,
    )


def load_archive_training_artifact(path: str | Path) -> list[dict[str, Any]]:
    import joblib

    payload = joblib.load(Path(path))
    if int(payload.get("format_version", 0)) not in {2, 3}:
        raise RuntimeError("Unsupported ML training artifact format.")
    records = list(payload.get("records") or [])
    for record in records:
        record["timestamp"] = ensure_utc(record["timestamp"])
    return records


def _price_action_playbook_features(
    bars: list[dict[str, Any]],
    index: int,
    features: dict[str, Any],
) -> dict[str, Any]:
    current_close = float(bars[index]["close"])
    previous_close = float(bars[index - 1]["close"])
    prior = bars[index - 21 : index - 1]
    prior_high = max(float(row["high"]) for row in prior)
    prior_low = min(float(row["low"]) for row in prior)
    return {
        "false_break_up": previous_close > prior_high and current_close < prior_high,
        "false_break_down": previous_close < prior_low and current_close > prior_low,
        "pullback_up": float(features.get("return_15m") or 0.0) > 0.001 and float(features.get("return_1m") or 0.0) < 0.0,
        "pullback_down": float(features.get("return_15m") or 0.0) < -0.001 and float(features.get("return_1m") or 0.0) > 0.0,
    }


def _classify_playbook(features: dict[str, Any]) -> str:
    if float(features.get("headline_event_risk") or 0.0) >= 0.60 or features.get("event_risk_active"):
        return "news_event"
    if float(features.get("spread_pct") or 1.0) <= 0.00035 and abs(float(features.get("quote_imbalance") or 0.0)) >= 0.30:
        return "spread_capture"
    if features.get("false_break_up") or features.get("false_break_down"):
        return "false_break_reversal"
    if features.get("breakout_20_high") or features.get("breakout_20_low"):
        return "proper_breakout"
    if float(features.get("compression_20") or 1.0) <= 0.45:
        return "compression_breakout"
    if features.get("pullback_up") or features.get("pullback_down"):
        return "pullback_continuation"
    return "trend_continuation"


def _archive_regime(features: dict[str, Any]) -> str:
    volatility = abs(float(features.get("return_15m") or 0.0))
    compression = float(features.get("compression_20") or 1.0)
    if volatility >= 0.004:
        return "high_volatility"
    if compression <= 0.45:
        return "range_compression"
    if abs(float(features.get("return_60m") or 0.0)) >= 0.003:
        return "trend"
    return "sideways_chop"


def _bar_features(bars: list[dict[str, Any]], index: int) -> dict[str, Any]:
    current = bars[index]
    close = float(current["close"])
    previous = bars[index - 1]
    window_5 = bars[index - 4 : index + 1]
    window_20 = bars[index - 19 : index + 1]
    window_60 = bars[index - 59 : index + 1]
    closes_20 = [float(row["close"]) for row in window_20]
    mean_20 = sum(closes_20) / len(closes_20)
    variance_20 = sum((value - mean_20) ** 2 for value in closes_20) / len(closes_20)
    volume_20 = sum(float(row.get("volume") or 0.0) for row in window_20) / len(window_20)
    minute_of_day = current["dt"].hour * 60 + current["dt"].minute
    angle = 2 * math.pi * minute_of_day / 1440
    return {
        "log_volume": math.log1p(float(current.get("volume") or 0.0)),
        "log_trade_count": math.log1p(float(current.get("trade_count") or 0.0)),
        "return_1m": safe_div(close - float(previous["close"]), float(previous["close"])),
        "return_5m": safe_div(close - float(bars[index - 5]["close"]), float(bars[index - 5]["close"])),
        "return_15m": safe_div(close - float(bars[index - 15]["close"]), float(bars[index - 15]["close"])),
        "range_1m_pct": safe_div(float(current["high"]) - float(current["low"]), close),
        "range_5m_pct": safe_div(max(float(row["high"]) for row in window_5) - min(float(row["low"]) for row in window_5), close),
        "range_20m_pct": safe_div(max(float(row["high"]) for row in window_20) - min(float(row["low"]) for row in window_20), close),
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
        "weekday": float(current["dt"].weekday()),
    }


def _quote_at_or_before(
    database: Database,
    symbol: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
    row = database.conn.execute(
        """
        SELECT timestamp, bid_price, ask_price, bid_size, ask_size, spread, spread_pct, quote_imbalance
        FROM quotes
        WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
        ORDER BY timestamp DESC LIMIT 1
        """,
        (symbol, start.isoformat(), end.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def _quote_features(quote: dict[str, Any] | None, close: float, timestamp: datetime) -> dict[str, Any]:
    if not quote:
        return {
            "spread_pct": 0.0004,
            "quote_imbalance": 0.0,
            "quote_age_seconds": 60.0,
            "quote_missing": True,
        }
    bid = float(quote.get("bid_price") or close)
    ask = float(quote.get("ask_price") or close)
    bid_size = float(quote.get("bid_size") or 0.0)
    ask_size = float(quote.get("ask_size") or 0.0)
    midpoint = (bid + ask) / 2
    spread = max(0.0, ask - bid)
    return {
        "bid_size": bid_size,
        "ask_size": ask_size,
        "spread": spread,
        "spread_pct": float(quote.get("spread_pct") or safe_div(spread, midpoint)),
        "quote_imbalance": float(quote.get("quote_imbalance") or safe_div(bid_size - ask_size, bid_size + ask_size)),
        "quote_age_seconds": max(0.0, (ensure_utc(quote["timestamp"]) - timestamp).total_seconds()),
        "quote_missing": False,
    }


def _outcome(
    current: dict[str, Any],
    future: list[dict[str, Any]],
    *,
    spread_pct: float,
    slippage_pct: float,
    minimum_edge_pct: float,
) -> dict[str, Any]:
    entry = float(current["close"])
    exit_price = float(future[-1]["close"])
    raw_return = safe_div(exit_price - entry, entry)
    cost = max(spread_pct, 0.0) + max(slippage_pct, 0.0)
    net_long = raw_return - cost
    net_short = -raw_return - cost
    mfe_long = safe_div(max(float(row["high"]) for row in future) - entry, entry) - cost
    mae_long = safe_div(min(float(row["low"]) for row in future) - entry, entry) - cost
    if net_long >= minimum_edge_pct and net_long > net_short:
        label = "long_good"
    elif net_short >= minimum_edge_pct and net_short > net_long:
        label = "short_good"
    else:
        label = "no_trade"
    return {
        "label": label,
        "raw_forward_return": raw_return,
        "net_return_long": net_long,
        "net_return_short": net_short,
        "spread_cost": spread_pct,
        "slippage_cost": slippage_pct,
        "total_cost": cost,
        "max_favorable_long": mfe_long,
        "max_adverse_long": mae_long,
    }


def _load_related_closes(
    database: Database,
    settings: Settings,
    start: datetime,
    end: datetime,
) -> dict[str, tuple[list[datetime], list[float]]]:
    result: dict[str, tuple[list[datetime], list[float]]] = {}
    for symbol in settings.related_symbols:
        rows = database.conn.execute(
            """
            SELECT timestamp, close FROM bars
            WHERE symbol = ? AND timeframe = '1Min'
              AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
            ORDER BY julianday(timestamp), id
            """,
            (symbol, start.isoformat(), end.isoformat()),
        ).fetchall()
        result[symbol] = ([ensure_utc(row["timestamp"]) for row in rows], [float(row["close"]) for row in rows])
    return result


def _related_features(
    related: dict[str, tuple[list[datetime], list[float]]],
    timestamp: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol, (timestamps, closes) in related.items():
        index = bisect.bisect_right(timestamps, timestamp) - 1
        key = symbol.lower()
        if index < 0 or timestamp - timestamps[index] > timedelta(minutes=5):
            result[f"related_{key}_missing"] = True
            continue
        if index >= 5:
            result[f"related_{key}_return_5m"] = safe_div(closes[index] - closes[index - 5], closes[index - 5])
        if index >= 15:
            result[f"related_{key}_return_15m"] = safe_div(closes[index] - closes[index - 15], closes[index - 15])
    return result


def _load_news_context(database: Database, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [
        {**dict(row), "dt": ensure_utc(row["timestamp"])}
        for row in database.conn.execute(
            """
            SELECT timestamp, sentiment_score, confidence_score, gold_score, usd_score, rates_score, risk_score, event_risk
            FROM news_items
            WHERE julianday(timestamp) >= julianday(?, '-1 day') AND julianday(timestamp) < julianday(?)
            ORDER BY julianday(timestamp), id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    ]


def _news_features(news: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any]:
    times = [item["dt"] for item in news]
    end_index = bisect.bisect_right(times, timestamp)
    start_index = bisect.bisect_left(times, timestamp - timedelta(hours=24), 0, end_index)
    window = news[start_index:end_index]
    recent = [item for item in window if timestamp - item["dt"] <= timedelta(hours=1)]
    return {
        "news_count_1h": float(len(recent)),
        "news_count_24h": float(len(window)),
        "news_gold_score_1h": _mean(recent, "gold_score"),
        "news_usd_score_1h": _mean(recent, "usd_score"),
        "news_rates_score_1h": _mean(recent, "rates_score"),
        "news_risk_score_1h": _mean(recent, "risk_score"),
        "headline_event_risk": max([float(item.get("event_risk") or 0.0) for item in recent] or [0.0]),
    }


def _load_event_context(database: Database, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [
        {**dict(row), "dt": ensure_utc(row["scheduled_at"])}
        for row in database.conn.execute(
            """
            SELECT scheduled_at, importance, surprise_pct, avoid_trading
            FROM economic_events
            WHERE julianday(scheduled_at) >= julianday(?, '-1 day') AND julianday(scheduled_at) < julianday(?, '+1 day')
            ORDER BY julianday(scheduled_at), id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    ]


def _event_features(events: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any]:
    times = [item["dt"] for item in events]
    next_index = bisect.bisect_left(times, timestamp)
    previous_index = next_index - 1
    next_event = events[next_index] if next_index < len(events) else None
    previous_event = events[previous_index] if previous_index >= 0 else None
    minutes_to_next = (next_event["dt"] - timestamp).total_seconds() / 60 if next_event else 1_000_000.0
    minutes_since_previous = (timestamp - previous_event["dt"]).total_seconds() / 60 if previous_event else 1_000_000.0
    return {
        "minutes_to_next_event": min(minutes_to_next, 1440.0),
        "minutes_since_event": min(minutes_since_previous, 1440.0),
        "event_risk_active": minutes_to_next <= 45 or minutes_since_previous <= 15,
        "next_event_high_importance": bool(next_event and str(next_event.get("importance") or "").lower() in {"high", "3"}),
        "previous_event_surprise_pct": float(previous_event.get("surprise_pct") or 0.0) if previous_event else 0.0,
    }


def _load_macro_context(database: Database) -> dict[str, tuple[list[datetime], list[float]]]:
    grouped: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in database.conn.execute(
        """
        SELECT series_id, observation_date, realtime_start, value
        FROM macro_series WHERE value IS NOT NULL
        ORDER BY series_id, observation_date, realtime_start
        """
    ).fetchall():
        observation = ensure_utc(f"{row['observation_date']}T00:00:00+00:00")
        realtime = ensure_utc(f"{row['realtime_start']}T00:00:00+00:00") if row["realtime_start"] else observation
        grouped[str(row["series_id"])].append((max(observation, realtime), float(row["value"])))
    result: dict[str, tuple[list[datetime], list[float]]] = {}
    for series, values in grouped.items():
        ordered = sorted(values, key=lambda item: item[0])
        result[series] = ([item[0] for item in ordered], [item[1] for item in ordered])
    return result


def _macro_features(macro: dict[str, tuple[list[datetime], list[float]]], timestamp: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for series, (times, values) in macro.items():
        index = bisect.bisect_right(times, timestamp) - 1
        if index >= 0:
            result[f"macro_{series.lower()}"] = values[index]
    return result


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row.get(key) or 0.0) for row in rows if row.get(key) is not None]
    return sum(values) / max(len(values), 1)


def _load_market_sessions(
    database: Database,
    start: datetime,
    end: datetime,
) -> dict[str, tuple[datetime, datetime]]:
    sessions: dict[str, tuple[datetime, datetime]] = {}
    eastern = ZoneInfo("America/New_York")
    rows = database.conn.execute(
        """
        SELECT calendar_date, open_time, close_time FROM market_calendar
        WHERE is_open = 1 AND calendar_date >= ? AND calendar_date <= ?
        ORDER BY calendar_date
        """,
        (start.date().isoformat(), end.date().isoformat()),
    ).fetchall()
    for row in rows:
        if not row["open_time"] or not row["close_time"]:
            continue
        raw_open = datetime.fromisoformat(str(row["open_time"]))
        raw_close = datetime.fromisoformat(str(row["close_time"]))
        local_open = raw_open.replace(tzinfo=None).replace(tzinfo=eastern)
        local_close = raw_close.replace(tzinfo=None).replace(tzinfo=eastern)
        sessions[str(row["calendar_date"])] = (local_open.astimezone(ZoneInfo("UTC")), local_close.astimezone(ZoneInfo("UTC")))
    return sessions


def _regular_session(timestamp: datetime, sessions: dict[str, tuple[datetime, datetime]]) -> bool:
    session = sessions.get(timestamp.date().isoformat())
    if session is not None:
        return session[0] <= timestamp < session[1]
    # Fallback used only by small fixtures or archives without a market calendar.
    minute = timestamp.hour * 60 + timestamp.minute
    return 13 * 60 + 30 <= minute < 21 * 60
