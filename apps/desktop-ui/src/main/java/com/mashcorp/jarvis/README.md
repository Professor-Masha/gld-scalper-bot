# Native Client Classes

The application flow is `Launcher -> JarvisApplication -> GatewayRuntime ->
GatewayClient -> Python gateway`. No Java class has broker execution authority.

| Class | Important state and connections |
|---|---|
| `Launcher` | The plain JVM entry point invokes JavaFX startup. |
| `GatewayRuntime` | `projectRoot`, `port`, `token`, `process`, and `instanceLock` own the local gateway lifecycle. It resolves paths, starts Python, waits for health and closes only its child. |
| `GatewayClient` | `baseUri`, `token`, `http`, and `json` implement loopback-only transport. Mutations carry idempotency/correlation metadata; errors are returned to the operator. |
| `ClientLatencyMonitor` | Keeps bounded end-to-end client timings without retaining request bodies or credentials. |
| `JarvisApplication` | `workspace`, labels, `latestSnapshot`, `socket`, and `eventSequence` project backend truth. Dedicated startup, telemetry, read, graph, command, and research executors keep I/O off the UI thread. |
| `JobWorkspace` | `action`, `scope`, `catalog`, archive selectors and input controls create bounded options for allowlisted Python jobs. The view timer tails the selected job and stops when detached. |
| `AnalyticsWorkspace` | `mode` separates actual paper outcomes from simulated backtest results; `metrics` and `plots` render gateway-provided evidence. It does not recompute trading labels. |
| `GraphRenderPlan` | Converts bounded summary JSON into deterministic open-ring positions away from the JavaFX thread. Types occupy stable arcs, the latest decision is centered, and market/risk evidence uses the inner band. |
| `DecisionCore3D` | `scene`, open ring, learned-state nodes, orbital fallback, and transitions are presentation only. Drag/zoom alter the camera, not model or risk state. |
| `MemoryGraphWorkspace` | Gives the graph most of the split view, preserves filters and selection, shows summary evidence immediately, and loads detailed node evidence lazily. |
| `NodeInspector` | Presents compact previews and complete node details as labeled human-readable sections rather than JSON. |
| `HumanReadableFormatter` | Converts gateway keys and typed values into bounded operator labels, local timestamps, money, percentages and safe summaries. |
| `HumanReadableView` | Reusable sectioned evidence control with an optional collapsed developer payload. |
| `AiLabWorkspace` | Preserves provider/prompt state, runs generation checks off-thread, polls managed review jobs, and renders completion or failure automatically. |
| `DesktopSmokeCheck` | `views`, `index` and `directory` capture read-only screens for explicit QA without activating a job. |

All GUI mutation belongs on the JavaFX application thread. Capture field values
before submitting background work. Do not put secrets in logs or URL parameters.
Update the folder and root manuals when adding a route, form or job option.
