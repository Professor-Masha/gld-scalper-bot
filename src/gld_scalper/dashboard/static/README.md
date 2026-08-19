# Dashboard Static Interface

This folder contains only browser-facing command-center assets. Trading logic and broker authority remain in Python.

- `index.html`: semantic shell and core views.
- `app.js`: dashboard controller, dynamic Analytics/AI workstations, live telemetry, and safe actions.
- `styles.css`, `enhancements.css`, `hud.css`: design system, responsive analytics, and Three.js layer.
- `manifest.webmanifest`: installable web-app identity.
- `assets/`: generated MC PNG and Windows ICO.
- `vendor/`: pinned local Three.js `0.185.1` modules.
- `js/`: focused browser classes.

The interface is localhost-only. Mutations require a per-launch token; browser code cannot submit broker orders directly.
