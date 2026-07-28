from datetime import datetime, timezone
from types import SimpleNamespace

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.order_reconciler import PaperOrderReconciler


class FakeTradingClient:
    def __init__(self, orders):
        self.orders = orders
        self.last_filter = None

    def get_orders(self, filter=None):
        self.last_filter = filter
        return self.orders


def test_reconciler_records_fills_outcome_and_journal(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'orders.db'}")
    db = Database(settings=settings)
    db.init_db()
    entry_time = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    exit_time = datetime(2026, 1, 1, 14, 35, tzinfo=timezone.utc)
    exit_order = SimpleNamespace(
        id="exit-1",
        parent_order_id=None,
        client_order_id="exit-client",
        symbol="GLD",
        side="sell",
        order_type="limit",
        order_class="bracket",
        time_in_force="day",
        limit_price=101.0,
        stop_price=None,
        status="filled",
        submitted_at=entry_time,
        filled_at=exit_time,
        filled_qty=10,
        filled_avg_price=101.0,
        qty=10,
        notional=None,
        legs=[],
    )
    entry_order = SimpleNamespace(
        id="entry-1",
        parent_order_id=None,
        client_order_id="GLD-TEST-LONG",
        symbol="GLD",
        side="buy",
        order_type="limit",
        order_class="bracket",
        time_in_force="day",
        limit_price=100.0,
        stop_price=None,
        status="filled",
        submitted_at=entry_time,
        filled_at=entry_time,
        filled_qty=10,
        filled_avg_price=100.0,
        qty=10,
        notional=None,
        legs=[exit_order],
    )

    result = PaperOrderReconciler(settings, db, FakeTradingClient([entry_order])).sync(exit_time)

    assert result == {"orders": 2, "fills": 2, "trade_outcomes": 1}
    assert db.count_rows("orders") == 2
    assert db.count_rows("fills") == 2
    assert db.count_rows("trade_outcomes") == 1
    assert db.count_rows("trading_journal") == 4
    assert db.count_rows("trade_reviews") == 1
    child = db.conn.execute("SELECT * FROM orders WHERE alpaca_order_id = 'exit-1'").fetchone()
    assert child["parent_order_id"] == "entry-1"
    assert child["position_side"] is None
    outcome = db.conn.execute("SELECT * FROM trade_outcomes LIMIT 1").fetchone()
    assert outcome["direction"] == "LONG"
    assert outcome["gross_pnl"] == 10.0
    assert outcome["net_pnl_estimated"] == 9.98
    assert outcome["estimated_live_cost"] == 0.02
    assert outcome["exit_reason"] == "take_profit"
    review = db.conn.execute("SELECT * FROM trade_reviews LIMIT 1").fetchone()
    assert review["review_type"] == "GOOD_TRADE"
    assert review["result_label"] == "long_good"


def test_reconciler_keeps_concurrent_brackets_as_separate_learning_episodes(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'concurrent-orders.db'}")
    db = Database(settings=settings)
    db.init_db()
    entry_time = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    exit_time = datetime(2026, 1, 1, 14, 35, tzinfo=timezone.utc)

    def completed_bracket(index: int, entry_price: float, exit_price: float):
        exit_order = SimpleNamespace(
            id=f"exit-{index}",
            parent_order_id=None,
            client_order_id=f"exit-client-{index}",
            symbol="GLD",
            side="sell",
            order_type="limit",
            order_class="bracket",
            time_in_force="day",
            limit_price=exit_price,
            stop_price=None,
            status="filled",
            submitted_at=entry_time,
            filled_at=exit_time,
            filled_qty=5,
            filled_avg_price=exit_price,
            qty=5,
            notional=None,
            legs=[],
        )
        return SimpleNamespace(
            id=f"entry-{index}",
            parent_order_id=None,
            client_order_id=f"GLD-TEST-LONG-{index}",
            symbol="GLD",
            side="buy",
            order_type="limit",
            order_class="bracket",
            time_in_force="day",
            limit_price=entry_price,
            stop_price=None,
            status="filled",
            submitted_at=entry_time,
            filled_at=entry_time,
            filled_qty=5,
            filled_avg_price=entry_price,
            qty=5,
            notional=None,
            legs=[exit_order],
        )

    orders = [completed_bracket(1, 100.0, 101.0), completed_bracket(2, 100.5, 101.5)]
    result = PaperOrderReconciler(settings, db, FakeTradingClient(orders)).sync(exit_time)

    assert result == {"orders": 4, "fills": 4, "trade_outcomes": 2}
    assert db.count_rows("trade_outcomes") == 2
    assert db.count_rows("trade_reviews") == 2
    assert db.count_rows("trading_journal") == 8
    trade_ids = {
        row["trade_id"] for row in db.conn.execute("SELECT trade_id FROM trade_outcomes").fetchall()
    }
    assert trade_ids == {"GLD-TEST-LONG-1", "GLD-TEST-LONG-2"}
    assert db.conn.execute("SELECT COUNT(*) FROM orders WHERE parent_order_id IS NOT NULL").fetchone()[0] == 2


