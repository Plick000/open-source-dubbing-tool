#!/usr/bin/env bash
set -euo pipefail

source ~/.bashrc || true
cd "/home/vvruntime/viralverse-dubbing-automation"
source "venv/bin/activate"

echo "[INFO] Running: _media_encoder_agent__.py"
if command -v python >/dev/null 2>&1; then
  python "_media_encoder_agent__.py" || true
else
  python3 "_media_encoder_agent__.py" || true
fi

echo "[INFO] Done: _media_encoder_agent__.py"
exec bash
