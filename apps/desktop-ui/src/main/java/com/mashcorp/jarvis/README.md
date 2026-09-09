# Native Client Classes

The application flow is `Launcher -> JarvisApplication -> GatewayRuntime ->
GatewayClient -> Python gateway`. No Java class has broker execution authority.

| Class | Important state and connections |
|---|---|
| `Launcher` | The plain JVM entry point invokes JavaFX startup. |
| `GatewayRuntime` | `projectRoot`, `port`, `token`, `process`, `gatewayLogPath`, and `instanceLock` own the local gateway lifecycle. It resolves paths, uses a unique session log when Windows locks the conventional log, starts Python, waits for health and closes only its child. |
| `GatewayClient` | `baseUri`, `token`, `http`, and `json` implement loopback-only transport. Mutations carry idempotency/correlation metadata; errors are returned to the operator. |
| `ClientLatencyMonitor` | Keeps bounded end-to-end client timings without retaining request bodies or credentials. |
| `JarvisApplication` | `workspace`, labels, `latestSnapshot`, `socket`, and `eventSequence` project backend truth. Dedicated startup, telemetry, read, graph, command, and research executors keep I/O off the UI thread. |
| `DecisionTelemetry` | Validates and freezes the latest gateway snapshot into display-safe quote, prediction, economics, evidence, episode, outcome, and performance records. |
| `LiveDecisionWorkspace` | Owns the Overview's Live Decision, Evidence Radar and Trade Anatomy tabs. Percentage-constrained grids keep all primary panels in one row, compact styles reduce density on narrow screens, and the latest frame survives reduced-motion-aware tab transitions. |
| `EvidenceOrderChain` | Projects each immutable evidence gate onto one continuous market-data-to-execution rail with state-colored nodes, snapshot times, and human-readable gate titles. It has no command or broker methods. |
| `DecisionPricePlot` | Keeps at most 160 timestamp-distinct midpoint observations and paints price/quote/episode markers on a lightweight canvas. |
| `DecisionRadar` | Paints six measured or gate-backed axes. It never infers a missing model probability or writes application state. |
| `MarketPulseWorkspace` | Composes the Evidence Radar operating deck: service header, GLD/context/decision rail, circular radar, typed live-frame event feed, four bounded telemetry plots, and governance legend. |
| `PulseSparkline` | Retains at most 160 real interface-frame values for return, spread, liquidity, or P/L and paints a bounded mini chart. |
| `CanvasSurface` | Clamps all canvas backing textures to finite 2048-pixel dimensions before Prism allocates GPU resources. |
| `JobWorkspace` | `action`, `scope`, `catalog`, archive selectors and input controls create bounded options for allowlisted Python jobs. The view timer tails the selected job and stops when detached. |
| `AnalyticsWorkspace` | `mode` separates actual paper outcomes from simulated backtest results; `metrics` and `plots` render gateway-provided evidence. It does not recompute trading labels. |
| `GraphRenderPlan` | Converts bounded summary JSON into deterministic open-ring positions away from the JavaFX thread. Types occupy stable arcs, the latest decision is centered, and market/risk evidence uses the inner band. |
| `DecisionCore3D` | `scene`, open ring, learned-state nodes, orbital fallback, and transitions are presentation only. Drag/zoom alter the camera, not model or risk state. |
| `MemoryGraphWorkspace` | Gives the graph most of the split view, preserves filters and selection, shows summary evidence immediately, and loads detailed node evidence lazily. |
| `NodeInspector` | Presents compact previews and complete node details as labeled human-readable sections rather than JSON. |
| `AiLabWorkspace` | Keeps provider generation health, focused-review state, and FinGPT source/cycle state separate. It launches only allowlisted offline jobs and renders bounded results without exposing broker methods. |
| `HumanReadableFormatter` | Converts gateway keys and typed values into bounded operator labels, local timestamps, money, percentages and safe summaries. |
| `HumanReadableView` | Reusable sectioned evidence control with an optional collapsed developer payload. |
| `AiLabWorkspace` | Preserves provider/prompt state, runs generation checks off-thread, polls managed review jobs, and renders completion or failure automatically. |
| `DesktopSmokeCheck` | `views`, `index`, `compactViewCount` and `directory` capture all three operational screens at desktop and compact sizes without activating a job. |

All GUI mutation belongs on the JavaFX application thread. Capture field values
before submitting background work. Do not put secrets in logs or URL parameters.
Update the folder and root manuals when adding a route, form or job option.

The Overview pipeline is `GatewayClient event -> JarvisApplication snapshot ->
DecisionTelemetry.from -> LiveDecisionWorkspace.update`. This path formats
already-computed evidence only. Python remains responsible for data alignment,
features, inference, risk, reconciliation and broker execution. Memory Graph has
its own lazy summary/detail pipeline and does not share the live canvas history.
