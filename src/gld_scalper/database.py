from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings, load_settings
from .utils.time_utils import ensure_utc, utc_iso, utc_now


_SQLITE_WRITE_LOCK = threading.RLock()


class SerializedSQLiteConnection(sqlite3.Connection):
    """Serialize in-process SQLite write transactions across runtime threads."""

    def __enter__(self):
        _SQLITE_WRITE_LOCK.acquire()
        try:
            return super().__enter__()
        except Exception:
            _SQLITE_WRITE_LOCK.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            _SQLITE_WRITE_LOCK.release()


@contextmanager
def sqlite_write_lock():
    with _SQLITE_WRITE_LOCK:
        yield

DATA_TABLES = [
    "bars",
    "bar_variants",
    "quotes",
    "market_trades",
    "account_snapshots",
    "orders",
    "decision_executions",
    "fills",
    "positions",
    "trade_outcomes",
    "trade_reviews",
    "trading_journal",
    "missed_opportunities",
    "macro_context",
    "news_items",
    "economic_events",
    "macro_series",
    "market_calendar",
    "microstructure_features",
    "data_gaps",
    "stream_diagnostics",
    "price_action_labels",
    "order_block_zones",
    "option_snapshots",
    "options_intelligence",
    "outcome_labels",
    "playbook_evaluations",
    "model_promotion_audits",
    "knowledge_artifacts",
    "data_source_runs",
    "llm_reviews",
    "llm_training_advice",
    "llm_signal_labels",
    "rl_experiments",
    "ema_cross_signals",
    "signals",
    "no_trade_logs",
    "model_predictions",
    "transformer_predictions",
    "model_drift_reports",
    "ml_training_experiments",
    "fast_scalp_decisions",
    "price_snapshots",
    "position_management_state",
    "position_management_events",
    "execution_episodes",
    "execution_episode_orders",
    "order_intents",
    "execution_safety_events",
    "performance_consistency_audits",
    "model_versions",
    "model_champion_history",
    "llm_offline_cycles",
    "system_logs",
]


