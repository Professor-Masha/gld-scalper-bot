from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.entry_quality import EntryCooldownPolicy, EntryQualityGate
from gld_scalper.performance_tracking import AccountPerformanceTracker, build_fill_cost_context
from gld_scalper.reports.performance_report import performance_breakdown_from_database


def _database(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'phase23.db'}")
    database = Database(settings=settings)
    database.init_db()
    return settings, database


def test_fast_entry_gate_blocks_poor_liquidity_regime(tmp_path):
    settings, _database_handle = _database(tmp_path)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    features = {
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 0.1,
        "trade_age_seconds": 0.1,
        "spread_pct": 0.0001,
        "spread_regime": "tight",
        "spread_stability_score": 0.9,
        "trade_intensity": 3.0,
        "liquidity_score": 0.9,
        "regime": "poor_liquidity",
        "playbook": "spread_capture",
        "playbook_direction": "LONG",
        "playbook_allowed": True,
        "playbook_score": 90,
    }

    result = EntryQualityGate(settings).evaluate(features, strategy_path="fast", now=now)

    assert not result.allowed
    assert "fast strategy disabled in poor_liquidity" in result.reason


def test_post_loss_cooldown_is_independent_per_strategy_path(tmp_path):
    settings, database = _database(tmp_path)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    database.insert_trade_outcome(
        {
            "trade_id": "GLD-FAST-LOSS",
            "symbol": "GLD",
            "direction": "LONG",
            "entry_time": now - timedelta(minutes=2),
            "exit_time": now - timedelta(seconds=30),
            "entry_price": 300,
            "exit_price": 299.9,
            "qty": 1,
            "notional": 300,
            "gross_pnl": -0.1,
            "net_pnl_estimated": -0.11,
            "net_pnl_after_costs": -0.11,
            "strategy_path": "fast",
            "playbook": "spread_capture",
            "regime": "bullish_trend",
        }
    )
    policy = EntryCooldownPolicy(settings, database)

    fast = policy.evaluate(strategy_path="fast", features={"regime": "bullish_trend", "playbook": "spread_capture"}, now=now)
    minute = policy.evaluate(strategy_path="minute", features={"regime": "bullish_trend", "playbook": "spread_capture"}, now=now)

    assert not fast.allowed
    assert minute.allowed


def test_fill_cost_context_uses_quote_and_reports_each_component(tmp_path):
    settings, database = _database(tmp_path)
    settings.estimated_fee_per_share = 0.01
    timestamp = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    with database.conn:
        database.conn.execute(
            """
            INSERT INTO quotes(symbol, timestamp, bid_price, ask_price, spread, spread_pct)
            VALUES ('GLD', ?, 99.99, 100.01, 0.02, 0.0002)
            """,
            (timestamp.isoformat(),),
        )
    database.insert_order(
        {
            "alpaca_order_id": "order-1",
            "client_order_id": "GLD-FAST-COST",
            "symbol": "GLD",
            "side": "buy",
            "limit_price": 100.0,
            "status": "filled",
        }
    )

    result = build_fill_cost_context(
        database,
        settings,
        order_id="order-1",
        client_order_id="GLD-FAST-COST",
        symbol="GLD",
        side="buy",
        qty=10,
        fill_price=100.02,
        fill_time=timestamp,
    )

    assert result.midpoint == 100.0
    assert round(result.slippage_cost, 6) == 0.1
    assert round(result.spread_cost, 6) == 0.1
    assert round(result.estimated_fee, 6) == 0.1
    assert round(result.estimated_live_cost, 6) == 0.3


def test_performance_report_aggregates_partial_tranches_into_root_episode(tmp_path):
    _settings, database = _database(tmp_path)
    entry = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    for suffix, net in (("-TAKE", 3.0), ("-RUN", -1.0)):
        database.insert_trade_outcome(
            {
                "trade_id": f"GLD-ROOT{suffix}",
                "root_episode_id": "GLD-ROOT",
                "symbol": "GLD",
                "direction": "LONG",
                "entry_time": entry,
                "exit_time": entry + timedelta(minutes=3),
                "entry_price": 300,
                "exit_price": 300 + net,
                "qty": 1,
                "notional": 300,
                "gross_pnl": net + 0.1,
                "net_pnl_estimated": net,
                "net_pnl_after_costs": net,
                "estimated_live_cost": 0.1,
                "strategy_path": "minute",
                "playbook": "proper_breakout",
                "regime": "bullish_trend",
                "confidence": 0.8,
                "exit_reason": "take_profit",
            }
        )

    report = performance_breakdown_from_database(database)

    assert report["summary"]["root_episode_count"] == 1
    assert report["summary"]["partial_exit_tranche_count"] == 2
    assert report["summary"]["net_pnl_after_costs"] == 2.0
    assert report["by_strategy_path"]["minute"]["episodes"] == 1
    assert report["summary_scope"] == "combined_normal_and_exploration"
    assert report["normal_strategy_summary"]["root_episode_count"] == 1
    assert report["exploration_summary"]["root_episode_count"] == 0
    assert report["by_evidence_lane"]["normal_strategy"]["episodes"] == 1


class _AccountClient:
    def get_account(self):
        return SimpleNamespace(
            equity="1000500",
            cash="900000",
            buying_power="3600000",
            daytrade_count=2,
            portfolio_value="1000500",
            multiplier="4",
            long_market_value="100500",
            short_market_value="0",
        )

    def get_all_positions(self):
        return [SimpleNamespace(symbol="GLD", unrealized_pl="25")]

    def get_orders(self, filter=None):
        return []


def test_account_tracker_records_equity_unrealized_and_drawdown_baseline(tmp_path):
    settings, database = _database(tmp_path)
    result = AccountPerformanceTracker(settings, database, _AccountClient()).capture("startup", force=True)

    assert result is not None
    assert result["equity"] == 1_000_500
    assert result["unrealized_pl"] == 25
    assert result["drawdown"] == 0
    assert database.count_rows("account_snapshots") == 1
