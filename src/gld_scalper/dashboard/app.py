from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import webbrowser
from dataclasses import asdict
from pathlib import Path
from threading import Timer
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import PROJECT_ROOT, load_settings
from .process_manager import ProcessManager
from .analytics import PerformanceAnalytics
from .catalog import TransformerCatalog
from .job_results import JobResultRepository
from .settings_store import EnvFileStore
from .telemetry import TelemetryRepository


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


class DashboardService:
    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve()
        self.env_store = EnvFileStore(self.project_root / ".env")
        self.processes = ProcessManager(self.project_root, self.env_store.child_environment)
        self.catalog = TransformerCatalog(self.project_root)
        self.results = JobResultRepository(self.project_root / "logs" / "dashboard")

    @property
    def telemetry(self) -> TelemetryRepository:
        settings = load_settings(self.project_root / ".env")
        return TelemetryRepository(settings.database_path)

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.telemetry.snapshot()
        snapshot["processes"] = self.processes.statuses()
        return snapshot

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
    app = FastAPI(title="Mashcorp GLD Command Center", docs_url=None, redoc_url=None)
    service = DashboardService(project_root)
    token = secrets.token_urlsafe(32)
    app.state.service = service
    app.state.dashboard_token = token
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def authorize(value: str | None) -> None:
        if not value or not secrets.compare_digest(value, token): raise HTTPException(403, "Invalid dashboard token")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.client and request.client.host not in LOCAL_HOSTS: raise HTTPException(403, "Dashboard is local-only")
        return await call_next(request)

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
        if name not in {"backtest", "llm_analysis", "llm_advice", "llm_labels", "llm_train", "llm_cycle", "llm_status"}:
            raise HTTPException(404, "Unsupported result type")
        return {"name": name, "result": await asyncio.to_thread(service.results.latest, name)}
    @app.get("/api/llm/activity")
    async def llm_activity(limit: int = 30): return await asyncio.to_thread(service.telemetry.llm_activity, limit)
    @app.get("/api/diagnostics")
    async def diagnostics(): return await asyncio.to_thread(service.telemetry.diagnostics)
    @app.get("/api/processes")
    async def processes(): return service.processes.statuses()
    @app.get("/api/logs/{name}")
    async def logs(name: str, lines: int = 160): return {"name":name, "lines":service.processes.tail(name,lines)}
    @app.get("/api/settings")
    async def settings(): return service.env_store.public_settings()

    @app.post("/api/processes/{action}/start")
    async def start(action: str, request: StartRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        try: return await asyncio.to_thread(service.start, action, request.options)
        except (ValueError,RuntimeError) as exc: raise HTTPException(409, str(exc)) from exc
    @app.post("/api/processes/{action}/stop")
    async def stop(action: str, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        try: return await asyncio.to_thread(service.stop, action)
        except (ValueError,RuntimeError,subprocess.SubprocessError) as exc: raise HTTPException(409,str(exc)) from exc
    @app.post("/api/settings")
    async def save_settings(request: SettingsRequest, x_dashboard_token: str | None = Header(default=None)):
        authorize(x_dashboard_token)
        if request.alpaca_data_feed not in {"iex","sip"}: raise HTTPException(400,"Data feed must be iex or sip")
        changes = {"ALPACA_API_KEY":request.alpaca_api_key, "ALPACA_SECRET_KEY":request.alpaca_secret_key,
            "ALPACA_ENDPOINT":"https://paper-api.alpaca.markets/v2", "ALPACA_DATA_FEED":request.alpaca_data_feed,
            "LLM_PROVIDER":request.llm_provider, "LLM_BASE_URL":request.llm_base_url, "LLM_MODEL":request.llm_model}
        await asyncio.to_thread(service.env_store.update, changes)
        return service.env_store.public_settings()

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
