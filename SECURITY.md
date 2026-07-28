# Security Policy

Copyright (c) 2026 @Mashcorp. All rights reserved.

## Secrets

Never commit credentials or private operational state. In particular, do not
commit:

- `.env`
- Alpaca API keys or secret keys
- FRED API keys
- GitHub tokens
- private SSH keys
- broker account exports
- local SQLite databases

Use `.env.example` only for documented placeholder values. Real credentials
belong in the local `.env` file or a server secret manager.

If a credential is committed, revoke and replace it immediately. Removing the
file in a later commit is not enough because the secret remains in Git history.

## Data Boundary

This repository may contain source code, documentation, curated local research
documents, and learned model artifacts. It must not contain raw or derived
training data, market data, broker logs, trading journals, CSV exports, or
SQLite databases.

## Reporting

Report security concerns privately to the repository owner rather than opening
a public issue.
