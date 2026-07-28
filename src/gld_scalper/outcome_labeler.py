from __future__ import annotations

import bisect
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import Settings, load_settings
from .database import Database
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_iso, utc_now


HORIZONS = (1, 3, 5, 15)
VALID_SOURCES = {"signal", "fast_scalp", "ema_cross"}


@dataclass(slots=True)
class OutcomeLabelingResult:
    scanned: int = 0
    labeled: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_entry_price: int = 0
    skipped_no_future_price: int = 0
    unavailable: int = 0
    complete: int = 0
    partial: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_price_source: dict[str, int] = field(default_factory=dict)
    horizon_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    price: float
    source: str
    spread_pct: float | None = None


class MultiHorizonOutcomeLabeler:
    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)

    def label_matured(
        self,
        *,
        now: datetime | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        sources: tuple[str, ...] = ("signal", "fast_scalp", "ema_cross"),
        retry_partial: bool = False,
    ) -> OutcomeLabelingResult:
        result = OutcomeLabelingResult()
        if not self.settings.enable_multi_horizon_outcome_labels:
            return result
        unknown = set(sources) - VALID_SOURCES
        if unknown:
            raise ValueError(f"Unsupported decision sources: {sorted(unknown)}")
        now = ensure_utc(now or utc_now())
        maturity_cutoff = now - timedelta(minutes=max(HORIZONS))
        requested_end = ensure_utc(end) if end is not None else maturity_cutoff
        cutoff = min(requested_end, maturity_cutoff)
        requested_start = ensure_utc(start) if start is not None else None
        batch_limit = int(limit or self.settings.outcome_label_batch_size)
        decisions = self._fetch_decisions(requested_start, cutoff, batch_limit, sources, retry_partial)
        result.scanned = len(decisions)
        if not decisions:
            return result

        resolver = _PriceResolver(
            self.database,
            self.settings,
            min(ensure_utc(item["timestamp"]) for item in decisions),
            max(ensure_utc(item["timestamp"]) for item in decisions) + timedelta(minutes=max(HORIZONS)),
        )
        source_counts: Counter[str] = Counter()
        price_source_counts: Counter[str] = Counter()
        horizon_counts: Counter[str] = Counter()
        for decision in decisions:
            record = self._build_label(decision, resolver)
            if record is None:
                if resolver.point(ensure_utc(decision["timestamp"])) is None:
                    result.skipped_no_entry_price += 1
                    reason = "unavailable_no_entry"
                else:
                    result.skipped_no_future_price += 1
                    reason = "unavailable_no_future"
                _, inserted = self._persist_unavailable(decision, reason)
                result.inserted += int(inserted)
                result.updated += int(not inserted)
                result.unavailable += 1
                continue
            _, inserted = self.database.upsert_outcome_label(record)
            result.inserted += int(inserted)
            result.updated += int(not inserted)
            result.labeled += 1
            available_horizons = sum(record.get(f"forward_return_{minutes}m") is not None for minutes in HORIZONS)
            result.complete += int(available_horizons == len(HORIZONS))
            result.partial += int(available_horizons < len(HORIZONS))
            source_counts[str(record["decision_source"])] += 1
            price_source_counts[str(record["price_source"])] += 1
            for minutes in HORIZONS:
                if record.get(f"forward_return_{minutes}m") is not None:
                    horizon_counts[f"{minutes}m"] += 1
        result.by_source = dict(source_counts)
        result.by_price_source = dict(price_source_counts)
        result.horizon_counts = dict(horizon_counts)
        return result

    def _persist_unavailable(self, decision: dict[str, Any], reason: str) -> tuple[int, bool]:
        decision_source = str(decision["decision_source"])
        decision_id = int(decision["decision_id"])
        existing = self.database.conn.execute(
            """
            SELECT id, price_source
            FROM outcome_labels
            WHERE decision_source = ? AND decision_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (decision_source, decision_id),
        ).fetchone()
        if existing is not None:
            if existing["price_source"] is None:
                with self.database.conn:
                    self.database.conn.execute(
                        "UPDATE outcome_labels SET price_source = ? WHERE id = ?",
                        (reason, int(existing["id"])),
                    )
            return int(existing["id"]), False
        row_id = self.database.insert_outcome_label(
            {
                "signal_id": decision_id if decision_source == "signal" else None,
                "decision_source": decision_source,
                "decision_id": decision_id,
                "timestamp": decision["timestamp"],
                "symbol": decision.get("symbol") or self.settings.bot_symbol,
                "decision": decision.get("decision"),
                "label": None,
                "price_source": reason,
                "raw_json": {"label_status": "unavailable", "reason": reason},
            }
        )
        return row_id, True

    def _fetch_decisions(
        self,
        start: datetime | None,
        cutoff: datetime,
        limit: int,
        sources: tuple[str, ...],
        retry_partial: bool,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if "signal" in sources:
            rows.extend(self._fetch_source("signals", "signal", start, cutoff, limit, retry_partial))
        if "fast_scalp" in sources:
            rows.extend(self._fetch_source("fast_scalp_decisions", "fast_scalp", start, cutoff, limit, retry_partial))
        if "ema_cross" in sources:
            rows.extend(self._fetch_source("ema_cross_signals", "ema_cross", start, cutoff, limit, retry_partial))
        rows.sort(key=lambda item: (ensure_utc(item["timestamp"]), item["decision_source"], int(item["decision_id"])))
        return rows[:limit]

    def _fetch_source(
        self,
        table: str,
        decision_source: str,
        start: datetime | None,
        cutoff: datetime,
        limit: int,
        retry_partial: bool,
    ) -> list[dict[str, Any]]:
        clauses = ["d.timestamp <= ?"]
        params: list[Any] = [utc_iso(cutoff)]
        if start is not None:
            clauses.append("d.timestamp >= ?")
            params.append(utc_iso(start))
        completion_clause = (
            """
              AND o.forward_return_1m IS NOT NULL
              AND o.forward_return_3m IS NOT NULL
              AND o.forward_return_5m IS NOT NULL
              AND o.forward_return_15m IS NOT NULL
            """
            if retry_partial
            else "AND o.price_source IS NOT NULL"
        )
        clauses.append(
            f"""
            NOT EXISTS (
                SELECT 1 FROM outcome_labels o
                WHERE o.decision_source = ? AND o.decision_id = d.id
                  {completion_clause}
            )
            """
        )
        params.append(decision_source)
        params.append(limit)
        spread_column = "d.spread_pct" if table == "fast_scalp_decisions" else "NULL"
        query = f"""
            SELECT d.id AS decision_id, d.timestamp, d.symbol,
                   COALESCE(de.executed_action, d.decision) AS decision,
                   de.execution_status, de.expected_slippage_pct, de.direction_available,
                   d.feature_snapshot_json, {spread_column} AS decision_spread_pct,
                   ? AS decision_source
            FROM {table} d
            LEFT JOIN decision_executions de
              ON de.decision_source = ? AND de.decision_id = d.id
            WHERE {' AND '.join(clauses)}
            ORDER BY d.timestamp ASC, d.id ASC
            LIMIT ?
        """
        query_params = [decision_source, decision_source, *params]
        return [dict(row) for row in self.database.conn.execute(query, query_params).fetchall()]

    def _build_label(self, decision: dict[str, Any], resolver: "_PriceResolver") -> dict[str, Any] | None:
        timestamp = ensure_utc(decision["timestamp"])
        entry = resolver.point(timestamp)
        if entry is None or entry.price <= 0:
            return None
        try:
            features = json.loads(decision.get("feature_snapshot_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            features = {}
        spread_cost = max(
            _float_or_zero(entry.spread_pct),
            _float_or_zero(decision.get("decision_spread_pct")),
            _float_or_zero(features.get("spread_pct")),
            0.0001,
        )
        slippage_cost = max(
            2 * self.settings.outcome_label_slippage_pct,
            _float_or_zero(decision.get("expected_slippage_pct")),
            self.settings.estimated_round_trip_slippage_pct,
        )
        fee_cost = safe_div(self.settings.estimated_fee_per_share * 2.0, entry.price)
        total_cost = spread_cost + slippage_cost + fee_cost
        returns: dict[int, float | None] = {}
        labels: dict[int, str | None] = {}
        directional: dict[int, dict[str, float | str]] = {}
        price_sources = {entry.source}
        for minutes in HORIZONS:
            future = resolver.point(timestamp + timedelta(minutes=minutes))
            if future is None:
                returns[minutes] = None
                labels[minutes] = None
                continue
            price_sources.add(future.source)
            raw_return = safe_div(future.price - entry.price, entry.price)
            net_long = raw_return - total_cost
            net_short = -raw_return - total_cost
            label = _label_direction(net_long, net_short, self.settings.outcome_label_min_edge_pct)
            returns[minutes] = raw_return
            labels[minutes] = label
            directional[minutes] = {
                "label": label,
                "raw_forward_return": raw_return,
                "net_return_long": net_long,
                "net_return_short": net_short,
            }
        if not any(value is not None for value in returns.values()):
            return None
        canonical_horizon = max(minutes for minutes, value in returns.items() if value is not None)
        available_horizons = [minutes for minutes, value in returns.items() if value is not None]
        canonical_label = labels[canonical_horizon] or "no_trade"
        canonical = directional[canonical_horizon]
        direction = str(decision.get("decision") or "NO_TRADE").upper()
        if direction == "LONG":
            net_outcome = float(canonical["net_return_long"])
        elif direction == "SHORT":
            net_outcome = float(canonical["net_return_short"])
        else:
            net_outcome = max(float(canonical["net_return_long"]), float(canonical["net_return_short"]))
        excursion = resolver.excursion(timestamp, timestamp + timedelta(minutes=canonical_horizon), entry.price)
        max_up = excursion[0]
        max_down = excursion[1]
        if direction == "SHORT":
            favorable, adverse = max_down, max_up
        else:
            favorable, adverse = max_up, max_down
        decision_source = str(decision["decision_source"])
        decision_id = int(decision["decision_id"])
        return {
            "signal_id": decision_id if decision_source == "signal" else None,
            "decision_source": decision_source,
            "decision_id": decision_id,
            "timestamp": timestamp,
            "symbol": str(decision.get("symbol") or self.settings.bot_symbol).upper(),
            "decision": direction,
            "label": canonical_label,
            "label_1m": labels.get(1),
            "label_3m": labels.get(3),
            "label_5m": labels.get(5),
            "label_15m": labels.get(15),
            "entry_price": entry.price,
            "price_source": "mixed" if len(price_sources) > 1 else next(iter(price_sources)),
            "forward_return_1m": returns.get(1),
            "forward_return_3m": returns.get(3),
            "forward_return_5m": returns.get(5),
            "forward_return_15m": returns.get(15),
            "max_favorable_excursion": favorable,
            "max_adverse_excursion": adverse,
            "stop_would_hit": adverse >= self.settings.stop_loss_pct_floor,
            "target_would_hit": favorable >= self.settings.take_profit_pct_min,
            "false_break": bool(features.get("false_break")),
            "proper_break": bool(features.get("proper_break")),
            "missed_opportunity": direction == "NO_TRADE" and canonical_label in {"long_good", "short_good"},
            "realized_spread_cost": total_cost,
            "net_outcome_after_costs": net_outcome,
            "raw_json": {
                "decision_source": decision_source,
                "decision_id": decision_id,
                "horizon_outcomes": {str(key): value for key, value in directional.items()},
                "spread_pct": spread_cost,
                "slippage_cost_pct": slippage_cost,
                "fee_cost_pct": fee_cost,
                "total_cost_pct": total_cost,
                "execution_status": decision.get("execution_status"),
                "direction_available": bool(decision.get("direction_available", True)),
                "minimum_edge_pct": self.settings.outcome_label_min_edge_pct,
                "price_sources": sorted(price_sources),
                "label_status": "complete" if len(available_horizons) == len(HORIZONS) else "partial",
                "available_horizons": available_horizons,
            },
        }


class _PriceResolver:
    def __init__(self, database: Database, settings: Settings, start: datetime, end: datetime) -> None:
        self.settings = settings
        padding = timedelta(minutes=settings.outcome_label_max_bar_gap_minutes + 1)
        start_iso = utc_iso(start - padding)
        end_iso = utc_iso(end + timedelta(seconds=settings.outcome_label_snapshot_tolerance_seconds))
        snapshots = database.conn.execute(
            """
            SELECT timestamp, midpoint, last_trade_price, spread_pct
            FROM price_snapshots
            WHERE symbol = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """,
            (settings.bot_symbol.upper(), start_iso, end_iso),
        ).fetchall()
        bars = database.conn.execute(
            """
            SELECT timestamp, close, high, low
            FROM bars
            WHERE symbol = ? AND timeframe = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """,
            (settings.bot_symbol.upper(), settings.trade_timeframe, start_iso, end_iso),
        ).fetchall()
        self.snapshot_points = [
            PricePoint(
                timestamp=ensure_utc(row["timestamp"]),
                price=float(row["midpoint"] or row["last_trade_price"] or 0.0),
                source="price_snapshot_1s",
                spread_pct=float(row["spread_pct"]) if row["spread_pct"] is not None else None,
            )
            for row in snapshots
            if float(row["midpoint"] or row["last_trade_price"] or 0.0) > 0
        ]
        self.snapshot_epochs = [item.timestamp.timestamp() for item in self.snapshot_points]
        self.bar_points = [
            PricePoint(
                timestamp=ensure_utc(row["timestamp"]) + timedelta(minutes=1),
                price=float(row["close"]),
                source="bar_1m_close",
            )
            for row in bars
            if float(row["close"] or 0.0) > 0
        ]
        self.bar_epochs = [item.timestamp.timestamp() for item in self.bar_points]
        self.bar_ranges = [
            (
                ensure_utc(row["timestamp"]),
                float(row["high"]),
                float(row["low"]),
            )
            for row in bars
        ]

    def point(self, timestamp: datetime) -> PricePoint | None:
        target = ensure_utc(timestamp).timestamp()
        snapshot_index = bisect.bisect_left(self.snapshot_epochs, target)
        if snapshot_index < len(self.snapshot_points):
            point = self.snapshot_points[snapshot_index]
            if point.timestamp.timestamp() - target <= self.settings.outcome_label_snapshot_tolerance_seconds:
                return point
        bar_index = bisect.bisect_right(self.bar_epochs, target) - 1
        if bar_index >= 0:
            point = self.bar_points[bar_index]
            gap = target - point.timestamp.timestamp()
            if gap <= self.settings.outcome_label_max_bar_gap_minutes * 60:
                return point
        return None

    def excursion(self, start: datetime, end: datetime, entry_price: float) -> tuple[float, float]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        snapshot_prices = [
            item.price
            for item in self.snapshot_points
            if start <= item.timestamp <= end
        ]
        if snapshot_prices:
            return (
                safe_div(max(snapshot_prices) - entry_price, entry_price),
                safe_div(entry_price - min(snapshot_prices), entry_price),
            )
        highs = [high for timestamp, high, _ in self.bar_ranges if start <= timestamp + timedelta(minutes=1) <= end]
        lows = [low for timestamp, _, low in self.bar_ranges if start <= timestamp + timedelta(minutes=1) <= end]
        if not highs or not lows:
            return 0.0, 0.0
        return safe_div(max(highs) - entry_price, entry_price), safe_div(entry_price - min(lows), entry_price)


def _label_direction(net_long: float, net_short: float, minimum_edge: float) -> str:
    if net_long >= minimum_edge and net_long > net_short:
        return "long_good"
    if net_short >= minimum_edge and net_short > net_long:
        return "short_good"
    return "no_trade"


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
