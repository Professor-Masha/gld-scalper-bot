# Browser Classes

- `api.js`: `ApiClient`, the HTTP query and mutation boundary.
- `charts.js`: responsive `MarketChart`, `SignalMatrixChart`, `LineChart`, `BarChart`, and `DonutChart` classes. Charts measure their containers using `ResizeObserver`.
- `hud-scene.js`: low-power Three.js `HudScene`; it reacts subtly to the pointer, pauses in hidden tabs, respects reduced motion, and never intercepts input.

`app.js` composes these classes. Broker and strategy logic must remain outside the browser layer.
