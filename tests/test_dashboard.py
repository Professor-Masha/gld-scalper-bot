from __future__ import annotations

from pathlib import Path

import pytest

from gld_scalper.config import Settings
from gld_scalper.dashboard.app import DashboardService, create_dashboard_app
from gld_scalper.dashboard.analytics import PerformanceAnalytics
from gld_scalper.dashboard.catalog import TransformerCatalog
from gld_scalper.dashboard.job_results import JobResultRepository
from gld_scalper.dashboard.settings_store import EnvFileStore
from gld_scalper.dashboard.telemetry import TelemetryRepository
from gld_scalper.database import Database


def test_env_store_masks_and_preserves_secrets(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("ALPACA_API_KEY=PK-ORIGINAL-1234\nALPACA_SECRET_KEY=secret-value\n", encoding="utf-8")
    store = EnvFileStore(path)

    store.update({"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": "", "ALPACA_DATA_FEED": "iex"})

    raw = store.read()
    public = store.public_settings()
    assert raw["ALPACA_API_KEY"] == "PK-ORIGINAL-1234"
    assert raw["ALPACA_SECRET_KEY"] == "secret-value"
    assert public["alpaca_api_key_hint"] == "PK-O...1234"
    assert "secret-value" not in str(public)
    assert raw["ALPACA_PAPER"] == "true"
    assert raw["BOT_DATA_MODE"] == "paper"


def test_dashboard_command_builder_rejects_paths_outside_project(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    service = DashboardService(tmp_path)
    outside = tmp_path.parent / "outside.joblib"
    outside.write_bytes(b"x")

    with pytest.raises(ValueError, match="inside the project"):
        service._command("ml_loop", {"artifact": str(outside)})


def test_dashboard_builds_allowlisted_paper_command(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    service = DashboardService(tmp_path)
    assert service._command("paper", {"no_retraining": True}) == ["run-paper", "--no-retraining"]


def test_telemetry_reads_performance_without_writing(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'dashboard.db'}")
    database = Database(settings=settings)
    database.init_db()
    database.conn.execute(
        """INSERT INTO trade_outcomes(symbol,direction,entry_time,exit_time,gross_pnl,
                   net_pnl_after_costs,holding_seconds,win_loss)
           VALUES ('GLD','LONG',datetime('now'),datetime('now'),12,9,42,'WIN')"""
    )
    database.conn.commit()
    database.close()

    snapshot = TelemetryRepository(settings.database_path).snapshot()

    assert snapshot["database_available"] is True
    assert snapshot["performance"]["trades"] == 1
    assert snapshot["performance"]["net_pnl"] == 9
    assert snapshot["performance"]["win_rate"] == 1


def test_dashboard_app_is_local_and_serves_expected_routes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    app = create_dashboard_app(tmp_path)
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/api/snapshot" in paths
    assert "/api/market-series" in paths
    assert "/api/analytics" in paths
    assert "/api/transformer/catalog" in paths
    assert "/api/processes/{action}/start" in paths
    assert "/ws/live" in paths
    assert len(app.state.dashboard_token) >= 32


def test_transformer_catalog_and_bounded_batch_command(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    artifact = tmp_path / "data" / "paper" / "ml_training" / "transformer" / "fast.seq"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        '{"scope":"fast_microstructure","sample_count":120,"sequence_length":60,"feature_count":12}',
        encoding="utf-8",
    )
    catalog = TransformerCatalog(tmp_path).payload()
    command = DashboardService(tmp_path)._command(
        "transformer_batch",
        {"artifacts": ["fast_microstructure=data/paper/ml_training/transformer/fast.seq"]},
    )
    assert catalog["artifacts"][0]["sample_count"] == 120
    assert "--maximum-cycles" in command
    assert "--watch" not in command


def test_analytics_and_job_result_are_chart_ready(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'analytics.db'}")
    database = Database(settings=settings)
    database.init_db()
    database.insert_trade_outcome({
        "trade_id": "episode-1", "symbol": "GLD", "direction": "LONG",
        "gross_pnl": 10, "net_pnl_after_costs": 7, "spread_cost": 2,
        "slippage_cost": 1, "holding_seconds": 90, "playbook": "pullback",
        "regime": "trend", "exit_reason": "trailing_profit",
    })
    database.close()
    analytics = PerformanceAnalytics(settings.database_path).build()
    log_root = tmp_path / "logs" / "dashboard"
    log_root.mkdir(parents=True)
    (log_root / "backtest.log").write_text('starting\n{"net_pnl": 42, "win_rate": 0.6}\n', encoding="utf-8")
    assert analytics["summary"]["net_pnl"] == 7
    assert analytics["breakdowns"]["playbook"][0]["label"] == "pullback"
    assert JobResultRepository(log_root).latest("backtest")["net_pnl"] == 42
