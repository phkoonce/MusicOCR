#!/bin/bash
# From the stored answers to the finished score; free (no model calls). Run from anywhere.
cd "$(dirname "$0")/../.."
set -o pipefail
for n in "$@"; do
  .venv/bin/python workflows/bach-suites/correct.py apply "$n" | tail -1 &&
  .venv/bin/python workflows/bach-suites/audit.py apply "$n" | tail -1 &&
  .venv/bin/python workflows/bach-suites/finish.py "$n" | grep -E "layout.*original|layout left|bars,|Error"
done
