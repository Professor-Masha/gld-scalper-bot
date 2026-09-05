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

## Files

| File | Responsibility |
|---|---|
| `pom.xml` | Pins Java 21, JavaFX 21, Jackson, compiler, and JavaFX launcher. |
| `Launcher.java` | Stable non-JavaFX JVM entry point. |
| `GatewayRuntime.java` | Secure lifecycle for the local Python gateway. |
| `GatewayClient.java` | REST/WebSocket transport and authenticated commands. |
| `JarvisApplication.java` | Native window, navigation, tables, charts, forms, and state projection. |
| `DecisionCore3D.java` | Native 3D force layout, color/size encoding, live-evidence pulses, selection, drag, and zoom. It consumes bounded JSON and has no database or broker dependency. |
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
a 384 MB heap cap. Rebuild after source changes using the command above or the
launcher `-Rebuild` switch. A failed build is not silently accepted.

`GatewayRuntime` holds an exclusive file lock in `logs/dashboard` to prevent
duplicate native instances. It verifies gateway health and owns only that
gateway process, not the bot's broker safety lifecycle. `JARVIS_PROJECT_ROOT`
selects the project; `JARVIS_PYTHON` is an optional development interpreter
override. Neither variable should contain credentials.

The app uses separate telemetry, operator-command and long-research workers.
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

For read-only rendering QA, set `JARVIS_SMOKE_DIR` to an ignored output folder
before launching. It waits for the first real telemetry snapshot, visits every view, captures desktop/compact screenshots,
writes `completed.txt`, and closes itself. It never fires start, stop, settings,
provider-test, or training buttons. Clear that variable before daily use.

Advanced browser volatility experiments and the original Three.js service
graph remain in the fallback client; the native client does not claim feature
parity for those research-only visuals. Its 3D core supports drag and zoom,
pauses when detached, and changes color/speed with backend health.

## Learned-State Memory Graph

Open **Memory Graph** to inspect what the bot has recorded and which approved
artifacts it can currently use. The JavaFX client requests
`/api/v1/memory-graph`; it never reads SQLite itself. Models, trades, playbooks,
datasets, training experiments, and LLM reviews can be filtered independently,
with one-day through all-history windows. Current decision, market, and risk
nodes remain present as orientation anchors.

The force layout runs on bounded response data outside the trading process.
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
or order method. Repeated refreshes stop prior edge animations before rendering
new ones, and a graph-fetch failure remains a local workspace error rather than
declaring the trading system degraded.

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
