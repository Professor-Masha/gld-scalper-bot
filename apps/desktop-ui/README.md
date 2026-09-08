# JavaFX Desktop Command Center

This folder owns the native Mashcorp GLD operator interface. Python remains the
only trading authority. JavaFX does not import Alpaca libraries, read SQLite,
submit broker orders, or implement risk rules.

## Runtime Boundary

1. `Launcher` enters JavaFX without placing JavaFX classes in the JVM launcher.
2. `GatewayRuntime` finds the project root, chooses an unused localhost port,
   generates a 256-bit session token, and starts `gld_scalper.main dashboard`.
3. The token is passed only through the child-process environment. It is not
   written to a URL, source file, command line, or dashboard log.
4. `GatewayClient` reads telemetry from `/api/v1`, authenticates mutations with
   `X-Dashboard-Token`, and consumes versioned `/api/v1/events` envelopes.
5. `JarvisApplication` renders operations, market, performance, trading,
   intelligence, training, AI, control-plane, and settings workspaces.
6. `DecisionCore3D` visualizes backend state and the bounded memory graph with a
   hardware-accelerated JavaFX `SubScene`. It is a display component and cannot
   influence a trade.

## Staged Startup And Readiness

JavaFX displays `StartupOverlay` immediately; it no longer waits behind Python
gateway startup with a blank or frozen-looking window. The progress bar advances
only after completed phases: JavaFX initialization, gateway health, database and
audit checks, the first telemetry snapshot, model/research discovery, compact
Memory Graph warmup, interface composition, and telemetry-listener startup.

One hundred percent means the **core interface is usable**. It does not claim
that the market is open, paper trading is active, or an LLM has completed a
generation. `ReadinessStrip` therefore keeps four states separate:

- **Interface:** whether the local operator application can be used.
- **Trading:** locked, available, active, stopping, or blocked by backend checks.
- **Market:** regular open, premarket, after-hours, closed, or degraded telemetry.
- **Research AI:** disabled or the configured provider. Configuration is not
  presented as proof that an Ollama/Kimi generation succeeded.

The Python `/api/v1/system/readiness` response is authoritative for the trading
gate. JavaFX cannot enable **Start Paper** merely because its own rendering has
finished. LLM readiness is deliberately non-blocking and has no broker authority.

## Files

| File | Responsibility |
|---|---|
| `pom.xml` | Pins Java 21, JavaFX 21, Jackson, compiler, and JavaFX launcher. |
| `Launcher.java` | Stable non-JavaFX JVM entry point. |
| `GatewayRuntime.java` | Secure lifecycle for the local Python gateway. |
| `GatewayClient.java` | REST/WebSocket transport and authenticated commands. |
| `ClientLatencyMonitor.java` | Bounded JavaFX end-to-end request, WebSocket-connect, and startup-to-usable percentiles. |
| `JarvisApplication.java` | Native window, navigation, tables, charts, forms, and state projection. |
| `StartupOverlay.java` | Immediate, phase-backed 0-100% startup presentation and failure state. |
| `ReadinessSnapshot.java` | Pure projection of the gateway readiness contract into human-facing states. |
| `ReadinessStrip.java` | Persistent interface, trading, market, and research-AI status display. |
| `DecisionCore3D.java` | Native 3D open-ring rendering, color/size encoding, live-evidence pulses, selection, drag, and zoom. It consumes bounded JSON and has no database or broker dependency. |
| `MemoryGraphWorkspace.java` | Read-only graph filters, node inspector, cache status, and chronological training-lineage timeline. |
| `HudBackdrop.java` | Lightweight canvas grid and corner registration marks behind the native command deck. |
| `JobWorkspace.java` | Scope presets, artifact discovery/browsing, multi-candidate forms, typed starts/stops, and five-second job logs. |
| `AnalyticsWorkspace.java` | Native line/pie/bar charts separating observed paper outcomes, simulated backtests, and model calibration/holdout/walk-forward evidence. |

The **Model validation** analytics mode reads `/api/v1/models/validation`. It compares model scopes using after-cost holdout and walk-forward return, selective accuracy, expected calibration error, abstention, trade count, profit factor, and bootstrap return intervals. It is read-only and cannot promote a model.
| `DesktopSmokeCheck.java` | Explicit opt-in, read-only visual checks; captures each view and exits without firing controls. |
| `GatewayClientTest.java` | Loopback contract tests with a fake HTTP server, never Alpaca. |
| `jarvis.css` | Restrained black/cyan/green command-center visual system. |

