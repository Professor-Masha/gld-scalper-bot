from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.execution_safety import (
    EntryBlockedError,
    ExecutionSafetyState,
    ExecutionSafetySupervisor,
    OrderIntentCoordinator,
)


class _NotFound(Exception):
    status_code = 404


class FakeSafetyClient:
    def __init__(self, *, minutes_to_close: int = 120) -> None:
        self.positions: list[object] = []
        self.orders: list[object] = []
        self.submitted_by_client_id: dict[str, object] = {}
        self.submit_calls = 0
        self.cancel_calls: list[str] = []
        self.cancel_failures = 0
        self.close_calls: list[str] = []
        self.minutes_to_close = minutes_to_close

    def get_all_positions(self):
        return list(self.positions)

    def get_orders(self, filter=None):
        return list(self.orders)

    def get_clock(self):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(is_open=True, next_close=now + timedelta(minutes=self.minutes_to_close))

    def get_order_by_client_id(self, client_order_id):
        if client_order_id not in self.submitted_by_client_id:
            raise _NotFound(client_order_id)
        return self.submitted_by_client_id[client_order_id]

    def get_order_by_id(self, order_id):
        for order in self.orders:
            if str(order.id) == str(order_id):
                return order
        raise _NotFound(order_id)

    def submit_order(self, order_data):
        self.submit_calls += 1
        order = SimpleNamespace(
            id=f"entry-{self.submit_calls}",
            client_order_id=order_data.client_order_id,
            symbol="GLD",
            status="new",
        )
        self.submitted_by_client_id[order.client_order_id] = order
        self.orders.append(order)
        return order

    def cancel_order_by_id(self, order_id):
        if self.cancel_failures:
            self.cancel_failures -= 1
            raise RuntimeError("temporary cancel failure")
        self.cancel_calls.append(str(order_id))
        self.orders = [item for item in self.orders if str(item.id) != str(order_id)]

    def close_position(self, symbol):
        self.close_calls.append(str(symbol))
        self.positions = []
        return SimpleNamespace(id=f"close-{len(self.close_calls)}", status="accepted", symbol=symbol)

    def replace_order_by_id(self, order_id, order_data):
        return SimpleNamespace(id=order_id, status="replaced")


def _settings(tmp_path, **overrides):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'safety.db'}",
        execution_reconcile_interval_seconds=1,
        execution_intent_timeout_seconds=5,
        execution_cancel_wait_seconds=2,
        execution_shutdown_timeout_seconds=2,
        execution_direction_switch_cooldown_seconds=1,
        **overrides,
    )


def test_coordinator_deduplicates_entry_by_client_order_id(tmp_path):
    settings = _settings(tmp_path)
    Database(settings=settings).init_db()
    client = FakeSafetyClient()
    coordinator = OrderIntentCoordinator(settings, client)
    request = SimpleNamespace(client_order_id="GLD-IDEMPOTENT-LONG")

    first = coordinator.submit_entry(
        request,
        client_order_id=request.client_order_id,
        direction="LONG",
        episode_id="GLD-IDEMPOTENT-LONG",
    )
    second = coordinator.submit_entry(
        request,
        client_order_id=request.client_order_id,
        direction="LONG",
        episode_id="GLD-IDEMPOTENT-LONG",
    )
    coordinator.stop()

    assert first is second
    assert client.submit_calls == 1
    db = Database(settings=settings)
    assert db.count_rows("order_intents") == 1


def test_startup_flattens_unprotected_residual_position_before_entries(tmp_path):
    settings = _settings(tmp_path)
    Database(settings=settings).init_db()
    client = FakeSafetyClient()
    client.positions = [SimpleNamespace(symbol="GLD", qty="-1", side="short")]
    coordinator = OrderIntentCoordinator(settings, client)
    supervisor = ExecutionSafetySupervisor(settings, client, coordinator)

    snapshot = supervisor.startup()

    assert snapshot.consistent
    assert snapshot.broker_position_qty == 0
    assert client.close_calls == ["GLD"]
    supervisor.state.assert_entry_allowed()
    supervisor.stop(flatten=False)
    coordinator.stop()


def test_close_window_freezes_new_entries(tmp_path):
    settings = _settings(tmp_path)
    Database(settings=settings).init_db()
    client = FakeSafetyClient(minutes_to_close=12)
    coordinator = OrderIntentCoordinator(settings, client)
    supervisor = ExecutionSafetySupervisor(settings, client, coordinator)

    supervisor.startup()

    with pytest.raises(EntryBlockedError, match="session_close_window"):
        coordinator.prepare_entry("LONG")
    supervisor.stop(flatten=False)
    coordinator.stop()


