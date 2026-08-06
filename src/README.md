# Python Source Tree

Installable application source rooted at the gld_scalper package.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

_This directory contains only child directories and this guide._

## How To Read The Source Tree

Python packaging uses the `src` layout. Keeping importable code below `src`
prevents accidental imports from the repository root during development.

```text
src/
`-- gld_scalper/              installable application package
    |-- main.py               CLI and process composition root
    |-- config.py             typed configuration and safety validation
    |-- database.py           durable repository boundary
    |-- models.py             shared in-memory domain messages
    |-- ml/                   fitting, registry, validation, inference
    |-- reports/              read-only projections and CSV/report output
    `-- utils/                low-level time, math, retry, and logging helpers
```

The root is not an alternative runtime package. Standalone programs in
`tools/` prepend `src` to `sys.path` only so they can reuse the installed
application modules. Production entry points should import `gld_scalper`, never
`src.gld_scalper`.

## Layer Direction

The intended import direction is from orchestration toward smaller domain
units:

```text
main.py
 -> runtimes/services
 -> calculations/domain dataclasses
 -> utils
```

`Database` and `Settings` are shared infrastructure dependencies. Broker client
construction belongs in `alpaca_clients.py` and is used only by collection,
execution, reconciliation, and account-state services. Pure feature, strategy,
ML, reporting, and LLM interpretation code must not gain hidden broker-order
authority.

Start with the [root programmer tour](../README.md#programmers-source-code-tour),
then continue into the [runtime package guide](gld_scalper/README.md).

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
