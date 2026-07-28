from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import sys
import threading

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.utils.logging_utils import SQLiteLogHandler


def test_database_upsert_prevents_duplicate_bars(tmp_path):
    db_path = tmp_path / "test.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()
    bar = {
        "symbol": "GLD",
        "timeframe": "1Min",
        "timestamp": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000,
    }
    db.upsert_bars([bar])
    db.upsert_bars([bar])
    assert db.count_rows("bars") == 1


def test_failed_execution_episode_records_terminal_close_reason(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'failed-episode.db'}")
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    db.create_execution_episode(
        {
            "episode_id": "GLD-FAILED-SUBMISSION",
            "symbol": "GLD",
            "direction": "LONG",
            "source": "minute",
            "planned_qty": 2,
            "opened_at": now,
        }
    )

    db.finalize_execution_episode_submission("GLD-FAILED-SUBMISSION", failed=True)

    row = db.conn.execute(
        "SELECT status, close_reason, closed_at FROM execution_episodes WHERE episode_id = ?",
        ("GLD-FAILED-SUBMISSION",),
    ).fetchone()
    assert row["status"] == "submit_failed"
    assert row["close_reason"] == "submission_failed"
    assert row["closed_at"] is not None


def test_database_serializes_concurrent_thread_writers(tmp_path):
    db_path = tmp_path / "concurrent.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    database = Database(settings=settings)
    database.init_db()
    errors = []

    def write_events(worker_id):
        worker_database = Database(settings=settings)
        try:
            worker_database.init_db()
            for sequence in range(25):
                worker_database.log_event("INFO", "test", "concurrent", "write", {"worker": worker_id, "sequence": sequence})
        except Exception as exc:
            errors.append(exc)
        finally:
            worker_database.close()

    threads = [threading.Thread(target=write_events, args=(worker_id,)) for worker_id in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert database.count_rows("system_logs") == 100


def test_sqlite_log_handler_formats_exception_traceback(tmp_path):
    database = Database(settings=Settings(database_url=f"sqlite:///{tmp_path / 'logging.db'}"))
    database.init_db()
    handler = SQLiteLogHandler(database)
    logger = logging.getLogger("test.sqlite-handler")
    try:
        raise ValueError("expected traceback")
    except ValueError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "logged failure",
            (),
            sys.exc_info(),
        )
    handler.emit(record)

    row = database.conn.execute("SELECT details_json FROM system_logs ORDER BY id DESC LIMIT 1").fetchone()
    details = json.loads(row["details_json"])
    assert "ValueError: expected traceback" in details["exc_info"]


def test_existing_database_migrates_parent_order_column_before_index(tmp_path):
    db_path = tmp_path / "migration.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    database = Database(settings=settings)
    database.init_db()
    database.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_orders_parent_order_id")
        connection.execute("ALTER TABLE orders DROP COLUMN parent_order_id")

    upgraded = Database(settings=settings)
    upgraded.init_db()
    columns = {row["name"] for row in upgraded.conn.execute("PRAGMA table_info(orders)").fetchall()}
    indexes = {row["name"] for row in upgraded.conn.execute("PRAGMA index_list(orders)").fetchall()}
    assert "parent_order_id" in columns
    assert "idx_orders_parent_order_id" in indexes


