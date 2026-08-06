# Linux Service Definitions

systemd templates for supervised server operation.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`gld-scalper.service`](../systemd/gld-scalper.service) | systemd service unit template. |

## Programmer Guide: Linux Process Lifecycle

`gld-scalper.service` is a process supervisor definition, not a second bot
implementation. Its important connections are:

```text
systemd
 -> WorkingDirectory=/opt/gld_scalper_bot
 -> EnvironmentFile=/opt/gld_scalper_bot/.env
 -> PYTHONPATH=/opt/gld_scalper_bot/src
 -> .venv/bin/python -m gld_scalper.main run-paper
 -> main.run_paper_command()
```

`After` and `Wants` request network readiness before startup. `Type=simple`
means systemd supervises the Python process directly. `Restart=on-failure`
restarts unexpected nonzero exits after the configured delay; an ordinary clean
stop is not treated as a crash.

The service runs as the configured non-root user, so that user must be able to
read `.env`, execute the virtual environment, and write the paper database,
logs, exports, and model-runtime paths. Keep `.env` permissions restrictive and
never embed credentials in the unit file.

Stopping the unit sends a termination signal to Python. The bot's own shutdown
path must still freeze entries, stop workers, reconcile, and confirm flattening.
Do not use an aggressive systemd timeout that kills Python before that bounded
shutdown can complete.

When changing installation paths, update `WorkingDirectory`, `EnvironmentFile`,
`PYTHONPATH`, and `ExecStart` together. Validate the rendered unit with
`systemd-analyze verify`, reload with `systemctl daemon-reload`, then inspect
`journalctl -u gld-scalper` after a controlled paper-only start and stop.

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
