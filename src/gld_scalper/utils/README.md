# Shared Utilities

Small cross-cutting helpers used by runtime and offline tooling.

Return to the [project manual](../../../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`__init__.py`](../../../src/gld_scalper/utils/__init__.py) | Utility helpers for the GLD scalper. |
| [`logging_utils.py`](../../../src/gld_scalper/utils/logging_utils.py) | Python module exposing `SQLiteLogHandler`, `configure_logging`, `json_dumps`. |
| [`math_utils.py`](../../../src/gld_scalper/utils/math_utils.py) | Python module exposing `clamp`, `safe_div`, `pct_change`. |
| [`retry_utils.py`](../../../src/gld_scalper/utils/retry_utils.py) | Python module exposing `retry`. |
| [`time_utils.py`](../../../src/gld_scalper/utils/time_utils.py) | Python module exposing `utc_now`, `ensure_utc`, `utc_iso`, `parse_date`. |

## Python Interfaces, Variables, And Linkage

#### `logging_utils.py`
**Public interfaces:** `SQLiteLogHandler`, `configure_logging`, `json_dumps`.

#### `math_utils.py`
**Public interfaces:** `clamp`, `safe_div`, `pct_change`.

#### `retry_utils.py`
**Public interfaces:** `retry`.
**Module constants:** `T`.

#### `time_utils.py`
**Public interfaces:** `utc_now`, `ensure_utc`, `utc_iso`, `parse_date`, `floor_to_minute`, `seconds_until_next_minute`, `market_session`, `minutes_since_open`, `minutes_before_close`.
**Module constants:** `UTC`.

The interface list is generated from public top-level classes/functions and uppercase module constants. Read type annotations and tests before changing semantics; private helpers are implementation details but can still participate in safety invariants.

## Programmer Guide: Leaf Dependencies

Utilities are deliberately small leaf modules. Higher layers may import them;
utilities should rarely import higher layers. The one notable infrastructure
link is `logging_utils.py` using the database write lock for SQLite logging.

### Time Is A Domain Rule

`time_utils.py` normalizes external timestamps with `ensure_utc()` and provides
New York market-session calculations. Alpaca timestamps, SQLite values, labels,
and model sequence indexes must be timezone-aware. Do not compare naive local
datetime values with UTC or assume Nairobi time is the exchange session.

`seconds_until_next_minute()` is used by the minute loop; `market_session()`,
`minutes_since_open()`, and `minutes_before_close()` affect entries, event
profiles, retraining windows, and shutdown management. A time-helper change can
therefore alter trading behavior and needs focused tests around daylight-saving
transitions, holidays, and boundaries.

### Numerical Guardrails

`math_utils.py` centralizes `safe_div`, `clamp`, and percentage change behavior.
These functions define how missing denominators and bounded scores behave across
features, risk, reports, and ML. Keep their units explicit: a decimal return of
`0.001` means `0.1%`, not `1%`.

### Retry And Logging

`retry_utils.py` is for bounded idempotent operations. Do not wrap order
submission in a generic retry unless idempotency is proven by the order
coordinator and client order ID.

`logging_utils.py` configures console/file/SQLite handlers. Structured
`event_type` and context fields are operational data used in audits. Never log
API keys, authorization headers, full secrets, or unrestricted provider
responses.

### Adding A Utility

Create a utility only when behavior is small, domain-neutral, reused, and easy
to test independently. Trading concepts such as stop geometry, liquidity, or
playbook scoring belong in their domain module even if they are mathematically
short.

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