def database_path_from_url(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// database URLs are supported.")
    raw = database_url.replace("sqlite:///", "", 1)
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return list(value.to_dict(orient="records"))
        except TypeError:
            pass
    return [dict(item) for item in value]


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


class Database:
    def __init__(
        self,
        database_url: str | None = None,
        settings: Settings | None = None,
        *,
        read_only: bool = False,
    ) -> None:
        self.settings = settings or load_settings()
        self.path = database_path_from_url(database_url or self.settings.database_url)
        self.read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            timeout_seconds = max(self.settings.database_busy_timeout_ms / 1_000, 1.0)
            if self.read_only:
                self._conn = sqlite3.connect(
                    f"file:{self.path.as_posix()}?mode=ro",
                    uri=True,
                    timeout=timeout_seconds,
                )
            else:
                self._conn = sqlite3.connect(
                    self.path,
                    timeout=timeout_seconds,
                    factory=SerializedSQLiteConnection,
                )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(f"PRAGMA busy_timeout = {self.settings.database_busy_timeout_ms}")
            if not self.read_only:
                with sqlite_write_lock():
                    self._conn.execute("PRAGMA journal_mode = WAL")
                    self._conn.execute("PRAGMA synchronous = NORMAL")
                    self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_db(self) -> None:
        if self.read_only:
            raise RuntimeError("Cannot initialize a read-only database connection.")
        schema_path = Path(__file__).with_name("schema.sql")
        with sqlite_write_lock():
            self.conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._run_migrations()
            self.conn.commit()

    def _run_migrations(self) -> None:
        for column, definition in {
            "stage": "TEXT NOT NULL DEFAULT 'minute'",
            "realized_pl": "REAL DEFAULT 0",
            "unrealized_pl": "REAL DEFAULT 0",
            "session_start_equity": "REAL",
            "session_peak_equity": "REAL",
            "drawdown": "REAL DEFAULT 0",
            "drawdown_pct": "REAL DEFAULT 0",
            "open_position_count": "INTEGER DEFAULT 0",
            "open_order_count": "INTEGER DEFAULT 0",
            "details_json": "TEXT",
        }.items():
            self._ensure_column("account_snapshots", column, definition)
        for column, definition in {
            "client_order_id": "TEXT",
            "episode_id": "TEXT",
            "strategy_path": "TEXT",
            "submitted_price": "REAL",
            "bid_price": "REAL",
            "ask_price": "REAL",
            "midpoint": "REAL",
            "spread_pct": "REAL",
            "slippage_per_share": "REAL",
            "slippage_cost": "REAL DEFAULT 0",
            "spread_cost": "REAL DEFAULT 0",
            "estimated_fee": "REAL DEFAULT 0",
            "estimated_live_cost": "REAL DEFAULT 0",
        }.items():
            self._ensure_column("fills", column, definition)
        for column, definition in {
            "strategy_path": "TEXT",
            "playbook": "TEXT",
            "maximum_loss": "REAL",
            "economic_breakeven_pct": "REAL",
            "risk_details_json": "TEXT",
        }.items():
            self._ensure_column("orders", column, definition)
        for column, definition in {
            "root_episode_id": "TEXT",
            "strategy_path": "TEXT DEFAULT 'minute'",
            "playbook": "TEXT",
            "regime": "TEXT",
            "ml_prediction": "TEXT",
            "confidence": "REAL",
            "spread_cost": "REAL DEFAULT 0",
            "slippage_cost": "REAL DEFAULT 0",
            "estimated_fees": "REAL DEFAULT 0",
            "estimated_live_cost": "REAL DEFAULT 0",
            "net_pnl_after_costs": "REAL",
            "opportunity_cost": "REAL DEFAULT 0",
            "profit_given_back": "REAL DEFAULT 0",
        }.items():
            self._ensure_column("trade_outcomes", column, definition)
        self._ensure_column("trading_journal", "pattern_classification", "TEXT")
        self._ensure_column("trading_journal", "pattern_quality", "REAL")
        self._ensure_column("trading_journal", "liquidity_score", "REAL")
        self._ensure_column("trading_journal", "volatility_regime", "TEXT")
        self._ensure_column("trading_journal", "missed_opportunity_label", "TEXT")
        self._ensure_column("trading_journal", "reasoning_agents_json", "TEXT")
        self._ensure_column("trading_journal", "macro_bias", "TEXT")
        self._ensure_column("trading_journal", "macro_confidence", "REAL")
        self._ensure_column("trading_journal", "target_exposure_pct", "REAL")
        self._ensure_column("trading_journal", "order_block_direction", "TEXT")
        self._ensure_column("trading_journal", "order_block_timeframe", "TEXT")
        self._ensure_column("trading_journal", "order_block_strength", "REAL")
        self._ensure_column("trading_journal", "order_block_retest_active", "INTEGER DEFAULT 0")
        self._ensure_column("trading_journal", "options_bias", "TEXT")
        self._ensure_column("trading_journal", "options_confidence", "REAL")
        self._ensure_column("trading_journal", "options_score_adjustment", "REAL")
        self._ensure_column("trading_journal", "options_event_risk", "REAL")
        self._ensure_column("trading_journal", "signal_id", "INTEGER")
        self._ensure_column("trading_journal", "exploration_trade", "INTEGER DEFAULT 0")
        self._ensure_column("trade_outcomes", "exploration_trade", "INTEGER DEFAULT 0")
        self._ensure_column("orders", "parent_order_id", "TEXT")
        self._ensure_column("model_predictions", "model_scope", "TEXT")
        for column, definition in {
            "model_scope": "TEXT NOT NULL DEFAULT 'entry:all'",
            "feature_profile": "TEXT",
            "model_parameters_json": "TEXT",
            "thresholds_json": "TEXT",
            "artifact_fingerprint": "TEXT",
            "training_data_start": "TEXT",
            "training_data_end": "TEXT",
            "parent_champion_version": "TEXT",
            "demoted_at": "TEXT",
            "demotion_reason": "TEXT",
        }.items():
            self._ensure_column("model_versions", column, definition)
        for column, definition in {
            "paper_outcome_count": "INTEGER DEFAULT 0",
            "paper_profit_factor": "REAL",
            "paper_drawdown": "REAL",
            "champion_demoted": "INTEGER DEFAULT 0",
            "demotion_reason": "TEXT",
        }.items():
            self._ensure_column("model_drift_reports", column, definition)
        for column, definition in {
            "related_spread_change_1m": "REAL",
            "related_spread_change_5m": "REAL",
            "related_spread_change_15m": "REAL",
            "outcome_linked": "INTEGER DEFAULT 0",
            "sentiment_trusted": "INTEGER DEFAULT 0",
        }.items():
            self._ensure_column("news_items", column, definition)
        self._ensure_column("macro_context", "news_linked_fraction", "REAL")
        self._ensure_column("macro_context", "advisory_only", "INTEGER DEFAULT 1")
        self.conn.execute("DROP INDEX IF EXISTS idx_one_champion_model")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_champion_per_scope "
            "ON model_versions(model_scope) WHERE status = 'champion'"
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO decision_executions(
                decision_source, decision_id, timestamp, symbol, strategy_path,
                original_action, executed_action, execution_status, client_order_id,
                root_episode_id, model_version, feature_snapshot_json
            )
            SELECT 'signal', s.id, s.timestamp, s.symbol, 'minute', s.decision,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM trading_journal submitted
                       WHERE submitted.signal_id = s.id AND submitted.event_type = 'ORDER_SUBMITTED'
                   ) THEN s.decision ELSE 'NO_TRADE' END,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM trading_journal submitted
                       WHERE submitted.signal_id = s.id AND submitted.event_type = 'ORDER_SUBMITTED'
                   ) THEN 'submitted' ELSE 'historical_no_execution' END,
                   (SELECT j.client_order_id FROM trading_journal j
                    WHERE j.signal_id = s.id AND j.client_order_id IS NOT NULL
                    ORDER BY j.id DESC LIMIT 1),
                   (SELECT j.client_order_id FROM trading_journal j
                    WHERE j.signal_id = s.id AND j.client_order_id IS NOT NULL
                    ORDER BY j.id ASC LIMIT 1),
                   s.model_version, s.feature_snapshot_json
            FROM signals s
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO decision_executions(
                decision_source, decision_id, timestamp, symbol, strategy_path,
                original_action, executed_action, execution_status, client_order_id,
                root_episode_id, model_version, feature_snapshot_json
            )
            SELECT 'fast_scalp', f.id, f.timestamp, f.symbol, 'fast', f.decision,
                   CASE WHEN f.client_order_id IS NOT NULL THEN f.decision ELSE 'NO_TRADE' END,
                   CASE WHEN f.client_order_id IS NOT NULL THEN 'submitted' ELSE COALESCE(f.status, 'historical_no_execution') END,
                   f.client_order_id, f.client_order_id, f.model_version, f.feature_snapshot_json
            FROM fast_scalp_decisions f
            """
        )
        for column, definition in {
            "decision_source": "TEXT NOT NULL DEFAULT 'signal'",
            "decision_id": "INTEGER",
            "label_1m": "TEXT",
            "label_3m": "TEXT",
            "label_5m": "TEXT",
            "label_15m": "TEXT",
            "entry_price": "REAL",
            "price_source": "TEXT",
            "forward_return_3m": "REAL",
        }.items():
            self._ensure_column("outcome_labels", column, definition)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trading_journal_client_order_id ON trading_journal(client_order_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_parent_order_id ON orders(parent_order_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_outcomes_root_episode ON trade_outcomes(root_episode_id, exit_time)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_outcomes_strategy ON trade_outcomes(strategy_path, playbook, regime, exit_time)")
        self.conn.execute(
            """
            UPDATE trade_outcomes
            SET root_episode_id = CASE
                WHEN UPPER(COALESCE(trade_id, '')) LIKE '%-TAKE' THEN SUBSTR(trade_id, 1, LENGTH(trade_id) - 5)
                WHEN UPPER(COALESCE(trade_id, '')) LIKE '%-RUN' THEN SUBSTR(trade_id, 1, LENGTH(trade_id) - 4)
                ELSE trade_id
            END
            WHERE root_episode_id IS NULL OR root_episode_id = ''
            """
        )
        self.conn.execute(
            """
            UPDATE trade_outcomes
            SET strategy_path = 'fast'
            WHERE UPPER(COALESCE(root_episode_id, trade_id, '')) LIKE '%FAST%'
            """
        )
        self.conn.execute("UPDATE trade_outcomes SET playbook = setup_type WHERE playbook IS NULL")
        self.conn.execute(
            """
            UPDATE trade_outcomes
            SET regime = COALESCE(
                    (SELECT j.regime FROM trading_journal j
                     WHERE j.client_order_id = trade_outcomes.trade_id
                     ORDER BY j.id ASC LIMIT 1),
                    regime
                ),
                ml_prediction = COALESCE(
                    (SELECT j.model_prediction FROM trading_journal j
                     WHERE j.client_order_id = trade_outcomes.trade_id
                     ORDER BY j.id ASC LIMIT 1),
                    ml_prediction
                ),
                confidence = COALESCE(
                    (SELECT j.confidence FROM trading_journal j
                     WHERE j.client_order_id = trade_outcomes.trade_id
                     ORDER BY j.id ASC LIMIT 1),
                    confidence
                )
            WHERE regime IS NULL OR ml_prediction IS NULL OR confidence IS NULL
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_outcome_labels_signal_id ON outcome_labels(signal_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_outcome_labels_decision ON outcome_labels(decision_source, decision_id)")
        self.conn.execute(
            """
            UPDATE outcome_labels
            SET decision_source = COALESCE(NULLIF(decision_source, ''), 'signal'),
                decision_id = COALESCE(decision_id, signal_id)
            WHERE signal_id IS NOT NULL
            """
        )
        for column, definition in {
            "author": "TEXT",
            "summary": "TEXT",
            "content_text": "TEXT",
            "symbols_json": "TEXT",
            "category": "TEXT",
            "event_type": "TEXT",
            "sentiment_score": "REAL",
            "confidence_score": "REAL",
            "novelty_score": "REAL",
            "gold_impact": "TEXT",
            "related_move_1m": "REAL",
            "related_move_5m": "REAL",
            "related_move_15m": "REAL",
            "related_move_1h": "REAL",
            "related_move_1d": "REAL",
            "raw_json": "TEXT",
        }.items():
            self._ensure_column("news_items", column, definition)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_news_items_event_type ON news_items(event_type)")
        self.conn.execute(
            """
            UPDATE orders
            SET position_side = NULL
            WHERE position_side IS NOT NULL
              AND LOWER(COALESCE(raw_json, '')) LIKE '%to_close%'
            """
        )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def get_last_bar_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT timestamp FROM bars WHERE symbol = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol.upper(), timeframe),
        ).fetchone()
        return ensure_utc(row["timestamp"]) if row else None

    def get_latest_database_timestamp(self, symbol: str = "GLD") -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(timestamp) AS timestamp FROM bars WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        return ensure_utc(row["timestamp"]) if row and row["timestamp"] else None

    def get_latest_quote(self, symbol: str = "GLD") -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def get_quote_at_or_before(self, symbol: str, timestamp: datetime | str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM quotes
            WHERE symbol = ? AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (symbol.upper(), utc_iso(timestamp)),
        ).fetchone()
        return dict(row) if row else None

    def fetch_recent_quotes(
        self,
        symbol: str = "GLD",
        *,
        since: datetime | str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [symbol.upper()]
        where = "WHERE symbol = ?"
        if since is not None:
            where += " AND timestamp >= ?"
            params.append(utc_iso(since))
        rows = self.conn.execute(
            f"SELECT * FROM quotes {where} ORDER BY timestamp DESC LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def insert_account_snapshot(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO account_snapshots(
                    equity, cash, buying_power, daytrade_count, portfolio_value, multiplier,
                    long_market_value, short_market_value, timestamp, stage, realized_pl,
                    unrealized_pl, session_start_equity, session_peak_equity, drawdown,
                    drawdown_pct, open_position_count, open_order_count, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("equity"),
                    record.get("cash"),
                    record.get("buying_power"),
                    record.get("daytrade_count"),
                    record.get("portfolio_value"),
                    record.get("multiplier"),
                    record.get("long_market_value"),
                    record.get("short_market_value"),
                    utc_iso(record.get("timestamp", utc_now())),
                    record.get("stage", "minute"),
                    record.get("realized_pl", 0.0),
                    record.get("unrealized_pl", 0.0),
                    record.get("session_start_equity"),
                    record.get("session_peak_equity"),
                    record.get("drawdown", 0.0),
                    record.get("drawdown_pct", 0.0),
                    record.get("open_position_count", 0),
                    record.get("open_order_count", 0),
                    _json(record.get("details_json", record.get("details"))),
                ),
            )
        return int(cursor.lastrowid)

    def get_latest_trade(self, symbol: str = "GLD") -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM market_trades WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def fetch_recent_trades(
        self,
        symbol: str = "GLD",
        *,
        since: datetime | str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [symbol.upper()]
        where = "WHERE symbol = ?"
        if since is not None:
            where += " AND timestamp >= ?"
            params.append(utc_iso(since))
        rows = self.conn.execute(
            f"""
            SELECT * FROM market_trades
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_latest_bar(self, symbol: str = "GLD", timeframe: str = "1Min") -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM bars WHERE symbol = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol.upper(), timeframe),
        ).fetchone()
        return dict(row) if row else None

    def upsert_bars(self, bars: Any) -> int:
        records = _records(bars)
        if not records:
            return 0
        count = 0
        with self.conn:
            for row in records:
                symbol = str(row["symbol"]).upper()
                timestamp = utc_iso(row["timestamp"])
                timeframe = str(row.get("timeframe", row.get("time_frame", "1Min")))
                self.conn.execute(
                    """
                    INSERT INTO bars(symbol, timeframe, timestamp, open, high, low, close, volume, trade_count, vwap, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        trade_count=excluded.trade_count,
                        vwap=excluded.vwap,
                        source=excluded.source
                    """,
                    (
                        symbol,
                        timeframe,
                        timestamp,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row.get("volume") or 0),
                        row.get("trade_count"),
                        row.get("vwap"),
                        row.get("source", "alpaca"),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
                count += 1
        return count

    def upsert_bar_variants(self, bars: Any, *, adjustment: str) -> int:
        records = _records(bars)
        if not records:
            return 0
        with self.conn:
            for row in records:
                self.conn.execute(
                    """
                    INSERT INTO bar_variants(symbol, timeframe, timestamp, adjustment, open, high, low, close,
                        volume, trade_count, vwap, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, timestamp, adjustment) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        trade_count=excluded.trade_count,
                        vwap=excluded.vwap,
                        source=excluded.source
                    """,
                    (
                        str(row["symbol"]).upper(),
                        str(row.get("timeframe", row.get("time_frame", "1Min"))),
                        utc_iso(row["timestamp"]),
                        adjustment,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row.get("volume") or 0),
                        row.get("trade_count"),
                        row.get("vwap"),
                        row.get("source", f"alpaca_{adjustment}"),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(records)

    def upsert_quotes(self, quotes: Any) -> int:
        records = _records(quotes)
        if not records:
            return 0
        with self.conn:
            for row in records:
                bid = float(row["bid_price"])
                ask = float(row["ask_price"])
                spread = row.get("spread", ask - bid)
                midpoint = (ask + bid) / 2 if ask and bid else 0
                spread_pct = row.get("spread_pct", spread / midpoint if midpoint else 0)
                bid_size = float(row.get("bid_size") or 0)
                ask_size = float(row.get("ask_size") or 0)
                imbalance = row.get("quote_imbalance")
                if imbalance is None:
                    total_size = bid_size + ask_size
                    imbalance = (bid_size - ask_size) / total_size if total_size else 0
                self.conn.execute(
                    """
                    INSERT INTO quotes(symbol, timestamp, bid_price, bid_size, ask_price, ask_size, spread, spread_pct, quote_imbalance, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        bid_price=excluded.bid_price,
                        bid_size=excluded.bid_size,
                        ask_price=excluded.ask_price,
                        ask_size=excluded.ask_size,
                        spread=excluded.spread,
                        spread_pct=excluded.spread_pct,
                        quote_imbalance=excluded.quote_imbalance
                    """,
                    (
                        str(row["symbol"]).upper(),
                        utc_iso(row["timestamp"]),
                        bid,
                        bid_size,
                        ask,
                        ask_size,
                        spread,
                        spread_pct,
                        imbalance,
                        row.get("source", "alpaca"),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(records)

    def upsert_trades(self, trades: Any) -> int:
        records = _records(trades)
        if not records:
            return 0
        with self.conn:
            for row in records:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO market_trades(symbol, timestamp, price, size, exchange, conditions, tape, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["symbol"]).upper(),
                        utc_iso(row["timestamp"]),
                        float(row["price"]),
                        float(row.get("size") or 0),
                        row.get("exchange"),
                        _json(row.get("conditions")),
                        row.get("tape"),
                        row.get("source", "alpaca"),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(records)

    def upsert_price_snapshot(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO price_snapshots(timestamp, symbol, bid_price, ask_price, midpoint, last_trade_price,
                    spread, spread_pct, quote_age_seconds, trade_age_seconds, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timestamp) DO UPDATE SET
                    bid_price=excluded.bid_price,
                    ask_price=excluded.ask_price,
                    midpoint=excluded.midpoint,
                    last_trade_price=excluded.last_trade_price,
                    spread=excluded.spread,
                    spread_pct=excluded.spread_pct,
                    quote_age_seconds=excluded.quote_age_seconds,
                    trade_age_seconds=excluded.trade_age_seconds,
                    source=excluded.source
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("bid_price"),
                    record.get("ask_price"),
                    record.get("midpoint"),
                    record.get("last_trade_price"),
                    record.get("spread"),
                    record.get("spread_pct"),
                    record.get("quote_age_seconds"),
                    record.get("trade_age_seconds"),
                    record.get("source", "alpaca_stream_1s"),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid or 0)

    def upsert_position_management_state(self, record: Mapping[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO position_management_state(trade_id, parent_order_id, symbol, direction, role, qty,
                    entry_time, entry_price, stop_order_id, take_profit_order_id, initial_stop_price,
                    current_stop_price, take_profit_price, high_water_price, low_water_price,
                    max_favorable_excursion, max_adverse_excursion, breakeven_armed, trailing_armed,
                    exit_requested, last_action, last_action_at, status, details_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    parent_order_id=excluded.parent_order_id,
                    symbol=excluded.symbol,
                    direction=excluded.direction,
                    role=excluded.role,
                    qty=excluded.qty,
                    entry_time=COALESCE(excluded.entry_time, position_management_state.entry_time),
                    entry_price=excluded.entry_price,
                    stop_order_id=COALESCE(excluded.stop_order_id, position_management_state.stop_order_id),
                    take_profit_order_id=COALESCE(excluded.take_profit_order_id, position_management_state.take_profit_order_id),
                    initial_stop_price=COALESCE(position_management_state.initial_stop_price, excluded.initial_stop_price),
                    current_stop_price=COALESCE(excluded.current_stop_price, position_management_state.current_stop_price),
                    take_profit_price=COALESCE(excluded.take_profit_price, position_management_state.take_profit_price),
                    high_water_price=MAX(COALESCE(position_management_state.high_water_price, excluded.high_water_price), excluded.high_water_price),
                    low_water_price=MIN(COALESCE(position_management_state.low_water_price, excluded.low_water_price), excluded.low_water_price),
                    max_favorable_excursion=MAX(COALESCE(position_management_state.max_favorable_excursion, 0), excluded.max_favorable_excursion),
                    max_adverse_excursion=MAX(COALESCE(position_management_state.max_adverse_excursion, 0), excluded.max_adverse_excursion),
                    breakeven_armed=MAX(position_management_state.breakeven_armed, excluded.breakeven_armed),
                    trailing_armed=MAX(position_management_state.trailing_armed, excluded.trailing_armed),
                    exit_requested=MAX(position_management_state.exit_requested, excluded.exit_requested),
                    last_action=COALESCE(excluded.last_action, position_management_state.last_action),
                    last_action_at=COALESCE(excluded.last_action_at, position_management_state.last_action_at),
                    status=excluded.status,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record["trade_id"],
                    record.get("parent_order_id"),
                    str(record["symbol"]).upper(),
                    record["direction"],
                    record.get("role", "single"),
                    record["qty"],
                    utc_iso(record["entry_time"]) if record.get("entry_time") else None,
                    record["entry_price"],
                    record.get("stop_order_id"),
                    record.get("take_profit_order_id"),
                    record.get("initial_stop_price"),
                    record.get("current_stop_price"),
                    record.get("take_profit_price"),
                    record.get("high_water_price"),
                    record.get("low_water_price"),
                    record.get("max_favorable_excursion", 0.0),
                    record.get("max_adverse_excursion", 0.0),
                    1 if record.get("breakeven_armed") else 0,
                    1 if record.get("trailing_armed") else 0,
                    1 if record.get("exit_requested") else 0,
                    record.get("last_action"),
                    utc_iso(record["last_action_at"]) if record.get("last_action_at") else None,
                    record.get("status", "active"),
                    _json(record.get("details")),
                    utc_iso(record.get("updated_at", utc_now())),
                ),
            )

    def insert_position_management_event(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO position_management_events(timestamp, trade_id, parent_order_id, symbol, direction,
                    role, action, reason, qty, entry_price, mark_price, pnl, pnl_pct, old_stop_price,
                    new_stop_price, high_water_price, low_water_price, max_favorable_excursion,
                    max_adverse_excursion, status, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record.get("trade_id"),
                    record.get("parent_order_id"),
                    str(record["symbol"]).upper(),
                    record.get("direction"),
                    record.get("role"),
                    record["action"],
                    record.get("reason"),
                    record.get("qty"),
                    record.get("entry_price"),
                    record.get("mark_price"),
                    record.get("pnl"),
                    record.get("pnl_pct"),
                    record.get("old_stop_price"),
                    record.get("new_stop_price"),
                    record.get("high_water_price"),
                    record.get("low_water_price"),
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    record.get("status"),
                    _json(record.get("details")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def create_execution_episode(self, record: Mapping[str, Any]) -> None:
        now = record.get("opened_at", utc_now())
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO execution_episodes(
                    episode_id, symbol, direction, source, status, planned_qty, submitted_qty,
                    filled_qty, remaining_qty, opened_at, details_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
                """,
                (
                    str(record["episode_id"]),
                    str(record["symbol"]).upper(),
                    str(record["direction"]).upper(),
                    record.get("source", "unknown"),
                    record.get("status", "submitting"),
                    float(record.get("planned_qty") or 0.0),
                    utc_iso(now),
                    _json(record.get("details")),
                    utc_iso(record.get("updated_at", now)),
                ),
            )

    def record_execution_episode_order(self, episode_id: str, record: Mapping[str, Any]) -> None:
        now = record.get("updated_at", utc_now())
        order_key = str(record.get("order_key") or record.get("client_order_id") or record.get("alpaca_order_id"))
        if not order_key:
            raise ValueError("Execution episode order requires a stable order key.")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO execution_episode_orders(
                    order_key, episode_id, alpaca_order_id, client_order_id, role, intent_type,
                    side, qty, filled_qty, filled_avg_price, status, submitted_at, updated_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_key) DO UPDATE SET
                    alpaca_order_id=COALESCE(excluded.alpaca_order_id, execution_episode_orders.alpaca_order_id),
                    client_order_id=COALESCE(excluded.client_order_id, execution_episode_orders.client_order_id),
                    role=excluded.role,
                    intent_type=excluded.intent_type,
                    side=COALESCE(excluded.side, execution_episode_orders.side),
                    qty=COALESCE(excluded.qty, execution_episode_orders.qty),
                    filled_qty=MAX(execution_episode_orders.filled_qty, excluded.filled_qty),
                    filled_avg_price=COALESCE(excluded.filled_avg_price, execution_episode_orders.filled_avg_price),
                    status=excluded.status,
                    submitted_at=COALESCE(execution_episode_orders.submitted_at, excluded.submitted_at),
                    updated_at=excluded.updated_at,
                    raw_json=COALESCE(excluded.raw_json, execution_episode_orders.raw_json)
                """,
                (
                    order_key,
                    str(episode_id),
                    record.get("alpaca_order_id"),
                    record.get("client_order_id"),
                    record.get("role", "unknown"),
                    record.get("intent_type", "entry"),
                    record.get("side"),
                    record.get("qty"),
                    float(record.get("filled_qty") or 0.0),
                    record.get("filled_avg_price"),
                    record.get("status", "unknown"),
                    utc_iso(record["submitted_at"]) if record.get("submitted_at") else None,
                    utc_iso(now),
                    _json(record.get("raw_json")),
                ),
            )
            self._refresh_execution_episode_locked(str(episode_id), now)

    def finalize_execution_episode_submission(self, episode_id: str, *, partial: bool = False, failed: bool = False) -> None:
        status = "submit_failed" if failed else "partially_submitted" if partial else "submitted"
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                UPDATE execution_episodes
                SET status = CASE WHEN filled_qty > 0 THEN 'active' ELSE ? END,
                    version = version + 1,
                    updated_at = ?
                WHERE episode_id = ? AND status NOT IN ('closed', 'flattened')
                """,
                (status, utc_iso(now), str(episode_id)),
            )

    def _refresh_execution_episode_locked(self, episode_id: str, now: datetime) -> None:
        totals = self.conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN intent_type = 'entry' AND status NOT IN
                    ('submit_failed', 'canceled', 'cancelled', 'expired', 'rejected') THEN qty ELSE 0 END), 0) AS submitted_qty,
                COALESCE(SUM(CASE WHEN intent_type = 'entry' THEN filled_qty ELSE 0 END), 0) AS entry_filled,
                COALESCE(SUM(CASE WHEN intent_type = 'exit' THEN filled_qty ELSE 0 END), 0) AS exit_filled,
                COALESCE(SUM(CASE WHEN intent_type = 'entry' THEN filled_qty * COALESCE(filled_avg_price, 0) ELSE 0 END), 0) AS entry_value,
                COALESCE(SUM(CASE WHEN intent_type = 'exit' THEN filled_qty * COALESCE(filled_avg_price, 0) ELSE 0 END), 0) AS exit_value
            FROM execution_episode_orders WHERE episode_id = ?
            """,
            (episode_id,),
        ).fetchone()
        submitted = float(totals["submitted_qty"] or 0.0)
        entry_filled = float(totals["entry_filled"] or 0.0)
        exit_filled = float(totals["exit_filled"] or 0.0)
        remaining = max(0.0, entry_filled - exit_filled)
        entry_avg = float(totals["entry_value"] or 0.0) / entry_filled if entry_filled > 0 else None
        exit_avg = float(totals["exit_value"] or 0.0) / exit_filled if exit_filled > 0 else None
        direction_row = self.conn.execute(
            "SELECT direction FROM execution_episodes WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        direction = str(direction_row["direction"] or "") if direction_row else ""
        closed_qty = min(entry_filled, exit_filled)
        realized_pnl = None
        if entry_avg is not None and exit_avg is not None and closed_qty > 0:
            move = exit_avg - entry_avg
            realized_pnl = move * closed_qty if direction == "LONG" else -move * closed_qty
        status = "closed" if entry_filled > 0 and remaining <= 1e-9 else "active" if entry_filled > 0 else "submitting"
        self.conn.execute(
            """
            UPDATE execution_episodes
            SET submitted_qty=?, filled_qty=?, remaining_qty=?, entry_avg_price=?, exit_avg_price=?,
                realized_pnl=?, status=?,
                closed_at=CASE WHEN ? = 'closed' THEN ? ELSE closed_at END,
                version=version+1, updated_at=?
            WHERE episode_id=?
            """,
            (
                submitted, entry_filled, remaining, entry_avg, exit_avg, realized_pnl,
                status, status, utc_iso(now), utc_iso(now), episode_id,
            ),
        )

    def fetch_active_execution_episodes(self, symbol: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM execution_episodes
            WHERE symbol = ? AND status NOT IN ('closed', 'flattened', 'submit_failed', 'canceled')
            ORDER BY opened_at, episode_id
            """,
            (str(symbol).upper(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def close_symbol_execution_state(self, symbol: str, reason: str, *, now: datetime | None = None) -> None:
        now = now or utc_now()
        with self.conn:
            self.conn.execute(
                """
                UPDATE execution_episodes SET status='flattened', remaining_qty=0, closed_at=?,
                    close_reason=?, version=version+1, updated_at=?
                WHERE symbol=? AND status NOT IN ('closed', 'flattened', 'submit_failed', 'canceled')
                """,
                (utc_iso(now), reason, utc_iso(now), str(symbol).upper()),
            )
            self.conn.execute(
                """
                UPDATE position_management_state SET status='flattened', last_action=?, last_action_at=?, updated_at=?
                WHERE symbol=? AND status NOT IN ('closed', 'flattened')
                """,
                (reason, utc_iso(now), utc_iso(now), str(symbol).upper()),
            )
            self.conn.execute(
                """
                UPDATE orders SET status='closed_by_safety', cancel_reason=COALESCE(cancel_reason, ?)
                WHERE symbol=? AND position_side IN ('LONG', 'SHORT')
                  AND LOWER(COALESCE(status, '')) NOT IN
                    ('canceled', 'cancelled', 'expired', 'filled', 'rejected', 'submit_failed', 'closed_by_safety')
                """,
                (reason, str(symbol).upper()),
            )

    def insert_order_intent(self, record: Mapping[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO order_intents(
                    intent_id, idempotency_key, intent_type, symbol, episode_id, order_id,
                    status, created_at, started_at, completed_at, error, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["intent_id"], record["idempotency_key"], record["intent_type"],
                    str(record.get("symbol", "GLD")).upper(), record.get("episode_id"), record.get("order_id"),
                    record.get("status", "queued"), utc_iso(record.get("created_at", utc_now())),
                    utc_iso(record["started_at"]) if record.get("started_at") else None,
                    utc_iso(record["completed_at"]) if record.get("completed_at") else None,
                    record.get("error"), _json(record.get("details")),
                ),
            )

    def update_order_intent(self, intent_id: str, *, status: str, order_id: str | None = None, error: str | None = None) -> None:
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                UPDATE order_intents SET status=?, order_id=COALESCE(?, order_id), error=?,
                    started_at=CASE WHEN ?='running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    completed_at=CASE WHEN ? IN ('completed', 'failed', 'timed_out') THEN ? ELSE completed_at END
                WHERE intent_id=?
                """,
                (status, order_id, error, status, utc_iso(now), status, utc_iso(now), str(intent_id)),
            )

    def insert_execution_safety_event(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO execution_safety_events(
                    timestamp, event_type, severity, symbol, entry_frozen, circuit_open,
                    broker_position_qty, broker_position_direction, broker_open_order_count,
                    database_episode_count, internal_episode_count, reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())), record["event_type"],
                    record.get("severity", "INFO"), str(record.get("symbol", "GLD")).upper(),
                    1 if record.get("entry_frozen") else 0, 1 if record.get("circuit_open") else 0,
                    record.get("broker_position_qty"), record.get("broker_position_direction"),
                    record.get("broker_open_order_count"), record.get("database_episode_count"),
                    record.get("internal_episode_count"), record.get("reason"), _json(record.get("details")),
                ),
            )
        return int(cursor.lastrowid or 0)

    def insert_performance_consistency_audit(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO performance_consistency_audits(
                    timestamp, stage, consistent, unmatched_fill_count, orphan_order_count,
                    open_episode_count, broker_position_qty, broker_open_order_count,
                    database_position_qty, reasons_json, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())),
                    record.get("stage", "runtime"),
                    1 if record.get("consistent") else 0,
                    record.get("unmatched_fill_count", 0),
                    record.get("orphan_order_count", 0),
                    record.get("open_episode_count", 0),
                    record.get("broker_position_qty", 0.0),
                    record.get("broker_open_order_count", 0),
                    record.get("database_position_qty", 0.0),
                    _json(record.get("reasons", [])),
                    _json(record.get("details", {})),
                ),
            )
        return int(cursor.lastrowid or 0)

    def upsert_ema_cross_signal(self, record: Mapping[str, Any]) -> tuple[int, bool]:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO ema_cross_signals(
                    timestamp, bar_timestamp, symbol, timeframe, timeframe_minutes,
                    strategy_name, decision, fast_ema, slow_ema, adx, atr,
                    adx_passed, cooldown_passed, eligible, confidence, score,
                    execution_status, signal_id, client_order_id, block_reason,
                    feature_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    utc_iso(record["bar_timestamp"]),
                    str(record["symbol"]).upper(),
                    record["timeframe"],
                    int(record["timeframe_minutes"]),
                    record.get("strategy_name", "ema_cross_filtered"),
                    record["decision"],
                    record.get("fast_ema"),
                    record.get("slow_ema"),
                    record.get("adx"),
                    record.get("atr"),
                    1 if record.get("adx_passed") else 0,
                    1 if record.get("cooldown_passed") else 0,
                    1 if record.get("eligible") else 0,
                    record.get("confidence"),
                    record.get("score"),
                    record.get("execution_status", "eligible"),
                    record.get("signal_id"),
                    record.get("client_order_id"),
                    record.get("block_reason"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                ),
            )
            inserted = cursor.rowcount > 0
            row = self.conn.execute(
                """
                SELECT id FROM ema_cross_signals
                WHERE symbol = ? AND timeframe_minutes = ? AND bar_timestamp = ? AND decision = ?
                """,
                (
                    str(record["symbol"]).upper(),
                    int(record["timeframe_minutes"]),
                    utc_iso(record["bar_timestamp"]),
                    record["decision"],
                ),
            ).fetchone()
        return int(row["id"]), inserted

    def update_ema_cross_signals(
        self,
        signal_ids: list[int] | tuple[int, ...],
        *,
        execution_status: str,
        signal_id: int | None = None,
        client_order_id: str | None = None,
        block_reason: str | None = None,
    ) -> int:
        ids = [int(item) for item in signal_ids if int(item) > 0]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self.conn:
            cursor = self.conn.execute(
                f"""
                UPDATE ema_cross_signals
                SET execution_status = ?,
                    signal_id = COALESCE(?, signal_id),
                    client_order_id = COALESCE(?, client_order_id),
                    block_reason = COALESCE(?, block_reason)
                WHERE id IN ({placeholders})
                """,
                (
                    execution_status,
                    signal_id,
                    client_order_id,
                    block_reason,
                    *ids,
                ),
            )
        return int(cursor.rowcount)

    def fetch_latest_executed_ema_cross_bars(self, symbol: str) -> dict[int, datetime]:
        rows = self.conn.execute(
            """
            SELECT timeframe_minutes, MAX(bar_timestamp) AS bar_timestamp
            FROM ema_cross_signals
            WHERE symbol = ?
              AND execution_status IN ('submitted', 'partially_submitted')
            GROUP BY timeframe_minutes
            """,
            (symbol.upper(),),
        ).fetchall()
        return {
            int(row["timeframe_minutes"]): ensure_utc(row["bar_timestamp"])
            for row in rows
            if row["bar_timestamp"]
        }

    def insert_signal(self, record: Mapping[str, Any]) -> int:
        feature_json = _json(record.get("feature_snapshot_json", record.get("features", {})))
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO signals(timestamp, symbol, bullish_score, bearish_score, no_trade_score, regime, decision, confidence, reason, feature_snapshot_json, model_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("bullish_score"),
                    record.get("bearish_score"),
                    record.get("no_trade_score"),
                    record.get("regime"),
                    record.get("decision"),
                    record.get("confidence"),
                    record.get("reason"),
                    feature_json,
                    record.get("model_version"),
                ),
            )
        return int(cursor.lastrowid)

    def insert_no_trade(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO no_trade_logs(timestamp, symbol, reason, bullish_score, bearish_score, regime, spread_pct,
                                          volume_condition, volatility_condition, risk_block_reason, model_rejection_reason,
                                          feature_snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("reason"),
                    record.get("bullish_score"),
                    record.get("bearish_score"),
                    record.get("regime"),
                    record.get("spread_pct"),
                    record.get("volume_condition"),
                    record.get("volatility_condition"),
                    record.get("risk_block_reason"),
                    record.get("model_rejection_reason"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                ),
            )
        return int(cursor.lastrowid)

    def insert_order(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR REPLACE INTO orders(alpaca_order_id, client_order_id, parent_order_id, symbol, side, position_side, qty, notional,
                    order_type, order_class, time_in_force, limit_price, stop_price, take_profit_price, status,
                    submitted_at, filled_at, filled_qty, filled_avg_price, cancel_reason, strategy_path,
                    playbook, maximum_loss, economic_breakeven_pct, risk_details_json, mode, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("alpaca_order_id"),
                    record.get("client_order_id"),
                    record.get("parent_order_id"),
                    str(record["symbol"]).upper(),
                    record.get("side"),
                    record.get("position_side"),
                    record.get("qty"),
                    record.get("notional"),
                    record.get("order_type"),
                    record.get("order_class"),
                    record.get("time_in_force"),
                    record.get("limit_price"),
                    record.get("stop_price"),
                    record.get("take_profit_price"),
                    record.get("status"),
                    utc_iso(record["submitted_at"]) if record.get("submitted_at") else None,
                    utc_iso(record["filled_at"]) if record.get("filled_at") else None,
                    record.get("filled_qty"),
                    record.get("filled_avg_price"),
                    record.get("cancel_reason"),
                    record.get("strategy_path"),
                    record.get("playbook"),
                    record.get("maximum_loss"),
                    record.get("economic_breakeven_pct"),
                    _json(record.get("risk_details")),
                    record.get("mode", "paper"),
                    _json(record.get("raw_json")),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_decision_execution(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO decision_executions(
                    decision_source, decision_id, timestamp, symbol, strategy_path, playbook,
                    original_action, executed_action, execution_status, client_order_id,
                    root_episode_id, model_scope, model_version, spread_pct,
                    expected_slippage_pct, fill_quality_score, direction_available,
                    session_phase, execution_error, feature_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_source, decision_id) DO UPDATE SET
                    executed_action=excluded.executed_action,
                    execution_status=excluded.execution_status,
                    client_order_id=COALESCE(excluded.client_order_id, decision_executions.client_order_id),
                    root_episode_id=COALESCE(excluded.root_episode_id, decision_executions.root_episode_id),
                    model_scope=COALESCE(excluded.model_scope, decision_executions.model_scope),
                    model_version=COALESCE(excluded.model_version, decision_executions.model_version),
                    direction_available=excluded.direction_available,
                    execution_error=excluded.execution_error,
                    feature_snapshot_json=excluded.feature_snapshot_json
                """,
                (
                    record["decision_source"],
                    int(record["decision_id"]),
                    utc_iso(record.get("timestamp", utc_now())),
                    str(record.get("symbol", self.settings.bot_symbol)).upper(),
                    record.get("strategy_path", "minute"),
                    record.get("playbook"),
                    record.get("original_action"),
                    record.get("executed_action", "NO_TRADE"),
                    record.get("execution_status", "blocked"),
                    record.get("client_order_id"),
                    record.get("root_episode_id"),
                    record.get("model_scope"),
                    record.get("model_version"),
                    record.get("spread_pct"),
                    record.get("expected_slippage_pct"),
                    record.get("fill_quality_score"),
                    1 if record.get("direction_available", True) else 0,
                    record.get("session_phase"),
                    record.get("execution_error"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                ),
            )
            row = self.conn.execute(
                "SELECT id FROM decision_executions WHERE decision_source = ? AND decision_id = ?",
                (record["decision_source"], int(record["decision_id"])),
            ).fetchone()
        return int(row["id"])

    def insert_llm_offline_cycle(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO llm_offline_cycles(
                    timestamp, cadence, provider, model, fingpt_source_fingerprint,
                    news_linked_fraction, status, outputs_json, error_message, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())),
                    record.get("cadence", "daily"),
                    record.get("provider"),
                    record.get("model"),
                    record.get("fingpt_source_fingerprint"),
                    record.get("news_linked_fraction"),
                    record.get("status", "completed"),
                    _json(record.get("outputs")),
                    record.get("error_message"),
                    utc_iso(record["completed_at"]) if record.get("completed_at") else None,
                ),
            )
        return int(cursor.lastrowid)

    def insert_fill(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO fills(
                    order_id, symbol, side, qty, price, timestamp, client_order_id, episode_id,
                    strategy_path, expected_price, submitted_price, bid_price, ask_price, midpoint,
                    spread_at_entry, spread_pct, slippage, slippage_per_share, slippage_cost,
                    spread_cost, estimated_fee, estimated_live_cost, mode, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("order_id"),
                    str(record["symbol"]).upper(),
                    record["side"],
                    record.get("qty"),
                    record.get("price"),
                    utc_iso(record["timestamp"]),
                    record.get("client_order_id"),
                    record.get("episode_id"),
                    record.get("strategy_path"),
                    record.get("expected_price"),
                    record.get("submitted_price"),
                    record.get("bid_price"),
                    record.get("ask_price"),
                    record.get("midpoint"),
                    record.get("spread_at_entry"),
                    record.get("spread_pct"),
                    record.get("slippage"),
                    record.get("slippage_per_share"),
                    record.get("slippage_cost", 0.0),
                    record.get("spread_cost", 0.0),
                    record.get("estimated_fee", 0.0),
                    record.get("estimated_live_cost", 0.0),
                    record.get("mode", "paper"),
                    _json(record.get("raw_json")),
                ),
            )
        return int(cursor.lastrowid)

    def fill_exists(self, order_id: str | None) -> bool:
        if not order_id:
            return False
        row = self.conn.execute("SELECT 1 FROM fills WHERE order_id = ? LIMIT 1", (str(order_id),)).fetchone()
        return row is not None

    def insert_trade_outcome(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO trade_outcomes(trade_id, symbol, direction, entry_time, exit_time, entry_price, exit_price, qty,
                    notional, gross_pnl, net_pnl_estimated, pnl_pct, max_favorable_excursion, max_adverse_excursion,
                    holding_seconds, exit_reason, win_loss, mistake_category, setup_type, model_version, strategy_version,
                    exploration_trade, root_episode_id, strategy_path, playbook, regime, ml_prediction, confidence,
                    spread_cost, slippage_cost, estimated_fees, estimated_live_cost, net_pnl_after_costs,
                    opportunity_cost, profit_given_back, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("trade_id"),
                    str(record["symbol"]).upper(),
                    record["direction"],
                    utc_iso(record["entry_time"]) if record.get("entry_time") else None,
                    utc_iso(record["exit_time"]) if record.get("exit_time") else None,
                    record.get("entry_price"),
                    record.get("exit_price"),
                    record.get("qty"),
                    record.get("notional"),
                    record.get("gross_pnl"),
                    record.get("net_pnl_estimated"),
                    record.get("pnl_pct"),
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    record.get("holding_seconds"),
                    record.get("exit_reason"),
                    record.get("win_loss"),
                    record.get("mistake_category"),
                    record.get("setup_type"),
                    record.get("model_version"),
                    record.get("strategy_version"),
                    1 if record.get("exploration_trade") else 0,
                    record.get("root_episode_id"),
                    record.get("strategy_path", "minute"),
                    record.get("playbook"),
                    record.get("regime"),
                    record.get("ml_prediction"),
                    record.get("confidence"),
                    record.get("spread_cost", 0.0),
                    record.get("slippage_cost", 0.0),
                    record.get("estimated_fees", 0.0),
                    record.get("estimated_live_cost", 0.0),
                    record.get("net_pnl_after_costs", record.get("net_pnl_estimated")),
                    record.get("opportunity_cost", 0.0),
                    record.get("profit_given_back", 0.0),
                    record.get("mode", "paper"),
                ),
            )
        return int(cursor.lastrowid)

    def update_trade_outcome_learning(self, outcome_id: int, record: Mapping[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE trade_outcomes
                SET max_favorable_excursion = ?, max_adverse_excursion = ?, mistake_category = ?,
                    setup_type = COALESCE(?, setup_type), model_version = COALESCE(?, model_version),
                    exploration_trade = ?, opportunity_cost = ?, profit_given_back = ?
                WHERE id = ?
                """,
                (
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    record.get("mistake_category"),
                    record.get("setup_type"),
                    record.get("model_version"),
                    1 if record.get("exploration_trade") else 0,
                    record.get("opportunity_cost", 0.0),
                    record.get("profit_given_back", 0.0),
                    int(outcome_id),
                ),
            )

    def trade_review_exists(self, trade_outcome_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM trade_reviews WHERE trade_outcome_id = ? LIMIT 1",
            (int(trade_outcome_id),),
        ).fetchone()
        return row is not None

    def insert_trade_review(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO trade_reviews(trade_outcome_id, trade_id, signal_id, journal_id, symbol,
                    direction, entry_time, exit_time, result_label, review_type, setup_quality, learning_reward,
                    net_return, max_favorable_excursion, max_adverse_excursion, exit_reason, mistake_category,
                    exploration_trade, entry_reason, lesson_summary, positive_factors_json, negative_factors_json,
                    counterfactual_json, feature_snapshot_json, outcome_snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["trade_outcome_id"],
                    record.get("trade_id"),
                    record.get("signal_id"),
                    record.get("journal_id"),
                    str(record["symbol"]).upper(),
                    record["direction"],
                    utc_iso(record["entry_time"]) if record.get("entry_time") else None,
                    utc_iso(record["exit_time"]) if record.get("exit_time") else None,
                    record["result_label"],
                    record["review_type"],
                    record.get("setup_quality"),
                    record.get("learning_reward"),
                    record.get("net_return"),
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    record.get("exit_reason"),
                    record.get("mistake_category"),
                    1 if record.get("exploration_trade") else 0,
                    record.get("entry_reason"),
                    record.get("lesson_summary"),
                    _json(record.get("positive_factors")),
                    _json(record.get("negative_factors")),
                    _json(record.get("counterfactual")),
                    _json(record.get("feature_snapshot")),
                    _json(record.get("outcome_snapshot")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def fetch_trade_decision(self, client_order_id: str | None) -> dict[str, Any] | None:
        if not client_order_id:
            return None
        row = self.conn.execute(
            """
            SELECT * FROM trading_journal
            WHERE client_order_id = ?
              AND event_type IN ('TRADE_DECISION', 'ORDER_SUBMITTED', 'FAST_ORDER_SUBMITTED')
            ORDER BY CASE event_type
                WHEN 'TRADE_DECISION' THEN 0
                WHEN 'FAST_ORDER_SUBMITTED' THEN 1
                ELSE 2
            END, id ASC
            LIMIT 1
            """,
            (str(client_order_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def count_paper_exploration_trades(self, start: datetime | str, end: datetime | str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM trading_journal
            WHERE event_type IN ('TRADE_DECISION', 'FAST_ORDER_SUBMITTED')
              AND exploration_trade = 1
              AND timestamp >= ? AND timestamp < ?
            """,
            (utc_iso(start), utc_iso(end)),
        ).fetchone()
        return int(row["count"] or 0)

    def latest_paper_exploration_time(self) -> datetime | None:
        row = self.conn.execute(
            """
            SELECT timestamp FROM trading_journal
            WHERE event_type IN ('TRADE_DECISION', 'FAST_ORDER_SUBMITTED')
              AND exploration_trade = 1
            ORDER BY timestamp DESC, id DESC LIMIT 1
            """
        ).fetchone()
        return ensure_utc(row["timestamp"]) if row is not None else None

    def fetch_active_trade_episodes(self, symbol: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT o.*,
                   COALESCE(
                       o.notional,
                       o.filled_qty * o.filled_avg_price,
                       o.qty * o.limit_price,
                       0
                   ) AS episode_notional
            FROM orders o
            WHERE o.symbol = ?
              AND o.position_side IN ('LONG', 'SHORT')
              AND o.parent_order_id IS NULL
              AND o.client_order_id IS NOT NULL
              AND LOWER(COALESCE(o.raw_json, '')) NOT LIKE '%to_close%'
              AND LOWER(COALESCE(o.status, '')) NOT IN (
                  'canceled', 'cancelled', 'expired', 'rejected', 'suspended',
                  'stopped', 'done_for_day', 'submit_failed'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM trade_outcomes t WHERE t.trade_id = o.client_order_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM execution_episodes e
                  WHERE e.episode_id = CASE
                      WHEN o.client_order_id LIKE '%-TAKE' THEN SUBSTR(o.client_order_id, 1, LENGTH(o.client_order_id) - 5)
                      WHEN o.client_order_id LIKE '%-RUN' THEN SUBSTR(o.client_order_id, 1, LENGTH(o.client_order_id) - 4)
                      ELSE o.client_order_id
                  END
                    AND e.status IN ('closed', 'flattened', 'submit_failed', 'canceled')
              )
            ORDER BY COALESCE(o.submitted_at, o.filled_at), o.id
            """,
            (str(symbol).upper(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def active_trade_episode_summary(self, symbol: str) -> dict[str, Any]:
        episodes = self.fetch_active_trade_episodes(symbol)
        directions = {str(row.get("position_side") or "").upper() for row in episodes if row.get("position_side")}
        return {
            "count": len(episodes),
            "direction": next(iter(directions)) if len(directions) == 1 else "MIXED" if directions else "",
            "notional": sum(float(row.get("episode_notional") or 0.0) for row in episodes),
            "client_order_ids": [str(row["client_order_id"]) for row in episodes],
        }

    def latest_position_management_event(
        self,
        trade_id: str,
        actions: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [str(trade_id)]
        action_clause = ""
        if actions:
            placeholders = ",".join("?" for _ in actions)
            action_clause = f" AND action IN ({placeholders})"
            params.extend(actions)
        row = self.conn.execute(
            f"""
            SELECT * FROM position_management_events
            WHERE trade_id = ?{action_clause}
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row is not None else None

    def get_position_management_state(self, trade_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM position_management_state WHERE trade_id = ? LIMIT 1",
            (str(trade_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def trade_outcome_exists(self, trade_id: str | None) -> bool:
        if not trade_id:
            return False
        row = self.conn.execute("SELECT 1 FROM trade_outcomes WHERE trade_id = ? LIMIT 1", (str(trade_id),)).fetchone()
        return row is not None

    def get_order(self, *, alpaca_order_id: str | None = None, client_order_id: str | None = None) -> dict[str, Any] | None:
        if alpaca_order_id:
            row = self.conn.execute(
                "SELECT * FROM orders WHERE alpaca_order_id = ? ORDER BY id DESC LIMIT 1",
                (str(alpaca_order_id),),
            ).fetchone()
            if row is not None:
                return dict(row)
        if client_order_id:
            row = self.conn.execute(
                "SELECT * FROM orders WHERE client_order_id = ? ORDER BY id DESC LIMIT 1",
                (str(client_order_id),),
            ).fetchone()
            if row is not None:
                return dict(row)
        return None

    def insert_trading_journal(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO trading_journal(timestamp, symbol, event_type, signal_id, decision, confidence, bullish_score,
                    bearish_score, no_trade_score, regime, reason, risk_block_reason, model_version, model_prediction,
                    probability_long, probability_short, probability_no_trade, order_id, client_order_id, side, qty,
                    price, notional, status, pnl, pattern_classification, pattern_quality, liquidity_score,
                    volatility_regime, missed_opportunity_label, reasoning_agents_json, macro_bias, macro_confidence,
                    target_exposure_pct, order_block_direction, order_block_timeframe, order_block_strength,
                    order_block_retest_active, options_bias, options_confidence, options_score_adjustment,
                    options_event_risk, exploration_trade, feature_snapshot_json, broker_snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record["event_type"],
                    record.get("signal_id"),
                    record.get("decision"),
                    record.get("confidence"),
                    record.get("bullish_score"),
                    record.get("bearish_score"),
                    record.get("no_trade_score"),
                    record.get("regime"),
                    record.get("reason"),
                    record.get("risk_block_reason"),
                    record.get("model_version"),
                    record.get("model_prediction"),
                    record.get("probability_long"),
                    record.get("probability_short"),
                    record.get("probability_no_trade"),
                    record.get("order_id"),
                    record.get("client_order_id"),
                    record.get("side"),
                    record.get("qty"),
                    record.get("price"),
                    record.get("notional"),
                    record.get("status"),
                    record.get("pnl"),
                    record.get("pattern_classification"),
                    record.get("pattern_quality"),
                    record.get("liquidity_score"),
                    record.get("volatility_regime"),
                    record.get("missed_opportunity_label"),
                    record.get("reasoning_agents_json"),
                    record.get("macro_bias"),
                    record.get("macro_confidence"),
                    record.get("target_exposure_pct"),
                    record.get("order_block_direction"),
                    record.get("order_block_timeframe"),
                    record.get("order_block_strength"),
                    1 if record.get("order_block_retest_active") else 0,
                    record.get("options_bias"),
                    record.get("options_confidence"),
                    record.get("options_score_adjustment"),
                    record.get("options_event_risk"),
                    1 if record.get("exploration_trade") else 0,
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                    _json(record.get("broker_snapshot_json", record.get("broker_snapshot", {}))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_missed_opportunity(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO missed_opportunities(signal_id, timestamp, symbol, decision, label,
                    entry_price, horizon_minutes, max_up_move_pct, max_down_move_pct, max_favorable_move_pct,
                    max_adverse_move_pct, review_reason, feature_snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("signal_id"),
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("decision"),
                    record["label"],
                    record.get("entry_price"),
                    record.get("horizon_minutes"),
                    record.get("max_up_move_pct"),
                    record.get("max_down_move_pct"),
                    record.get("max_favorable_move_pct"),
                    record.get("max_adverse_move_pct"),
                    record.get("review_reason"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def missed_opportunity_exists(self, signal_id: int | None) -> bool:
        if signal_id is None:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM missed_opportunities WHERE signal_id = ? LIMIT 1",
            (signal_id,),
        ).fetchone()
        return row is not None

    def fetch_unlabeled_no_trade_signals(self, *, before: datetime | str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT s.*
            FROM signals s
            WHERE s.decision = 'NO_TRADE'
              AND s.timestamp <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM missed_opportunities m
                  WHERE m.signal_id = s.id
              )
            ORDER BY s.timestamp ASC
            LIMIT ?
            """,
            (utc_iso(before), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_model_prediction(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO model_predictions(timestamp, symbol, model_version, model_scope, predicted_direction, probability_long,
                    probability_short, probability_no_trade, expected_return, confidence, feature_snapshot_json,
                    actual_outcome_when_known)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("model_version"),
                    record.get("model_scope"),
                    record.get("predicted_direction"),
                    record.get("probability_long"),
                    record.get("probability_short"),
                    record.get("probability_no_trade"),
                    record.get("expected_return"),
                    record.get("confidence"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                    record.get("actual_outcome_when_known"),
                ),
            )
        return int(cursor.lastrowid)

    def insert_transformer_prediction(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO transformer_predictions(
                    timestamp, symbol, model_version, model_scope, model_role, predicted_direction,
                    probability_long, probability_short, probability_no_trade,
                    expected_return_1m, expected_return_3m, expected_return_5m, expected_return_15m,
                    expected_cost, uncertainty, inference_latency_ms, cache_age_seconds, status,
                    fallback_model_version, feature_snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("model_version"),
                    record["model_scope"],
                    record.get("model_role", "paper_shadow"),
                    record.get("predicted_direction"),
                    record.get("probability_long"),
                    record.get("probability_short"),
                    record.get("probability_no_trade"),
                    record.get("expected_return_1m"),
                    record.get("expected_return_3m"),
                    record.get("expected_return_5m"),
                    record.get("expected_return_15m"),
                    record.get("expected_cost"),
                    record.get("uncertainty"),
                    record.get("inference_latency_ms"),
                    record.get("cache_age_seconds"),
                    record.get("status", "shadow"),
                    record.get("fallback_model_version"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_fair_value_gaps(self, records: Any) -> int:
        rows = list(records)
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO fair_value_gaps(
                    gap_key, symbol, timeframe, direction, detected_at, zone_low,
                    zone_high, midpoint, status, fill_fraction, age_bars,
                    invalidated, last_touched_at, feature_snapshot_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gap_key) DO UPDATE SET
                    status=excluded.status,
                    fill_fraction=excluded.fill_fraction,
                    age_bars=excluded.age_bars,
                    invalidated=excluded.invalidated,
                    last_touched_at=excluded.last_touched_at,
                    feature_snapshot_json=excluded.feature_snapshot_json,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["gap_key"],
                        str(row.get("symbol") or "GLD").upper(),
                        row.get("timeframe") or "1Min",
                        row["direction"],
                        utc_iso(row["detected_at"]),
                        row["zone_low"],
                        row["zone_high"],
                        row["midpoint"],
                        row["status"],
                        row.get("fill_fraction", 0.0),
                        row.get("age_bars", 0),
                        1 if row.get("invalidated") else 0,
                        utc_iso(row["last_touched_at"]) if row.get("last_touched_at") else None,
                        _json(row.get("features", {})),
                        utc_iso(row.get("updated_at", utc_now())),
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def insert_fast_scalp_decision(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO fast_scalp_decisions(timestamp, symbol, decision, trigger_type, allowed, confidence,
                    score, reason, latency_ms, spread_pct, quote_imbalance, trade_intensity, liquidity_score,
                    volatility_burst, model_version, model_prediction, probability_long, probability_short,
                    probability_no_trade, client_order_id, status, feature_snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record["decision"],
                    record.get("trigger_type"),
                    1 if record.get("allowed") else 0,
                    record.get("confidence"),
                    record.get("score"),
                    record.get("reason"),
                    record.get("latency_ms"),
                    record.get("spread_pct"),
                    record.get("quote_imbalance"),
                    record.get("trade_intensity"),
                    record.get("liquidity_score"),
                    1 if record.get("volatility_burst") else 0,
                    record.get("model_version"),
                    record.get("model_prediction"),
                    record.get("probability_long"),
                    record.get("probability_short"),
                    record.get("probability_no_trade"),
                    record.get("client_order_id"),
                    record.get("status"),
                    _json(record.get("feature_snapshot_json", record.get("features", {}))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_news_item(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO news_items(timestamp, source, headline, url, author, summary, content_text,
                    symbols_json, category, event_type, sentiment_score, confidence_score, novelty_score, gold_impact,
                    gold_score, usd_score, rates_score, risk_score, event_risk, related_move_1m, related_move_5m,
                    related_move_15m, related_move_1h, related_move_1d, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record.get("source"),
                    record["headline"],
                    record.get("url"),
                    record.get("author"),
                    record.get("summary"),
                    record.get("content_text"),
                    _json(record.get("symbols")),
                    record.get("category"),
                    record.get("event_type"),
                    record.get("sentiment_score"),
                    record.get("confidence_score"),
                    record.get("novelty_score"),
                    record.get("gold_impact"),
                    record.get("gold_score"),
                    record.get("usd_score"),
                    record.get("rates_score"),
                    record.get("risk_score"),
                    record.get("event_risk"),
                    record.get("related_move_1m"),
                    record.get("related_move_5m"),
                    record.get("related_move_15m"),
                    record.get("related_move_1h"),
                    record.get("related_move_1d"),
                    _json(record.get("raw_json", record.get("raw"))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_economic_events(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                key = row.get("event_key") or f"{utc_iso(row['scheduled_at'])}|{row.get('country', '')}|{row['event_name']}"
                self.conn.execute(
                    """
                    INSERT INTO economic_events(event_key, scheduled_at, country, event_name, category, importance,
                        forecast, previous_value, actual_value, revision, surprise_value, surprise_pct, expected_impact,
                        realized_gld_move_1m, realized_gld_move_5m, realized_gld_move_15m, realized_gld_move_1h,
                        realized_gld_move_1d, realized_spread_widening, avoid_trading, source, raw_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        actual_value=excluded.actual_value,
                        revision=excluded.revision,
                        surprise_value=excluded.surprise_value,
                        surprise_pct=excluded.surprise_pct,
                        realized_gld_move_1m=excluded.realized_gld_move_1m,
                        realized_gld_move_5m=excluded.realized_gld_move_5m,
                        realized_gld_move_15m=excluded.realized_gld_move_15m,
                        realized_gld_move_1h=excluded.realized_gld_move_1h,
                        realized_gld_move_1d=excluded.realized_gld_move_1d,
                        realized_spread_widening=excluded.realized_spread_widening,
                        avoid_trading=excluded.avoid_trading,
                        raw_json=excluded.raw_json
                    """,
                    (
                        str(key),
                        utc_iso(row["scheduled_at"]),
                        row.get("country"),
                        row["event_name"],
                        row.get("category"),
                        row.get("importance"),
                        row.get("forecast"),
                        row.get("previous_value"),
                        row.get("actual_value"),
                        row.get("revision"),
                        row.get("surprise_value"),
                        row.get("surprise_pct"),
                        row.get("expected_impact"),
                        row.get("realized_gld_move_1m"),
                        row.get("realized_gld_move_5m"),
                        row.get("realized_gld_move_15m"),
                        row.get("realized_gld_move_1h"),
                        row.get("realized_gld_move_1d"),
                        row.get("realized_spread_widening"),
                        1 if row.get("avoid_trading") else 0,
                        row.get("source"),
                        _json(row.get("raw_json", row.get("raw"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def upsert_macro_series(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO macro_series(series_id, observation_date, value, realtime_start,
                        realtime_end, units, title, source, raw_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["series_id"]).upper(),
                        str(row["observation_date"]),
                        row.get("value"),
                        row.get("realtime_start"),
                        row.get("realtime_end"),
                        row.get("units"),
                        row.get("title"),
                        row.get("source"),
                        _json(row.get("raw_json", row.get("raw"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def upsert_market_calendar(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO market_calendar(calendar_date, market, is_open, open_time, close_time, session_type,
                        source, raw_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(calendar_date, market) DO UPDATE SET
                        is_open=excluded.is_open,
                        open_time=excluded.open_time,
                        close_time=excluded.close_time,
                        session_type=excluded.session_type,
                        raw_json=excluded.raw_json
                    """,
                    (
                        str(row["calendar_date"]),
                        row.get("market", "US_EQUITY"),
                        1 if row.get("is_open") else 0,
                        utc_iso(row["open_time"]) if row.get("open_time") else None,
                        utc_iso(row["close_time"]) if row.get("close_time") else None,
                        row.get("session_type"),
                        row.get("source"),
                        _json(row.get("raw_json", row.get("raw"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def upsert_microstructure_features(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO microstructure_features(timestamp, symbol, bid_price, ask_price, bid_size, ask_size,
                        midpoint, spread, spread_pct, spread_regime, quote_imbalance, quote_age_seconds,
                        trade_intensity, signed_volume, aggressive_buy_volume, aggressive_sell_volume, liquidity_score,
                        volatility_burst, stale_data, source, feature_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        bid_price=excluded.bid_price,
                        ask_price=excluded.ask_price,
                        bid_size=excluded.bid_size,
                        ask_size=excluded.ask_size,
                        midpoint=excluded.midpoint,
                        spread=excluded.spread,
                        spread_pct=excluded.spread_pct,
                        spread_regime=excluded.spread_regime,
                        quote_imbalance=excluded.quote_imbalance,
                        trade_intensity=excluded.trade_intensity,
                        signed_volume=excluded.signed_volume,
                        aggressive_buy_volume=excluded.aggressive_buy_volume,
                        aggressive_sell_volume=excluded.aggressive_sell_volume,
                        liquidity_score=excluded.liquidity_score,
                        volatility_burst=excluded.volatility_burst,
                        stale_data=excluded.stale_data,
                        feature_json=excluded.feature_json
                    """,
                    (
                        utc_iso(row["timestamp"]),
                        str(row["symbol"]).upper(),
                        row.get("bid_price"),
                        row.get("ask_price"),
                        row.get("bid_size"),
                        row.get("ask_size"),
                        row.get("midpoint"),
                        row.get("spread"),
                        row.get("spread_pct"),
                        row.get("spread_regime"),
                        row.get("quote_imbalance"),
                        row.get("quote_age_seconds"),
                        row.get("trade_intensity"),
                        row.get("signed_volume"),
                        row.get("aggressive_buy_volume"),
                        row.get("aggressive_sell_volume"),
                        row.get("liquidity_score"),
                        1 if row.get("volatility_burst") else 0,
                        1 if row.get("stale_data") else 0,
                        row.get("source"),
                        _json(row.get("feature_json", row.get("features"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def insert_data_gap(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO data_gaps(detected_at, symbol, data_type, start_time, end_time, gap_seconds,
                    severity, reason, source, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("detected_at", utc_now())),
                    str(record["symbol"]).upper() if record.get("symbol") else None,
                    record["data_type"],
                    utc_iso(record["start_time"]) if record.get("start_time") else None,
                    utc_iso(record["end_time"]) if record.get("end_time") else None,
                    record.get("gap_seconds"),
                    record.get("severity"),
                    record.get("reason"),
                    record.get("source"),
                    _json(record.get("raw_json", record.get("raw"))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_stream_diagnostic(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO stream_diagnostics(timestamp, websocket_connected, stream_stale, last_message_age_seconds,
                    last_bar_age_seconds, last_quote_age_seconds, last_trade_age_seconds, bar_count, quote_count,
                    trade_count, stale_reason, last_error, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())),
                    1 if record.get("websocket_connected") else 0,
                    1 if record.get("stream_stale") else 0,
                    record.get("last_message_age_seconds", record.get("stream_message_age_seconds")),
                    record.get("last_bar_age_seconds", record.get("stream_bar_age_seconds")),
                    record.get("last_quote_age_seconds", record.get("stream_quote_age_seconds")),
                    record.get("last_trade_age_seconds", record.get("stream_trade_age_seconds")),
                    record.get("bar_count", record.get("stream_bar_count")),
                    record.get("quote_count", record.get("stream_quote_count")),
                    record.get("trade_count", record.get("stream_trade_count")),
                    record.get("stale_reason", record.get("stream_stale_reason")),
                    record.get("last_error", record.get("stream_last_error")),
                    _json(record.get("raw_json", record)),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_price_action_labels(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO price_action_labels(timestamp, symbol, timeframe, pattern_classification, pattern_quality,
                        buildup_detected, buildup_side, proper_break, false_break, tease_break, pullback, support_level,
                        resistance_level, range_compression, compression_duration, breakout_volume_confirmation,
                        vwap_rejection, trend_continuation, trend_exhaustion, candle_body_strength, wick_rejection,
                        raw_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                        pattern_classification=excluded.pattern_classification,
                        pattern_quality=excluded.pattern_quality,
                        buildup_detected=excluded.buildup_detected,
                        buildup_side=excluded.buildup_side,
                        proper_break=excluded.proper_break,
                        false_break=excluded.false_break,
                        tease_break=excluded.tease_break,
                        pullback=excluded.pullback,
                        support_level=excluded.support_level,
                        resistance_level=excluded.resistance_level,
                        range_compression=excluded.range_compression,
                        compression_duration=excluded.compression_duration,
                        breakout_volume_confirmation=excluded.breakout_volume_confirmation,
                        raw_json=excluded.raw_json
                    """,
                    (
                        utc_iso(row["timestamp"]),
                        str(row["symbol"]).upper(),
                        row.get("timeframe", "1Min"),
                        row.get("pattern_classification"),
                        row.get("pattern_quality"),
                        1 if row.get("buildup_detected") else 0,
                        row.get("buildup_side"),
                        1 if row.get("proper_break") else 0,
                        1 if row.get("false_break") else 0,
                        1 if row.get("tease_break") else 0,
                        1 if row.get("pullback") else 0,
                        row.get("support_level"),
                        row.get("resistance_level"),
                        1 if row.get("range_compression") else 0,
                        row.get("compression_duration"),
                        1 if row.get("breakout_volume_confirmation") else 0,
                        1 if row.get("vwap_rejection") else 0,
                        1 if row.get("trend_continuation") else 0,
                        1 if row.get("trend_exhaustion") else 0,
                        row.get("candle_body_strength"),
                        row.get("wick_rejection"),
                        _json(row.get("raw_json", row.get("features", row))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def upsert_order_block_zones(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO order_block_zones(detected_at, symbol, timeframe, direction, zone_low, zone_high,
                        origin_timestamp, confirmed_at, strength, displacement_pct, displacement_atr, volume_ratio,
                        break_of_structure, fair_value_gap, retest_count, mitigated, invalidated, age_bars, source,
                        features_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, direction, origin_timestamp) DO UPDATE SET
                        detected_at=excluded.detected_at,
                        zone_low=excluded.zone_low,
                        zone_high=excluded.zone_high,
                        confirmed_at=excluded.confirmed_at,
                        strength=excluded.strength,
                        displacement_pct=excluded.displacement_pct,
                        displacement_atr=excluded.displacement_atr,
                        volume_ratio=excluded.volume_ratio,
                        break_of_structure=excluded.break_of_structure,
                        fair_value_gap=excluded.fair_value_gap,
                        retest_count=excluded.retest_count,
                        mitigated=excluded.mitigated,
                        invalidated=excluded.invalidated,
                        age_bars=excluded.age_bars,
                        source=excluded.source,
                        features_json=excluded.features_json
                    """,
                    (
                        utc_iso(row.get("detected_at", utc_now())),
                        str(row["symbol"]).upper(),
                        row["timeframe"],
                        row["direction"],
                        row["zone_low"],
                        row["zone_high"],
                        utc_iso(row["origin_timestamp"]),
                        utc_iso(row["confirmed_at"]),
                        row.get("strength"),
                        row.get("displacement_pct"),
                        row.get("displacement_atr"),
                        row.get("volume_ratio"),
                        1 if row.get("break_of_structure") else 0,
                        1 if row.get("fair_value_gap") else 0,
                        row.get("retest_count"),
                        1 if row.get("mitigated") else 0,
                        1 if row.get("invalidated") else 0,
                        row.get("age_bars"),
                        row.get("source"),
                        _json(row.get("features_json", row.get("features"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def upsert_option_snapshots(self, records: Any) -> int:
        rows = _records(records)
        if not rows:
            return 0
        with self.conn:
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO option_snapshots(timestamp, underlying_symbol, underlying_price, contract_symbol,
                        option_type, expiration_date, strike_price, days_to_expiration, bid_price, ask_price, bid_size,
                        ask_size, midpoint, spread_pct, last_trade_price, last_trade_size, last_trade_timestamp,
                        implied_volatility, delta, gamma, theta, vega, quote_age_seconds, feed, source, raw_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contract_symbol, timestamp) DO UPDATE SET
                        underlying_price=excluded.underlying_price,
                        bid_price=excluded.bid_price,
                        ask_price=excluded.ask_price,
                        bid_size=excluded.bid_size,
                        ask_size=excluded.ask_size,
                        midpoint=excluded.midpoint,
                        spread_pct=excluded.spread_pct,
                        last_trade_price=excluded.last_trade_price,
                        last_trade_size=excluded.last_trade_size,
                        last_trade_timestamp=excluded.last_trade_timestamp,
                        implied_volatility=excluded.implied_volatility,
                        delta=excluded.delta,
                        gamma=excluded.gamma,
                        theta=excluded.theta,
                        vega=excluded.vega,
                        quote_age_seconds=excluded.quote_age_seconds,
                        feed=excluded.feed,
                        source=excluded.source,
                        raw_json=excluded.raw_json
                    """,
                    (
                        utc_iso(row["timestamp"]),
                        str(row["underlying_symbol"]).upper(),
                        row.get("underlying_price"),
                        row["contract_symbol"],
                        row["option_type"],
                        str(row["expiration_date"]),
                        row["strike_price"],
                        row.get("days_to_expiration"),
                        row.get("bid_price"),
                        row.get("ask_price"),
                        row.get("bid_size"),
                        row.get("ask_size"),
                        row.get("midpoint"),
                        row.get("spread_pct"),
                        row.get("last_trade_price"),
                        row.get("last_trade_size"),
                        utc_iso(row["last_trade_timestamp"]) if row.get("last_trade_timestamp") else None,
                        row.get("implied_volatility"),
                        row.get("delta"),
                        row.get("gamma"),
                        row.get("theta"),
                        row.get("vega"),
                        row.get("quote_age_seconds"),
                        row.get("feed"),
                        row.get("source"),
                        _json(row.get("raw_json", row.get("raw"))),
                        utc_iso(row.get("created_at", utc_now())),
                    ),
                )
        return len(rows)

    def insert_options_intelligence(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO options_intelligence(timestamp, underlying_symbol, underlying_price, contracts_analyzed,
                    options_bias, confidence, call_put_activity_ratio, call_aggressive_flow, put_aggressive_flow,
                    atm_iv, put_call_iv_skew, expected_move_pct, options_liquidity_score, options_event_risk,
                    score_adjustment, stale, reason, feed, source, features_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["underlying_symbol"]).upper(),
                    record.get("underlying_price"),
                    record.get("contracts_analyzed"),
                    record.get("options_bias"),
                    record.get("confidence"),
                    record.get("call_put_activity_ratio"),
                    record.get("call_aggressive_flow"),
                    record.get("put_aggressive_flow"),
                    record.get("atm_iv"),
                    record.get("put_call_iv_skew"),
                    record.get("expected_move_pct"),
                    record.get("options_liquidity_score"),
                    record.get("options_event_risk"),
                    record.get("score_adjustment"),
                    1 if record.get("stale") else 0,
                    record.get("reason"),
                    record.get("feed"),
                    record.get("source"),
                    _json(record.get("features_json", record.get("features"))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def get_latest_options_intelligence(
        self,
        *,
        max_age_seconds: int | None = None,
        now: datetime | str | None = None,
        underlying_symbol: str = "GLD",
    ) -> dict[str, Any] | None:
        params: list[Any] = [underlying_symbol.upper()]
        where = "WHERE underlying_symbol = ?"
        if max_age_seconds is not None:
            cutoff = ensure_utc(now or utc_now()) - timedelta(seconds=max_age_seconds)
            where += " AND timestamp >= ?"
            params.append(utc_iso(cutoff))
        row = self.conn.execute(
            f"SELECT * FROM options_intelligence {where} ORDER BY timestamp DESC, id DESC LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None

    def insert_outcome_label(self, record: Mapping[str, Any]) -> int:
        decision_source = str(record.get("decision_source") or "signal")
        decision_id = record.get("decision_id", record.get("signal_id"))
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO outcome_labels(signal_id, journal_id, decision_source, decision_id,
                    timestamp, symbol, decision, label, label_1m, label_3m, label_5m, label_15m,
                    entry_price, price_source, forward_return_1m, forward_return_3m, forward_return_5m,
                    forward_return_15m, forward_return_30m, forward_return_1h,
                    max_favorable_excursion, max_adverse_excursion, stop_would_hit, target_would_hit, false_break,
                    proper_break, missed_opportunity, best_exit_minutes, worst_drawdown_before_profit,
                    realized_spread_cost, net_outcome_after_costs, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("signal_id"),
                    record.get("journal_id"),
                    decision_source,
                    decision_id,
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("decision"),
                    record.get("label"),
                    record.get("label_1m"),
                    record.get("label_3m"),
                    record.get("label_5m"),
                    record.get("label_15m"),
                    record.get("entry_price"),
                    record.get("price_source"),
                    record.get("forward_return_1m"),
                    record.get("forward_return_3m"),
                    record.get("forward_return_5m"),
                    record.get("forward_return_15m"),
                    record.get("forward_return_30m"),
                    record.get("forward_return_1h"),
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    1 if record.get("stop_would_hit") else 0,
                    1 if record.get("target_would_hit") else 0,
                    1 if record.get("false_break") else 0,
                    1 if record.get("proper_break") else 0,
                    1 if record.get("missed_opportunity") else 0,
                    record.get("best_exit_minutes"),
                    record.get("worst_drawdown_before_profit"),
                    record.get("realized_spread_cost"),
                    record.get("net_outcome_after_costs"),
                    _json(record.get("raw_json", record.get("features"))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_outcome_label(self, record: Mapping[str, Any]) -> tuple[int, bool]:
        decision_source = str(record.get("decision_source") or "signal")
        decision_id = record.get("decision_id", record.get("signal_id"))
        if decision_id is None:
            return self.insert_outcome_label(record), True
        existing = self.conn.execute(
            """
            SELECT id
            FROM outcome_labels
            WHERE decision_source = ? AND decision_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (decision_source, decision_id),
        ).fetchone()
        if existing is None:
            return self.insert_outcome_label({**dict(record), "decision_source": decision_source, "decision_id": decision_id}), True
        with self.conn:
            self.conn.execute(
                """
                UPDATE outcome_labels
                SET signal_id = COALESCE(?, signal_id),
                    journal_id = COALESCE(?, journal_id),
                    timestamp = ?, symbol = ?, decision = ?, label = ?,
                    label_1m = ?, label_3m = ?, label_5m = ?, label_15m = ?,
                    entry_price = ?, price_source = ?,
                    forward_return_1m = ?, forward_return_3m = ?, forward_return_5m = ?,
                    forward_return_15m = ?, forward_return_30m = COALESCE(?, forward_return_30m),
                    forward_return_1h = COALESCE(?, forward_return_1h),
                    max_favorable_excursion = ?, max_adverse_excursion = ?,
                    stop_would_hit = ?, target_would_hit = ?, false_break = ?, proper_break = ?,
                    missed_opportunity = ?, best_exit_minutes = COALESCE(?, best_exit_minutes),
                    worst_drawdown_before_profit = COALESCE(?, worst_drawdown_before_profit),
                    realized_spread_cost = ?, net_outcome_after_costs = ?, raw_json = ?
                WHERE id = ?
                """,
                (
                    record.get("signal_id"),
                    record.get("journal_id"),
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record.get("decision"),
                    record.get("label"),
                    record.get("label_1m"),
                    record.get("label_3m"),
                    record.get("label_5m"),
                    record.get("label_15m"),
                    record.get("entry_price"),
                    record.get("price_source"),
                    record.get("forward_return_1m"),
                    record.get("forward_return_3m"),
                    record.get("forward_return_5m"),
                    record.get("forward_return_15m"),
                    record.get("forward_return_30m"),
                    record.get("forward_return_1h"),
                    record.get("max_favorable_excursion"),
                    record.get("max_adverse_excursion"),
                    1 if record.get("stop_would_hit") else 0,
                    1 if record.get("target_would_hit") else 0,
                    1 if record.get("false_break") else 0,
                    1 if record.get("proper_break") else 0,
                    1 if record.get("missed_opportunity") else 0,
                    record.get("best_exit_minutes"),
                    record.get("worst_drawdown_before_profit"),
                    record.get("realized_spread_cost"),
                    record.get("net_outcome_after_costs"),
                    _json(record.get("raw_json", record.get("features"))),
                    int(existing["id"]),
                ),
            )
        return int(existing["id"]), False

    def insert_playbook_evaluation(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO playbook_evaluations(timestamp, symbol, playbook, direction, score, allowed,
                    block_reason, expected_hold_minutes, target_r_multiple, feature_snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record["playbook"],
                    record.get("direction"),
                    record.get("score"),
                    1 if record.get("allowed") else 0,
                    record.get("block_reason"),
                    record.get("expected_hold_minutes"),
                    record.get("target_r_multiple"),
                    _json(record.get("feature_snapshot_json", record.get("features"))),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_model_promotion_audit(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO model_promotion_audits(timestamp, model_version, candidate_metrics_json,
                    benchmark_metrics_json, walk_forward_json, promoted, promotion_reason, minimum_rules_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())),
                    record.get("model_version"),
                    _json(record.get("candidate_metrics")),
                    _json(record.get("benchmark_metrics")),
                    _json(record.get("walk_forward")),
                    1 if record.get("promoted") else 0,
                    record.get("promotion_reason"),
                    _json(record.get("minimum_rules")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def upsert_knowledge_artifact(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO knowledge_artifacts(path, title, artifact_type, sha256, summary, tags_json, created_at, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    title=excluded.title,
                    artifact_type=excluded.artifact_type,
                    sha256=excluded.sha256,
                    summary=excluded.summary,
                    tags_json=excluded.tags_json,
                    indexed_at=excluded.indexed_at
                """,
                (
                    str(record["path"]),
                    record.get("title"),
                    record.get("artifact_type"),
                    record.get("sha256"),
                    record.get("summary"),
                    _json(record.get("tags")),
                    utc_iso(record.get("created_at", utc_now())),
                    utc_iso(record.get("indexed_at", utc_now())) if record.get("indexed_at", utc_now()) else None,
                ),
            )
        return int(cursor.lastrowid)

    def insert_data_source_run(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO data_source_runs(timestamp, source, data_type, start_time, end_time, status,
                    rows_inserted, message, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record.get("timestamp", utc_now())),
                    record["source"],
                    record["data_type"],
                    utc_iso(record["start_time"]) if record.get("start_time") else None,
                    utc_iso(record["end_time"]) if record.get("end_time") else None,
                    record.get("status"),
                    int(record.get("rows_inserted") or 0),
                    record.get("message"),
                    _json(record.get("details")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_macro_context(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO macro_context(timestamp, horizon, gold_news_sentiment, usd_sentiment,
                    fed_rate_sentiment, risk_off_sentiment, headline_event_risk, sentiment_alignment,
                    macro_bias, macro_confidence, news_linked_fraction, advisory_only,
                    positive_developments_json, potential_concerns_json,
                    forecast_summary, source, raw_inputs_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record.get("horizon", "daily"),
                    record.get("gold_news_sentiment"),
                    record.get("usd_sentiment"),
                    record.get("fed_rate_sentiment"),
                    record.get("risk_off_sentiment"),
                    record.get("headline_event_risk"),
                    record.get("sentiment_alignment"),
                    record.get("macro_bias"),
                    record.get("macro_confidence"),
                    record.get("news_linked_fraction"),
                    1 if record.get("advisory_only", True) else 0,
                    _json(record.get("positive_developments")),
                    _json(record.get("potential_concerns")),
                    record.get("forecast_summary"),
                    record.get("source", "local_macro_context"),
                    _json(record.get("raw_inputs")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def get_latest_macro_context(self, *, max_age_minutes: int | None = None, now: datetime | str | None = None) -> dict[str, Any] | None:
        params: list[Any] = []
        where = ""
        if max_age_minutes is not None:
            reference = ensure_utc(now or utc_now())
            cutoff = reference - timedelta(minutes=max_age_minutes)
            where = "WHERE timestamp >= ?"
            params.append(utc_iso(cutoff))
        row = self.conn.execute(
            f"SELECT * FROM macro_context {where} ORDER BY timestamp DESC, id DESC LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None

    def insert_llm_review(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO llm_reviews(timestamp, review_type, summary, bull_case, bear_case, risk_critique,
                    execution_critique, journal_review, recommendations_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record.get("review_type", "coach"),
                    record.get("summary"),
                    record.get("bull_case"),
                    record.get("bear_case"),
                    record.get("risk_critique"),
                    record.get("execution_critique"),
                    record.get("journal_review"),
                    _json(record.get("recommendations")),
                    _json(record.get("evidence")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_llm_training_advice(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO llm_training_advice(timestamp, provider, model, summary,
                    feature_recommendations_json, labeling_recommendations_json, training_actions_json,
                    risk_warnings_json, candidate_notes_json, raw_response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record.get("provider"),
                    record.get("model"),
                    record.get("summary"),
                    _json(record.get("feature_recommendations")),
                    _json(record.get("labeling_recommendations")),
                    _json(record.get("training_actions")),
                    _json(record.get("risk_warnings")),
                    _json(record.get("candidate_notes")),
                    _json(record.get("raw_response")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def insert_llm_signal_label(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR REPLACE INTO llm_signal_labels(signal_id, timestamp, symbol, suggested_label,
                    confidence, rationale, provider, model, raw_response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("signal_id"),
                    utc_iso(record["timestamp"]),
                    str(record["symbol"]).upper(),
                    record["suggested_label"],
                    record.get("confidence"),
                    record.get("rationale"),
                    record.get("provider"),
                    record.get("model"),
                    _json(record.get("raw_response")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def fetch_signals_for_llm_labeling(self, *, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT s.*
            FROM signals s
            WHERE NOT EXISTS (
                SELECT 1 FROM llm_signal_labels l
                WHERE l.signal_id = s.id
            )
            ORDER BY s.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_rl_experiment(self, record: Mapping[str, Any]) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO rl_experiments(timestamp, experiment_name, policy_name, train_start, train_end,
                    test_start, test_end, total_reward, total_trades, win_rate, max_drawdown,
                    benchmark_reward, promoted, promotion_reason, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_iso(record["timestamp"]),
                    record["experiment_name"],
                    record.get("policy_name"),
                    utc_iso(record["train_start"]) if record.get("train_start") else None,
                    utc_iso(record["train_end"]) if record.get("train_end") else None,
                    utc_iso(record["test_start"]) if record.get("test_start") else None,
                    utc_iso(record["test_end"]) if record.get("test_end") else None,
                    record.get("total_reward"),
                    record.get("total_trades"),
                    record.get("win_rate"),
                    record.get("max_drawdown"),
                    record.get("benchmark_reward"),
                    1 if record.get("promoted") else 0,
                    record.get("promotion_reason"),
                    _json(record.get("metrics")),
                    utc_iso(record.get("created_at", utc_now())),
                ),
            )
        return int(cursor.lastrowid)

    def fetch_recent_table_rows(self, table: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if table not in set(DATA_TABLES):
            raise ValueError(f"Unsupported table: {table}")
        rows = self.conn.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def log_event(self, level: str, module: str, event_type: str, message: str, details: Any | None = None) -> int:
        try:
            with self.conn:
                cursor = self.conn.execute(
                    """
                    INSERT INTO system_logs(timestamp, level, module, event_type, message, details_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (utc_iso(utc_now()), level, module, event_type, message, _json(details or {})),
                )
            return int(cursor.lastrowid)
        except sqlite3.Error:
            return 0

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [symbol.upper(), timeframe]
        where = "WHERE symbol = ? AND timeframe = ?"
        if start is not None:
            where += " AND timestamp >= ?"
            params.append(utc_iso(start))
        if end is not None:
            where += " AND timestamp <= ?"
            params.append(utc_iso(end))
        sql = f"SELECT * FROM bars {where} ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def fetch_latest_bars(self, symbol: str, timeframe: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC
            """,
            (symbol.upper(), timeframe, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_rows(self, table: str) -> int:
        if table not in set(DATA_TABLES):
            raise ValueError(f"Unsupported table: {table}")
        return int(self.conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])

    def clear_all_data(self) -> dict[str, int]:
        deleted: dict[str, int] = {}
        with self.conn:
            for table in DATA_TABLES:
                before = self.count_rows(table)
                self.conn.execute(f"DELETE FROM {table}")
                deleted[table] = before
            placeholders = ",".join("?" for _ in DATA_TABLES)
            self.conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", DATA_TABLES)
        return deleted
