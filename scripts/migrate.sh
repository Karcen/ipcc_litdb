#!/usr/bin/env bash
# Apply the PostgreSQL schema and import the SQLite snapshot into an empty database.
# Usage: bash scripts/migrate.sh [--strip-nul] [postgresql://user:password@host/database]
set -euo pipefail
cd "$(dirname "$0")/.."
command -v psql >/dev/null || { echo "Install the PostgreSQL psql client first." >&2; exit 2; }
export_options=()
if [ "${1:-}" = "--strip-nul" ]; then
  export_options=(--strip-nul)
  shift
fi
[ "$#" -le 1 ] || { echo "Usage: bash scripts/migrate.sh [--strip-nul] [postgresql-url]" >&2; exit 2; }
if [ "$#" -eq 1 ]; then
  target_url="$1"
else
  target_url="$(python - <<'ENV_PY'
import os
from dotenv import load_dotenv
load_dotenv('.env', override=False)
print(os.environ.get('POSTGRES_URL', ''))
ENV_PY
)"
fi
[ -n "$target_url" ] || { echo "Pass a PostgreSQL URL or set POSTGRES_URL in .env." >&2; exit 2; }
python scripts/restore_database.py
temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
echo "Exporting the SQLite snapshot."
python database/export_for_postgres.py "${export_options[@]}" > "$temporary_dir/snapshot.sql"
echo "Applying the PostgreSQL schema."
psql "$target_url" -X -v ON_ERROR_STOP=1 --single-transaction -f database/schema/schema_postgres.sql
echo "Importing into PostgreSQL."
psql "$target_url" -X -v ON_ERROR_STOP=1 -f "$temporary_dir/snapshot.sql"
echo "PostgreSQL import complete."
