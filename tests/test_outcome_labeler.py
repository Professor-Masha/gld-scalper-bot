from datetime import datetime, timedelta, timezone

import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ml.dataset_builder import build_training_records
from gld_scalper.outcome_labeler import MultiHorizonOutcomeLabeler


def _database(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'labels.db'}",
        outcome_label_min_edge_pct=0.0002,
        outcome_label_slippage_pct=0.0001,
    )
    database = Database(settings=settings)
    database.init_db()
    return settings, database


def _features():
    return {
        "spread_pct": 0.0001,
        "liquidity_score": 0.9,
        "playbook": "proper_breakout",
        "proper_break": True,
    }


def _insert_signal(database, timestamp):
    return database.insert_signal(
        {
            "timestamp": timestamp,
            "symbol": "GLD",
            "decision": "NO_TRADE",
            "confidence": 0.7,
            "feature_snapshot_json": _features(),
        }
    )


def test_labels_signal_and_fast_decision_at_all_horizons_from_snapshots(tmp_path):
    settings, database = _database(tmp_path)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    signal_id = _insert_signal(database, timestamp)
    fast_id = database.insert_fast_scalp_decision(
        {
            "timestamp": timestamp,
            "symbol": "GLD",
            "decision": "LONG",
            "allowed": True,
            "spread_pct": 0.0001,
            "feature_snapshot_json": _features(),
        }
    )
    database.upsert_decision_execution(
        {
            "decision_source": "signal",
            "decision_id": signal_id,
            "timestamp": timestamp,
            "symbol": "GLD",
            "strategy_path": "minute",
            "original_action": "NO_TRADE",
            "executed_action": "NO_TRADE",
            "execution_status": "blocked",
            "direction_available": True,
        }
    )
    database.upsert_decision_execution(
        {
            "decision_source": "fast_scalp",
            "decision_id": fast_id,
            "timestamp": timestamp,
            "symbol": "GLD",
            "strategy_path": "fast",
            "playbook": "proper_breakout",
            "original_action": "LONG",
            "executed_action": "LONG",
            "execution_status": "submitted",
            "direction_available": True,
        }
    )
    database.insert_outcome_label(
        {
            "signal_id": signal_id,
            "timestamp": timestamp,
            "symbol": "GLD",
            "decision": "NO_TRADE",
            "label": "no_trade",
            "raw_json": {"origin": "trade_review"},
        }
    )
    for minutes, price in ((0, 100.0), (1, 101.0), (3, 99.0), (5, 100.01), (15, 102.0)):
        database.upsert_price_snapshot(
            {
                "timestamp": timestamp + timedelta(minutes=minutes),
                "symbol": "GLD",
                "midpoint": price,
                "spread_pct": 0.0001,
            }
        )

    result = MultiHorizonOutcomeLabeler(settings, database).label_matured(now=timestamp + timedelta(minutes=20))

    assert result.scanned == 2
    assert result.inserted == 1
    assert result.updated == 1
    assert result.horizon_counts == {"1m": 2, "3m": 2, "5m": 2, "15m": 2}
    assert result.by_source == {"signal": 1, "fast_scalp": 1}
    signal = database.conn.execute(
        "SELECT * FROM outcome_labels WHERE decision_source = 'signal' AND decision_id = ?",
        (signal_id,),
    ).fetchone()
    assert signal["label_1m"] == "long_good"
    assert signal["label_3m"] == "short_good"
    assert signal["label_5m"] == "no_trade"
    assert signal["label_15m"] == "long_good"
    assert signal["forward_return_3m"] == pytest.approx(-0.01)
    assert signal["price_source"] == "price_snapshot_1s"
    assert database.count_rows("outcome_labels") == 2

    records = build_training_records(database, lookback_days=2)
    fast_record = next(record for record in records if record["source"] == "paper_fast")
    assert set(fast_record["outcomes_by_horizon"]) == {1, 3, 5, 15}
    assert fast_record["outcomes_by_horizon"][3]["label"] == "short_good"
    assert MultiHorizonOutcomeLabeler(settings, database).label_matured(
        now=timestamp + timedelta(minutes=20)
    ).scanned == 0


def test_uses_last_completed_one_minute_bar_without_lookahead(tmp_path):
    settings, database = _database(tmp_path)
    timestamp = datetime.now(timezone.utc).replace(second=30, microsecond=0) - timedelta(hours=1)
    minute = timestamp.replace(second=0)
    signal_id = _insert_signal(database, timestamp)
    bar_prices = {
        -1: 100.0,
        0: 101.0,
        2: 99.0,
        4: 100.01,
        14: 102.0,
    }
    database.upsert_bars(
        [
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": minute + timedelta(minutes=offset),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
            }
            for offset, price in bar_prices.items()
        ]
    )

    result = MultiHorizonOutcomeLabeler(settings, database).label_matured(now=timestamp + timedelta(minutes=20))

    assert result.labeled == 1
    row = database.conn.execute(
        "SELECT * FROM outcome_labels WHERE decision_source = 'signal' AND decision_id = ?",
        (signal_id,),
    ).fetchone()
    assert row["entry_price"] == pytest.approx(100.0)
    assert row["forward_return_1m"] == pytest.approx(0.01)
    assert row["forward_return_3m"] == pytest.approx(-0.01)
    assert row["price_source"] == "bar_1m_close"


def test_partial_label_is_not_retried_unless_data_repair_is_requested(tmp_path):
    settings, database = _database(tmp_path)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    _insert_signal(database, timestamp)
    for minutes, price in ((0, 100.0), (1, 101.0)):
        database.upsert_price_snapshot(
            {
                "timestamp": timestamp + timedelta(minutes=minutes),
                "symbol": "GLD",
                "midpoint": price,
                "spread_pct": 0.0001,
            }
        )
    labeler = MultiHorizonOutcomeLabeler(settings, database)

    first = labeler.label_matured(now=timestamp + timedelta(minutes=20))
    normal_retry = labeler.label_matured(now=timestamp + timedelta(minutes=20))
    repair_retry = labeler.label_matured(now=timestamp + timedelta(minutes=20), retry_partial=True)

    assert first.partial == 1
    assert first.complete == 0
    assert normal_retry.scanned == 0
    assert repair_retry.scanned == 1
    assert repair_retry.updated == 1


def test_unavailable_decision_is_audited_without_blocking_future_batches(tmp_path):
    settings, database = _database(tmp_path)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    signal_id = _insert_signal(database, timestamp)
    labeler = MultiHorizonOutcomeLabeler(settings, database)

    first = labeler.label_matured(now=timestamp + timedelta(minutes=20))
    second = labeler.label_matured(now=timestamp + timedelta(minutes=20))

    assert first.unavailable == 1
    assert first.skipped_no_entry_price == 1
    assert first.inserted == 1
    assert second.scanned == 0
    row = database.conn.execute(
        "SELECT label, price_source FROM outcome_labels WHERE decision_source = 'signal' AND decision_id = ?",
        (signal_id,),
    ).fetchone()
    assert row["label"] is None
    assert row["price_source"] == "unavailable_no_entry"
