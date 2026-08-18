from __future__ import annotations

import bisect
import hashlib
import json
import math
import shutil
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np

from ..config import Settings
from ..database import Database
from ..utils.time_utils import ensure_utc
from .dataset_builder import _flatten_features
from .transformer_model import ENTRY_CLASSES, EXIT_CLASSES, HORIZONS_MINUTES


NY = ZoneInfo("America/New_York")
FEATURE_PRIORITY_TERMS = (
    "return",
    "spread",
    "imbalance",
    "signed_volume",
    "trade_intensity",
    "liquidity",
    "volatility",
    "quote_age",
    "trade_age",
    "data_age",
    "proper_break",
    "false_break",
    "tease_break",
    "pullback",
    "compression",
    "support",
    "resistance",
    "order_block",
    "pattern",
    "session",
    "time_of_day",
    "news",
    "sentiment",
    "event_risk",
    "technical_family",
    "technical_route",
    "fibonacci",
    "fib_",
    "fvg",
    "rsi_divergence",
    "options",
    "pnl",
    "excursion",
    "stop",
)


@dataclass(slots=True)
class TransformerDatasetResult:
    path: Path
    scope: str
    source: str
    timeline_rows: int
    samples: int
    feature_count: int
    class_counts: dict[str, int]
    start: str | None
    end: str | None
    fingerprint: str


@dataclass(slots=True)
class LoadedSequenceArtifact:
    path: Path
    manifest: dict[str, Any]
    features: np.ndarray
    timestamps: np.ndarray
    session_ids: np.ndarray
    sample_end_indices: np.ndarray
    labels: np.ndarray
    returns: np.ndarray
    costs: np.ndarray
    regime_ids: np.ndarray
    baseline_probabilities: np.ndarray


@dataclass(slots=True)
class _Timeline:
    timestamps: list[datetime]
    features: list[dict[str, float]]
    labels: list[str | None]
    returns: list[list[float]]
    costs: list[float]
    eligible: list[bool]
    regimes: list[str]
    baseline_probabilities: list[list[float]]
    baseline_versions: list[str]
    session_keys: list[str]
    classes: tuple[str, ...]
    source: str


def build_transformer_sequence_artifact(
    database: Database,
    settings: Settings,
    *,
    scope: str,
    start: datetime | str,
    end: datetime | str,
    output: str | Path | None = None,
    source: str = "auto",
    sequence_length: int | None = None,
    window_seconds: int | None = None,
    stride: int = 1,
    max_samples: int | None = 50_000,
    max_features: int = 64,
    overwrite: bool = False,
) -> TransformerDatasetResult:
    start_dt, end_dt = ensure_utc(start), ensure_utc(end)
    if end_dt <= start_dt:
        raise ValueError("end must be later than start")
    scope = _normalize_scope(scope)
    defaults = sequence_defaults(scope)
    sequence_length = int(sequence_length or defaults["sequence_length"])
    window_seconds = int(window_seconds or defaults["window_seconds"])
    if sequence_length < 2 or window_seconds < 2 or stride < 1:
        raise ValueError("sequence length, window, and stride must be positive")
    if max_features < 8:
        raise ValueError("max_features must be at least eight")

    path = _artifact_path(settings, scope, output)
    resolved_source = _resolve_source(database, scope, source, start_dt, end_dt)
    fast_checkpoint_path: Path | None = None
    if scope == "fast_microstructure" and resolved_source == "raw":
        fast_checkpoint_path = path.with_name(f"{path.name}.building")
        timeline = _raw_fast_timeline(
            database,
            settings,
            start_dt,
            end_dt,
            stride,
            max_samples,
            checkpoint_path=fast_checkpoint_path,
        )
    elif scope == "minute":
        timeline = (
            _decision_timeline(database, settings, "signal", start_dt, end_dt, stride)
            if resolved_source == "decisions"
            else _raw_minute_timeline(database, settings, start_dt, end_dt, stride)
        )
    elif scope == "fast_microstructure":
        timeline = _decision_timeline(database, settings, "fast_scalp", start_dt, end_dt, stride)
    elif scope == "news_event":
        timeline = _news_timeline(database, settings, start_dt, end_dt)
    elif scope == "exit":
        timeline = _exit_timeline(database, settings, start_dt, end_dt)
    else:  # pragma: no cover - normalized above
        raise ValueError(f"Unsupported Transformer scope: {scope}")

    if not timeline.timestamps:
        raise RuntimeError(f"No {scope} timeline data was available for {start_dt} to {end_dt}.")
    eligible = [index for index, flag in enumerate(timeline.eligible) if flag and timeline.labels[index] in timeline.classes]
    if max_samples is not None and len(eligible) > max_samples:
        positions = np.linspace(0, len(eligible) - 1, num=max_samples, dtype=np.int64)
        selected = {eligible[int(position)] for position in positions}
        timeline.eligible = [flag and index in selected for index, flag in enumerate(timeline.eligible)]

    result = _write_artifact(
        timeline,
        path=path,
        scope=scope,
        sequence_length=sequence_length,
        window_seconds=window_seconds,
        max_features=max_features,
        overwrite=overwrite,
    )
    if fast_checkpoint_path is not None and fast_checkpoint_path.exists():
        shutil.rmtree(fast_checkpoint_path)
    return result