def test_direction_switch_cancels_orders_flattens_and_reconciles(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    db.create_execution_episode(
        {
            "episode_id": "GLD-OLD-LONG",
            "symbol": "GLD",
            "direction": "LONG",
            "source": "minute",
            "planned_qty": 1,
        }
    )
    db.record_execution_episode_order(
        "GLD-OLD-LONG",
        {
            "order_key": "GLD-OLD-LONG",
            "client_order_id": "GLD-OLD-LONG",
            "role": "entry",
            "intent_type": "entry",
            "qty": 1,
            "filled_qty": 1,
            "status": "filled",
        },
    )
    client = FakeSafetyClient()
    client.positions = [SimpleNamespace(symbol="GLD", qty="1", side="long")]
    client.orders = [
        SimpleNamespace(
            id="stop-1",
            client_order_id="GLD-OLD-LONG-STOP",
            symbol="GLD",
            status="new",
            type="stop",
            position_intent="sell_to_close",
        )
    ]
    coordinator = OrderIntentCoordinator(settings, client)
    _supervisor = ExecutionSafetySupervisor(settings, client, coordinator)

    coordinator.prepare_entry("SHORT")

    assert client.cancel_calls == ["stop-1"]
    assert client.close_calls == ["GLD"]
    assert db.fetch_active_execution_episodes("GLD") == []
    coordinator.stop()


def test_stuck_root_order_is_requeried_and_canceled(tmp_path):
    settings = _settings(tmp_path, execution_order_state_timeout_seconds=10)
    Database(settings=settings).init_db()
    client = FakeSafetyClient()
    client.orders = [
        SimpleNamespace(
            id="stuck-1",
            client_order_id="GLD-STUCK-LONG",
            symbol="GLD",
            status="new",
            submitted_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            legs=[],
        )
    ]
    coordinator = OrderIntentCoordinator(settings, client)
    supervisor = ExecutionSafetySupervisor(settings, client, coordinator)

    supervisor._recover_stuck_orders()

    assert client.cancel_calls == ["stuck-1"]
    coordinator.stop()


def test_circuit_breaker_latches_after_configured_failures(tmp_path):
    settings = _settings(tmp_path, execution_broker_rejection_threshold=2)
    state = ExecutionSafetyState(settings)

    assert not state.record_failure("broker_rejection", "first")
    assert state.record_failure("broker_rejection", "second")

    with pytest.raises(EntryBlockedError, match="circuit breaker open"):
        state.assert_entry_allowed()


def test_failed_cancel_can_retry_without_weakening_entry_idempotency(tmp_path):
    settings = _settings(tmp_path, execution_broker_rejection_threshold=3)
    Database(settings=settings).init_db()
    client = FakeSafetyClient()
    client.cancel_failures = 1
    client.orders = [SimpleNamespace(id="cancel-1", symbol="GLD")]
    coordinator = OrderIntentCoordinator(settings, client)

    with pytest.raises(RuntimeError, match="temporary cancel failure"):
        coordinator.cancel_order("cancel-1", reason="test")
    coordinator.cancel_order("cancel-1", reason="test")
    coordinator.stop()

    assert client.cancel_calls == ["cancel-1"]
    db = Database(settings=settings)
    assert db.count_rows("order_intents") == 2


def test_episode_accounting_closes_atomically_after_exit_fill(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings=settings)
    db.init_db()
    db.create_execution_episode(
        {
            "episode_id": "GLD-ATOMIC-LONG",
            "symbol": "GLD",
            "direction": "LONG",
            "source": "minute",
            "planned_qty": 2,
        }
    )
    db.record_execution_episode_order(
        "GLD-ATOMIC-LONG",
        {
            "order_key": "entry",
            "role": "entry",
            "intent_type": "entry",
            "qty": 2,
            "filled_qty": 2,
            "status": "filled",
        },
    )
    db.record_execution_episode_order(
        "GLD-ATOMIC-LONG",
        {
            "order_key": "exit",
            "role": "take_profit",
            "intent_type": "exit",
            "qty": 2,
            "filled_qty": 2,
            "status": "filled",
        },
    )

    episode = db.conn.execute(
        "SELECT * FROM execution_episodes WHERE episode_id='GLD-ATOMIC-LONG'"
    ).fetchone()
    assert episode["status"] == "closed"
    assert episode["filled_qty"] == 2
    assert episode["remaining_qty"] == 0
    assert episode["version"] >= 3
