# Local Operations Dashboard

This package provides the local Python control gateway used by the native JavaFX command center. It does not implement a second trading engine and it cannot submit an order directly. Every long-running operation is launched through an allowlisted `gld_scalper.main` command, so the existing reconciliation, risk, idempotency, journaling, and shutdown logic remains authoritative. The tracked browser assets are a fallback and API-development harness.

## Current Workstations

- **Overview:** native SQLite/Alpaca GLD candlesticks, volume, account telemetry, open episodes, responsive Signal Matrix, and logs.
- **Market:** local one-minute bars with one-, three-, and five-session ranges. The unreliable embedded TradingView widget was removed.
- **Analytics:** after-cost root-episode metrics, cumulative P/L, outcome donut, direction/playbook bars, and exit-reason analysis.
- **Training:** scope-aware dataset presets, discovered `.seq` archives, single or bounded multi-candidate Transformer training, and classical ML controls.
- **Backtest Lab:** chronological simulation controls, structured metrics, direction/economics bars, an outcome donut, and raw result evidence.
- **AI Lab:** selectable Ollama or Kimi engines, generation-level diagnostics, and eight FinGPT-assisted research, RAG, council, labeling, advice, and candidate workflows. Large evidence payloads are compacted for the local 1B model. Recoverable Kimi quota/availability failures fall back to local Ollama for the current research job; the configured provider is not silently rewritten. The LLM has no broker authority.
- **Volatility Lab:** a video-reference-inspired research workstation using local GLD one-minute bars. Interactive controls recalculate log-return volatility, empirical low/normal/high/extreme clusters, transition persistence, historical VaR and CVaR, horizon volatility, and a bounded position-size multiplier. A synchronized Three.js temporal network, regime timeline, return histogram, and transition matrix explain the result. Nothing is written into live risk settings.
- **3D Core:** an Obsidian-style Three.js knowledge graph of real bot services and flows. Nodes represent the stream, feature engine, deterministic agents, classical ML, Transformer shadow runtime, decision council, risk, execution, Alpaca paper broker, SQLite, journal, outcomes, and offline LLM research. Status, pulse, color, and selected-node details are driven by telemetry.
- **Memory Graph:** a native JavaFX, force-directed map built from bounded SQLite summaries and immutable model manifests. It connects the current decision to evidence, playbooks, candidates, champions, training runs, datasets, outcomes, LLM reviews, and safety state. Filters and inspectors are analytical only.
- **White Paper:** an in-app reader for `docs/BOT_WHITE_PAPER.md`.
- **Control Plane:** backend-derived health/readiness, API version, managed jobs, safety state, correlation IDs, and a tamper-evident operator-command ledger.
- **Settings:** paper credentials and offline provider settings with secret masking.

## Class Boundaries

`DashboardService` validates actions. `ControlPlane` serializes typed commands and enforces idempotency and a local command-rate bound. `AuditLedger` writes redacted SHA-256-linked evidence. `contracts.py` owns API/event schemas and version identifiers. `ProcessManager` owns child jobs. `TelemetryRepository` performs read-only SQLite queries. `TransformerCatalog` supplies presets and complete archives. `PerformanceAnalytics` builds chart-ready summaries. `JobResultRepository` parses structured job output. `LLMProviderService` manages secret-safe provider selection and verifies actual text generation rather than treating a model-list response as proof of inference. `WhitePaperRepository` exposes the versioned local document. The native client is documented in `apps/desktop-ui/README.md`; fallback browser classes are documented in `static/js/README.md`.

`TelemetryRepository.model_validation()` powers `/api/model-validation` and `/api/v1/models/validation`. It flattens immutable registry evidence for the JavaFX analytics view while retaining the complete structured payload for audit. The route is read-only and degrades to an empty collection when an older database has no model rows.

`MemoryGraphRepository.graph()` powers the compatibility routes and `/api/v1/memory-graph/summary`; it returns compact render metadata only. `node_detail()` powers `/api/v1/memory-graph/nodes/{id}` and uses allowlisted, parameterized lookups to produce bounded human-readable sections for one selected node. Both open SQLite with URI `mode=ro` plus `PRAGMA query_only=ON`, impose a node ceiling, and read only decision, registry, training, dataset, outcome, review, and safety tables. They never scan `quotes` or `market_trades`. Summaries and details have separate TTL caches. A graph request therefore cannot promote a model, mutate a record, submit an order, or turn the 244-million-quote archive into an interactive query workload.

`decision_explanation.py` maps machine audit phrases to stable reason codes,
deduplicates repeated evidence, and groups it for people while retaining the
original reason. It cannot alter a signal or risk veto.

`TelemetryRepository.latency_summary()` powers `/api/v1/performance/latency`
and reports stage percentiles from `execution_latency_events` without joining
raw market archives.

