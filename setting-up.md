# New PC Setup — Full Command List (Single Runbook)

> Notes:
> - Lines starting with `#` are comments.
> - Run the **PowerShell** section in **Windows PowerShell (Admin)**.
> - Run the **WSL** section inside **Ubuntu (WSL)**.

# =========================
# 1) WINDOWS (PowerShell Admin)
# =========================
wsl --install
wsl --set-default-version 2
winget install -e --id Docker.DockerDesktop

# =========================
# 2) WSL (Ubuntu) — Runtime folder + shims
# =========================
mkdir -p ~/dubbing-runtime/docker_shims
cd ~/dubbing-runtime

# Copy these two files into the folder before continuing:
#   ~/dubbing-runtime/docker_shims/cmd.exe
#   ~/dubbing-runtime/docker_shims/powershell.exe

dos2unix ./docker_shims/cmd.exe ./docker_shims/powershell.exe
chmod +x ./docker_shims/cmd.exe ./docker_shims/powershell.exe

# =========================
# 3) WSL (Ubuntu) — Install Node.js 20 + Gemini CLI
# =========================
sudo apt-get update
sudo apt-get install -y curl ca-certificates

curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v
npm -v

mkdir -p ~/.npm-global
npm config set prefix "$HOME/.npm-global"

grep -q 'npm-global/bin' ~/.bashrc || echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

npm install -g @google/gemini-cli
gemini --version

# Login once (creates ~/.gemini)
gemini
ls -la ~/.gemini

apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    tzdata \
    curl \
    bash \
    dos2unix


export HF_HUB_DISABLE_XET=1
export HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_DOWNLOAD_TIMEOUT=600

# =========================
# 4) WSL (Ubuntu) — Set Docker Hub username in compose
# =========================
# Replace YOUR_USERNAME_HERE with your Docker Hub username (only once)
sed -i 's/DOCKERHUB_USER/YOUR_USERNAME_HERE/g' ~/dubbing-runtime/docker-compose.yml

# =========================
# 5) WSL (Ubuntu) — Pull image + start container
# =========================
cd ~/dubbing-runtime
docker login
docker compose pull
docker compose up -d --force-recreate

# =========================
# 6) WSL (Ubuntu) — Quick verification (no full pipeline)
# =========================
docker exec -it dubbing-tool bash -lc "cmd.exe /C echo CMD_OK"
docker exec -it dubbing-tool bash -lc "powershell.exe -NoProfile -Command \"Write-Output PS_OK\""
docker exec -it dubbing-tool bash -lc "cmd.exe /C tasklist | head -n 5"
docker exec -it dubbing-tool bash -lc "ls -la /mnt/z | head"
docker exec -it dubbing-tool bash -lc "which gemini && gemini --version"



apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    tzdata \
    curl \
    bash \
    dos2unix

mountpoint -q /mnt/z && echo "Z mounted OK" || echo "Z NOT mounted"