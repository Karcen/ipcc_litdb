#!/usr/bin/env bash
# Load .env, restore a missing SQLite database, and start the dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python scripts/run_server.py "$@"
