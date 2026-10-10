#!/usr/bin/env bash
# Check Python, shell, JavaScript, and Compose syntax.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m compileall -q -x '(^|/)\._' backend analytics database scripts tests
for script in scripts/*.sh; do bash -n "$script"; done
if command -v node >/dev/null; then
  node --check frontend/app.js
else
  echo "JavaScript syntax check skipped: Node.js is not installed."
fi
if command -v docker >/dev/null; then
  docker compose config --quiet
else
  echo "Compose syntax check skipped: Docker is not installed."
fi
echo "Syntax checks passed."