def load_transformer_sequence_artifact(path: str | Path, *, mmap_mode: str | None = "r") -> LoadedSequenceArtifact:
    root = Path(path)
    manifest_path = root / "manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(f"Transformer sequence artifact not found: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    arrays = {
        name: np.load(root / f"{name}.npy", mmap_mode=mmap_mode, allow_pickle=False)
        for name in (
            "features",
            "timestamps",
            "session_ids",
            "sample_end_indices",
            "labels",
            "returns",
            "costs",
            "regime_ids",
            "baseline_probabilities",
        )
    }
    return LoadedSequenceArtifact(path=root, manifest=manifest, **arrays)


def sequence_defaults(scope: str) -> dict[str, int]:
    scope = _normalize_scope(scope)
    if scope == "fast_microstructure":
        return {"sequence_length": 120, "window_seconds": 120}
    if scope == "minute":
        return {"sequence_length": 90, "window_seconds": 90 * 60}
    if scope == "news_event":
        return {"sequence_length": 90, "window_seconds": 90 * 60}
    return {"sequence_length": 120, "window_seconds": 15 * 60}


def transformer_registry_scope(scope: str) -> str:
    scope = _normalize_scope(scope)
    if scope == "exit":
        return "transformer:exit"
    return f"transformer:entry:{scope}"


def _normalize_scope(scope: str) -> str:
    value = scope.strip().lower().replace("-", "_")
    aliases = {
        "fast": "fast_microstructure",
        "microstructure": "fast_microstructure",
        "minute_setup": "minute",
        "minute_setups": "minute",
        "news": "news_event",
    }
    value = aliases.get(value, value)
    if value not in {"fast_microstructure", "minute", "news_event", "exit"}:
        raise ValueError("scope must be fast_microstructure, minute, news_event, or exit")
    return value


def _resolve_source(
    database: Database,
    scope: str,
    source: str,
    start: datetime,
    end: datetime,
) -> str:
    source = source.strip().lower()
    if source not in {"auto", "raw", "decisions"}:
        raise ValueError("source must be auto, raw, or decisions")
    if source != "auto":
        return source
    table = "fast_scalp_decisions" if scope == "fast_microstructure" else "signals"
    if scope in {"fast_microstructure", "minute"} and _table_has_rows(database, table, start, end):
        return "decisions"
    return "raw"


def _decision_timeline(
    database: Database,
    settings: Settings,
    decision_source: str,
    start: datetime,
    end: datetime,
    stride: int,
) -> _Timeline:
    table = "fast_scalp_decisions" if decision_source == "fast_scalp" else "signals"
    columns = _table_columns(database, table)
    if not columns:
        return _empty_timeline(ENTRY_CLASSES, f"paper_{decision_source}")
    base_columns = ["id", "timestamp", "feature_snapshot_json"]
    optional = [
        name
        for name in (
            "decision",
            "regime",
            "spread_pct",
            "quote_imbalance",
            "trade_intensity",
            "liquidity_score",
            "volatility_burst",
            "probability_long",
            "probability_short",
            "probability_no_trade",
            "model_version",
        )
        if name in columns
    ]
    rows = database.conn.execute(
        f"""
        SELECT {', '.join(base_columns + optional)}
        FROM {table}
        WHERE symbol = ? AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
        ORDER BY julianday(timestamp), id
        """,
        (settings.bot_symbol, start.isoformat(), end.isoformat()),
    ).fetchall()
    outcome_map = _outcomes_by_decision(database, decision_source, start, end)
    by_bucket: dict[str, dict[str, Any]] = {}
    bucket_seconds = 1 if decision_source == "fast_scalp" else 60
    for offset, raw in enumerate(rows):
        if offset % stride:
            continue
        row = dict(raw)
        timestamp = ensure_utc(row["timestamp"])
        bucket_epoch = int(timestamp.timestamp()) // bucket_seconds * bucket_seconds
        bucket = str(bucket_epoch)
        raw_features = _safe_json(row.get("feature_snapshot_json"))
        features = _flatten_features(raw_features)
        features.update(_flatten_features({key: row.get(key) for key in optional if key not in {"model_version"}}))
        features.update(_clock_features(timestamp))
        outcome = outcome_map.get(int(row["id"]))
        baseline = [
            _finite(features.get("ml_probability_long"), _finite(row.get("probability_long"), math.nan)),
            _finite(features.get("ml_probability_short"), _finite(row.get("probability_short"), math.nan)),
            _finite(features.get("ml_probability_no_trade"), _finite(row.get("probability_no_trade"), math.nan)),
        ]
        by_bucket[bucket] = {
            "timestamp": timestamp,
            "features": features,
            "outcome": outcome,
            "regime": str(row.get("regime") or _feature_category(features, "regime") or "unknown"),
            "baseline": baseline,
            "baseline_version": str(raw_features.get("ml_model_version") or row.get("model_version") or ""),
        }
    timeline = _empty_timeline(ENTRY_CLASSES, f"paper_{decision_source}")
    for item in sorted(by_bucket.values(), key=lambda value: value["timestamp"]):
        outcome = item["outcome"] or {}
        labels = [outcome.get(f"label_{minutes}m") for minutes in HORIZONS_MINUTES]
        primary = labels[0] if decision_source == "fast_scalp" else labels[2]
        timeline.timestamps.append(item["timestamp"])
        timeline.features.append(item["features"])
        timeline.labels.append(primary if primary in ENTRY_CLASSES else None)
        timeline.returns.append([_finite(outcome.get(f"forward_return_{minutes}m"), math.nan) for minutes in HORIZONS_MINUTES])
        timeline.costs.append(abs(_finite(outcome.get("realized_spread_cost"), 0.0)) + settings.outcome_label_slippage_pct)
        timeline.eligible.append(primary in ENTRY_CLASSES)
        timeline.regimes.append(item["regime"])
        timeline.baseline_probabilities.append(item["baseline"])
        timeline.baseline_versions.append(item["baseline_version"])
        timeline.session_keys.append(_session_key(item["timestamp"]))
    return timeline


