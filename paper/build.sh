#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--reproduce" ]]; then
  if [[ $# != 2 ]]; then
    echo "Usage: bash build.sh --reproduce /path/to/ipcc_litdb.sqlite" >&2
    exit 2
  fi
  "${MANUSCRIPT_PYTHON:-python3}" analyse_corpus.py --database "$2"
  "${MANUSCRIPT_PYTHON:-python3}" deepen_analysis.py
  "${MANUSCRIPT_PYTHON:-python3}" make_figures.py --render-only
elif [[ $# != 0 ]]; then
  echo "Usage: bash build.sh [--reproduce /path/to/ipcc_litdb.sqlite]" >&2
  exit 2
fi

latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
