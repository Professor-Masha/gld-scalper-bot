# Native Client Classes

The application flow is `Launcher -> JarvisApplication -> GatewayRuntime ->
GatewayClient -> Python gateway`. No Java class has broker execution authority.

| Class | Important state and connections |
|---|---|
| `Launcher` | The plain JVM entry point invokes JavaFX startup. |
| `GatewayRuntime` | `projectRoot`, `port`, `token`, `process`, and `instanceLock` own the local gateway lifecycle. It resolves paths, starts Python, waits for health and closes only its child. |
| `GatewayClient` | `baseUri`, `token`, `http`, and `json` implement loopback-only transport. Mutations carry idempotency/correlation metadata; errors are returned to the operator. |
| `JarvisApplication` | `workspace`, labels, `latestSnapshot`, `socket`, and `eventSequence` project backend truth. Separate `worker`, `commands`, and `research` executors keep I/O off the UI thread. |
| `JobWorkspace` | `action`, `scope`, `catalog`, archive selectors and input controls create bounded options for allowlisted Python jobs. The view timer tails the selected job and stops when detached. |
| `AnalyticsWorkspace` | `mode` separates actual paper outcomes from simulated backtest results; `metrics` and `plots` render gateway-provided evidence. It does not recompute trading labels. |
| `DecisionCore3D` | `scene`, `nucleus`, orbital groups and transitions are presentation only. Drag/zoom alter the camera, not model or risk state. |
| `DesktopSmokeCheck` | `views`, `index` and `directory` capture read-only screens for explicit QA without activating a job. |

All GUI mutation belongs on the JavaFX application thread. Capture field values
before submitting background work. Do not put secrets in logs or URL parameters.
Update the folder and root manuals when adding a route, form or job option.
