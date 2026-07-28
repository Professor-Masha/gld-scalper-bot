# Repository Workflow

This is a private, proprietary @Mashcorp project. Changes should be small,
reviewable, tested, and described by meaningful commits.

## Before Editing

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
git switch main
git pull --ff-only
git status
```

Do not start from a dirty working tree unless the existing changes are
understood and intentionally included.

## Validate A Change

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe -m pip check
```

Use focused tests during development, then run the full checks before pushing.

## Review And Commit

```powershell
git status --short
git diff --check
git diff
git add <changed-files>
git diff --cached
git commit -m "Describe the completed change"
git push origin main
```

Good commit messages explain the result:

- `Add filtered EMA cross paper strategy`
- `Fix duplicate exit handling during reconciliation`
- `Document offline transformer training`

Avoid vague messages such as `update`, `changes`, or `fix`.

## Repository Boundary

Allowed:

- project source code
- tests and command-line tools
- README and project documentation
- configuration templates without real credentials
- curated `Knowledge` documents
- learned model artifacts and their metadata

Forbidden:

- `.env` and credentials
- SQLite databases
- raw or derived training data
- quotes, trades, bars, headlines, or economic-event archives
- CSV exports, logs, journals, and account snapshots
- Ollama model weights and installers
- virtual environments, caches, backups, and test output

Learned model binaries are stored through Git LFS. Do not bypass LFS for those
files.

## Every Completed Codex Change

For future bot work, the completion sequence is:

1. Implement the requested change.
2. Run appropriate tests and static checks.
3. Review the exact staged files for data or secrets.
4. Commit with a specific comment describing the change.
5. Push the commit to the private GitHub repository.
6. Report the commit SHA and verification results.
