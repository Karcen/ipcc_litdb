#!/usr/bin/env bash
# Restore the packaged snapshot; an existing working database is kept.
# To replace a working database, stop the server and pass --force explicitly.
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/restore_database.py "$@"
