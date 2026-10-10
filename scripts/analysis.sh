#!/usr/bin/env bash
# Refresh the automated analyses and the insight feed.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m analytics.cli "${1:-refresh}"
