#!/usr/bin/env bash
set -euo pipefail

source ~/.bashrc || true
cd "/home/vvruntime/viralverse-dubbing-automation"
source "venv/bin/activate"

echo "[INFO] Running: _kill_automation_pipeline__.py"
if command -v python >/dev/null 2>&1; then
  python "_kill_automation_pipeline__.py" || true
else
  python3 "_kill_automation_pipeline__.py" || true
fi

echo "[INFO] Done: _kill_automation_pipeline__.py"
exec bash
