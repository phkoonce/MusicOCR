#!/usr/bin/env bash
# One-time setup for the MusicOCR pipeline.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> poppler (pdfinfo / pdftoppm)"
if ! command -v pdfinfo >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install poppler
  else
    echo "   Homebrew not found — install poppler manually." >&2
  fi
else
  echo "   already installed: $(pdfinfo -v 2>&1 | head -1)"
fi

echo "==> python venv (.venv)"
if [ ! -d .venv ]; then
  # Needs Python >= 3.11 (stdlib tomllib); prefer newest available.
  PY=""
  for c in python3.13 python3.12 python3.11; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  if [ -n "$PY" ]; then
    "$PY" -m venv .venv
  elif command -v uv >/dev/null 2>&1; then
    uv venv --python 3.13 --seed .venv
  else
    echo "   Need Python >= 3.11 (python3.13/3.12/3.11 or uv) — e.g. brew install python@3.13" >&2
    exit 1
  fi
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[test]"

echo
echo "==> done. Next:"
echo "   source .venv/bin/activate"
echo "   python -m musicocr doctor"