## Development

From the project root, run `scripts\launch_javafx_dashboard.ps1`. The script
checks the Java toolchain and starts the prebuilt classes directly. Use
`scripts\bootstrap_javafx.ps1` once when Java or Maven is absent.

Build and test from PowerShell (project root):

```powershell
$env:JAVA_HOME = (Resolve-Path .tools\jdk-21).Path
& .\.tools\apache-maven-3.9.11\bin\mvn.cmd -f apps\desktop-ui\pom.xml package
```

`target/classes` contains compiled application classes; `target/lib` contains
resolved runtime dependencies. Both are ignored. The Maven launcher remains a
developer option, but the operator shortcut uses the direct JVM launcher with
a 384 MB heap cap. The launcher rebuilds automatically when Java source or
`pom.xml` is newer than the compiled launcher; `-Rebuild` remains available to
force a package. A failed build is not silently accepted.

`GatewayRuntime` holds an exclusive file lock in `logs/dashboard` to prevent
duplicate native instances. It verifies gateway health and owns only that
gateway process, not the bot's broker safety lifecycle. Each launch writes a
fresh `logs/dashboard/javafx_gateway.log` and preserves the prior attempt as
`javafx_gateway.previous.log`. Startup failures include the final gateway log
lines on the boot screen. `JARVIS_PROJECT_ROOT`
selects the project; `JARVIS_PYTHON` is an optional development interpreter
override. Neither variable should contain credentials.

The app uses separate startup, scheduler, telemetry, general-read, graph-read,
operator-command, and long-research workers. A slow Memory Graph request cannot
sit in front of telemetry fallback, and a slow analytics query cannot block a
node inspection. Only one telemetry fallback poll may be queued at a time, so a
temporarily busy SQLite database cannot create an unbounded poll backlog.
JavaFX controls are read on the application thread before requests are queued;
UI changes are applied with `Platform.runLater`. WebSocket events carry sequence
numbers, with read-only HTTP polling as fallback. Live job logs refresh every
five seconds and selected market/trading/intelligence pages every ten seconds.
The gateway reads process logs backward in bounded blocks, so years of retained
bot logs cannot turn the two-second telemetry feed into a full-file scan.
The client gives a newly opened event stream an eight-second first-message grace
before REST fallback, avoiding duplicate cold snapshots against large retained
paper databases. Workspace request failures remain local notifications; only a
telemetry transport failure can mark the command center degraded.

The Python gateway reports liveness before optional cache work begins. It warms
the small provider and Transformer catalogs in the background, while Overview
and Memory Graph data load lazily so a large SQLite archive cannot block boot.
Every HTTP
response includes `X-Dashboard-Response-Ms` and `Server-Timing`; the bounded
gateway aggregate is available at `/api/v1/system/interface-latency`. JavaFX
separately measures full client-observed latency, including transport and queue
effects, and writes `logs/dashboard/javafx_client_latency.json` when the window
closes. These measurements contain route names, status counts, and percentiles,
not credentials or request bodies.

For read-only rendering QA, set `JARVIS_SMOKE_DIR` to an ignored output folder
before launching. It waits for the first real telemetry snapshot, visits every view, captures desktop/compact screenshots,
writes `completed.txt`, and closes itself. It never fires start, stop, settings,
provider-test, or training buttons. Clear that variable before daily use.

Advanced browser volatility experiments and the original Three.js service
graph remain in the fallback client; the native client does not claim feature
parity for those research-only visuals. Its 3D core supports drag and zoom,
pauses when detached, and changes color/speed with backend health.

## Human-Readable Evidence

The native dashboard does not present gateway JSON as the primary operator
interface. `HumanReadableFormatter` converts snake-case keys, UTC timestamps,
booleans, percentages, currency, latency and state values into consistent
labels and local-time descriptions. `HumanReadableView` groups nested results
into bounded evidence sections used by Analytics, AI Lab and Control Plane.
Tables use the same formatting rules, so an order, signal or model has the same
meaning in every workspace.

Exact gateway responses remain available in a collapsed **Developer payload**
section for diagnosis. Secret-like keys are excluded from generated evidence
and never rendered into that operator summary. Live logs and training logs stay
monospace because they are intentionally terminal streams rather than business
records.

