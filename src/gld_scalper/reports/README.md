# Report Builders

Programmatic performance and research report generation.

Return to the [project manual](../../../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`__init__.py`](../../../src/gld_scalper/reports/__init__.py) | Reporting helpers. |
| [`csv_exporter.py`](../../../src/gld_scalper/reports/csv_exporter.py) | Python module exposing `CSVExportResult`, `HourlyCSVExportScheduler`, `export_database_to_csv`. |
| [`daily_report.py`](../../../src/gld_scalper/reports/daily_report.py) | Python module exposing `generate_daily_report`. |
| [`performance_report.py`](../../../src/gld_scalper/reports/performance_report.py) | Python module exposing `root_episode_rows`, `performance_breakdown_from_database`, `trade_metrics_from_database`. |

## Python Interfaces, Variables, And Linkage

#### `csv_exporter.py`
**Public interfaces:** `CSVExportResult`, `HourlyCSVExportScheduler`, `export_database_to_csv`.
**Module constants:** `EXPORT_TABLES`, `EXPORT_FILTER_COLUMNS`, `EXPORT_ORDER_COLUMNS`, `EXPORT_TABLE_CATEGORIES`.

#### `daily_report.py`
**Public interfaces:** `generate_daily_report`.

#### `performance_report.py`
**Public interfaces:** `root_episode_rows`, `performance_breakdown_from_database`, `trade_metrics_from_database`.

The interface list is generated from public top-level classes/functions and uppercase module constants. Read type annotations and tests before changing semantics; private helpers are implementation details but can still participate in safety invariants.

## Programmer Guide: From SQLite To Human Evidence

Reporting modules are read-only projections over durable trading state. They do
not recalculate broker truth, repair episodes, promote models, or mutate trading
decisions.

### CSV Export Path

`csv_exporter.py` owns both explicit exports and the hourly scheduler:

```text
run_paper_command
 -> HourlyCSVExportScheduler.maybe_export(Database, now)
 -> export_database_to_csv()
 -> category/table CSV files in a timestamped directory
 -> latest-access copy/pointer structure
```

`EXPORT_TABLES` defines the export surface. `EXPORT_TABLE_CATEGORIES` places
each table in a unique subject folder, while filter and order-column mappings
keep a one-hour export bounded and chronologically readable. Adding a database
table does not automatically export it; add it deliberately with a stable
timestamp/order contract and test it.

### Performance Aggregation

`performance_report.py` starts from root execution episodes. Partial-profit
tranches are children of one trading idea and must not be counted as independent
strategy wins. `root_episode_rows()` normalizes that unit of analysis, and the
breakdown functions group it by direction, strategy path, playbook, regime,
hour, exit reason, model prediction, and confidence.

The report consumes stored cost and excursion fields. It should not infer a
fill from an order status or replace missing costs with flattering assumptions.

### Daily Report

`daily_report.py` composes account snapshots, episode-level performance,
decisions, and operational evidence into a text report. The CLI route is:

```text
main.report_command -> generate_daily_report(Database, report_date)
```

Keep calculations in reusable report functions rather than embedding SQL and
math directly in the CLI command. Tests should assert episode counts and metric
semantics, not only that a file was created.

### Extending Reports

First identify the authoritative source table and the correct grain: event,
decision, order, fill, tranche, root episode, account snapshot, or model
version. Join on explicit IDs, preserve UTC timestamps, state how missing values
are treated, and compare totals with the consistency audit before presenting a
new metric as performance.

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
