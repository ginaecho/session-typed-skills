#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HERE/logs"
mkdir -p "$LOG_DIR"
# venv lives in the sibling stjp_core/ folder; resolve it relative to this script
PY="$HERE/../stjp_core/.venv/Scripts/python.exe"
for case in clinical_enrollment code_review travel_saga auction; do
  echo "========================================================================"
  echo "  SMOKE: $case  -> $LOG_DIR/${case}_smoke.log"
  echo "========================================================================"
  "$PY" -u "$HERE/scripts/case_runner.py" "$case" 1 2>&1 | tee "$LOG_DIR/${case}_smoke.log"
done
