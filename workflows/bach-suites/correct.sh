#!/bin/bash
# Every correct.py step for the named files, stopping at the first failure of each.
cd "$(dirname "$0")/../.."
for n in "$@"; do
  for c in items run retry apply; do
    .venv/bin/python workflows/bach-suites/correct.py "$c" "$n" 2>&1 | tail -3
    [ "${PIPESTATUS[0]}" = 0 ] || { echo "$n FAILED at $c"; break; }
  done
done
