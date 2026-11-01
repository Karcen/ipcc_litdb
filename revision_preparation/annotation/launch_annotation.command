#!/bin/sh
TASK_ANNOTATION_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TASK_PYTHON=""
for TASK_CANDIDATE in "$TASK_ANNOTATION_DIR/../../.venv/bin/python" /opt/homebrew/bin/python3 python3; do
  if "$TASK_CANDIDATE" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    TASK_PYTHON="$TASK_CANDIDATE"
    break
  fi
done
if [ -z "$TASK_PYTHON" ]; then
  echo "Python 3.10 or later is required. Activate the project environment or install a current Python release."
  exit 1
fi
exec "$TASK_PYTHON" "$TASK_ANNOTATION_DIR/local_annotation_app.py" "$@"
