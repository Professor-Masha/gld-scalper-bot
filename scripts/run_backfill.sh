#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
python -m gld_scalper.main backfill --symbols GLD IAU SLV GDX UUP TLT SPY QQQ --days "${1:-90}"
