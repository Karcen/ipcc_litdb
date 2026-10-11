#!/usr/bin/env bash
# Refresh the automated analyses and the insight feed.
set -euo pipefail
cd "$(dirname "$0")/.."
if (( $# == 0 )); then set -- refresh; fi
python -m analytics.cli "$@"
