#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gld_scalper_bot}"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git sqlite3

sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"

if [ ! -f "$APP_DIR/pyproject.toml" ]; then
  echo "Copy or clone the project into $APP_DIR before running dependency installation."
fi

cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
mkdir -p logs data models

echo "Install complete. Create $APP_DIR/.env from .env.example before running the bot."
