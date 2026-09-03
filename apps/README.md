# Operator Applications

`desktop-ui/` is the native JavaFX Windows command center. Its transport boundary
is the local FastAPI gateway in `src/gld_scalper/dashboard/`. Trading, model
training, promotion, SQLite writes and risk enforcement remain Python-owned.

See [desktop-ui/README.md](desktop-ui/README.md) for build instructions, class
ownership, test entry points and operational limitations. Do not place data,
secrets, downloaded runtimes or model archives in this folder.
