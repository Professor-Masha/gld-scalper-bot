from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gld_scalper.config import Settings
from gld_scalper.dashboard.app import DashboardService, create_dashboard_app
from gld_scalper.dashboard.audit import AuditLedger
from gld_scalper.dashboard.contracts import CommandRequest, EventEnvelope
from gld_scalper.dashboard.control_plane import ControlPlane
from gld_scalper.dashboard.analytics import PerformanceAnalytics
from gld_scalper.dashboard.catalog import TransformerCatalog
from gld_scalper.dashboard.job_results import JobResultRepository
from gld_scalper.dashboard.llm_providers import LLMProviderService
from gld_scalper.dashboard.process_manager import ProcessManager
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


def test_llm_provider_activation_preserves_secrets_and_enforces_offline_mode(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("MOONSHOT_API_KEY=existing-secret\n", encoding="utf-8")
    store = EnvFileStore(path)
    service = LLMProviderService(tmp_path, store)

    result = service.activate(
        "kimi",
        base_url="https://api.moonshot.ai/v1",
        model="moonshot-v1-8k",
        api_key="",
    )

    raw = store.read()
    assert result["active_provider"] == "kimi"
    assert raw["MOONSHOT_API_KEY"] == "existing-secret"
    assert raw["LLM_OFFLINE_ONLY"] == "true"
    assert raw["ENABLE_LLM_LIVE_TRADING"] == "false"
    assert "existing-secret" not in str(result)


def test_llm_provider_catalog_distinguishes_engine_from_fingpt(tmp_path: Path) -> None:
    source = tmp_path / "FINGPT" / "FinGPT-1.0.0" / "fingpt" / "FinGPT_RAG"
    source.mkdir(parents=True)
    store = EnvFileStore(tmp_path / ".env")
    store.update({"LLM_PROVIDER": "ollama"})

    payload = LLMProviderService(tmp_path, store).catalog()

    assert {item["id"] for item in payload["providers"]} == {"ollama", "kimi"}
    assert payload["fingpt"]["available"] is True
    assert payload["fingpt"]["reasoning_engine"] == "ollama"
    assert payload["safety"]["live_broker_authority"] is False


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
    assert "/api/llm/providers" in paths
    assert "/api/llm/providers/activate" in paths
    assert "/api/llm/providers/test" in paths
    assert "/api/whitepaper" in paths
    assert "/api/processes/{action}/start" in paths
    assert "/ws/live" in paths
    assert "/api/v1/system/health" in paths
    assert "/api/v1/system/readiness" in paths
    assert "/api/v1/system/status" in paths
    assert "/api/v1/training/jobs" in paths
    assert "/api/v1/audit/events" in paths
    assert "/api/v1/events" in paths
    assert len(app.state.dashboard_token) >= 32


def test_dashboard_accepts_desktop_session_token_from_process_environment(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    token = "desktop-session-token-with-more-than-32-characters"

    with patch.dict("os.environ", {"DASHBOARD_SESSION_TOKEN": token}):
        app = create_dashboard_app(tmp_path)

    assert app.state.dashboard_token == token


def test_dashboard_rejects_weak_desktop_session_token(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")

    with patch.dict("os.environ", {"DASHBOARD_SESSION_TOKEN": "too-short"}):
        with pytest.raises(RuntimeError, match="at least 32"):
            create_dashboard_app(tmp_path)


def test_partial_alpaca_settings_preserve_provider_and_authenticate(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=kimi\nLLM_MODEL=kimi-k2.6\nLLM_BASE_URL=https://api.moonshot.ai/v1\nALPACA_SECRET_KEY=keep-secret\n", encoding="utf-8")
    app = create_dashboard_app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/settings", json={"alpaca_data_feed": "sip"}).status_code == 403
    response = client.post("/api/settings", headers={"X-Dashboard-Token": app.state.dashboard_token}, json={"alpaca_data_feed": "sip", "alpaca_secret_key": ""})
    assert response.status_code == 200
    assert response.json()["llm_provider"] == "kimi"
    assert response.json()["llm_model"] == "kimi-k2.6"
    assert EnvFileStore(env).read()["ALPACA_SECRET_KEY"] == "keep-secret"


def test_job_result_preserves_nested_completed_result(tmp_path: Path) -> None:
    (tmp_path / "backtest.log").write_text('log line\n{"net_pnl": 10, "nested": {"wins": 2}}\n', encoding="utf-8")
    assert JobResultRepository(tmp_path).latest("backtest") == {"net_pnl": 10, "nested": {"wins": 2}}


def test_process_log_tail_reads_only_latest_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "bot.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("\n".join(f"event-{index}" for index in range(5000)), encoding="utf-8")
    manager = ProcessManager(tmp_path, lambda: {})

    assert manager.tail("bot", 45) == [f"event-{index}" for index in range(4955, 5000)]


def test_process_log_tail_caps_pathological_log_reads(tmp_path: Path) -> None:
    log_path = tmp_path / "oversized.log"
    log_path.write_bytes(b"x" * 4096 + b"\nlatest-one\nlatest-two")

    result = ProcessManager._tail_file(log_path, 10, max_bytes=64, chunk_size=16)

    assert result[0].startswith("[dashboard log tail truncated")
    assert result[-2:] == ["latest-one", "latest-two"]


def test_control_plane_audit_is_redacted_hash_chained_and_idempotent(tmp_path: Path) -> None:
    starts: list[tuple[str, dict[str, object]]] = []
    ledger = AuditLedger(tmp_path / "audit.jsonl")

    def start(name: str, options: dict[str, object]) -> dict[str, object]:
        starts.append((name, options))
        return {"name": name, "pid": 4312, "state": "running"}

    plane = ControlPlane(audit=ledger, start=start, stop=lambda name: {"name": name, "state": "stopping"})
    request = CommandRequest(
        command_type="bot.start",
        parameters={"options": {"no_retraining": True, "api_key": "must-not-leak"}},
        idempotency_key="stable-paper-start-key",
    )

    first = plane.execute(request)
    second = plane.execute(request)

    assert first.accepted is True
    assert second.idempotent_replay is True
    assert starts == [("paper", {"no_retraining": True, "api_key": "must-not-leak"})]
    assert ledger.verify()["valid"] is True
    assert ledger.verify()["records"] == 2
    assert "must-not-leak" not in ledger.path.read_text(encoding="utf-8")


def test_event_envelope_has_versioned_traceable_contract() -> None:
    event = EventEnvelope(event_type="decision.created", symbol="GLD", sequence=7, payload={"decision": "NO_TRADE"})

    assert event.version == 1
    assert event.event_id.startswith("evt-")
    assert event.trace_id.startswith("trace-")
    assert event.sequence == 7
    assert event.payload["decision"] == "NO_TRADE"


def test_dashboard_control_status_is_backend_derived(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    service = DashboardService(tmp_path)

    status = service.control_status(snapshot={"database_available": True, "processes": []})
    readiness = service.readiness()

    assert status["state"] == "READY"
    assert status["environment"] == "paper"
    assert status["ui_broker_authority"] is False
    assert readiness["checks"]["paper_mode"] is True


def test_dashboard_includes_volatility_research_workspace() -> None:
    static_root = Path(__file__).parents[1] / "src" / "gld_scalper" / "dashboard" / "static"
    workspace_source = (static_root / "js" / "workspaces.js").read_text(encoding="utf-8")
    lab_source = (static_root / "js" / "volatility-lab.js").read_text(encoding="utf-8")

    assert "Volatility Lab" in workspace_source
    assert "volatilityNetwork" in workspace_source
    assert "varLoss" in lab_source
    assert "cvar" in lab_source
    assert "new THREE.WebGLRenderer" in lab_source


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


def test_analytics_groups_legacy_episode_ids_out_of_playbook_chart(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'legacy-analytics.db'}")
    database = Database(settings=settings)
    database.init_db()
    for trade_id in ("episode-1", "episode-2"):
        database.insert_trade_outcome({
            "trade_id": trade_id, "symbol": "GLD", "direction": "SHORT",
            "gross_pnl": 0, "net_pnl_after_costs": 0,
            "playbook": f"paper_bracket:{trade_id}",
        })
    database.close()

    breakdown = PerformanceAnalytics(settings.database_path).build()["breakdowns"]["playbook"]

    assert breakdown == [{
        "label": "legacy_unclassified", "trades": 2, "wins": 0,
        "net_pnl": 0.0, "average_net": 0.0, "win_rate": 0.0,
    }]
