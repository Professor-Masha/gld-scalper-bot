# Project Documentation Assets

Version-controlled visual and supporting documentation used by the root manual.

[`BOT_WHITE_PAPER.md`](BOT_WHITE_PAPER.md) is the concise, versioned technical
white paper for the platform. It covers the problem, architecture, equations,
playbooks, execution, risk, ML, causal Transformers, Ollama/Kimi/FinGPT roles,
validation, security, limitations, and deployment governance. The local
command center renders this same source in its **White Paper** workspace.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

_This directory contains only child directories and this guide._

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
