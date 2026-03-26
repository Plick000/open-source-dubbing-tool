#!/usr/bin/env bash
set -euo pipefail

source ~/.bashrc || true
cd "/home/vvruntime/viralverse-dubbing-automation"
source "venv/bin/activate"

echo "[INFO] Running: __run__.py"
if command -v python >/dev/null 2>&1; then
  python "__run__.py" || true
else
  python3 "__run__.py" || true
fi

echo "[INFO] Done: __run__.py"
exec bash
