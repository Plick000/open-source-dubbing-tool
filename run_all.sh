#!/usr/bin/env bash
set -euo pipefail

# =========================
# 0) Config: 4 python files (run one per terminal)
# =========================
PY_FILES=(
  "_queue_again__.py"
  "_media_encoder_agent__.py"
  "__run__.py"
  "_kill_automation_pipeline__.py"
)

chmod +x /home/vvruntime/.npm-global/bin/gemini

# =========================
# 1) Ensure Z: is mounted to /mnt/z (AUTO-MOUNTED; NO sudo)
# =========================
if ! mountpoint -q /mnt/z; then
  echo "[ERROR] /mnt/z is NOT mounted."
  echo "[ERROR] Auto-mount should handle this. Fix:"
  echo "  1) Ensure Windows Z: drive exists on this PC"
  echo "  2) In PowerShell run: wsl --shutdown"
  echo "  3) Start distro again and retry"
  exit 11
else
  echo "[INFO] /mnt/z is mounted."
fi

# =========================
# 2) Make PowerShell available (optional)
# =========================
export PATH="$PATH:/mnt/c/Windows/System32/WindowsPowerShell/v1.0"

# =========================
# 3) Load shell env (optional)
# =========================
source ~/.bashrc || true

# =========================
# 4) Activate venv (verify project root)
# =========================
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
else
  echo "[ERROR] venv/bin/activate not found. Run this from the project root."
  exit 2
fi

PROJECT_ROOT="$(pwd)"
DISTRO_NAME="${WSL_DISTRO_NAME:-Ubuntu-22.04}"

# =========================
# Helper: launch each file in its own terminal
# =========================
launch_in_terminal() {
  local title="$1"
  local pyfile="$2"

  # Runner script stored INSIDE project so it always exists
  local runner="$PROJECT_ROOT/.vv_runner_${title}.sh"

  cat > "$runner" <<EOF
#!/usr/bin/env bash
set -euo pipefail

source ~/.bashrc || true
cd "$PROJECT_ROOT"
source "venv/bin/activate"

echo "[INFO] Running: $pyfile"
if command -v python >/dev/null 2>&1; then
  python "$pyfile" || true
else
  python3 "$pyfile" || true
fi

echo "[INFO] Done: $pyfile"
exec bash
EOF

  chmod +x "$runner"

  # Prefer Windows Terminal tabs if available
  if command -v wt.exe >/dev/null 2>&1; then
    # IMPORTANT: the "--" makes sure WT does NOT steal -d (startingDirectory)
    wt.exe new-tab --title "$title" -- wsl.exe -d "$DISTRO_NAME" -e bash -lc "bash '$runner'" >/dev/null 2>&1
  else
    # Fallback: open a new console window
    cmd.exe /c start "$title" wsl.exe -d "$DISTRO_NAME" -e bash -lc "bash '$runner'" >/dev/null 2>&1
  fi
}

# =========================
# 5) Validate files exist, then launch terminals
# =========================
for f in "${PY_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "[ERROR] File not found: $f (in $PROJECT_ROOT)"
    exit 3
  fi
done

echo "[INFO] Launching ${#PY_FILES[@]} terminals..."
i=1
for f in "${PY_FILES[@]}"; do
  launch_in_terminal "VV-${i}" "$f"
  i=$((i + 1))
done

echo "[INFO] All terminals launched."
exit 0