The native **AI Lab** is a cached `AiLabWorkspace`. It preserves the current
prompt while navigating, polls `/api/v1/llm/status` every three seconds only
while attached to a scene, and automatically surfaces review completion or
failure. Provider state explicitly distinguishes not configured, not tested,
testing, healthy and failed. A model-list response is not enough to display
healthy; the provider must complete a small generation request.

FinGPT has a separate source and cycle panel in the same workspace. Source
readiness means the configured local FinGPT workflow files were discovered; it
does not mean a large FinGPT checkpoint is loaded. **Run hourly pipeline** and
**Run daily pipeline** launch only the allowlisted `llm-offline-cycle` command.
The hourly workflow links news reactions and produces focused context. The
daily workflow additionally reviews the journal, generates training advice and
creates advisory labels. Cycle state, bounded activity and the latest
structured result update automatically and remain distinct from the ordinary
`llm_analysis` review. Both paths use the selected Ollama/Kimi reasoning engine
and have no broker authority.

## Learned-State Memory Graph

Open **Memory Graph** to inspect what the bot has recorded and which approved
artifacts it can currently use. The JavaFX client requests the compact
`/api/v1/memory-graph/summary`; it never reads SQLite itself. Models, trades, playbooks,
datasets, training experiments, and LLM reviews can be filtered independently,
with one-day through all-history windows. Current decision, market, and risk
nodes remain present as orientation anchors.

The deterministic layout groups models, training runs, datasets, LLM reviews,
trades, and playbooks along a stable elliptical ring with an opening at the top;
the current decision stays in the center while market and risk evidence occupy
the inner band. It is calculated on a Java worker, never on the JavaFX
application thread or trading process. Because the geometry is stable, filters
and refreshes no longer make nodes drift or repeatedly settle.

Selection immediately renders the compact node subtitle, status, and preview
already present in the graph summary. A separate lazy request then replaces it
with complete human-readable evidence. A selection generation guard discards a
late response when the operator has already clicked another node. Content and
layout fingerprints suppress unchanged work; `DecisionCore3D` diff-updates
scene objects by ID and reuses positions when topology is unchanged. One
animation timer pulses active edges.
Green marks approved champions and profitable outcomes, cyan marks current
market/decision flow, blue marks datasets and training artifacts, purple marks
Transformer models, yellow marks candidates or uncertain evidence, red marks
losses/rejections/drift/safety faults, and gray marks archived or unavailable
records. Node size represents evidence volume; brighter pulsing edges identify
the current live path. Selecting a node fills the inspector with the exact
manifest or outcome fields supplied by Python. Selecting a timeline event finds
the corresponding graph node.

This graph is an audit and interpretation surface. It visualizes model
registries, manifests, outcomes, and labels; it is not the model's parameter
memory itself. It exposes no mutation control, promotion action, risk override,
or order method. Overview and Memory Graph workspaces are retained across
navigation so selection and camera context survive. Page changes use a brief
simultaneous crossfade; **Settings > Reduce interface motion** disables page and
core animation persistently. Loading indicators hold the layout while evidence
arrives. A graph-fetch failure remains a local workspace error rather than
declaring the trading system degraded.

Opt-in desktop smoke tests write `frame-times.json` beside screenshots. It
contains sampled JavaFX pulse p50/p95/p99, maximum interval, and frames over
33 ms. `GraphRenderPlanTest` separately guards bounded layout preparation.

## Visual System

The native command deck uses near-black neutral surfaces with role-specific
emerald, cyan, blue, violet, amber, and red accents. Color communicates meaning:
emerald is healthy execution or profit, cyan is data and navigation, blue is
market state, violet is modeling, amber is caution, and red is loss or danger.
An active numbered navigation rail, telemetry status band, bounded panel depth,
and responsive metric cards keep the interface scannable during live operation.

`HudBackdrop` paints a low-cost coordinate grid directly on a JavaFX `Canvas`.
`DecisionCore3D` uses native spheres, three independent orbital systems, a
deterministic depth field, two lights, and a breathing wireframe halo. State
changes alter the core lighting and animation rate. Page changes use a short
fade-and-lift transition; no animation owns or mutates trading state.

The browser dashboard remains in `src/gld_scalper/dashboard/static` as a
fallback and API development harness. The installed operator shortcut launches
this JavaFX client.

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
