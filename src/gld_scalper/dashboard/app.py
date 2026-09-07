from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Timer
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from ..config import PROJECT_ROOT, load_settings
from ..utils.time_utils import market_session, utc_now
from .audit import AuditLedger
from .contracts import (
    API_VERSION,
    BotCommandRequest,
    CommandRequest,
    EventEnvelope,
    TrainingJobRequest,
    new_identifier,
)
from .control_plane import ControlPlane
from .process_manager import ProcessManager
from .analytics import PerformanceAnalytics
from .catalog import TransformerCatalog
from .job_results import JobResultRepository
from .llm_providers import LLMProviderService
from .memory_graph import GRAPH_TYPES, MemoryGraphRepository
from .decision_explanation import explain_decision
from .interface_performance import RequestLatencyMonitor
from .settings_store import EnvFileStore
from .telemetry import TelemetryRepository
from .whitepaper import WhitePaperRepository


STATIC_ROOT = Path(__file__).with_name("static")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class StartRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


class SettingsRequest(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_data_feed: str = "iex"
    llm_provider: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "llama3.2:1b"
    moonshot_api_key: str = ""


class ProviderRequest(BaseModel):
    provider: str
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class ProviderTestRequest(BaseModel):
    provider: str | None = None


class DashboardService:
    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve()
        self.env_store = EnvFileStore(self.project_root / ".env")
        self.processes = ProcessManager(self.project_root, self.env_store.child_environment)
        self.catalog = TransformerCatalog(self.project_root)
        self.results = JobResultRepository(self.project_root / "logs" / "dashboard")
        self.llm_providers = LLMProviderService(self.project_root, self.env_store)
        self.whitepaper = WhitePaperRepository(self.project_root)
        self.audit = AuditLedger(self.project_root / "logs" / "dashboard" / "control_plane_audit.jsonl")
        self.control_plane = ControlPlane(audit=self.audit, start=self.start, stop=self.stop)
        self.memory_graph = MemoryGraphRepository(
            self.project_root,
            lambda: load_settings(self.project_root / ".env").database_path,
        )
        self.interface_latency = RequestLatencyMonitor()
        self.cache_warmup: dict[str, Any] = {"status": "pending", "steps": {}}

    def warm_caches(self) -> dict[str, Any]:
        started = time.perf_counter()
        steps: dict[str, float] = {}

        def run(name: str, operation) -> None:
            step_started = time.perf_counter()
            operation()
            steps[name] = round((time.perf_counter() - step_started) * 1000, 3)

        try:
            run("provider_catalog", self.llm_providers.catalog)
            run("transformer_catalog", self.catalog.payload)
            run("overview_graph", lambda: self.memory_graph.graph(
                types={"decision", "market", "model", "playbook", "risk"}, window="1d"
            ))
            run("memory_workspace", lambda: self.memory_graph.graph(window="30d"))
            self.cache_warmup = {
                "status": "ready",
                "steps": steps,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        except Exception as exc:
            self.cache_warmup = {
                "status": "degraded",
                "steps": steps,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": str(exc)[:300],
            }
        return self.cache_warmup

    @property
    def telemetry(self) -> TelemetryRepository:
        settings = load_settings(self.project_root / ".env")
        return TelemetryRepository(settings.database_path)

    def snapshot(self) -> dict[str, Any]:
        snapshot = self._telemetry_snapshot()
        if isinstance(snapshot.get("signal"), dict):
            snapshot["signal"]["explanation"] = explain_decision(snapshot["signal"])
        snapshot["processes"] = self.processes.statuses()
        snapshot["control_plane"] = self.control_status(snapshot=snapshot)
        return snapshot

    def _telemetry_snapshot(self) -> dict[str, Any]:
        try:
            return self.telemetry.snapshot()
        except (RuntimeError, ValueError) as exc:
            return {
                "database_available": False,
                "configuration_valid": False,
                "configuration_error": str(exc),
            }

    def _telemetry_available(self) -> tuple[bool, bool, str | None]:
        try:
            return self.telemetry.available(), True, None
        except (RuntimeError, ValueError) as exc:
            return False, False, str(exc)

    def control_status(self, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        if snapshot is None:
            database_available, configuration_valid, configuration_error = self._telemetry_available()
            current = {
                "database_available": database_available,
                "configuration_valid": configuration_valid,
                "configuration_error": configuration_error,
            }
        else:
            current = snapshot
        processes = current.get("processes") or self.processes.statuses()
        paper = next((item for item in processes if item.get("name") == "paper"), None)
        audit = self.audit.verify()
        database_available = bool(current.get("database_available"))
        if paper and paper.get("state") in {"running", "stopping"}:
            state = "RUNNING" if paper.get("state") == "running" else "DEGRADED"
        elif database_available and audit.get("valid"):
            state = "READY"
        else:
            state = "DEGRADED"
        return {
            "api_version": API_VERSION,
            "state": state,
            "environment": "paper",
            "trading_authority": "python-risk-engine",
            "ui_broker_authority": False,
            "database_available": database_available,
            "audit": audit,
            "paper_process": paper,
            "training_processes": [item for item in processes if item.get("name") != "paper"],
        }

    def health(self) -> dict[str, Any]:
        audit = self.audit.verify()
        return {
            "status": "healthy" if audit.get("valid") else "degraded",
            "api_version": API_VERSION,
            "service": "gld-dashboard-gateway",
            "audit_integrity": bool(audit.get("valid")),
            "cache_warmup": self.cache_warmup,
        }

    def readiness(self) -> dict[str, Any]:
        database_available, configuration_valid, configuration_error = self._telemetry_available()
        audit = self.audit.verify()
        checks = {
            "database": database_available,
            "configuration": configuration_valid,
            "audit_ledger": bool(audit.get("valid")),
            "paper_mode": True,
            "allowlisted_commands": True,
        }
        ready = all(checks.values())
        session = market_session(utc_now(), extended_hours=True)
        providers = self.llm_providers.catalog()
        active_provider = str(providers.get("active_provider") or "none")
        result = {
            "ready": ready,
            "checks": checks,
            "api_version": API_VERSION,
            "interface": {"ready": True, "state": "ready"},
            "trading": {
                "ready": ready,
                "state": "available" if ready else "blocked",
                "authority": "python-risk-engine",
            },
            "market": {
                "session": session,
                "regular_open": session == "regular",
                "data_stream_required_for_orders": True,
            },
            "llm": {
                "provider": active_provider,
                "configured": active_provider in {"ollama", "kimi"},
                "blocking": False,
                "broker_authority": False,
            },
        }
        if configuration_error:
            result["configuration_error"] = configuration_error
        return result

    def start(self, action: str, options: dict[str, Any]) -> dict[str, Any]:
        command = self._command(action, options)
        return asdict(self.processes.start(action, command))

    def stop(self, action: str) -> dict[str, Any]:
        if action in {"ml_loop", "transformer_loop"}:
            stop_command = "stop-training-loop" if action == "ml_loop" else "stop-transformer-training"
            subprocess.run(
                [self._python(), "-m", "gld_scalper.main", stop_command], cwd=self.project_root,
                env=self.env_store.child_environment(), timeout=30, check=True,
            )
            return {"name": action, "state": "stop_requested"}
        return asdict(self.processes.stop(action))

    def _command(self, action: str, options: dict[str, Any]) -> list[str]:
        if action == "paper":
            command = ["run-paper"]
            if bool(options.get("no_retraining", True)): command.append("--no-retraining")
            return command
        if action == "ml_train":
            return ["train", "--lookback-days", str(_integer(options, "lookback_days", 90, 1, 3650))]
        if action == "ml_loop":
            command = ["train-loop", "--artifact", str(self._path(options, "artifact")), "--watch", "--clear-stop"]
            command += ["--interval-minutes", str(_integer(options, "interval_minutes", 60, 5, 1440))]
            return command
        if action == "transformer_dataset":
            command = ["build-transformer-dataset", "--database", str(self._path(options, "database")),
                "--scope", _choice(options, "scope", {"fast_microstructure","minute","news_event","exit"}, "fast_microstructure"),
                "--source", _choice(options, "source", {"auto","raw","decisions"}, "auto"),
                "--start", _date(options, "start"), "--end", _date(options, "end"),
                "--max-samples", str(_integer(options, "max_samples", 50000, 100, 2000000)),
                "--output", str(self._path(options, "output", must_exist=False))]
            for key, flag, default, minimum, maximum in (
                ("sequence_length", "--sequence-length", 0, 1, 10000),
                ("window_seconds", "--window-seconds", 0, 1, 86400),
                ("stride", "--stride", 1, 1, 10000),
                ("max_features", "--max-features", 64, 1, 512),
            ):
                value = _integer(options, key, default, 0 if default == 0 else minimum, maximum)
                if value: command += [flag, str(value)]
            if bool(options.get("overwrite")): command.append("--overwrite")
            return command
        if action == "transformer_train":
            return ["train-transformer", "--artifact", str(self._path(options, "artifact")),
                "--d-model", str(_choice(options, "d_model", {32,48,64}, 32)),
                "--layers", str(_choice(options, "layers", {2,3}, 2)),
                "--batch-size", str(_integer(options, "batch_size", 16, 1, 512)),
                "--epochs", str(_integer(options, "epochs", 10, 1, 100)),
                "--patience", str(_integer(options, "patience", 3, 1, 20))]
        if action in {"transformer_loop", "transformer_batch"}:
            artifacts = options.get("artifacts") or []
            if not isinstance(artifacts, list) or not artifacts: raise ValueError("At least one scope=artifact is required")
            command = ["transformer-train-loop"]
            for item in artifacts:
                scope, separator, path = str(item).partition("=")
                if not separator or scope not in {"fast_microstructure","minute","news_event","exit"}: raise ValueError("Invalid Transformer artifact mapping")
                command += ["--artifact", f"{scope}={self._resolved_path(path)}"]
            command += ["--clear-stop", "--epochs", str(_integer(options,"epochs",8,1,100)),
                "--walk-forward-epochs", str(_integer(options,"walk_forward_epochs",2,1,50)),
                "--batch-size", str(_integer(options,"batch_size",16,1,512))]
            if action == "transformer_loop":
                command += ["--watch", "--interval-minutes", str(_integer(options,"interval_minutes",60,5,1440))]
            else:
                command += ["--maximum-cycles", "1"]
            return command
        if action == "backtest":
            return ["backtest", "--start", _date(options,"start"), "--end", _date(options,"end")]
        if action == "export": return ["export-csv"]
        if action == "labels": return ["label-paper-outcomes", "--limit", str(_integer(options,"limit",100000,1,1000000))]
        if action == "research": return ["collect-research-data", "--days", str(_integer(options,"days",7,1,3650))]
        if action == "report": return ["report", "--date", str(options.get("date") or "today")]
        if action == "llm_analysis": return ["llm-analyze", "--query", str(options.get("query") or "Analyze performance, execution, and learning quality.")[:2000]]
        if action == "llm_cycle": return ["llm-offline-cycle", "--cadence", _choice(options,"cadence",{"hourly","daily"},"daily")]
        if action == "llm_macro": return ["llm-macro-context"]
        if action == "llm_coach":
            return ["review-coach", "--query", str(options.get("query") or "Review GLD performance, execution, risk, and missed opportunities")[:1000],
                "--limit", str(_integer(options,"limit",12,1,100))]
        if action == "llm_council":
            return ["tradingagents-advisory", "--horizon", _choice(options,"horizon",{"hourly","daily"},"daily")]
        if action == "llm_advice": return ["llm-training-advice"]
        if action == "llm_labels": return ["llm-label-signals", "--limit", str(_integer(options,"limit",25,1,500))]
        if action == "llm_train":
            command = ["llm-train-candidate", "--lookback-days", str(_integer(options,"lookback_days",90,1,3650)),
                "--label-limit", str(_integer(options,"label_limit",25,1,500))]
            if bool(options.get("with_advice", True)): command.append("--with-advice")
            return command
        if action == "llm_status": return ["llm-provider-status"]
        raise ValueError(f"Unsupported dashboard action: {action}")

    def _path(self, options: dict[str, Any], key: str, *, must_exist: bool = True) -> Path:
        raw = str(options.get(key) or "").strip()
        if not raw: raise ValueError(f"{key} is required")
        return self._resolved_path(raw, must_exist=must_exist)

    def _resolved_path(self, raw: str, *, must_exist: bool = True) -> Path:
        path = Path(raw); path = path if path.is_absolute() else self.project_root / path
        path = path.resolve()
        if not path.is_relative_to(self.project_root): raise ValueError("Dashboard paths must remain inside the project folder")
        if must_exist and not path.exists(): raise ValueError(f"Path does not exist: {path}")
        return path

    @staticmethod
    def _python() -> str:
        import sys
        return sys.executable


def create_dashboard_app(project_root: Path = PROJECT_ROOT) -> FastAPI:
    service = DashboardService(project_root)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(service.warm_caches)
        yield

    app = FastAPI(
        title="Mashcorp GLD Command Center",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    supplied_token = os.getenv("DASHBOARD_SESSION_TOKEN", "").strip()
    if supplied_token and len(supplied_token) < 32:
        raise RuntimeError("DASHBOARD_SESSION_TOKEN must contain at least 32 characters")
    token = supplied_token or secrets.token_urlsafe(32)
    app.state.service = service
    app.state.dashboard_token = token
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def authorize(value: str | None) -> None:
        if not value or not secrets.compare_digest(value, token): raise HTTPException(403, "Invalid dashboard token")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.client and request.client.host not in LOCAL_HOSTS: raise HTTPException(403, "Dashboard is local-only")
        correlation_id = request.headers.get("X-Correlation-ID") or new_identifier("trace")
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            service.interface_latency.observe(request.method, request.url.path, elapsed_ms, status_code)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Dashboard-Response-Ms"] = f"{elapsed_ms:.3f}"
        response.headers["Server-Timing"] = f"dashboard;dur={elapsed_ms:.3f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8").replace("__DASHBOARD_TOKEN__", token)
        return HTMLResponse(html, headers={"Cache-Control":"no-store", "X-Frame-Options":"DENY"})

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, Any]: return await asyncio.to_thread(service.snapshot)
    @app.get("/api/trades")
    async def trades(limit: int = 100): return await asyncio.to_thread(service.telemetry.trades, limit)
    @app.get("/api/decisions")
    async def decisions(limit: int = 100): return await asyncio.to_thread(service.telemetry.decisions, limit)
    @app.get("/api/models")
    async def models(): return await asyncio.to_thread(service.telemetry.models)
    @app.get("/api/model-validation")
    async def model_validation(): return await asyncio.to_thread(service.telemetry.model_validation)
    @app.get("/api/memory-graph")
    async def memory_graph(types: str = "", window: str = "30d"):
        selected = {value.strip() for value in types.split(",") if value.strip()}
        unknown = selected - GRAPH_TYPES
        if unknown:
            raise HTTPException(400, f"Unknown memory graph types: {', '.join(sorted(unknown))}")
        try:
            return await asyncio.to_thread(service.memory_graph.graph, types=selected or None, window=window)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    @app.get("/api/equity")
    async def equity(): return await asyncio.to_thread(service.telemetry.equity_curve)
    @app.get("/api/market-series")
    async def market_series(limit: int = 390): return await asyncio.to_thread(service.telemetry.market_series, limit)
    @app.get("/api/analytics")
    async def analytics(limit: int = 5000):
        return await asyncio.to_thread(PerformanceAnalytics(service.telemetry.database_path).build, limit)
    @app.get("/api/transformer/catalog")
    async def transformer_catalog(): return await asyncio.to_thread(service.catalog.payload)
    @app.get("/api/results/{name}")
    async def job_result(name: str):
        if name not in {
            "backtest", "llm_analysis", "llm_advice", "llm_labels", "llm_train",
            "llm_cycle", "llm_status", "llm_macro", "llm_coach", "llm_council",
        }:
            raise HTTPException(404, "Unsupported result type")
        return {"name": name, "result": await asyncio.to_thread(service.results.latest, name)}
    @app.get("/api/llm/activity")
    async def llm_activity(limit: int = 30): return await asyncio.to_thread(service.telemetry.llm_activity, limit)
    @app.get("/api/llm/providers")
    async def llm_providers(): return await asyncio.to_thread(service.llm_providers.catalog)
    @app.get("/api/whitepaper")
    async def whitepaper(): return await asyncio.to_thread(service.whitepaper.payload)
    @app.get("/api/diagnostics")
    async def diagnostics(): return await asyncio.to_thread(service.telemetry.diagnostics)
    @app.get("/api/v1/performance/latency")
    async def v1_performance_latency(limit: int = 5000):
        return await asyncio.to_thread(service.telemetry.latency_summary, limit)
    @app.get("/api/processes")
    async def processes(): return service.processes.statuses()
    @app.get("/api/logs/{name}")
    async def logs(name: str, lines: int = 160): return {"name":name, "lines":service.processes.tail(name,lines)}
    @app.get("/api/settings")
    async def settings(): return service.env_store.public_settings()

    # Versioned control-plane contract. Legacy /api routes remain available for
    # the installed dashboard and are routed through the same implementation.
    @app.get("/api/v1/system/health")
    async def v1_health(): return await asyncio.to_thread(service.health)
    @app.get("/api/v1/system/readiness")
    async def v1_readiness(): return await asyncio.to_thread(service.readiness)
    @app.get("/api/v1/system/interface-latency")
    async def v1_interface_latency(): return service.interface_latency.snapshot()
    @app.get("/api/v1/system/status")
    async def v1_status(): return await asyncio.to_thread(service.control_status)
    @app.get("/api/v1/market/GLD/snapshot")
    async def v1_market_snapshot():
        snapshot = await asyncio.to_thread(service.telemetry.snapshot)
        return {"symbol": "GLD", "quote": snapshot.get("quote"), "signal": snapshot.get("signal"), "server_time": snapshot.get("server_time")}
    @app.get("/api/v1/market/GLD/bars")
    async def v1_market_bars(timeframe: str = "1Min", limit: int = 500):
        if timeframe != "1Min": raise HTTPException(400, "The dashboard gateway currently exposes 1Min bars")
        return {"symbol": "GLD", "timeframe": timeframe, "bars": await asyncio.to_thread(service.telemetry.market_series, limit)}
    @app.get("/api/v1/account")
    async def v1_account(): return (await asyncio.to_thread(service.telemetry.snapshot)).get("account") or {}
    @app.get("/api/v1/positions")
    async def v1_positions(): return (await asyncio.to_thread(service.telemetry.snapshot)).get("active_episodes") or []
    @app.get("/api/v1/orders")
    async def v1_orders(limit: int = 100): return await asyncio.to_thread(service.telemetry.orders, limit)
    @app.get("/api/v1/decisions/latest")
    async def v1_latest_decision():
        values = await asyncio.to_thread(service.telemetry.decisions, 1)
        return values[0] if values else {}
    @app.get("/api/v1/models")
    async def v1_models(): return await asyncio.to_thread(service.telemetry.models)
    @app.get("/api/v1/models/validation")
    async def v1_model_validation(): return await asyncio.to_thread(service.telemetry.model_validation)
    @app.get("/api/v1/memory-graph")
    async def v1_memory_graph(types: str = "", window: str = "30d"):
        return await memory_graph(types=types, window=window)
    @app.get("/api/v1/memory-graph/summary")
    async def v1_memory_graph_summary(types: str = "", window: str = "30d"):
        return await memory_graph(types=types, window=window)
    @app.get("/api/v1/memory-graph/nodes/{node_id:path}")
    async def v1_memory_graph_node(node_id: str):
        try:
            return await asyncio.to_thread(service.memory_graph.node_detail, node_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    @app.get("/api/v1/training/jobs")
    async def v1_training_jobs(): return [item for item in service.processes.statuses() if item.get("name") != "paper"]
    @app.get("/api/v1/audit/events")
    async def v1_audit_events(limit: int = 100): return await asyncio.to_thread(service.audit.tail, limit)
    @app.get("/api/v1/audit/verify")
    async def v1_audit_verify(): return await asyncio.to_thread(service.audit.verify)

    async def execute_control_command(command: CommandRequest):
        try:
            result = await asyncio.to_thread(service.control_plane.execute, command)
        except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post("/api/v1/bot/start")
    async def v1_bot_start(request: BotCommandRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        return await execute_control_command(CommandRequest(
            actor_id=request.actor_id, command_type="bot.start", parameters={"options": request.options},
            idempotency_key=request.idempotency_key,
        ))
    @app.post("/api/v1/bot/stop")
    async def v1_bot_stop(request: BotCommandRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        return await execute_control_command(CommandRequest(
            actor_id=request.actor_id, command_type="bot.stop", parameters={},
            idempotency_key=request.idempotency_key,
        ))
    @app.post("/api/v1/training/jobs")
    async def v1_training_start(request: TrainingJobRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        return await execute_control_command(CommandRequest(
            actor_id=request.actor_id, command_type="job.start",
            parameters={"action": request.action, "options": request.options},
            idempotency_key=request.idempotency_key,
        ))
    @app.post("/api/v1/training/jobs/{action}/cancel")
    async def v1_training_cancel(action: str, request: BotCommandRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        return await execute_control_command(CommandRequest(
            actor_id=request.actor_id, command_type="job.stop", parameters={"action": action},
            idempotency_key=request.idempotency_key,
        ))

    @app.post("/api/processes/{action}/start")
    async def start(action: str, request: StartRequest, x_dashboard_token: str | None = Header(default=None), x_idempotency_key: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        command_type = "bot.start" if action == "paper" else "job.start"
        parameters = {"options": request.options} if action == "paper" else {"action": action, "options": request.options}
        result = await execute_control_command(CommandRequest(
            command_type=command_type, parameters=parameters,
            idempotency_key=x_idempotency_key or new_identifier("idem"),
        ))
        return result["result"] | {"correlation_id": result["correlation_id"], "command_id": result["command_id"]}
    @app.post("/api/processes/{action}/stop")
    async def stop(action: str, x_dashboard_token: str | None = Header(default=None), x_idempotency_key: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        command_type = "bot.stop" if action == "paper" else "job.stop"
        parameters = {} if action == "paper" else {"action": action}
        result = await execute_control_command(CommandRequest(
            command_type=command_type, parameters=parameters,
            idempotency_key=x_idempotency_key or new_identifier("idem"),
        ))
        return result["result"] | {"correlation_id": result["correlation_id"], "command_id": result["command_id"]}
    @app.post("/api/settings")
    async def save_settings(request: SettingsRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        if request.alpaca_data_feed not in {"iex","sip"}: raise HTTPException(400,"Data feed must be iex or sip")
        if request.llm_provider not in {"ollama", "kimi"}: raise HTTPException(400, "LLM provider must be ollama or kimi")
        if request.llm_provider == "kimi" and request.llm_base_url.rstrip("/") != "https://api.moonshot.ai/v1":
            raise HTTPException(400, "Kimi must use the official Moonshot endpoint")
        changes = {"ALPACA_API_KEY":request.alpaca_api_key, "ALPACA_SECRET_KEY":request.alpaca_secret_key,
            "ALPACA_ENDPOINT":"https://paper-api.alpaca.markets/v2", "ALPACA_DATA_FEED":request.alpaca_data_feed,
            "LLM_PROVIDER":request.llm_provider, "LLM_BASE_URL":request.llm_base_url, "LLM_MODEL":request.llm_model,
            "MOONSHOT_API_KEY": request.moonshot_api_key}
        # Partial native-client forms must not reset unrelated provider settings.
        field_keys = {"alpaca_api_key": "ALPACA_API_KEY", "alpaca_secret_key": "ALPACA_SECRET_KEY",
            "alpaca_data_feed": "ALPACA_DATA_FEED", "llm_provider": "LLM_PROVIDER",
            "llm_base_url": "LLM_BASE_URL", "llm_model": "LLM_MODEL", "moonshot_api_key": "MOONSHOT_API_KEY"}
        included = {field_keys[field] for field in request.model_fields_set if field in field_keys}
        changes = {key: value for key, value in changes.items() if key in included or key == "ALPACA_ENDPOINT"}
        await asyncio.to_thread(service.env_store.update, changes)
        await asyncio.to_thread(
            service.audit.append,
            event_type="settings.updated", actor_id="local-operator", status="completed",
            correlation_id=new_identifier("trace"), action="settings.update", details={"changes": changes},
        )
        return service.env_store.public_settings()

    @app.post("/api/llm/providers/activate")
    async def activate_provider(request: ProviderRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        try:
            return await asyncio.to_thread(
                service.llm_providers.activate,
                request.provider,
                base_url=request.base_url,
                model=request.model,
                api_key=request.api_key,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/llm/providers/test")
    async def test_provider(request: ProviderTestRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        try:
            return await asyncio.to_thread(service.llm_providers.test, request.provider)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.websocket("/ws/live")
    async def live(websocket: WebSocket):
        if websocket.client and websocket.client.host not in LOCAL_HOSTS: await websocket.close(code=1008); return
        await websocket.accept()
        try:
            supplied_token = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if not secrets.compare_digest(supplied_token, token):
                await websocket.close(code=1008)
                return
            while True:
                payload = await asyncio.to_thread(service.snapshot)
                payload["log_tail"] = service.processes.tail("bot", 45)
                await websocket.send_text(json.dumps(payload, default=str))
                await asyncio.sleep(2)
        except WebSocketDisconnect: pass

    @app.websocket("/api/v1/events")
    async def v1_events(websocket: WebSocket):
        if websocket.client and websocket.client.host not in LOCAL_HOSTS: await websocket.close(code=1008); return
        await websocket.accept()
        try:
            supplied_token = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if not secrets.compare_digest(supplied_token, token):
                await websocket.close(code=1008)
                return
            sequence = 0
            trace_id = new_identifier("trace")
            while True:
                sequence += 1
                payload = await asyncio.to_thread(service.snapshot)
                payload["log_tail"] = service.processes.tail("bot", 45)
                event = EventEnvelope(
                    event_type="system.snapshot", trace_id=trace_id, symbol="GLD",
                    sequence=sequence, payload=payload,
                )
                await websocket.send_text(event.model_dump_json())
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            pass

    return app


def run_dashboard(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    if host not in {"127.0.0.1","localhost","::1"}: raise RuntimeError("Dashboard may only bind to localhost")
    import uvicorn
    url=f"http://127.0.0.1:{port}"
    if open_browser: Timer(1.0, lambda: webbrowser.open(url)).start()
    # Access logging is unnecessary for the local operator process. The
    # WebSocket token is sent as the first frame, never in the request URL.
    uvicorn.run(create_dashboard_app(), host=host, port=port, log_level="info", access_log=False)


def _integer(options: dict[str,Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value=int(options.get(key,default))
    if not minimum<=value<=maximum: raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value
def _choice(options: dict[str,Any], key: str, allowed: set[Any], default: Any) -> Any:
    value=options.get(key,default)
    if value not in allowed: raise ValueError(f"Invalid {key}")
    return value
def _date(options: dict[str,Any], key: str) -> str:
    value=str(options.get(key) or "").strip()
    if len(value)<10 or value[4]!="-" or value[7]!="-": raise ValueError(f"{key} must be an ISO date")
    return value
