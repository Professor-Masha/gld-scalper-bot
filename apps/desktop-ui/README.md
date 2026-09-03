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
6. `DecisionCore3D` visualizes backend state with a hardware-accelerated JavaFX
   `SubScene`. It is a display component and cannot influence a trade.

## Files

| File | Responsibility |
|---|---|
| `pom.xml` | Pins Java 21, JavaFX 21, Jackson, compiler, and JavaFX launcher. |
| `Launcher.java` | Stable non-JavaFX JVM entry point. |
| `GatewayRuntime.java` | Secure lifecycle for the local Python gateway. |
| `GatewayClient.java` | REST/WebSocket transport and authenticated commands. |
| `JarvisApplication.java` | Native window, navigation, tables, charts, forms, and state projection. |
| `DecisionCore3D.java` | Native 3D telemetry visualization. |
| `JobWorkspace.java` | Scope presets, artifact discovery/browsing, multi-candidate forms, typed starts/stops, and five-second job logs. |
| `AnalyticsWorkspace.java` | Native line/pie/bar charts separating observed paper outcomes from simulated backtest metrics. |
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

For read-only rendering QA, set `JARVIS_SMOKE_DIR` to an ignored output folder
before launching. It visits every view, captures desktop/compact screenshots,
writes `completed.txt`, and closes itself. It never fires start, stop, settings,
provider-test, or training buttons. Clear that variable before daily use.

Advanced browser volatility experiments and the original Three.js service
graph remain in the fallback client; the native client does not claim feature
parity for those research-only visuals. Its 3D core supports drag and zoom,
pauses when detached, and changes color/speed with backend health.

The browser dashboard remains in `src/gld_scalper/dashboard/static` as a
fallback and API development harness. The installed operator shortcut launches
this JavaFX client.

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