def test_existing_database_migrates_multi_horizon_outcome_columns_before_index(tmp_path):
    db_path = tmp_path / "outcome-migration.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE outcome_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                journal_id INTEGER,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision TEXT,
                label TEXT,
                forward_return_1m REAL,
                forward_return_5m REAL,
                forward_return_15m REAL,
                forward_return_30m REAL,
                forward_return_1h REAL,
                created_at TEXT
            )
            """
        )
    settings = Settings(database_url=f"sqlite:///{db_path}")
    database = Database(settings=settings)

    database.init_db()

    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(outcome_labels)").fetchall()}
    indexes = {row["name"] for row in database.conn.execute("PRAGMA index_list(outcome_labels)").fetchall()}
    assert {"decision_source", "decision_id", "label_3m", "forward_return_3m"} <= columns
    assert "idx_outcome_labels_decision" in indexes


def test_database_migration_repairs_close_legs_misclassified_as_active_episodes(tmp_path):
    db_path = tmp_path / "close-leg-repair.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    database = Database(settings=settings)
    database.init_db()
    database.insert_order(
        {
            "alpaca_order_id": "close-1",
            "client_order_id": "broker-close-1",
            "symbol": "GLD",
            "side": "sell",
            "position_side": "SHORT",
            "qty": 2,
            "status": "filled",
            "submitted_at": datetime(2026, 7, 13, 17, 3, tzinfo=timezone.utc),
            "raw_json": {"position_intent": "sell_to_close", "type": "stop"},
        }
    )
    assert database.active_trade_episode_summary("GLD")["count"] == 0
    database.close()

    upgraded = Database(settings=settings)
    upgraded.init_db()

    row = upgraded.conn.execute("SELECT position_side FROM orders WHERE alpaca_order_id = 'close-1'").fetchone()
    assert row["position_side"] is None


def test_fetch_latest_bars_returns_newest_in_ascending_order(tmp_path):
    db_path = tmp_path / "latest.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()
    bars = []
    for minute in range(5):
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": datetime(2026, 1, 1, 14, 30 + minute, tzinfo=timezone.utc),
                "open": 100 + minute,
                "high": 101 + minute,
                "low": 99 + minute,
                "close": 100.5 + minute,
                "volume": 1000 + minute,
            }
        )
    db.upsert_bars(bars)
    latest = db.fetch_latest_bars("GLD", "1Min", limit=2)
    assert [row["close"] for row in latest] == [103.5, 104.5]


def test_clear_all_data_removes_collected_rows(tmp_path):
    db_path = tmp_path / "clear.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()
    db.upsert_bars(
        [
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1000,
            }
        ]
    )
    deleted = db.clear_all_data()
    assert deleted["bars"] == 1
    assert db.count_rows("bars") == 0


def test_trading_journal_is_reset_with_collected_data(tmp_path):
    db_path = tmp_path / "journal.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()
    db.insert_trading_journal(
        {
            "timestamp": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
            "symbol": "GLD",
            "event_type": "TRADE_DECISION",
            "decision": "LONG",
            "confidence": 0.9,
            "reason": "test journal",
            "feature_snapshot_json": {"close": 100.0},
        }
    )

    deleted = db.clear_all_data()

    assert deleted["trading_journal"] == 1
    assert db.count_rows("trading_journal") == 0


def test_order_block_and_options_intelligence_round_trip(tmp_path):
    db_path = tmp_path / "market_context.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)

    db.upsert_order_block_zones(
        [
            {
                "detected_at": now,
                "symbol": "GLD",
                "timeframe": "5Min",
                "direction": "bullish",
                "zone_low": 199.5,
                "zone_high": 200.0,
                "origin_timestamp": now - timedelta(minutes=15),
                "confirmed_at": now - timedelta(minutes=5),
                "strength": 0.8,
                "displacement_pct": 0.003,
                "displacement_atr": 1.5,
                "volume_ratio": 1.4,
                "break_of_structure": True,
                "fair_value_gap": False,
                "retest_count": 1,
                "mitigated": True,
                "invalidated": False,
                "age_bars": 1,
                "features": {"test": True},
            }
        ]
    )
    db.upsert_option_snapshots(
        [
            {
                "timestamp": now,
                "underlying_symbol": "GLD",
                "underlying_price": 200.0,
                "contract_symbol": "GLD260717C00200000",
                "option_type": "call",
                "expiration_date": "2026-07-17",
                "strike_price": 200.0,
                "days_to_expiration": 16,
                "bid_price": 2.0,
                "ask_price": 2.1,
                "bid_size": 50,
                "ask_size": 60,
                "midpoint": 2.05,
                "spread_pct": 0.0488,
                "quote_age_seconds": 1.0,
                "feed": "indicative",
            }
        ]
    )
    db.insert_options_intelligence(
        {
            "timestamp": now,
            "underlying_symbol": "GLD",
            "underlying_price": 200.0,
            "contracts_analyzed": 1,
            "options_bias": "bullish",
            "confidence": 0.7,
            "score_adjustment": 1.5,
            "stale": False,
            "reason": "test",
            "feed": "indicative",
            "features": {"options_bias": "bullish"},
        }
    )
    db.insert_trading_journal(
        {
            "timestamp": now,
            "symbol": "GLD",
            "event_type": "TRADE_DECISION",
            "order_block_direction": "bullish",
            "order_block_retest_active": True,
            "options_bias": "bullish",
            "options_confidence": 0.7,
        }
    )

    assert db.count_rows("order_block_zones") == 1
    assert db.count_rows("option_snapshots") == 1
    assert db.get_latest_options_intelligence()["options_bias"] == "bullish"
    journal = db.conn.execute("SELECT * FROM trading_journal LIMIT 1").fetchone()
    assert journal["order_block_direction"] == "bullish"
    assert journal["options_bias"] == "bullish"
