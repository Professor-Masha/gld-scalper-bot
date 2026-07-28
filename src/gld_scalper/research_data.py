from __future__ import annotations

import csv
import hashlib
import json
import logging
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .alpaca_clients import get_trading_client
from .config import PROJECT_ROOT, Settings, load_settings
from .database import Database
from .outcome_labeler import MultiHorizonOutcomeLabeler
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_iso, utc_now

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResearchDataResult:
    source: str
    data_type: str
    rows: int
    status: str = "completed"
    message: str = ""


class ResearchDataCollector:
    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)

    def collect_all(self, *, start: datetime | None = None, end: datetime | None = None, include_news_content: bool = True) -> list[ResearchDataResult]:
        end = end or utc_now()
        start = start or end - timedelta(days=7)
        tasks = [
            ("alpaca_news", lambda: self.collect_alpaca_news(start=start, end=end, include_content=include_news_content)),
            ("economic_calendar", lambda: self.import_economic_calendar()),
            ("market_calendar", lambda: self.collect_market_calendar(start=start.date(), end=end.date())),
            ("macro_series", lambda: self.collect_fred_macro_series(start=start.date(), end=end.date())),
            ("knowledge_artifacts", lambda: self.index_knowledge_artifacts()),
            ("feature_audit", lambda: self.audit_recent_features()),
            ("outcome_labels", lambda: self.label_signal_outcomes()),
            ("news_price_moves", lambda: self.label_news_price_moves()),
            ("event_price_moves", lambda: self.label_economic_event_price_moves()),
        ]
        results: list[ResearchDataResult] = []
        for data_type, task in tasks:
            try:
                result = task()
                results.append(result)
                self._record_run(result, start=start, end=end)
            except Exception as exc:  # pragma: no cover - external source dependent
                logger.exception("research data collection failed for %s: %s", data_type, exc)
                result = ResearchDataResult("local", data_type, 0, "failed", str(exc))
                results.append(result)
                self._record_run(result, start=start, end=end)
        return results

    def collect_alpaca_news(self, *, start: datetime, end: datetime, include_content: bool = True, limit: int = 50, max_pages: int = 100) -> ResearchDataResult:
        if not self.settings.enable_news_collection:
            return ResearchDataResult("alpaca", "news_items", 0, "skipped", "news collection disabled")
        self.settings.require_credentials()
        try:
            from alpaca.data.historical import NewsClient
            from alpaca.data.requests import NewsRequest
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("alpaca-py NewsClient is unavailable.") from exc

        client = NewsClient(self.settings.alpaca_api_key, self.settings.alpaca_secret_key)
        inserted = 0
        page_token = None
        for _ in range(max_pages):
            request = NewsRequest(
                start=start,
                end=end,
                symbols=",".join(self.settings.news_symbols),
                include_content=include_content,
                exclude_contentless=False,
                limit=limit,
                sort="desc",
                page_token=page_token,
            )
            response = client.get_news(request)
            rows = news_response_to_records(response)
            for row in rows:
                self.database.insert_news_item(row)
                inserted += 1
            page_token = getattr(response, "next_page_token", None)
            if not page_token:
                break
        return ResearchDataResult("alpaca", "news_items", inserted)

    def collect_market_calendar(self, *, start: date, end: date) -> ResearchDataResult:
        if not self.settings.enable_market_calendar_collection:
            return ResearchDataResult("alpaca_trading", "market_calendar", 0, "skipped", "market calendar collection disabled")
        try:
            from alpaca.trading.requests import GetCalendarRequest
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("alpaca-py GetCalendarRequest is unavailable.") from exc
        client = get_trading_client(self.settings)
        calendars = client.get_calendar(GetCalendarRequest(start=start, end=end))
        records = []
        for item in calendars:
            open_time = _combine_calendar_time(item, "open")
            close_time = _combine_calendar_time(item, "close")
            session_type = "regular"
            if open_time and close_time and (close_time - open_time).total_seconds() < 6 * 60 * 60:
                session_type = "half_day"
            records.append(
                {
                    "calendar_date": str(getattr(item, "date", start)),
                    "market": "US_EQUITY",
                    "is_open": True,
                    "open_time": open_time,
                    "close_time": close_time,
                    "session_type": session_type,
                    "source": "alpaca_trading_calendar",
                    "raw_json": _model_dump(item),
                }
            )
        inserted = self.database.upsert_market_calendar(records)
        return ResearchDataResult("alpaca_trading", "market_calendar", inserted)

    def import_economic_calendar(self) -> ResearchDataResult:
        if not self.settings.enable_event_calendar:
            return ResearchDataResult("local_csv", "economic_events", 0, "skipped", "event calendar disabled")
        path = _resolve_path(self.settings.economic_calendar_path)
        if not path.exists():
            return ResearchDataResult("local_csv", "economic_events", 0, "skipped", f"calendar file not found: {path}")
        rows: list[dict[str, Any]] = []
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                scheduled = row.get("scheduled_at") or row.get("timestamp") or row.get("date")
                if not scheduled or not row.get("event_name"):
                    continue
                record = {
                    "scheduled_at": _parse_datetime(scheduled),
                    "country": row.get("country", "US"),
                    "event_name": row["event_name"],
                    "category": row.get("category"),
                    "importance": row.get("importance"),
                    "forecast": _float_or_none(row.get("forecast")),
                    "previous_value": _float_or_none(row.get("previous_value")),
                    "actual_value": _float_or_none(row.get("actual_value")),
                    "revision": _float_or_none(row.get("revision")),
                    "expected_impact": row.get("expected_impact"),
                    "avoid_trading": str(row.get("avoid_trading", "")).lower() in {"1", "true", "yes", "y"},
                    "source": "local_economic_calendar_csv",
                    "raw_json": row,
                }
                record["surprise_value"], record["surprise_pct"] = _surprise(record)
                record["event_key"] = row.get("event_key") or f"{utc_iso(record['scheduled_at'])}|{record['country']}|{record['event_name']}"
                rows.append(record)
        inserted = self.database.upsert_economic_events(rows)
        return ResearchDataResult("local_csv", "economic_events", inserted)

    def collect_fred_macro_series(self, *, start: date, end: date) -> ResearchDataResult:
        if not self.settings.enable_macro_series_collection:
            return ResearchDataResult("fred", "macro_series", 0, "skipped", "macro series collection disabled")
        if not self.settings.fred_api_key:
            return ResearchDataResult("fred", "macro_series", 0, "skipped", "FRED_API_KEY is not set")
        all_rows: list[dict[str, Any]] = []
        for series_id in self.settings.macro_series_ids:
            all_rows.extend(self._download_fred_series(series_id, start=start, end=end))
        inserted = self.database.upsert_macro_series(all_rows)
        return ResearchDataResult("fred", "macro_series", inserted)

    def index_knowledge_artifacts(self, knowledge_dir: str | Path = "Knowledge") -> ResearchDataResult:
        root = _resolve_path(knowledge_dir)
        if not root.exists():
            return ResearchDataResult("local_files", "knowledge_artifacts", 0, "skipped", f"knowledge folder not found: {root}")
        rows = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.database.upsert_knowledge_artifact(
                {
                    "path": str(path),
                    "title": path.stem,
                    "artifact_type": path.suffix.lstrip(".").lower() or "file",
                    "sha256": digest,
                    "summary": "",
                    "tags": ["knowledge", "trading", "research"],
                    "indexed_at": utc_now(),
                }
            )
            rows += 1
        return ResearchDataResult("local_files", "knowledge_artifacts", rows)

    def audit_recent_features(self, *, limit: int = 500) -> ResearchDataResult:
        if not self.settings.enable_feature_audit_tables:
            return ResearchDataResult("sqlite", "feature_audit", 0, "skipped", "feature audit tables disabled")
        bars = self.database.fetch_latest_bars(self.settings.bot_symbol, self.settings.trade_timeframe, limit=limit)
        if not bars:
            return ResearchDataResult("sqlite", "feature_audit", 0, "skipped", "no bars available")
        from .feature_engine import build_feature_snapshot
        from .microstructure import build_microstructure_features
        from .price_action import analyze_price_action

        latest = bars[-1]
        now = ensure_utc(latest["timestamp"])
        quote = self.database.get_latest_quote(self.settings.bot_symbol)
        recent_trades = self.database.fetch_recent_trades(self.settings.bot_symbol, limit=250)
        features = build_feature_snapshot(bars_1m=bars, quote=quote, now=now)
        features.update(build_microstructure_features(bars=bars, quote=quote, recent_trades=recent_trades, now=now))
        pattern = analyze_price_action(bars)
        features.update(pattern)
        self.database.upsert_microstructure_features([_microstructure_record(now, self.settings.bot_symbol, features)])
        self.database.upsert_price_action_labels([_price_action_record(now, self.settings.bot_symbol, self.settings.trade_timeframe, features)])
        return ResearchDataResult("sqlite", "feature_audit", 2)

    def label_signal_outcomes(self, *, limit: int = 1000) -> ResearchDataResult:
        result = MultiHorizonOutcomeLabeler(self.settings, self.database).label_matured(limit=limit)
        return ResearchDataResult(
            "sqlite",
            "outcome_labels",
            result.labeled,
            message=json.dumps(result.to_dict(), sort_keys=True),
        )

    def label_news_price_moves(self) -> ResearchDataResult:
        rows = self.database.conn.execute(
            """
            SELECT *
            FROM news_items
            WHERE related_move_15m IS NULL OR outcome_linked = 0
            ORDER BY timestamp ASC
            LIMIT 500
            """
        ).fetchall()
        count = 0
        for row in rows:
            moves = self._forward_moves(row["timestamp"])
            if not moves:
                continue
            spread_changes = self._forward_spread_changes(row["timestamp"])
            fully_linked = moves.get(15) is not None and any(
                spread_changes.get(minutes) is not None for minutes in (1, 5, 15)
            )
            with self.database.conn:
                self.database.conn.execute(
                    """
                    UPDATE news_items
                    SET related_move_1m = ?, related_move_5m = ?, related_move_15m = ?,
                        related_move_1h = ?, related_move_1d = ?,
                        related_spread_change_1m = ?, related_spread_change_5m = ?,
                        related_spread_change_15m = ?, outcome_linked = ?
                    WHERE id = ?
                    """,
                    (
                        moves.get(1),
                        moves.get(5),
                        moves.get(15),
                        moves.get(60),
                        moves.get(390),
                        spread_changes.get(1),
                        spread_changes.get(5),
                        spread_changes.get(15),
                        1 if fully_linked else 0,
                        row["id"],
                    ),
                )
            count += 1
        return ResearchDataResult("sqlite", "news_price_moves", count)

    def _forward_spread_changes(self, timestamp: datetime | str) -> dict[int, float | None]:
        event_time = ensure_utc(timestamp)
        baseline = self._spread_near(event_time)
        if baseline is None:
            return {}
        return {
            minutes: (
                None
                if (spread := self._spread_near(event_time + timedelta(minutes=minutes))) is None
                else spread - baseline
            )
            for minutes in (1, 5, 15)
        }

    def _spread_near(self, timestamp: datetime) -> float | None:
        row = self.database.conn.execute(
            """
            SELECT bid_price, ask_price FROM quotes
            WHERE symbol = ?
              AND julianday(timestamp) >= julianday(?, '-30 seconds')
              AND julianday(timestamp) <= julianday(?, '+90 seconds')
            ORDER BY ABS(julianday(timestamp) - julianday(?)), id
            LIMIT 1
            """,
            (self.settings.bot_symbol, timestamp.isoformat(), timestamp.isoformat(), timestamp.isoformat()),
        ).fetchone()
        if row is None:
            return None
        bid = float(row["bid_price"] or 0.0)
        ask = float(row["ask_price"] or 0.0)
        midpoint = (bid + ask) / 2
        return (ask - bid) / midpoint if bid > 0 and ask >= bid and midpoint > 0 else None

    def label_economic_event_price_moves(self) -> ResearchDataResult:
        rows = self.database.conn.execute(
            """
            SELECT *
            FROM economic_events
            WHERE realized_gld_move_15m IS NULL
            ORDER BY scheduled_at ASC
            LIMIT 500
            """
        ).fetchall()
        count = 0
        for row in rows:
            moves = self._forward_moves(row["scheduled_at"])
            if not moves:
                continue
            with self.database.conn:
                self.database.conn.execute(
                    """
                    UPDATE economic_events
                    SET realized_gld_move_1m = ?, realized_gld_move_5m = ?, realized_gld_move_15m = ?,
                        realized_gld_move_1h = ?, realized_gld_move_1d = ?
                    WHERE id = ?
                    """,
                    (moves.get(1), moves.get(5), moves.get(15), moves.get(60), moves.get(390), row["id"]),
                )
            count += 1
        return ResearchDataResult("sqlite", "event_price_moves", count)

    def _download_fred_series(self, series_id: str, *, start: date, end: date) -> list[dict[str, Any]]:
        params = {
            "series_id": series_id,
            "api_key": self.settings.fred_api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        }
        url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - configured official data URL
            payload = json.loads(response.read().decode("utf-8"))
        rows = []
        for item in payload.get("observations", []):
            value = _float_or_none(item.get("value"))
            rows.append(
                {
                    "series_id": series_id,
                    "observation_date": item.get("date"),
                    "value": value,
                    "realtime_start": item.get("realtime_start"),
                    "realtime_end": item.get("realtime_end"),
                    "source": "fred",
                    "raw_json": item,
                }
            )
        return rows

    def _forward_moves(self, timestamp: str | datetime) -> dict[int, float]:
        start = ensure_utc(timestamp)
        bars = self.database.fetch_bars(self.settings.bot_symbol, self.settings.trade_timeframe, start, start + timedelta(days=2))
        entry = _first_bar_at_or_after(bars, start)
        if entry is None:
            return {}
        entry_price = float(entry["close"])
        moves: dict[int, float] = {}
        for minutes in (1, 5, 15, 60, 390):
            future = _first_bar_at_or_after(bars, start + timedelta(minutes=minutes))
            if future:
                moves[minutes] = safe_div(float(future["close"]) - entry_price, entry_price)
        return moves

    def _record_run(self, result: ResearchDataResult, *, start: datetime, end: datetime) -> None:
        self.database.insert_data_source_run(
            {
                "source": result.source,
                "data_type": result.data_type,
                "start_time": start,
                "end_time": end,
                "status": result.status,
                "rows_inserted": result.rows,
                "message": result.message,
                "details": {},
            }
        )


class ResearchDataScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_news_at: datetime | None = None
        self.last_macro_series_at: datetime | None = None
        self.last_calendar_at: datetime | None = None
        self.last_feature_audit_at: datetime | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending_results: list[ResearchDataResult] = []
        self._last_error: str | None = None

    def maybe_collect(
        self,
        database: Database,
        now: datetime | None = None,
        *,
        include_heavy: bool = True,
    ) -> list[ResearchDataResult]:
        now = now or utc_now()
        collector = ResearchDataCollector(self.settings, database)
        results: list[ResearchDataResult] = []
        if self._due(self.last_news_at, self.settings.news_collection_interval_minutes) and self.settings.enable_news_collection:
            results.append(collector.collect_alpaca_news(start=now - timedelta(hours=8), end=now, include_content=True))
            self.last_news_at = now
        if self._due(self.last_macro_series_at, self.settings.macro_series_interval_hours * 60) and self.settings.enable_macro_series_collection:
            results.append(collector.collect_fred_macro_series(start=(now - timedelta(days=14)).date(), end=now.date()))
            self.last_macro_series_at = now
        if self._due(self.last_calendar_at, 24 * 60) and self.settings.enable_market_calendar_collection:
            results.append(collector.collect_market_calendar(start=(now - timedelta(days=5)).date(), end=(now + timedelta(days=10)).date()))
            self.last_calendar_at = now
        if (
            include_heavy
            and self._due(self.last_feature_audit_at, 5)
            and self.settings.enable_feature_audit_tables
        ):
            results.append(collector.audit_recent_features())
            results.append(collector.label_signal_outcomes(limit=self.settings.outcome_label_batch_size))
            results.append(collector.label_news_price_moves())
            results.append(collector.label_economic_event_price_moves())
            self.last_feature_audit_at = now
        for result in results:
            collector._record_run(result, start=now, end=now)
        return results

    def poll(
        self,
        now: datetime | None = None,
        *,
        include_heavy: bool = True,
    ) -> list[ResearchDataResult]:
        """Return completed work and start the next due collection without blocking decisions."""
        now = now or utc_now()
        with self._lock:
            completed = list(self._pending_results)
            self._pending_results.clear()
            if self._thread is not None and self._thread.is_alive():
                return completed
            if not self._has_due_work(now, include_heavy=include_heavy):
                return completed
            self._thread = threading.Thread(
                target=self._run_background,
                args=(now, include_heavy),
                name="research-data-collector",
                daemon=True,
            )
            self._thread.start()
        return completed

    def _has_due_work(self, now: datetime, *, include_heavy: bool) -> bool:
        return any(
            (
                self.settings.enable_news_collection
                and self._due_at(self.last_news_at, self.settings.news_collection_interval_minutes, now),
                self.settings.enable_macro_series_collection
                and self._due_at(
                    self.last_macro_series_at,
                    self.settings.macro_series_interval_hours * 60,
                    now,
                ),
                self.settings.enable_market_calendar_collection
                and self._due_at(self.last_calendar_at, 24 * 60, now),
                include_heavy
                and self.settings.enable_feature_audit_tables
                and self._due_at(self.last_feature_audit_at, 5, now),
            )
        )

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "in_progress": self._thread is not None and self._thread.is_alive(),
                "pending_results": len(self._pending_results),
                "last_error": self._last_error,
            }

    def _run_background(self, now: datetime, include_heavy: bool) -> None:
        database = Database(settings=self.settings)
        try:
            database.init_db()
            results = self.maybe_collect(database, now, include_heavy=include_heavy)
            with self._lock:
                self._pending_results.extend(results)
                self._last_error = None
        except Exception as exc:  # pragma: no cover - external services and large local data
            logger.exception("background research data collection failed: %s", exc)
            with self._lock:
                self._last_error = str(exc)
                self._pending_results.append(
                    ResearchDataResult("runtime", "scheduled_research", 0, "failed", str(exc))
                )
        finally:
            database.close()

    def _due(self, last: datetime | None, minutes: int) -> bool:
        return last is None or utc_now() >= last + timedelta(minutes=minutes)

    @staticmethod
    def _due_at(last: datetime | None, minutes: int, now: datetime) -> bool:
        return last is None or now >= last + timedelta(minutes=minutes)


