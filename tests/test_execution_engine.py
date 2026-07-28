from types import SimpleNamespace

import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.execution_engine import ExecutionEngine
from gld_scalper.models import OrderPlan


class _PaperClient:
    def __init__(self) -> None:
        self.open_orders = []
        self.positions = []

    def get_all_positions(self):
        return list(self.positions)

    def get_orders(self, filter=None):
        return list(self.open_orders)

    def submit_order(self, order_data):
        order = SimpleNamespace(
            id=f"order-{len(self.open_orders) + 1}",
            status="accepted",
            symbol=order_data.symbol,
            client_order_id=order_data.client_order_id,
            parent_order_id=None,
        )
        self.open_orders.append(order)
        return order


def test_submission_gate_prevents_minute_and_fast_paths_from_overlapping(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data' / 'paper' / 'execution.db'}")
    database = Database(settings=settings)
    database.init_db()
    client = _PaperClient()
    engine = ExecutionEngine(settings, database, trading_client=client)
    plan = OrderPlan(
        symbol="GLD",
        direction="LONG",
        side="buy",
        qty=3,
        target_notional=900.0,
        estimated_notional=900.0,
        entry_limit_price=300.0,
        take_profit_price=300.3,
        stop_loss_price=299.76,
        reason="paper learning probe",
    )

    engine.submit_entry_with_protection(plan)

    plan.client_order_id = None
    with pytest.raises(RuntimeError, match="open order already exists"):
        engine.submit_entry_with_protection(plan)


def test_paper_learning_gate_allows_same_direction_tranches_and_enforces_cap(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data' / 'paper' / 'tranches.db'}",
        paper_learning_mode=True,
        enable_partial_profit_tranches=False,
        paper_learning_max_concurrent_trades=3,
        paper_learning_max_aggregate_notional=3_000.0,
    )
    database = Database(settings=settings)
    database.init_db()
    client = _PaperClient()
    engine = ExecutionEngine(settings, database, trading_client=client)

    for _ in range(3):
        engine.submit_entry_with_protection(_plan("LONG"))

    summary = database.active_trade_episode_summary("GLD")
    assert summary["count"] == 3
    assert summary["direction"] == "LONG"
    with pytest.raises(RuntimeError, match="concurrent tranche limit reached"):
        engine.submit_entry_with_protection(_plan("LONG"))


def test_paper_learning_gate_rejects_opposite_direction_tranche(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data' / 'paper' / 'direction.db'}",
        paper_learning_mode=True,
        enable_partial_profit_tranches=False,
        paper_learning_max_concurrent_trades=5,
        paper_learning_max_aggregate_notional=5_000.0,
    )
    database = Database(settings=settings)
    database.init_db()
    client = _PaperClient()
    engine = ExecutionEngine(settings, database, trading_client=client)

    engine.submit_entry_with_protection(_plan("LONG"))

    with pytest.raises(RuntimeError, match="opposite-direction tranche rejected"):
        engine.submit_entry_with_protection(_plan("SHORT"))


def _plan(direction: str) -> OrderPlan:
    is_long = direction == "LONG"
    return OrderPlan(
        symbol="GLD",
        direction=direction,
        side="buy" if is_long else "sell",
        qty=3,
        target_notional=900.0,
        estimated_notional=900.0,
        entry_limit_price=300.0,
        take_profit_price=300.3 if is_long else 299.7,
        stop_loss_price=299.76 if is_long else 300.24,
        reason="paper learning tranche",
    )


def test_partial_profit_submission_uses_two_independently_protected_tranches(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data' / 'paper' / 'partial-profit.db'}",
        paper_learning_mode=True,
        enable_partial_profit_tranches=True,
    )
    database = Database(settings=settings)
    database.init_db()
    client = _PaperClient()
    engine = ExecutionEngine(settings, database, trading_client=client)

    submission = engine.submit_entry_with_protection(_plan("LONG"))

    assert [entry.role for entry in submission.entries] == ["take_profit", "runner"]
    assert [entry.qty for entry in submission.entries] == [1, 2]
    assert submission.entries[0].client_order_id.endswith("-TAKE")
    assert submission.entries[1].client_order_id.endswith("-RUN")
    assert submission.entries[1].plan.take_profit_price > submission.entries[0].plan.take_profit_price
    assert database.active_trade_episode_summary("GLD")["count"] == 2
    episode_orders = database.conn.execute(
        """
        SELECT role, intent_type, strategy_path
        FROM execution_episode_orders
        WHERE episode_id = ?
        ORDER BY role, order_key
        """,
        (submission.primary.client_order_id.rsplit("-TAKE", 1)[0],),
    ).fetchall()
    assert len(episode_orders) == 6
    assert sum(row["role"] == "stop" for row in episode_orders) == 2
    assert sum(row["role"] == "take_profit" for row in episode_orders) == 3
    assert all(row["strategy_path"] == "minute" for row in episode_orders)
