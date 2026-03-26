# ViralVerse Dubbing Automation — New PC Setup From Scratch (Nothing Missing)

This document is a **single complete checklist** for setting up a brand-new PC from scratch for ViralVerse dubbing automation on **Windows + WSL Ubuntu 22.04**.

✅ Includes:
- Windows prerequisites + commands  
- WSL2 + Ubuntu 22.04 install  
- Z: mapping and WSL mount  
- Python + venv + packages  
- Git + repo setup  
- Sudo NOPASSWD so scripts never ask for password  
- Common runtime folders  
- Adobe install verification  
- (Optional but included) Docker + Compose setup  
- (Optional but included) n8n setup scaffolding  
- Validation commands at the end

> IMPORTANT: Replace placeholders like:
> - `\\SERVER\SHARE` (your network share)
> - `<YOUR_REPO_URL>` (your repo)
> - `<YOUR_WINDOWS_USERNAME>`
> - `<YOUR_LINUX_USERNAME>`
> - Any API keys / webhooks
>
> Run PowerShell commands in **PowerShell (Admin)** unless stated.
> Run Linux commands inside **Ubuntu (WSL)**.

---

## 1) Windows — Confirm system + enable required features

### 1.1 Confirm Windows build (should be Win10 2004+ or Win11)
```powershell
winver


wsl --shutdown



wsl -l -v
wsl --install

Get-Item D:\wsl_exports\viralverse-dubbing-automation.tar | Format-List Name,Length,LastWriteTime
Get-FileHash D:\wsl_exports\viralverse-dubbing-automation.tar -Algorithm SHA256


mkdir D:\WSL\ViralVerse -Force


### NEW PC
wsl -l -v
wsl --install

Get-Item D:\wsl_exports\viralverse-dubbing-automation.tar | Format-List Name,Length,LastWriteTime
Get-FileHash D:\wsl_exports\viralverse-dubbing-automation.tar -Algorithm SHA256

mkdir C:\WSL\ViralVerse -Force
wsl --import Ubuntu-22.04-ViralVerse C:\WSL\ViralVerse D:\wsl_exports\Ubuntu-22.04.tar --version 2


wsl -d Ubuntu-22.04-ViralVerse
wsl --set-default Ubuntu-22.04-ViralVerse


ls -la ~
ls -la ~/viralverse-dubbing-automation


python3 --version
ls -la ~/viralverse-dubbing-automation/venv/bin/python || true


ls -la /mnt || true
ls -la /mnt/z || true


Terminal 1

cd ~/viralverse-dubbing-automation
source venv/bin/activate
python _queue_again__.py


Terminal 2

cd ~/viralverse-dubbing-automation
source venv/bin/activate
python _media_encoder_agent__.py


Terminal 3

cd ~/viralverse-dubbing-automation
source venv/bin/activate
python __run__.py

IF YOU WANT TO REMOVE OR ANYTHING ELSE

mkdir D:\WSL\TestImport -Force
wsl --import Ubuntu-22.04-TEST D:\WSL\TestImport D:\wsl_exports\viralverse-dubbing-automation.tar --version 2
wsl -d Ubuntu-22.04-TEST -- bash -lc "echo OK && python3 --version && ls -la /home"


wsl --unregister Ubuntu-22.04-TEST

wsl --unregister Ubuntu-22.04-ViralVerse

wsl -l -v

wsl --shutdown




#### NEW PC ######
wsl --install

mkdir D:\WSL\VVRuntime -Force

wsl --import VVRuntime D:\WSL\VVRuntime D:\wsl_exports\viralverse-dubbing-automation.tar --version 2

wsl -d VVRuntime

whoami
python3 --version
node -v
npm -v
gemini --version
ffmpeg -version | head -n 2


ls -la ~
ls -la ~/viralverse-dubbing-automation 2>/dev/null || true

cat /etc/passwd | grep vvruntime
adduser vvruntime
usermod -aG sudo vvruntime

wsl -d VVRuntime -u vvruntime

cd ~/viralverse-dubbing-automation
source venv/bin/activate 2>/dev/null || source fwenv/bin/activate
python --version

wsl -l -v


Get-Volume -DriveLetter D | Select DriveLetter, FileSystem, DriveType, HealthStatus, SizeRemaining

wsl --shutdown
wsl --unregister VVRuntime

mkdir C:\WSL\VVRuntime -Force
wsl --import VVRuntime C:\WSL\VVRuntime D:\wsl_exports\viralverse-dubbing-automation.tar --version 2

wsl -d VVRuntime

wsl --shutdown
wsl --update
Restart-Service LxssManager
wsl -d VVRuntime


wsl --shutdown
net stop LxssManager
net start LxssManager
wsl -d VVRuntime

wsl -l -v


Get-WinEvent -LogName Microsoft-Windows-Lxss/Operational -MaxEvents 30 |
  Select TimeCreated, Id, LevelDisplayName, Message |
  Format-List


  wsl --shutdown
wsl --unregister VVRuntime

mkdir C:\WSL\VVRuntime -Force
wsl --import VVRuntime C:\WSL\VVRuntime D:\wsl_exports\viralverse-dubbing-automation.tar --version 2

wsl -d VVRuntime

wevtutil qe Microsoft-Windows-Hyper-V-Compute-Admin /c:50 /f:text
wevtutil qe Microsoft-Windows-Hyper-V-Compute-Operational /c:50 /f:text

Windows Security → Device Security → Core isolation → Memory integrity OFF

wsl --shutdown
wsl -d VVRuntime

wsl --shutdown
dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart
dism.exe /online /disable-feature /featurename:VirtualMachinePlatform /norestart
shutdown /r /t 0


dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
wsl --update
shutdown /r /t 0

wsl --set-default-version 2
wsl --shutdown
wsl -d VVRuntime

wsl --version

printf "[automount]\nmountFsTab = false\n" > /etc/wsl.conf

wsl --terminate Ubuntu-22.04-ViralVerse
wsl -d Ubuntu-22.04-ViralVerse

cat /etc/fstab
mount -av


mkdir D:\WSL\Ubuntu-22.04 -Force
wsl --import Ubuntu-22.04 D:\WSL\Ubuntu-22.04 D:\WSL_Exports\Ubuntu-22.04_2026-02-06.tar --version 2



(Powershell) - wsl -d Ubuntu-22.04-ViralVerse -u vvruntime
cd ~/viralverse-dubbing-automation
clear
./run_all.sh