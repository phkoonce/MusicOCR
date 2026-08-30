#!/usr/bin/env bash
# Convenience wrapper: activates .venv and forwards args to the CLI.
#   ./run.sh doctor
#   ./run.sh run input/my-part.pdf
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "no .venv — run scripts/setup.sh first" >&2
  exit 1
fi
exec ./.venv/bin/python -m musicocr "$@"
