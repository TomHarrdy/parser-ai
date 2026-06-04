#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f ".env" ]; then
  echo "ERROR: .env file not found. Copy config/.env.example and fill in your keys."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3.12 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python -m src.main "${@:-run}"