The locally vendored Three.js HUD is visual only. Its low-power renderer pauses with a hidden tab and cannot affect signals, risk, or execution.

## Files

- `app.py`: FastAPI application, local-only middleware, per-session control token, compatibility routes, versioned `/api/v1` routes, sequenced WebSocket envelopes, and the Uvicorn launcher.
- `contracts.py`: Pydantic command, result, training-job, and event-envelope schemas shared by the versioned gateway.
- `control_plane.py`: serialized typed command dispatcher, idempotent replay, rate limiting, and audit completion records.
- `audit.py`: append-only redacted JSONL records, SHA-256 hash chaining, verification, and recent-event lookup.
- `process_manager.py`: starts one named child process per operation, records PID/state, writes output to `logs/dashboard`, prevents duplicate starts, sends graceful interrupt signals, and reads log tails backward with a fixed memory/I/O ceiling.
- `settings_store.py`: masks Alpaca and Kimi credentials, updates `.env` atomically, preserves blank secret fields, and enforces paper/offline safety for dashboard-launched children.
- `llm_providers.py`: defines Ollama/Kimi profiles, activation rules, small generation tests, and FinGPT source discovery without exposing provider secrets. A successful model-list request alone is not reported as a healthy LLM.
- `whitepaper.py`: reads the versioned white paper for the local interface.
- `telemetry.py`: opens SQLite separately in read-only/query-only mode and produces account, quote, episode, outcome, model, decision, safety, and equity views. Performance analytics groups malformed legacy bracket identifiers under `legacy_unclassified` instead of presenting them as playbooks.
- `memory_graph.py`: builds the bounded learned-state graph, model lineage, node inspector payloads, time-filtered memory timeline, visual status encoding, and TTL cache without touching raw quote/trade archives.
- `decision_explanation.py`: stable reason-code classification and grouped plain-language decision evidence.
- `static/index.html`: operational views for market, performance, trades, intelligence, training, system diagnostics, and local settings.
- `static/styles.css`: responsive cyan/green command-center visual system.
- `static/app.js`: live WebSocket updates, native chart telemetry, research providers, forms, process controls, tables, settings, and terminal output.
- `static/js/volatility-lab.js`: local causal volatility simulation, tail-risk metrics, synchronized canvas charts, and an animated Three.js temporal regime graph.

## Security Boundary

The server binds only to `127.0.0.1`, `localhost`, or `::1`. State-changing requests require a random token. JavaFX creates a 256-bit token and supplies it only in the gateway child-process environment; a directly launched fallback browser receives a gateway-generated token in its same-origin page. WebSockets send the token in their first frame rather than their URL, keeping it out of access logs. CORS is not enabled. Secrets are written only to the ignored local `.env`; the API returns configured flags and a short key hint, never the secret. Dashboard paths must resolve inside the project folder. Paper mode and the paper Alpaca endpoint are enforced during settings updates and child-process creation.

## Runtime Flow

1. `GatewayRuntime` starts `gld-scalper dashboard` on an unused loopback port with an in-memory one-session token.
2. JavaFX waits for `/api/v1/system/health`, then consumes versioned REST and WebSocket contracts.
3. `/api/v1/events` sends a versioned, traced, sequenced SQLite snapshot and log tail every two seconds.
4. A start control creates a typed command with an idempotency key.
5. `ControlPlane` serializes the operation, records the request, and maps it to a fixed CLI argument list.
6. `ProcessManager` launches that CLI in its own process group and captures output. The Desktop launcher separately records the dashboard server PID and exact start time so its stop script cannot target a reused PID.
7. Completion or rejection is recorded in the hash-chained audit ledger with its correlation ID.
8. Paper execution continues through the existing bot; the dashboard only observes its database and process state.
9. Stop sends `CTRL_BREAK_EVENT` on Windows so the bot can run its normal safety shutdown.
10. Memory-graph requests run through an independent read-only repository. The JavaFX animation thread receives already-bounded JSON and never enters the bot's decision or order threads.

JavaFX is the primary Windows presentation client. The browser/PWA is retained as a fallback and API-development harness. Both use the same stable gateway; neither duplicates or rewrites the Python execution engine. See `docs/architecture/JARVIS_CONTROL_PLANE.md`.

FinGPT is a workflow and financial-RAG layer, not a third reasoning endpoint. It uses the active Ollama or Kimi engine. Provider tests never grant broker authority, and every training result remains subject to the normal validation and promotion gates. On the target 8 GB laptop, Ollama receives bounded context, keeps the selected model warm for 15 minutes, and retries one timeout with a smaller response budget. Kimi can still reject generation when the remote account has no balance even if authentication and model discovery succeed; the AI Lab now reports that condition and the analysis service can use Ollama as a local fallback.
