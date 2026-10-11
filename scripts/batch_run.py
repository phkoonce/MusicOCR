#!/usr/bin/env python3
"""Thin entrypoint for the resumable batch runner (logic lives in musicocr.batch).

  .venv/bin/python scripts/batch_run.py --batch batches/zerberus.toml [--only hn01,eu2] [--redo]
"""
from __future__ import annotations

import sys

from musicocr.batch import main

if __name__ == "__main__":
    sys.exit(main())