def _raw_minute_timeline(
    database: Database,
    settings: Settings,
    start: datetime,
    end: datetime,
    stride: int,
) -> _Timeline:
    rows = [
        dict(row)
        for row in database.conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume, trade_count, vwap
            FROM bars
            WHERE symbol = ? AND timeframe = '1Min'
              AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
            ORDER BY julianday(timestamp), id
            """,
            (settings.bot_symbol, start.isoformat(), end.isoformat()),
        ).fetchall()
    ]
    rows = [row for row in rows if _regular_session(ensure_utc(row["timestamp"]))]
    timeline = _empty_timeline(ENTRY_CLASSES, "historical_1min_bars")
    if len(rows) < 100:
        return timeline
    timestamps = [ensure_utc(row["timestamp"]) for row in rows]
    timestamp_values = [value.timestamp() for value in timestamps]
    closes = [_finite(row.get("close")) for row in rows]
    volumes = [_finite(row.get("volume")) for row in rows]
    for index, (row, timestamp) in enumerate(zip(rows, timestamps)):
        close = closes[index]
        prior = closes[max(index - 1, 0)] or close
        lookback = closes[max(0, index - 30) : index + 1]
        volume_window = volumes[max(0, index - 30) : index + 1]
        returns = [lookback[i] / lookback[i - 1] - 1.0 for i in range(1, len(lookback)) if lookback[i - 1] > 0]
        high = _finite(row.get("high"), close)
        low = _finite(row.get("low"), close)
        open_price = _finite(row.get("open"), close)
        recent_high = max(closes[max(0, index - 20) : index] or [close])
        recent_low = min(closes[max(0, index - 20) : index] or [close])
        range_pct = (high - low) / max(close, 1e-12)
        average_range = np.mean(
            [
                (_finite(item.get("high")) - _finite(item.get("low"))) / max(_finite(item.get("close")), 1e-12)
                for item in rows[max(0, index - 20) : index + 1]
            ]
        )
        feature = {
            "return_1m": close / max(prior, 1e-12) - 1.0,
            "return_3m": close / max(closes[max(index - 3, 0)], 1e-12) - 1.0,
            "return_5m": close / max(closes[max(index - 5, 0)], 1e-12) - 1.0,
            "return_15m": close / max(closes[max(index - 15, 0)], 1e-12) - 1.0,
            "range_pct": range_pct,
            "candle_body_strength": abs(close - open_price) / max(high - low, 1e-12),
            "upper_wick_pct": (high - max(open_price, close)) / max(close, 1e-12),
            "lower_wick_pct": (min(open_price, close) - low) / max(close, 1e-12),
            "vwap_deviation_pct": close / max(_finite(row.get("vwap"), close), 1e-12) - 1.0,
            "volume": _finite(row.get("volume")),
            "trade_count": _finite(row.get("trade_count")),
            "relative_volume": _finite(row.get("volume")) / max(float(np.mean(volume_window)), 1e-12),
            "realized_volatility_30": float(np.std(returns)) if returns else 0.0,
            "volatility_burst": float(bool(returns) and abs(returns[-1]) > max(float(np.std(returns)) * 2.0, 0.0005)),
            "range_compression": float(range_pct < float(average_range) * 0.65),
            "proper_break": float(close > recent_high or close < recent_low),
            "false_break": float((high > recent_high and close <= recent_high) or (low < recent_low and close >= recent_low)),
            "support_distance_pct": (close - recent_low) / max(close, 1e-12),
            "resistance_distance_pct": (recent_high - close) / max(close, 1e-12),
            "data_age_seconds": 0.0,
            "quote_age_seconds": math.nan,
            "trade_age_seconds": math.nan,
            "spread_pct": math.nan,
            **_clock_features(timestamp),
        }
        future_returns = _future_returns(timestamp_values, timestamps, closes, index, HORIZONS_MINUTES)
        cost = settings.outcome_label_slippage_pct + settings.economic_breakeven_safety_buffer_pct
        label = _direction_label(future_returns[2], cost, settings.outcome_label_min_edge_pct)
        timeline.timestamps.append(timestamp)
        timeline.features.append(_flatten_features(feature))
        timeline.labels.append(label)
        timeline.returns.append(future_returns)
        timeline.costs.append(cost)
        timeline.eligible.append(index >= 30 and index % stride == 0 and all(math.isfinite(value) for value in future_returns))
        timeline.regimes.append(_bar_regime(feature))
        timeline.baseline_probabilities.append([math.nan, math.nan, math.nan])
        timeline.baseline_versions.append("")
        timeline.session_keys.append(_session_key(timestamp))
    return timeline


def _raw_fast_timeline(
    database: Database,
    settings: Settings,
    start: datetime,
    end: datetime,
    stride: int,
    max_samples: int | None,
    checkpoint_path: Path | None = None,
) -> _Timeline:
    quote_columns = _table_columns(database, "quotes")
    if not quote_columns:
        return _empty_timeline(ENTRY_CLASSES, "historical_quotes_trades")
    # Uniform, contiguous windows represent the whole archive without materializing
    # hundreds of millions of quotes. A LIMIT on one five-year GROUP BY would select
    # only the earliest period and still force SQLite to scan most of the archive.
    timeline = _empty_timeline(ENTRY_CLASSES, "historical_quotes_trades")
    windows = _fast_archive_windows(start, end, max_samples)
    checkpoint_path = checkpoint_path or Path(".transformer-fast-building")
    checkpoint_path.mkdir(parents=True, exist_ok=True)
    checkpoint_manifest = checkpoint_path / "manifest.json"
    signature = {
        "format_version": 1,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "stride": stride,
        "max_samples": max_samples,
        "window_count": len(windows),
    }
    if checkpoint_manifest.exists():
        stored_signature = json.loads(checkpoint_manifest.read_text(encoding="utf-8"))
        if stored_signature != signature:
            raise RuntimeError(
                f"Fast Transformer checkpoint does not match this build: {checkpoint_path}. "
                "Remove that checkpoint directory or rerun with the original arguments."
            )
    else:
        checkpoint_manifest.write_text(json.dumps(signature, indent=2, sort_keys=True), encoding="utf-8")

    started = time.monotonic()
    resumed_windows = 0
    for window_number, (window_start, window_end) in enumerate(windows):
        checkpoint_file = checkpoint_path / f"window_{window_number:04d}.json"
        if checkpoint_file.exists():
            _append_fast_checkpoint(timeline, checkpoint_file)
            resumed_windows += 1
            _print_fast_progress(
                window_number + 1,
                len(windows),
                timeline,
                started,
                resumed=True,
            )
            continue
        quote_rows = database.conn.execute(
            """
            SELECT substr(timestamp, 1, 19) AS bucket,
                   avg(bid_price) AS bid_price, avg(ask_price) AS ask_price,
                   avg(bid_size) AS bid_size, avg(ask_size) AS ask_size,
                   avg(spread_pct) AS spread_pct, avg(quote_imbalance) AS quote_imbalance,
                   count(*) AS quote_count
            FROM quotes
            WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
            GROUP BY bucket ORDER BY bucket
            """,
            (settings.bot_symbol, window_start.isoformat(), window_end.isoformat()),
        ).fetchall()
        if not quote_rows:
            _write_fast_checkpoint(checkpoint_file, [])
            _print_fast_progress(window_number + 1, len(windows), timeline, started)
            continue
        trade_map = {
            str(row["bucket"]): dict(row)
            for row in database.conn.execute(
                """
                SELECT substr(timestamp, 1, 19) AS bucket, avg(price) AS trade_price,
                       sum(size) AS trade_volume, count(*) AS trade_count,
                       min(price) AS trade_low, max(price) AS trade_high
                FROM market_trades
                WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
                GROUP BY bucket ORDER BY bucket
                """,
                (settings.bot_symbol, window_start.isoformat(), window_end.isoformat()),
            ).fetchall()
        }
        mids: list[float] = []
        vol_window: deque[float] = deque(maxlen=30)
        window_key = f"{_session_key(window_start)}:sample-{window_number}"
        checkpoint_rows: list[dict[str, Any]] = []
        for raw in quote_rows:
            timestamp = ensure_utc(str(raw["bucket"]) + "+00:00")
            bid, ask = _finite(raw["bid_price"]), _finite(raw["ask_price"])
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            mid = (bid + ask) / 2.0
            trade = trade_map.get(str(raw["bucket"]), {})
            trade_price = _finite(trade.get("trade_price"), mid)
            trade_volume = _finite(trade.get("trade_volume"))
            signed_volume = trade_volume * (1.0 if trade_price > mid else -1.0 if trade_price < mid else 0.0)
            current_return = mid / max(mids[-1], 1e-12) - 1.0 if mids else 0.0
            vol_window.append(current_return)
            volatility = float(np.std(vol_window)) if len(vol_window) >= 5 else 0.0
            spread_pct = _finite(raw["spread_pct"], (ask - bid) / mid)
            imbalance = _finite(raw["quote_imbalance"], (_finite(raw["bid_size"]) - _finite(raw["ask_size"])) / max(_finite(raw["bid_size"]) + _finite(raw["ask_size"]), 1e-12))
            intensity = _finite(trade.get("trade_count"))
            liquidity = max(0.0, min(1.0, 1.0 - spread_pct / max(settings.max_spread_pct, 1e-12)))
            feature = {
                "midpoint": mid,
                "return_1s": current_return,
                "spread_pct": spread_pct,
                "quote_imbalance": imbalance,
                "bid_size": _finite(raw["bid_size"]),
                "ask_size": _finite(raw["ask_size"]),
                "quote_count": _finite(raw["quote_count"]),
                "trade_price_deviation": trade_price / mid - 1.0,
                "trade_intensity": intensity,
                "trade_volume": trade_volume,
                "signed_volume": signed_volume,
                "aggressive_pressure": signed_volume / max(trade_volume, 1.0),
                "realized_volatility_30s": volatility,
                "volatility_burst": float(abs(current_return) > max(volatility * 2.0, settings.fast_scalp_volatility_burst_pct)),
                "liquidity_score": liquidity,
                "quote_age_seconds": 0.0,
                "trade_age_seconds": 0.0 if intensity else math.nan,
                "data_age_seconds": 0.0,
                **_clock_features(timestamp),
            }
            mids.append(mid)
            timeline.timestamps.append(timestamp)
            timeline.features.append(_flatten_features(feature))
            timeline.labels.append(None)
            timeline.returns.append([math.nan] * len(HORIZONS_MINUTES))
            timeline.costs.append(spread_pct + settings.outcome_label_slippage_pct)
            timeline.eligible.append(False)
            timeline.regimes.append("volatility_burst" if feature["volatility_burst"] else "microstructure")
            timeline.baseline_probabilities.append([math.nan, math.nan, math.nan])
            timeline.baseline_versions.append("")
            timeline.session_keys.append(window_key)
            checkpoint_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "features": timeline.features[-1],
                    "cost": timeline.costs[-1],
                    "regime": timeline.regimes[-1],
                    "session_key": window_key,
                }
            )
        _write_fast_checkpoint(checkpoint_file, checkpoint_rows)
        _print_fast_progress(window_number + 1, len(windows), timeline, started)
    _label_raw_timeline(timeline, primary_horizon=1, stride=stride, settings=settings)
    if resumed_windows:
        print(f"resumed {resumed_windows} completed fast Transformer windows", flush=True)
    return timeline


def _write_fast_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _append_fast_checkpoint(timeline: _Timeline, path: Path) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        timeline.timestamps.append(ensure_utc(row["timestamp"]))
        timeline.features.append({str(key): float(value) for key, value in row["features"].items()})
        timeline.labels.append(None)
        timeline.returns.append([math.nan] * len(HORIZONS_MINUTES))
        timeline.costs.append(float(row["cost"]))
        timeline.eligible.append(False)
        timeline.regimes.append(str(row["regime"]))
        timeline.baseline_probabilities.append([math.nan, math.nan, math.nan])
        timeline.baseline_versions.append("")
        timeline.session_keys.append(str(row["session_key"]))


def _print_fast_progress(
    completed: int,
    total: int,
    timeline: _Timeline,
    started: float,
    *,
    resumed: bool = False,
) -> None:
    elapsed = max(time.monotonic() - started, 0.001)
    processed_this_run = max(completed, 1)
    remaining_seconds = elapsed / processed_this_run * max(total - completed, 0)
    state = "resumed" if resumed else "completed"
    print(
        f"fast dataset window {completed}/{total} {state}; "
        f"timeline_rows={len(timeline.timestamps)} elapsed={elapsed:.1f}s "
        f"estimated_remaining={remaining_seconds:.1f}s",
        flush=True,
    )


def _fast_archive_windows(start: datetime, end: datetime, max_samples: int | None) -> list[tuple[datetime, datetime]]:
    local_start = start.astimezone(NY).date()
    local_end = end.astimezone(NY).date()
    phases = (0, 60, 150, 270, 350)
    candidates: list[tuple[datetime, datetime]] = []
    day = local_start
    phase_index = 0
    while day <= local_end:
        if day.weekday() < 5:
            offset = phases[phase_index % len(phases)]
            phase_index += 1
            local = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY) + timedelta(minutes=offset)
            window_start = local.astimezone(ZoneInfo("UTC"))
            window_end = window_start + timedelta(minutes=20)
            if window_end > start and window_start < end:
                candidates.append((max(window_start, start), min(window_end, end)))
        day += timedelta(days=1)
    if not candidates:
        return []
    desired = min(len(candidates), max(1, math.ceil((max_samples or 50_000) / 240)))
    selected = np.linspace(0, len(candidates) - 1, num=desired, dtype=np.int64)
    return [candidates[int(index)] for index in selected]


def _news_timeline(database: Database, settings: Settings, start: datetime, end: datetime) -> _Timeline:
    timeline = _raw_minute_timeline(database, settings, start, end, stride=1)
    if not timeline.timestamps or not _table_columns(database, "news_items"):
        return timeline
    for index in range(len(timeline.eligible)):
        timeline.eligible[index] = False
    timestamp_values = [value.timestamp() for value in timeline.timestamps]
    available = _table_columns(database, "news_items")
    selected = [
        name
        for name in (
            "timestamp",
            "sentiment_score",
            "confidence_score",
            "novelty_score",
            "gold_score",
            "usd_score",
            "rates_score",
            "risk_score",
            "event_risk",
            "related_spread_change_1m",
            "related_spread_change_5m",
            "related_spread_change_15m",
            "outcome_linked",
            "sentiment_trusted",
            "category",
            "event_type",
            "gold_impact",
        )
        if name in available
    ]
    rows = database.conn.execute(
        f"""
        SELECT {', '.join(selected)} FROM news_items
        WHERE julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
        ORDER BY julianday(timestamp), id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    for raw in rows:
        event_time = ensure_utc(raw["timestamp"])
        index = bisect.bisect_left(timestamp_values, event_time.timestamp())
        if index >= len(timeline.timestamps):
            continue
        values = dict(raw)
        timeline.features[index].update(_flatten_features({f"news_{key}": value for key, value in values.items() if key != "timestamp"}))
        timeline.eligible[index] = timeline.labels[index] in ENTRY_CLASSES
        timeline.regimes[index] = f"news:{values.get('event_type') or values.get('category') or 'unknown'}"
    timeline.source = "historical_news_events"
    return timeline


def _exit_timeline(database: Database, settings: Settings, start: datetime, end: datetime) -> _Timeline:
    columns = _table_columns(database, "position_management_events")
    timeline = _empty_timeline(EXIT_CLASSES, "paper_position_management")
    if not columns:
        return timeline
    rows = database.conn.execute(
        """
        SELECT * FROM position_management_events
        WHERE symbol = ? AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
        ORDER BY trade_id, julianday(timestamp), id
        """,
        (settings.bot_symbol, start.isoformat(), end.isoformat()),
    ).fetchall()
    trade_numbers: dict[str, int] = {}
    for raw in rows:
        row = dict(raw)
        timestamp = ensure_utc(row["timestamp"])
        trade_id = str(row.get("trade_id") or row.get("parent_order_id") or "unknown")
        trade_numbers.setdefault(trade_id, len(trade_numbers) + 1)
        action = str(row.get("action") or "").lower()
        if any(token in action for token in ("close", "flatten", "exit")):
            label = "close"
        elif any(token in action for token in ("reduce", "partial", "replace_stop", "trail")):
            label = "reduce"
        else:
            label = "hold"
        feature = _flatten_features({key: value for key, value in row.items() if key not in {"id", "timestamp", "trade_id", "created_at"}})
        feature.update(_flatten_features(_safe_json(row.get("details_json"))))
        feature.update(_clock_features(timestamp))
        timeline.timestamps.append(timestamp)
        timeline.features.append(feature)
        timeline.labels.append(label)
        timeline.returns.append([math.nan] * len(HORIZONS_MINUTES))
        timeline.costs.append(settings.estimated_round_trip_slippage_pct + settings.economic_breakeven_safety_buffer_pct)
        timeline.eligible.append(True)
        timeline.regimes.append(str(row.get("status") or "position_open"))
        timeline.baseline_probabilities.append([math.nan, math.nan, math.nan])
        timeline.baseline_versions.append("")
        timeline.session_keys.append(f"trade:{trade_numbers[trade_id]}")
    return timeline


def _label_raw_timeline(timeline: _Timeline, *, primary_horizon: int, stride: int, settings: Settings) -> None:
    timestamps = [value.timestamp() for value in timeline.timestamps]
    mids = [_finite(item.get("midpoint"), math.nan) for item in timeline.features]
    primary_index = HORIZONS_MINUTES.index(primary_horizon)
    for index, timestamp in enumerate(timestamps):
        returns: list[float] = []
        for minutes in HORIZONS_MINUTES:
            future = bisect.bisect_left(timestamps, timestamp + minutes * 60, lo=index + 1)
            target = timestamp + minutes * 60
            if (
                future >= len(mids)
                or timeline.session_keys[future] != timeline.session_keys[index]
                or timestamps[future] > target + 2.0
            ):
                returns.append(math.nan)
            else:
                returns.append(mids[future] / max(mids[index], 1e-12) - 1.0)
        timeline.returns[index] = returns
        if all(math.isfinite(value) for value in returns):
            timeline.labels[index] = _direction_label(
                returns[primary_index],
                timeline.costs[index],
                settings.outcome_label_min_edge_pct,
            )
            timeline.eligible[index] = index % stride == 0


def _write_artifact(
    timeline: _Timeline,
    *,
    path: Path,
    scope: str,
    sequence_length: int,
    window_seconds: int,
    max_features: int,
    overwrite: bool,
) -> TransformerDatasetResult:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"Transformer artifact already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=False)
    columns = _select_feature_columns(timeline.features, max_features=max_features)
    if not columns:
        raise RuntimeError("Transformer dataset contains no numeric feature columns.")
    matrix = np.full((len(timeline.features), len(columns)), np.nan, dtype=np.float32)
    for row_index, row in enumerate(timeline.features):
        for column_index, column in enumerate(columns):
            if column in row:
                value = _finite(row[column], math.nan)
                matrix[row_index, column_index] = value
    eligible = [
        index
        for index, flag in enumerate(timeline.eligible)
        if flag and timeline.labels[index] in timeline.classes
    ]
    if not eligible:
        raise RuntimeError("Transformer dataset contains no fully labeled samples.")
    class_to_index = {label: index for index, label in enumerate(timeline.classes)}
    regime_names = list(dict.fromkeys(timeline.regimes))
    regime_to_index = {name: index for index, name in enumerate(regime_names)}
    session_names = list(dict.fromkeys(timeline.session_keys))
    session_to_index = {name: index for index, name in enumerate(session_names)}
    arrays = {
        "features": matrix,
        "timestamps": np.asarray([value.timestamp() for value in timeline.timestamps], dtype=np.float64),
        "session_ids": np.asarray([session_to_index[value] for value in timeline.session_keys], dtype=np.int32),
        "sample_end_indices": np.asarray(eligible, dtype=np.int64),
        "labels": np.asarray([class_to_index[str(timeline.labels[index])] for index in eligible], dtype=np.int64),
        "returns": np.asarray([timeline.returns[index] for index in eligible], dtype=np.float32),
        "costs": np.asarray([timeline.costs[index] for index in eligible], dtype=np.float32),
        "regime_ids": np.asarray([regime_to_index[timeline.regimes[index]] for index in eligible], dtype=np.int32),
        "baseline_probabilities": np.asarray([timeline.baseline_probabilities[index] for index in eligible], dtype=np.float32),
    }
    for name, array in arrays.items():
        np.save(path / f"{name}.npy", array, allow_pickle=False)
    counts = Counter(str(timeline.labels[index]) for index in eligible)
    manifest: dict[str, Any] = {
        "format_version": 1,
        "scope": scope,
        "registry_scope": transformer_registry_scope(scope),
        "source": timeline.source,
        "classes": list(timeline.classes),
        "horizons_minutes": list(HORIZONS_MINUTES),
        "feature_columns": columns,
        "feature_count": len(columns),
        "timeline_rows": len(timeline.timestamps),
        "samples": len(eligible),
        "class_counts": dict(counts),
        "sequence_length": sequence_length,
        "window_seconds": window_seconds,
        "start": timeline.timestamps[0].isoformat() if timeline.timestamps else None,
        "end": timeline.timestamps[-1].isoformat() if timeline.timestamps else None,
        "regime_names": regime_names,
        "session_count": len(session_names),
        "baseline_probability_rows": int(np.isfinite(arrays["baseline_probabilities"]).all(axis=1).sum()),
        "baseline_model_versions": sorted({value for value in timeline.baseline_versions if value}),
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    fingerprint = _directory_fingerprint(path)
    manifest["fingerprint"] = fingerprint
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return TransformerDatasetResult(
        path=path,
        scope=scope,
        source=timeline.source,
        timeline_rows=len(timeline.timestamps),
        samples=len(eligible),
        feature_count=len(columns),
        class_counts=dict(counts),
        start=manifest["start"],
        end=manifest["end"],
        fingerprint=fingerprint,
    )


def _select_feature_columns(rows: Iterable[dict[str, float]], *, max_features: int) -> list[str]:
    counts: Counter[str] = Counter()
    row_count = 0
    for row in rows:
        row_count += 1
        counts.update(key for key, value in row.items() if math.isfinite(_finite(value, math.nan)))
    minimum = max(1, int(row_count * 0.01))
    candidates = [key for key, count in counts.items() if count >= minimum]
    candidates.sort(
        key=lambda key: (
            -int(any(term in key.lower() for term in FEATURE_PRIORITY_TERMS)),
            -counts[key],
            key,
        )
    )
    return candidates[:max_features]


def _outcomes_by_decision(database: Database, source: str, start: datetime, end: datetime) -> dict[int, dict[str, Any]]:
    columns = _table_columns(database, "outcome_labels")
    if not {"decision_source", "decision_id"}.issubset(columns):
        return {}
    selected = [
        name
        for name in (
            "decision_id",
            "label_1m",
            "label_3m",
            "label_5m",
            "label_15m",
            "forward_return_1m",
            "forward_return_3m",
            "forward_return_5m",
            "forward_return_15m",
            "realized_spread_cost",
        )
        if name in columns
    ]
    rows = database.conn.execute(
        f"""
        SELECT {', '.join(selected)} FROM outcome_labels
        WHERE decision_source = ? AND decision_id IS NOT NULL
          AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
        ORDER BY id
        """,
        (source, start.isoformat(), end.isoformat()),
    ).fetchall()
    return {int(row["decision_id"]): dict(row) for row in rows}


def _future_returns(
    epoch: list[float],
    timestamps: list[datetime],
    prices: list[float],
    index: int,
    horizons: Iterable[int],
) -> list[float]:
    result: list[float] = []
    session = _session_key(timestamps[index])
    for minutes in horizons:
        future = bisect.bisect_left(epoch, epoch[index] + minutes * 60, lo=index + 1)
        if future >= len(prices) or _session_key(timestamps[future]) != session:
            result.append(math.nan)
        else:
            result.append(prices[future] / max(prices[index], 1e-12) - 1.0)
    return result


def _direction_label(raw_return: float, cost: float, edge: float) -> str | None:
    if not math.isfinite(raw_return):
        return None
    long_net = raw_return - cost
    short_net = -raw_return - cost
    if long_net > max(short_net, edge):
        return "long_good"
    if short_net > max(long_net, edge):
        return "short_good"
    return "no_trade"


def _clock_features(timestamp: datetime) -> dict[str, float]:
    local = ensure_utc(timestamp).astimezone(NY)
    minute = local.hour * 60 + local.minute
    angle = 2.0 * math.pi * minute / (24 * 60)
    phase = (
        "open"
        if (local.hour, local.minute) < (10, 30)
        else "mid_session"
        if local.hour < 14
        else "afternoon"
        if (local.hour, local.minute) < (15, 30)
        else "close"
    )
    return {
        "minute_of_day_sin": math.sin(angle),
        "minute_of_day_cos": math.cos(angle),
        f"session_phase__{phase}": 1.0,
        "day_of_week": float(local.weekday()),
    }


def _session_key(timestamp: datetime) -> str:
    return ensure_utc(timestamp).astimezone(NY).date().isoformat()


def _regular_session(timestamp: datetime) -> bool:
    local = ensure_utc(timestamp).astimezone(NY)
    return local.weekday() < 5 and (local.hour, local.minute) >= (9, 30) and (local.hour, local.minute) < (16, 0)


def _bar_regime(feature: dict[str, Any]) -> str:
    volatility = _finite(feature.get("realized_volatility_30"))
    if _finite(feature.get("volatility_burst")) > 0:
        return "high_volatility"
    if volatility < 0.00025:
        return "low_volatility"
    if _finite(feature.get("proper_break")) > 0:
        return "breakout"
    if _finite(feature.get("range_compression")) > 0:
        return "compression"
    return "normal"


def _feature_category(features: dict[str, float], prefix: str) -> str | None:
    marker = f"{prefix}__"
    matches = [key[len(marker) :] for key, value in features.items() if key.startswith(marker) and value > 0.5]
    return matches[0] if matches else None


def _empty_timeline(classes: tuple[str, ...], source: str) -> _Timeline:
    return _Timeline([], [], [], [], [], [], [], [], [], [], classes, source)


def _table_columns(database: Database, table: str) -> set[str]:
    return {str(row["name"]) for row in database.conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_has_rows(database: Database, table: str, start: datetime, end: datetime) -> bool:
    if not _table_columns(database, table):
        return False
    row = database.conn.execute(
        f"SELECT 1 FROM {table} WHERE julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?) LIMIT 1",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return row is not None


def _artifact_path(settings: Settings, scope: str, output: str | Path | None) -> Path:
    if output is not None:
        return Path(output)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return settings.data_root / "ml_training" / "transformer" / f"{scope}_{stamp}.seq"


def _directory_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(path.glob("*.npy")):
        digest.update(file_path.name.encode("utf-8"))
        with file_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("fingerprint", None)
    digest.update(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default
