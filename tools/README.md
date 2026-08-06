# Developer And Data Tools

Standalone maintenance, download, export, reporting, diagram, and documentation utilities.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`backtest_2023_2026_report.py`](../tools/backtest_2023_2026_report.py) | Python module exposing `main`, `configure_run`, `parse_utc_date`, `build_paths`. |
| [`download_historical_data.py`](../tools/download_historical_data.py) | Python implementation module. |
| [`download_news_archive.py`](../tools/download_news_archive.py) | Python implementation module. |
| [`export_historical_dataset.py`](../tools/export_historical_dataset.py) | Python module exposing `main`. |
| [`generate_folder_readmes.py`](../tools/generate_folder_readmes.py) | Python module exposing `tracked_files`, `module_details`, `description`, `file_inventory`. |
| [`render_architecture_diagrams.py`](../tools/render_architecture_diagrams.py) | Python module exposing `font`, `text_lines`, `box`, `diamond`. |
| [`update_readme_presentation.py`](../tools/update_readme_presentation.py) | Python module exposing `replace_mermaid_after_heading`, `main`. |

## Python Interfaces, Variables, And Linkage

#### `backtest_2023_2026_report.py`
**Public interfaces:** `main`, `configure_run`, `parse_utc_date`, `build_paths`, `build_settings`, `copy_source_database`, `remove_sqlite_sidecars`, `download_historical_dataset`, `download_chunk`, `run_backtest`, `run_backtest_segmented`, `load_existing_backtest_result`, `dataset_coverage`, `analyze_result`, `grouped_stats`, `equity_curve_rows`, `trade_to_dict`, `build_observations`.
**Module constants:** `PROJECT_ROOT`, `SRC_ROOT`, `DATASET_NAME`, `START`, `END`.
**Internal dependencies:** `gld_scalper.alpaca_clients`, `gld_scalper.backtester`, `gld_scalper.config`, `gld_scalper.data_collector`, `gld_scalper.database`, `gld_scalper.models`, `gld_scalper.reports.csv_exporter`, `gld_scalper.utils.time_utils`.

#### `export_historical_dataset.py`
**Public interfaces:** `main`.
**Module constants:** `PROJECT_ROOT`, `SRC_ROOT`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.reports.csv_exporter`, `gld_scalper.utils.time_utils`.

#### `generate_folder_readmes.py`
**Public interfaces:** `tracked_files`, `module_details`, `description`, `file_inventory`, `python_interfaces`, `model_inventory`, `write_readme`, `main`.
**Module constants:** `ROOT`, `FOLDERS`, `HANDWRITTEN`.

#### `render_architecture_diagrams.py`
**Public interfaces:** `font`, `text_lines`, `box`, `diamond`, `center`, `arrow`, `canvas`, `save`, `runtime_architecture`, `decision_flow`, `learning_flow`, `main`.
**Module constants:** `ROOT`, `OUTPUT`, `WIDTH`, `HEIGHT`, `INK`, `MUTED`, `LINE`, `BLUE`, `GREEN`, `AMBER`, `RED`, `VIOLET`, `TEAL`, `GRAY`, `WHITE`, `TITLE`, `SUBTITLE`, `BOX_TITLE`.

#### `update_readme_presentation.py`
**Public interfaces:** `replace_mermaid_after_heading`, `main`.
**Module constants:** `ROOT`, `README`, `DIAGRAMS`, `SYMBOL_GUIDE`.

The interface list is generated from public top-level classes/functions and uppercase module constants. Read type annotations and tests before changing semantics; private helpers are implementation details but can still participate in safety invariants.

## Programmer Guide: Standalone Tool Architecture

Files in this folder are executable maintenance programs, not modules imported
by the live paper loop. They may reuse `gld_scalper` services, but they construct
their own settings/database context and should be run with `run-paper` stopped
when they perform heavy work or write the same database.

### Historical Data Downloader

`download_historical_data.py` builds a dataset-specific database below the
historical paper-data root. Its architecture is:

```text
CLI arguments
 -> build_paths/build_settings
 -> date chunk generator
 -> Alpaca paginated request
 -> normalization to database records
 -> bounded batch upsert
 -> atomic progress checkpoint
 -> optional research sidecars/labels
 -> manifest and coverage summary
```

Bars use larger windows; quote/trade microstructure uses adaptive windows that
split on memory-heavy failures. Completed chunk keys make reruns resumable.
Progress files are files, not directories. Coverage over enormous quote tables
can be skipped or cached because a full aggregate scan is expensive.

### News Archive Downloader

`download_news_archive.py` fetches small time windows, normalizes provider
records, records matched macro terms and event classes, checkpoints completed
windows, exports CSV, and later links news timestamps to GLD price/spread
outcomes. Rate-limit waits are bounded and provider errors remain visible.

### Historical Export And Backtest Report

`export_historical_dataset.py` opens one named historical database and delegates
to the normal CSV exporter. `backtest_2023_2026_report.py` creates an isolated
working database, runs segmented backtests, aggregates direction/regime/hour
statistics, writes CSV evidence, and constructs the DOCX report. It must not
write results into the live paper database.

### Documentation Tools

`render_architecture_diagrams.py` draws the JPEG architecture assets referenced
by the root README. `update_readme_presentation.py` updates those references and
math presentation. `generate_folder_readmes.py` inventories tracked files and
public Python interfaces.

The programmer guides in these READMEs contain hand-written architecture
explanations. Review generator output before replacing a README so those manual
sections are not accidentally discarded.

### Reading Or Changing A Tool

Start at its `main()`, then follow argument parsing, path construction, settings
overrides, external calls, persistence, checkpointing, and final output. Keep
network pagination separate from normalization and database writes. A failed
window must be safely repeatable; an interrupted run must not mark unfinished
data complete.

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