def test_standalone_closing_leg_never_becomes_active_direction_episode(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'closing-leg.db'}")
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 17, 3, tzinfo=timezone.utc)
    closing_leg = SimpleNamespace(
        id="exit-stop-1",
        parent_order_id=None,
        client_order_id="broker-generated-close-id",
        symbol="GLD",
        side="sell",
        position_intent="sell_to_close",
        order_type="stop",
        order_class="bracket",
        time_in_force="day",
        limit_price=None,
        stop_price=366.49,
        status="filled",
        submitted_at=now,
        filled_at=now,
        filled_qty=2,
        filled_avg_price=366.43,
        qty=2,
        notional=None,
        legs=[],
    )

    PaperOrderReconciler(settings, db, FakeTradingClient([closing_leg])).sync(now)

    row = db.conn.execute("SELECT * FROM orders WHERE alpaca_order_id = 'exit-stop-1'").fetchone()
    assert row["position_side"] is None
    assert db.active_trade_episode_summary("GLD") == {
        "count": 0,
        "direction": "",
        "notional": 0,
        "client_order_ids": [],
    }


def test_closed_atomic_episode_materializes_one_root_outcome_with_metadata(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'atomic-outcome.db'}")
    db = Database(settings=settings)
    db.init_db()
    entry_time = datetime(2026, 7, 27, 19, 3, tzinfo=timezone.utc)
    exit_time = datetime(2026, 7, 27, 19, 8, tzinfo=timezone.utc)
    episode_id = "GLD-ATOMIC-PROPER-LONG"
    db.create_execution_episode(
        {
            "episode_id": episode_id,
            "symbol": "GLD",
            "direction": "LONG",
            "source": "minute",
            "strategy_path": "minute",
            "playbook": "proper_breakout",
            "planned_qty": 5,
            "opened_at": entry_time,
        }
    )
    db.record_execution_episode_order(
        episode_id,
        {
            "order_key": "entry-atomic",
            "alpaca_order_id": "entry-atomic",
            "client_order_id": episode_id,
            "role": "entry",
            "intent_type": "entry",
            "strategy_path": "minute",
            "playbook": "proper_breakout",
            "side": "buy",
            "qty": 5,
            "filled_qty": 5,
            "filled_avg_price": 100.0,
            "status": "filled",
            "submitted_at": entry_time,
        },
    )
    db.record_execution_episode_order(
        episode_id,
        {
            "order_key": "close-atomic",
            "alpaca_order_id": "close-atomic",
            "client_order_id": "broker-close-atomic",
            "role": "safety_flatten",
            "intent_type": "exit",
            "strategy_path": "minute",
            "playbook": "proper_breakout",
            "close_reason": "setup_invalidation",
            "side": "sell",
            "qty": 5,
            "filled_qty": 5,
            "filled_avg_price": 100.5,
            "status": "filled",
            "submitted_at": exit_time,
            "updated_at": exit_time,
        },
    )
    db.conn.execute(
        """
        INSERT INTO trade_outcomes(trade_id, symbol, direction, root_episode_id)
        VALUES (?, 'GLD', 'LONG', ?)
        """,
        (f"{episode_id}-LEGACY-TRANCHE", episode_id),
    )
    db.conn.commit()
    entry_order = SimpleNamespace(
        id="entry-atomic",
        parent_order_id=None,
        client_order_id=episode_id,
        symbol="GLD",
        side="buy",
        order_type="limit",
        order_class="bracket",
        time_in_force="day",
        status="filled",
        submitted_at=entry_time,
        filled_at=entry_time,
        filled_qty=5,
        filled_avg_price=100.0,
        qty=5,
        notional=None,
        legs=[],
    )
    close_order = SimpleNamespace(
        id="close-atomic",
        parent_order_id=None,
        client_order_id="broker-close-atomic",
        symbol="GLD",
        side="sell",
        position_intent="sell_to_close",
        order_type="market",
        order_class="simple",
        time_in_force="day",
        status="filled",
        submitted_at=exit_time,
        filled_at=exit_time,
        filled_qty=5,
        filled_avg_price=100.5,
        qty=5,
        notional=None,
        legs=[],
    )

    result = PaperOrderReconciler(
        settings,
        db,
        FakeTradingClient([entry_order, close_order]),
    ).sync(exit_time)

    assert result["trade_outcomes"] == 1
    outcome = db.conn.execute(
        "SELECT * FROM trade_outcomes WHERE trade_id = ?",
        (episode_id,),
    ).fetchone()
    assert outcome["trade_id"] == episode_id
    assert outcome["root_episode_id"] == episode_id
    assert outcome["strategy_path"] == "minute"
    assert outcome["playbook"] == "proper_breakout"
    assert outcome["exit_reason"] == "setup_invalidation"
    assert outcome["gross_pnl"] == 2.5
    episode = db.conn.execute(
        "SELECT * FROM execution_episodes WHERE episode_id = ?",
        (episode_id,),
    ).fetchone()
    assert episode["close_reason"] == "setup_invalidation"
    fills = db.conn.execute(
        "SELECT strategy_path, playbook, episode_id FROM fills ORDER BY timestamp"
    ).fetchall()
    assert all(row["strategy_path"] == "minute" for row in fills)
    assert all(row["playbook"] == "proper_breakout" for row in fills)
    assert all(row["episode_id"] == episode_id for row in fills), [dict(row) for row in fills]
    assert db.count_rows("trade_outcomes") == 2
