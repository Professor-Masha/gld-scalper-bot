# Dashboard Static Interface

This folder contains only browser-facing command-center assets. Trading logic and broker authority remain in Python.

- `index.html`: semantic shell and core views.
- `app.js`: dashboard controller, dynamic workstations, live telemetry, provider controls, and safe actions.
- `styles.css`, `enhancements.css`, `hud.css`: design system, responsive analytics, and Three.js layer.
- `manifest.webmanifest`: installable web-app identity.
- `assets/`: generated MC PNG and Windows ICO.
- `vendor/`: local Three.js modules and the Lucide browser icon bundle, keeping
  the installed dashboard independent of external CDNs.
- `js/`: focused browser classes.

The dynamic workstations include Analytics, AI Lab, 3D Core, Backtest Lab, and
the White Paper reader. The 3D scene is a visualization and receives only
telemetry; it has no order or model-promotion methods.

The interface is localhost-only. Mutations require a per-launch token; browser code cannot submit broker orders directly.
