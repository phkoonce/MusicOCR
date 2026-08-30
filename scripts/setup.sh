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
  python3 -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo
echo "==> done. Next:"
echo "   source .venv/bin/activate"
echo "   python -m musicocr doctor"
