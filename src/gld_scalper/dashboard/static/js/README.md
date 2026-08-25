# Browser Classes

- `api.js`: `ApiClient`, the HTTP query and mutation boundary.
- `charts.js`: responsive `MarketChart`, `SignalMatrixChart`, `LineChart`, `BarChart`, and `DonutChart` classes. Charts measure their containers using `ResizeObserver`.
- `hud-scene.js`: telemetry-reactive Three.js `HudScene` implemented as an Obsidian-style operational knowledge graph. Sixteen selectable nodes and directed flow edges describe the real data, agent, model, risk, execution, broker, memory, and research topology. Live process/database/signal telemetry changes node status and edge activity. Drag orbits the graph, the wheel zooms it, ray-casting selects a node, and reduced-motion support remains available. It has no trading authority.
- `workspaces.js`: semantic markup factory for Analytics, AI Lab, 3D Core, Backtest Lab, and White Paper workspaces.
- `whitepaper-view.js`: safe local Markdown renderer and section index for the versioned bot white paper.

`app.js` composes these classes. Broker and strategy logic must remain outside the browser layer.
