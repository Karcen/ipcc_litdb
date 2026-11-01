#!/usr/bin/env bash
# Export papers / references / unresolved via the running API.
#   bash scripts/export.sh papers csv [report_id]
set -euo pipefail
KIND="${1:-papers}"; FMT="${2:-csv}"; REP="${3:-}"
BASE="${API_BASE:-http://127.0.0.1:8077}"
URL="$BASE/api/export/$KIND.$FMT?limit=100000"
[ -n "$REP" ] && URL="$URL&report_id=$REP"
OUT="ipcc_${KIND}${REP:+_$REP}.$FMT"
curl -fsS "$URL" -o "$OUT" && echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
