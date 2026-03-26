#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# vv_inventory_safe.sh  (READ-ONLY INVENTORY)
# - Does NOT install/update/remove anything
# - Outputs manifests listing:
#   * apt manual packages + dpkg full list
#   * python venv/conda env pip freeze (auto-detected)
#   * node/npm/gemini versions + npm global list
#   * project lockfiles (requirements, package-lock, etc.)
#   * grep scan for external CLI usage inside code
# ============================================================

ROOT="${1:-.}"                          # Project root to scan
OUTDIR="${2:-vv_manifests}"             # Output folder name

cd "$ROOT"
mkdir -p "$OUTDIR"

stamp() { date +"%Y-%m-%d %H:%M:%S"; }

echo "[INFO] Starting inventory at $(stamp)"
echo "[INFO] ROOT   : $(pwd)"
echo "[INFO] OUTDIR : $OUTDIR"

# ---------------------------
# Basic system info
# ---------------------------
{
  echo "timestamp=$(stamp)"
  echo "pwd=$(pwd)"
  echo "user=$(whoami 2>/dev/null || true)"
  echo "uname=$(uname -a 2>/dev/null || true)"
  echo "wsl=$(grep -i microsoft /proc/version 2>/dev/null | head -n 1 || true)"
  echo ""
  echo "---- OS RELEASE ----"
  (lsb_release -a 2>/dev/null || cat /etc/os-release 2>/dev/null || true)
} > "$OUTDIR/system_info.txt" || true

# ---------------------------
# APT packages (read-only)
# ---------------------------
if command -v apt-mark >/dev/null 2>&1; then
  apt-mark showmanual 2>/dev/null | sort > "$OUTDIR/apt_manual.txt" || true
else
  echo "apt-mark not found" > "$OUTDIR/apt_manual.txt"
fi

if command -v dpkg >/dev/null 2>&1; then
  dpkg -l 2>/dev/null > "$OUTDIR/dpkg_list.txt" || true
else
  echo "dpkg not found" > "$OUTDIR/dpkg_list.txt"
fi

# ---------------------------
# Key binaries + versions
# ---------------------------
{
  echo "---- PATHS ----"
  echo "python3=$(command -v python3 2>/dev/null || true)"
  echo "pip3=$(command -v pip3 2>/dev/null || true)"
  echo "python=$(command -v python 2>/dev/null || true)"
  echo "pip=$(command -v pip 2>/dev/null || true)"
  echo "node=$(command -v node 2>/dev/null || true)"
  echo "npm=$(command -v npm 2>/dev/null || true)"
  echo "gemini=$(command -v gemini 2>/dev/null || true)"
  echo "ffmpeg=$(command -v ffmpeg 2>/dev/null || true)"
  echo "jq=$(command -v jq 2>/dev/null || true)"
  echo "curl=$(command -v curl 2>/dev/null || true)"
  echo "wget=$(command -v wget 2>/dev/null || true)"
  echo ""
  echo "---- VERSIONS ----"
  (python3 --version 2>&1 || true)
  (pip3 --version 2>&1 || true)
  (python --version 2>&1 || true)
  (pip --version 2>&1 || true)
  (node -v 2>&1 || true)
  (npm -v 2>&1 || true)
  (gemini --version 2>&1 || true)
  (ffmpeg -version 2>&1 | head -n 5 || true)
  (jq --version 2>&1 || true)
  (curl --version 2>&1 | head -n 2 || true)
  (wget --version 2>&1 | head -n 2 || true)
} > "$OUTDIR/binaries_versions.txt" || true

# ---------------------------
# npm globals (read-only)
# ---------------------------
if command -v npm >/dev/null 2>&1; then
  npm -g ls --depth=0 2>/dev/null > "$OUTDIR/npm_globals.txt" || true
else
  echo "npm not found" > "$OUTDIR/npm_globals.txt"
fi

# ---------------------------
# Copy common project lockfiles (read-only)
# ---------------------------
mkdir -p "$OUTDIR/lockfiles"
for f in \
  requirements.txt requirements*.txt \
  pyproject.toml poetry.lock \
  Pipfile Pipfile.lock \
  package.json package-lock.json \
  yarn.lock pnpm-lock.yaml \
  .python-version \
  .nvmrc \
  ; do
  if [ -f "$f" ]; then
    cp -a "$f" "$OUTDIR/lockfiles/" 2>/dev/null || true
  fi
done

