# VV Dubbing Automation — Install Commands (Copy/Paste)

## WSL (Ubuntu) — One-shot install
Run inside WSL:

```bash
set -e

# 1) System packages (APT)
sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  zip \
  unzip \
  python3 \
  python3-venv \
  python3-pip \
  ffmpeg \
  jq \
  wget \
  p7zip-full \
  sox \
  imagemagick

# 2) Node.js + npm (pin a version; keep same on all PCs)
# Example: Node 20.x
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v
npm -v

# 3) Gemini CLI (global)
sudo npm i -g @google/gemini-cli
gemini --version

# 4) Create project venv + install Python deps
# Change PROJECT_DIR to your project folder path in WSL
PROJECT_DIR="$HOME/viralverse-dubbing-automation"
cd "$PROJECT_DIR"

python3 -m venv fwenv
source fwenv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

# 5) Sanity checks
python -c "import sys; print('Python OK:', sys.version)"
ffmpeg -version | head -n 2
jq --version
gemini --version
```

## Windows — required components
- `powershell.exe` and `cmd.exe` are included by default on Windows.
- Install these apps via **Adobe Creative Cloud**:
  - Adobe Premiere Pro 2025  
  - Adobe After Effects 2025  
  - Adobe Media Encoder 2025  

# ================================
# WSL Setup (Premiere Hard-Close)
# Single Command Panel
# ================================

# 1) Enable interop + append Windows PATH
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[interop]
enabled=true
appendWindowsPath=true
EOF

# 2) (Run THIS from Windows PowerShell) restart WSL:
# wsl --shutdown

# 3) Verify WSL + Windows commands
grep -qi microsoft /proc/version && echo "WSL: OK" || echo "NOT WSL"
which powershell.exe && powershell.exe -NoProfile -Command "echo POWERSHELL_OK"
which cmd.exe && cmd.exe /c echo CMD_OK

# 4) Temporary PATH fix (current session)
export PATH="$PATH:/mnt/c/Windows/System32:/mnt/c/Windows/System32/WindowsPowerShell/v1.0"
hash -r
which powershell.exe
which cmd.exe

# 5) Permanent PATH fix (persist in ~/.bashrc)
grep -qxF 'export PATH="$PATH:/mnt/c/Windows/System32:/mnt/c/Windows/System32/WindowsPowerShell/v1.0"' ~/.bashrc \
  || echo 'export PATH="$PATH:/mnt/c/Windows/System32:/mnt/c/Windows/System32/WindowsPowerShell/v1.0"' >> ~/.bashrc
source ~/.bashrc

