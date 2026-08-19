# Operations Scripts

PowerShell and shell entry points for Windows and server deployment.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`launch_dashboard.ps1`](../scripts/launch_dashboard.ps1) | Starts or reuses the local dashboard server and opens Edge in standalone app mode. |
| [`stop_dashboard.ps1`](../scripts/stop_dashboard.ps1) | Validates and stops only the recorded dashboard server process. |
| [`install_dashboard_shortcut.ps1`](../scripts/install_dashboard_shortcut.ps1) | Creates the current user's Desktop application shortcut. |
| [`install_git_hooks.ps1`](../scripts/install_git_hooks.ps1) | Windows PowerShell operations entry point. |
| [`install_ubuntu.sh`](../scripts/install_ubuntu.sh) | POSIX shell operations entry point. |
| [`run_backfill.sh`](../scripts/run_backfill.sh) | POSIX shell operations entry point. |
| [`run_paper.sh`](../scripts/run_paper.sh) | POSIX shell operations entry point. |
| [`train_model.sh`](../scripts/train_model.sh) | POSIX shell operations entry point. |

## Programmer Guide: Thin Operational Wrappers

Scripts in this folder are intentionally thin. Business logic belongs in the
Python package so Windows, Linux, tests, and service deployment all execute the
same implementation.

| Script | Connection to Python |
|---|---|
| `launch_dashboard.ps1` | Health-checks localhost, starts `gld_scalper.main dashboard` in a hidden process when needed, then opens the local app. |
| `stop_dashboard.ps1` | Checks the PID and command line before stopping the dashboard; it does not stop or replace the bot's safety shutdown. |
| `install_dashboard_shortcut.ps1` | Uses Windows Script Host to create a Desktop `.lnk` that invokes the launcher without exposing a console window. |
| `run_paper.sh` | Changes to the project context and invokes `python -m gld_scalper.main run-paper`. |
| `run_backfill.sh` | Invokes the CLI backfill command using environment configuration. |
| `train_model.sh` | Invokes the classical training command; promotion remains controlled by Python. |
| `install_ubuntu.sh` | Creates/uses the server environment and installs project dependencies. |
| `install_git_hooks.ps1` | Installs repository-local Git hooks for the Windows development workflow. |

The wrapper process inherits `.env` and shell environment values consumed by
`load_settings()`. Do not place API keys directly in script source or command
history. Keep quoting correct for project paths containing spaces.

When adding a script, make it fail on command errors, resolve the project path
explicitly, invoke a documented CLI command, and propagate the Python exit code.
Do not duplicate risk settings, SQL, training formulas, or broker logic in shell
code.

## Linkage And Change Discipline

1. Start at the composition root in `src/gld_scalper/main.py` or the invoking tool/script.
2. Follow typed settings from `config.py`; environment values should not be read ad hoc elsewhere.
3. Follow persistence through `database.py` and `schema.sql`; multi-row execution state must remain transactional.
4. Follow behavioral evidence into the matching tests before changing a public interface.
5. Run focused tests first, then the complete suite. Paper execution is the final verification stage, not the first.

## Data And Security

Tracked code and promoted model memory may be committed. Raw market data, account data, exports, logs, API keys, and local Ollama model blobs stay outside Git. Model artifacts must retain their checksum, manifest, training range, exact feature profile, metrics, and rollback lineage.

---

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