def news_response_to_records(response: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if hasattr(response, "df"):
        df = response.df
        if getattr(df, "empty", True):
            return []
        for row in df.reset_index().to_dict(orient="records"):
            rows.append(_news_row_from_mapping(row))
        return rows
    data = getattr(response, "data", response)
    if isinstance(data, dict):
        iterable = data.values()
    else:
        iterable = data if isinstance(data, list) else []
    for item in iterable:
        rows.append(_news_row_from_object(item))
    return rows


def _news_row_from_mapping(row: dict[str, Any]) -> dict[str, Any]:
    headline = row.get("headline") or row.get("title") or ""
    summary = row.get("summary") or ""
    content = row.get("content") or row.get("content_text")
    source = row.get("source")
    timestamp = row.get("created_at") or row.get("updated_at") or row.get("timestamp") or utc_now()
    symbols = row.get("symbols") or []
    scores = _score_news_text(f"{headline} {summary} {content or ''}")
    return {
        "timestamp": timestamp,
        "source": source,
        "headline": str(headline),
        "url": row.get("url"),
        "author": row.get("author"),
        "summary": summary,
        "content_text": content,
        "symbols": symbols,
        "category": _news_category(headline, summary),
        "event_type": _news_event_type(headline, summary),
        **scores,
        "raw_json": row,
    }


def _news_row_from_object(item: Any) -> dict[str, Any]:
    raw = _model_dump(item)
    return _news_row_from_mapping(raw)


def _score_news_text(text: str) -> dict[str, Any]:
    lower = text.lower()
    risk_terms = ["war", "missile", "conflict", "bank crisis", "default", "sanction", "geopolitical", "safe haven"]
    fed_hawkish = ["rate hike", "higher for longer", "hawkish", "inflation hot", "yields rise"]
    fed_dovish = ["rate cut", "dovish", "inflation cool", "yields fall", "recession"]
    usd_bullish = ["dollar strengthens", "dollar rises", "usd rises", "dxy rises"]
    usd_bearish = ["dollar weakens", "dollar falls", "usd falls", "dxy falls"]
    risk = min(1.0, sum(term in lower for term in risk_terms) / 3)
    rates = sum(term in lower for term in fed_dovish) - sum(term in lower for term in fed_hawkish)
    usd = sum(term in lower for term in usd_bearish) - sum(term in lower for term in usd_bullish)
    gold = max(-1.0, min(1.0, 0.35 * risk + 0.25 * rates + 0.25 * usd))
    return {
        "sentiment_score": gold,
        "confidence_score": 0.35 if text.strip() else 0.0,
        "novelty_score": 0.0,
        "gold_impact": "bullish" if gold > 0.15 else "bearish" if gold < -0.15 else "neutral",
        "gold_score": gold,
        "usd_score": usd,
        "rates_score": rates,
        "risk_score": risk,
        "event_risk": risk,
    }


def _news_category(headline: Any, summary: Any) -> str:
    text = f"{headline} {summary}".lower()
    if any(term in text for term in ["fed", "fomc", "powell", "rates"]):
        return "fed_rates"
    if any(term in text for term in ["cpi", "pce", "inflation"]):
        return "inflation"
    if any(term in text for term in ["jobs", "payroll", "unemployment", "nfp"]):
        return "labor"
    if any(term in text for term in ["war", "conflict", "geopolitical"]):
        return "geopolitical"
    if any(term in text for term in ["dollar", "usd", "dxy"]):
        return "usd"
    return "general"


def _news_event_type(headline: Any, summary: Any) -> str:
    category = _news_category(headline, summary)
    mapping = {
        "fed_rates": "fed_rate_sentiment",
        "inflation": "inflation_release_or_commentary",
        "labor": "labor_market_release_or_commentary",
        "geopolitical": "headline_event_risk",
        "usd": "usd_sentiment",
    }
    return mapping.get(category, "market_news")


def _price_action_record(timestamp: datetime, symbol: str, timeframe: str, features: dict[str, Any]) -> dict[str, Any]:
    pattern = str(features.get("pattern_classification") or "")
    return {
        "timestamp": timestamp,
        "symbol": symbol,
        "timeframe": timeframe,
        "pattern_classification": pattern,
        "pattern_quality": features.get("pattern_quality"),
        "buildup_detected": features.get("buildup_detected"),
        "buildup_side": features.get("buildup_side"),
        "proper_break": features.get("proper_break") or pattern.startswith("proper_break"),
        "false_break": pattern.startswith("false_break"),
        "tease_break": pattern.startswith("tease_break"),
        "pullback": pattern.startswith("pullback"),
        "support_level": features.get("support_level"),
        "resistance_level": features.get("resistance_level"),
        "range_compression": features.get("range_compression"),
        "compression_duration": features.get("compression_duration"),
        "breakout_volume_confirmation": features.get("breakout_volume_confirmation"),
        "vwap_rejection": features.get("vwap_rejection"),
        "trend_continuation": features.get("trend_continuation"),
        "trend_exhaustion": features.get("trend_exhaustion"),
        "candle_body_strength": features.get("body_to_range_ratio"),
        "wick_rejection": features.get("wick_rejection"),
        "features": features,
    }


def _microstructure_record(timestamp: datetime, symbol: str, features: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "symbol": symbol,
        "bid_price": features.get("bid_price"),
        "ask_price": features.get("ask_price"),
        "bid_size": features.get("bid_size"),
        "ask_size": features.get("ask_size"),
        "midpoint": features.get("midpoint"),
        "spread": features.get("spread"),
        "spread_pct": features.get("spread_pct"),
        "spread_regime": features.get("spread_regime"),
        "quote_imbalance": features.get("quote_imbalance"),
        "quote_age_seconds": features.get("quote_age_seconds"),
        "trade_intensity": features.get("trade_intensity"),
        "signed_volume": features.get("signed_volume"),
        "aggressive_buy_volume": features.get("aggressive_buy_volume"),
        "aggressive_sell_volume": features.get("aggressive_sell_volume"),
        "liquidity_score": features.get("liquidity_score"),
        "volatility_burst": features.get("volatility_burst"),
        "stale_data": features.get("stream_stale") or features.get("data_age_seconds", 0) > 120,
        "source": "derived",
        "features": features,
    }


def _first_bar_at_or_after(bars: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    for row in bars:
        if ensure_utc(row["timestamp"]) >= timestamp:
            return row
    return None


def _combine_calendar_time(item: Any, field: str) -> datetime | None:
    item_date = getattr(item, "date", None)
    value = getattr(item, field, None)
    if item_date is None or value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    raw = f"{item_date}T{value}:00-05:00" if len(str(value)) <= 5 else f"{item_date}T{value}-05:00"
    return ensure_utc(datetime.fromisoformat(raw))


def _parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if "T" not in raw and " " in raw:
        raw = raw.replace(" ", "T", 1)
    if "T" not in raw:
        raw = f"{raw}T00:00:00+00:00"
    if "+" not in raw and raw[-6:-5] not in {"+", "-"}:
        raw = f"{raw}+00:00"
    return ensure_utc(datetime.fromisoformat(raw))


def _surprise(record: dict[str, Any]) -> tuple[float | None, float | None]:
    actual = record.get("actual_value")
    forecast = record.get("forecast")
    if actual is None or forecast is None:
        return None, None
    surprise = actual - forecast
    return surprise, safe_div(surprise, abs(forecast))


def _float_or_none(value: Any) -> float | None:
    if value in {None, "", "."}:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _model_dump(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    return {"repr": repr(item)}


def _resolve_path(path: str | Path) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else PROJECT_ROOT / resolved
