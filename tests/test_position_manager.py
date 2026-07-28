from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.position_manager import (
    DynamicPositionRuntime,
    ManagedTrade,
    _managed_trades_from_orders,
)


def _episode(now: datetime) -> ManagedTrade:
    return ManagedTrade(
        trade_id="GLD-TEST-LONG-RUN",
        parent_order_id="parent-1",
        symbol="GLD",
        direction="LONG",
        role="runner",
        qty=2,
        entry_time=now,
        entry_price=200.0,
        stop_order_id="stop-1",
        take_profit_order_id="target-1",
        initial_stop_price=199.4,
        current_stop_price=199.4,
        take_profit_price=200.4,
        high_water_price=200.0,
        low_water_price=200.0,
    )


def test_broker_order_discovery_requires_open_protective_legs(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'managed.db'}")
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    stop = SimpleNamespace(id="stop-1", type="stop", status="new", stop_price=199.4)
    target = SimpleNamespace(id="target-1", type="limit", status="new", limit_price=200.4)
    parent = SimpleNamespace(
        id="parent-1",
        client_order_id="GLD-TEST-LONG-RUN",
        symbol="GLD",
        side="buy",
        position_intent="buy_to_open",
        status="filled",
        filled_qty=2,
        filled_avg_price=200.0,
        filled_at=now,
        legs=[target, stop],
    )

    episodes = _managed_trades_from_orders([parent], db)

    assert len(episodes) == 1
    assert episodes[0].direction == "LONG"
    assert episodes[0].role == "runner"
    assert episodes[0].stop_order_id == "stop-1"
    assert episodes[0].take_profit_order_id == "target-1"


def test_broker_order_discovery_rejects_standalone_closing_leg(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'closing.db'}")
    db = Database(settings=settings)
    db.init_db()
    closing_order = SimpleNamespace(
        id="closing-stop-1",
        client_order_id="broker-generated-close-id",
        symbol="GLD",
        side="sell",
        position_intent="sell_to_close",
        status="filled",
        filled_qty=2,
        filled_avg_price=199.4,
        filled_at=datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc),
        legs=[],
    )

    assert _managed_trades_from_orders([closing_order], db) == []


def test_negative_trade_gets_recovery_room_without_invalidation():
    settings = Settings(
        position_trailing_trigger_pct=0.002,
        position_emergency_stop_pct=0.003,
    )
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    episode = _episode(now)
    episode.update_mark(199.8)
    runtime._latest_quote = {"bid_price": 199.79, "ask_price": 199.81}

    action = runtime._next_action(episode, episode.pnl_pct(199.8), 0.0001, now + timedelta(minutes=2))

    assert action is None


def test_negative_trade_exits_only_after_required_invalidation_votes():
    settings = Settings(
        position_invalidation_min_loss_pct=0.00025,
        position_invalidation_min_confidence=0.80,
        position_invalidation_required_votes=2,
    )
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    episode = _episode(now)
    episode.update_mark(199.9)
    runtime._latest_quote = {"bid_price": 199.89, "ask_price": 199.91}

    runtime.update_context({"decision": "SHORT", "confidence": 0.90})
    one_vote = runtime._next_action(episode, episode.pnl_pct(199.9), 0.0001, now + timedelta(minutes=2))
    runtime.update_context({"ml_predicted_direction": "short_good", "ml_confidence": 0.90})
    two_votes = runtime._next_action(episode, episode.pnl_pct(199.9), 0.0001, now + timedelta(minutes=2))

    assert one_vote is None
    assert two_votes is not None
    assert two_votes[0:2] == (
        "request_exit",
        "setup_invalidated: opposite deterministic signal, opposite ML signal",
    )


def test_profitable_trade_arms_breakeven_before_trailing():
    settings = Settings(
        position_trailing_trigger_pct=0.002,
        position_emergency_stop_pct=0.003,
    )
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    episode = _episode(now)
    episode.update_mark(200.12)
    runtime._latest_quote = {"bid_price": 200.11, "ask_price": 200.13}

    action = runtime._next_action(episode, episode.pnl_pct(200.12), 0.0001, now + timedelta(seconds=5))

    assert action == ("replace_stop", "economic_breakeven_profit_lock", 200.06)


def test_profitable_trade_after_max_holding_requests_protected_exit():
    settings = Settings(max_holding_minutes=15)
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 13, 15, 30, tzinfo=timezone.utc)
    episode = _episode(now - timedelta(minutes=16))
    episode.update_mark(200.10)
    runtime._latest_quote = {"bid_price": 200.09, "ask_price": 200.11}

    action = runtime._next_action(episode, episode.pnl_pct(200.10), 0.0001, now)

    assert action == ("request_exit", "max_holding_time_in_profit", 0.0)


def test_price_snapshot_is_compact_one_row_per_second(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'snapshots.db'}")
    db = Database(settings=settings)
    db.init_db()
    runtime = DynamicPositionRuntime(settings)
    now = datetime(2026, 7, 13, 15, 0, 0, 100_000, tzinfo=timezone.utc)
    runtime._latest_quote = {
        "bid_price": 200.0,
        "ask_price": 200.02,
        "received_at": now,
    }
    runtime._latest_trade = {"price": 200.01, "received_at": now}

    runtime._persist_price_snapshot(db, now)
    runtime._persist_price_snapshot(db, now + timedelta(milliseconds=500))
    runtime._persist_price_snapshot(db, now + timedelta(seconds=1))

    assert db.count_rows("price_snapshots") == 2
    first = db.conn.execute("SELECT * FROM price_snapshots ORDER BY timestamp LIMIT 1").fetchone()
    assert first["midpoint"] == 200.01
    assert round(first["spread_pct"], 7) == round(0.02 / 200.01, 7)
