#!/usr/bin/env bash
# Install into the active virtual environment and restore the bundled snapshot.
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  "") requirements="backend/requirements.txt" ;;
  --dev) requirements="requirements-dev.txt" ;;
  *) echo "Usage: bash scripts/install.sh [--dev]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "Usage: bash scripts/install.sh [--dev]" >&2; exit 2; }

python - <<'CHECKPY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or later is required.")
if sys.prefix == sys.base_prefix:
    raise SystemExit("Activate a virtual environment first: python3 -m venv .venv; source .venv/bin/activate")
CHECKPY
python -m pip install -r "$requirements"
python scripts/restore_database.py
echo "Installed. Start the dashboard with: bash scripts/dev.sh"
