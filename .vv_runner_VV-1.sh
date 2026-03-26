#!/usr/bin/env bash
set -euo pipefail

source ~/.bashrc || true
cd "/home/vvruntime/viralverse-dubbing-automation"
source "venv/bin/activate"

echo "[INFO] Running: _queue_again__.py"
if command -v python >/dev/null 2>&1; then
  python "_queue_again__.py" || true
else
  python3 "_queue_again__.py" || true
fi

echo "[INFO] Done: _queue_again__.py"
exec bash