# ---------------------------
# Detect Python environments and freeze
# - Finds venv folders by pyvenv.cfg
# - Also checks common names (fwenv, venv, .venv)
# ---------------------------
> "$OUTDIR/python_envs_found.txt"

add_env_if_valid() {
  local ENV_DIR="$1"
  [ -d "$ENV_DIR" ] || return 0
  local PY="$ENV_DIR/bin/python"
  [ -x "$PY" ] || return 0

  echo "$ENV_DIR" >> "$OUTDIR/python_envs_found.txt"
}

# Common env names
add_env_if_valid "./fwenv"
add_env_if_valid "./venv"
add_env_if_valid "./.venv"

# Discover more venvs by pyvenv.cfg (up to depth 5)
while IFS= read -r cfg; do
  env_dir="$(dirname "$cfg")"
  add_env_if_valid "$env_dir"
done < <(find . -maxdepth 5 -type f -name "pyvenv.cfg" 2>/dev/null || true)

# De-duplicate list
if [ -s "$OUTDIR/python_envs_found.txt" ]; then
  sort -u "$OUTDIR/python_envs_found.txt" -o "$OUTDIR/python_envs_found.txt"
fi

mkdir -p "$OUTDIR/pip_freezes"

if [ -s "$OUTDIR/python_envs_found.txt" ]; then
  while IFS= read -r ENV_DIR; do
    [ -n "$ENV_DIR" ] || continue
    PY="$ENV_DIR/bin/python"
    SAFE_NAME="$(echo "$ENV_DIR" | sed 's|^\./||; s|/|__|g; s| |_|g')"
    OUTFILE="$OUTDIR/pip_freezes/pip_freeze__${SAFE_NAME}.txt"

    echo "[INFO] pip freeze from: $ENV_DIR"
    "$PY" -m pip freeze 2>/dev/null > "$OUTFILE" || echo "FAILED: $ENV_DIR" > "$OUTFILE"
  done < "$OUTDIR/python_envs_found.txt"
else
  echo "No venv found (pyvenv.cfg) in project tree." > "$OUTDIR/pip_freezes/README.txt"
fi

# Also capture "system python" freeze (optional but helpful)
if command -v python3 >/dev/null 2>&1; then
  python3 -m pip freeze 2>/dev/null > "$OUTDIR/pip_freezes/pip_freeze__system_python3.txt" || true
fi

# ---------------------------
# Scan codebase for CLI usage (no extra tools needed)
# Uses grep (available by default)
# ---------------------------
mkdir -p "$OUTDIR/code_scans"

# Note: this may take time on huge folders; it is still read-only.
# Exclude common bulky folders if present.
EXCLUDES=(
  "./fwenv"
  "./venv"
  "./.venv"
  "./node_modules"
  "./.git"
  "./__pycache__"
)

# Build grep exclude args
GREP_EXCLUDE_ARGS=()
for ex in "${EXCLUDES[@]}"; do
  GREP_EXCLUDE_ARGS+=( "--exclude-dir=$(basename "$ex")" )
done

# Common external commands you mentioned + typical pipeline tools
PATTERN='(gemini|node|npm|ffmpeg|jq|curl|wget|yt-dlp|gsutil|gcloud|sox|magick|convert|7z|unzip|zip|python3|pip3|powershell\.exe|cmd\.exe)'

# Grep in typical text/code files to reduce noise
grep -RInE "${GREP_EXCLUDE_ARGS[@]}" \
  --include="*.py" --include="*.sh" --include="*.bash" --include="*.js" --include="*.ts" --include="*.json" \
  --include="*.yml" --include="*.yaml" --include="*.txt" --include="*.md" --include="*.ps1" --include="*.bat" --include="*.cmd" \
  "$PATTERN" . 2>/dev/null > "$OUTDIR/code_scans/cli_references_in_code.txt" || true

# ---------------------------
# Finish
# ---------------------------
echo "[INFO] Done at $(stamp)"
echo "[INFO] Output written to: $(pwd)/$OUTDIR"

# Helpful summary file
{
  echo "Inventory completed: $(stamp)"
  echo "Project root: $(pwd)"
  echo "Output folder: $OUTDIR"
  echo ""
  echo "Key files:"
  echo " - system_info.txt"
  echo " - apt_manual.txt"
  echo " - dpkg_list.txt"
  echo " - binaries_versions.txt"
  echo " - npm_globals.txt"
  echo " - lockfiles/*"
  echo " - pip_freezes/*"
  echo " - code_scans/cli_references_in_code.txt"
} > "$OUTDIR/README_SUMMARY.txt" || true
