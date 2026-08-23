# Local Operations Dashboard

This package provides the browser-based paper-trading command center. It does not implement a second trading engine and it cannot submit an order directly. Every long-running operation is launched through an allowlisted `gld_scalper.main` command, so the existing reconciliation, risk, idempotency, journaling, and shutdown logic remains authoritative.

## Current Workstations

- **Overview:** native SQLite/Alpaca GLD candlesticks, volume, account telemetry, open episodes, responsive Signal Matrix, and logs.
- **Market:** local one-minute bars with one-, three-, and five-session ranges. The unreliable embedded TradingView widget was removed.
- **Analytics:** after-cost root-episode metrics, cumulative P/L, outcome donut, direction/playbook bars, and exit-reason analysis.
- **Training:** scope-aware dataset presets, discovered `.seq` archives, single or bounded multi-candidate Transformer training, and classical ML controls.
- **Backtest Lab:** chronological simulation controls, structured metrics, direction/economics bars, an outcome donut, and raw result evidence.
- **AI Lab:** selectable Ollama or Kimi engines, connection diagnostics, and eight FinGPT-assisted research, RAG, council, labeling, advice, and candidate workflows. The LLM has no broker authority.
- **3D Core:** an interactive full-viewport Three.js decision field driven by telemetry and isolated from trading logic.
- **White Paper:** an in-app reader for `docs/BOT_WHITE_PAPER.md`.
- **System/Settings:** process supervision, safety logs, paper credentials, and provider settings.

## Class Boundaries

`DashboardService` validates actions. `ProcessManager` owns child jobs. `TelemetryRepository` performs read-only SQLite queries. `TransformerCatalog` supplies presets and complete archives. `PerformanceAnalytics` builds chart-ready summaries. `JobResultRepository` parses structured job output. `LLMProviderService` manages secret-safe provider selection and health checks. `WhitePaperRepository` exposes the versioned local document. Browser classes are documented in `static/js/README.md`.

The locally vendored Three.js HUD is visual only. Its low-power renderer pauses with a hidden tab and cannot affect signals, risk, or execution.

## Files

- `app.py`: FastAPI application, local-only middleware, per-session control token, REST/WebSocket routes, validated action-to-CLI mapping, and the Uvicorn launcher.
- `process_manager.py`: starts one named child process per operation, records PID/state, writes output to `logs/dashboard`, prevents duplicate starts, and sends graceful interrupt signals.
- `settings_store.py`: masks Alpaca and Kimi credentials, updates `.env` atomically, preserves blank secret fields, and enforces paper/offline safety for dashboard-launched children.
- `llm_providers.py`: defines Ollama/Kimi profiles, activation rules, connection tests, and FinGPT source discovery without exposing provider secrets.
- `whitepaper.py`: reads the versioned white paper for the local interface.
- `telemetry.py`: opens SQLite separately in read-only/query-only mode and produces account, quote, episode, outcome, model, decision, safety, and equity views.
- `static/index.html`: operational views for market, performance, trades, intelligence, training, system diagnostics, and local settings.
- `static/styles.css`: responsive cyan/green command-center visual system.
- `static/app.js`: live WebSocket updates, native chart telemetry, research providers, forms, process controls, tables, settings, and terminal output.

## Security Boundary

The server binds only to `127.0.0.1`, `localhost`, or `::1`. State-changing requests require a random token embedded in the same-origin page. The WebSocket sends that token in its first frame rather than its URL, keeping it out of access logs. CORS is not enabled. Secrets are written only to the ignored local `.env`; the API returns configured flags and a short key hint, never the secret. Dashboard paths must resolve inside the project folder. Paper mode and the paper Alpaca endpoint are enforced during settings updates and child-process creation.

## Runtime Flow

1. `gld-scalper dashboard` creates `DashboardService`.
2. The browser loads the static interface and receives a one-session control token.
3. `/ws/live` sends a SQLite snapshot and log tail every two seconds.
4. A start control maps its form to a fixed CLI argument list.
5. `ProcessManager` launches that CLI in its own process group and captures output. The Desktop launcher separately records the dashboard server PID and exact start time so its stop script cannot target a reused PID.
6. Paper execution continues through the existing bot; the dashboard only observes its database and process state.
7. Stop sends `CTRL_BREAK_EVENT` on Windows so the bot can run its normal safety shutdown.

FinGPT is a workflow and financial-RAG layer, not a third reasoning endpoint. It uses the active Ollama or Kimi engine. Provider tests never grant broker authority, and every training result remains subject to the normal validation and promotion gates.
