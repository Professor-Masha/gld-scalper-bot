from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.no_trade_learning import MISSED_LONG, MissedOpportunityAnalyzer


def test_missed_opportunity_analyzer_labels_clean_skipped_long(tmp_path):
    db_path = tmp_path / "missed.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        missed_opportunity_horizon_minutes=5,
        missed_opportunity_min_move_pct=0.002,
        missed_opportunity_max_adverse_pct=0.001,
    )
    db = Database(settings=settings)
    db.init_db()
    signal_time = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    signal_id = db.insert_signal(
        {
            "timestamp": signal_time,
            "symbol": "GLD",
            "decision": "NO_TRADE",
            "bullish_score": 60,
            "bearish_score": 20,
            "no_trade_score": 90,
            "regime": "sideways_chop",
            "confidence": 0.6,
            "reason": "wait",
            "feature_snapshot_json": {
                "latest_price": 100.0,
                "pattern_classification": "buildup_resistance",
                "pattern_quality": 0.62,
                "liquidity_score": 0.80,
                "gold_volatility_regime": "normal",
                "agent_reasoning_json": "[]",
            },
        }
    )
    db.upsert_bars(
        [
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": signal_time + timedelta(minutes=idx),
                "open": 100.02 + idx * 0.05,
                "high": 100.08 + idx * 0.06,
                "low": 99.96,
                "close": 100.05 + idx * 0.05,
                "volume": 1500,
            }
            for idx in range(1, 6)
        ]
    )

    counts = MissedOpportunityAnalyzer(settings, db).label_matured_no_trades(signal_time + timedelta(minutes=6))

    assert counts[MISSED_LONG] == 1
    assert db.missed_opportunity_exists(signal_id)
    assert db.count_rows("missed_opportunities") == 1
    assert db.count_rows("trading_journal") == 1


def test_database_migration_adds_journal_context_columns(tmp_path):
    db_path = tmp_path / "journal_columns.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings=settings)
    db.init_db()

    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(trading_journal)").fetchall()}

    assert "pattern_classification" in columns
    assert "liquidity_score" in columns
    assert "reasoning_agents_json" in columns
