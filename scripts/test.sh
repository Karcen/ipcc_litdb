#!/usr/bin/env bash
# Run the test suite. Install development dependencies first.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pytest tests/ -v --tb=short "$@"
