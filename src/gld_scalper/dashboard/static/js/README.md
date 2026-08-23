# Browser Classes

- `api.js`: `ApiClient`, the HTTP query and mutation boundary.
- `charts.js`: responsive `MarketChart`, `SignalMatrixChart`, `LineChart`, `BarChart`, and `DonutChart` classes. Charts measure their containers using `ResizeObserver`.
- `hud-scene.js`: telemetry-reactive Three.js `HudScene` with orbital geometry, a market-bar ring, trace paths, pointer parallax, reduced-motion support, and no trading authority.
- `workspaces.js`: semantic markup factory for Analytics, AI Lab, 3D Core, Backtest Lab, and White Paper workspaces.
- `whitepaper-view.js`: safe local Markdown renderer and section index for the versioned bot white paper.

`app.js` composes these classes. Broker and strategy logic must remain outside the browser layer.
