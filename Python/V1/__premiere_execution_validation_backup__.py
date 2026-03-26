#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
import subprocess
import time
import sys
from pathlib import Path
from urllib import request, error as urlerror


# -----------------------------
# Path helpers
# -----------------------------
def win_to_wsl_path(p: str) -> Path:
    """Convert Windows path to WSL mount path.

    Examples:
      C:\\Dir\\File.json  or  C:/Dir/File.json -> /mnt/c/Dir/File.json

    If already unix path, returns as-is.
    """
    s = str(p or "").strip()
    if not s:
        return Path()

    if s.startswith("/") or s.startswith("~"):
        return Path(s).expanduser()

    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")

    return Path(s)


def wsl_to_win_path(p: Path) -> str:
    """Convert /mnt/c/Dir/File.json -> C:\\Dir\\File.json.

    If not /mnt/<drive>/..., returns empty string.
    """
    s = str(p).strip().replace("\\", "/")
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", s)
    if not m:
        return ""
    drive = m.group(1).upper()
    rest = m.group(2).replace("/", "\\")
    return f"{drive}:\\{rest}"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# -----------------------------
# File deletion (after validation)
# -----------------------------
def delete_with_retries(path: Path, retries: int = 10, sleep_s: float = 0.3) -> bool:
    """Best-effort delete.

    Retries help if Windows is momentarily locking the file.
    """
    for _ in range(max(1, retries)):
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception:
            time.sleep(sleep_s)
    return not path.exists()


# -----------------------------
# Windows Agent (Docker-safe fallback)
# -----------------------------
def _agent_url() -> str:
    # Set this on Docker/WSL machines if /mnt/c/Windows/... is not available inside container.
    # Example: http://192.168.1.50:8787   (your agent base)
    return (Path().expanduser() and "") or str(
        (
            __import__("os").environ.get("WIN_AGENT_URL")
            or __import__("os").environ.get("POWERSHELL_AGENT_URL")
            or __import__("os").environ.get("WINDOWS_AGENT_URL")
            or ""
        )
    ).strip().rstrip("/")


def _agent_token() -> str:
    return str(__import__("os").environ.get("WIN_AGENT_TOKEN") or "").strip()


def _ps_quote_literal(s: str) -> str:
    # PowerShell single-quote literal escaping: ' -> ''
    return "'" + str(s).replace("'", "''") + "'"


def _agent_post_json(url: str, payload: dict, timeout_s: int = 20) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    tok = _agent_token()
    if tok:
        headers["X-Token"] = tok

    req = request.Request(url, data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {"_raw": raw, "_http": getattr(resp, "status", 200)}


def _agent_run_powershell(ps_script: str, timeout_s: int = 25) -> dict:
    base = _agent_url()
    if not base:
        return {}

    url = base + "/powershell"
    payload_variants = [
        {"cmd": ps_script},
        {"command": ps_script},
        {"script": ps_script},
        {"powershell": ps_script},
    ]

    last = {}
    for payload in payload_variants:
        try:
            out = _agent_post_json(url, payload, timeout_s=timeout_s)
            if isinstance(out, dict):
                last = out
                if "exitCode" in out or "stdout" in out or "stderr" in out:
                    return out
                if out:
                    return out
        except Exception as e:
            last = {"error": str(e)}
            continue
    return last


def _agent_exitcode(resp: dict) -> int:
    try:
        return int(resp.get("exitCode"))
    except Exception:
        return 9999


def _run_ps_local_or_agent(ps_script: str, timeout_s: int = 35) -> dict:
    """Run PowerShell via local WSL bridge (/mnt/c/...) if possible, else via WIN_AGENT_URL.

    Returns a dict that may include: exitCode, stdout, stderr.
    """
    # 1) Local PowerShell (WSL bridge)
    try:
        ps = _powershell_exe()
        r = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {"exitCode": int(r.returncode), "stdout": r.stdout or "", "stderr": r.stderr or ""}
    except Exception:
        pass

    # 2) Agent fallback
    return _agent_run_powershell(ps_script, timeout_s=timeout_s) or {}

def _ame_is_running() -> bool:
    probe = r"""
    $ErrorActionPreference = 'SilentlyContinue'
    $hits = @()
    try {
      $hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          ($_.Name -as [string]) -like '*Media Encoder*' -or
          ($_.Name -as [string]) -like '*MediaEncoder*' -or
          ($_.CommandLine -as [string]) -like '*Adobe Media Encoder*' -or
          ($_.ExecutablePath -as [string]) -like '*Adobe Media Encoder*'
        }
    } catch {}
    if ($hits -and $hits.Count -gt 0) { exit 0 } else { exit 1 }
    """
    try:
        resp = _run_ps_local_or_agent(probe, timeout_s=15)
        rc = _agent_exitcode(resp) if isinstance(resp, dict) else 9999
        if rc in (0, 1):
            return rc == 0
    except Exception:
        pass
    return True


# -----------------------------
# Windows helpers (PowerShell/cmd/schtasks + agent fallback)
# -----------------------------
def _powershell_exe() -> str:
    ps = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if Path(ps).exists():
        return ps
    return "powershell.exe"


def _cmd_exe() -> str:
    cmd = "/mnt/c/Windows/System32/cmd.exe"
    if Path(cmd).exists():
        return cmd
    return "cmd.exe"


def _schtasks_exe() -> str:
    st = "/mnt/c/Windows/System32/schtasks.exe"
    if Path(st).exists():
        return st
    return "schtasks.exe"


def _run_quiet(cmd_list) -> int:
    try:
        r = subprocess.run(
            cmd_list,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return int(r.returncode)
    except Exception:
        return 9999


def _local_windows_bridge_available() -> bool:
    ps_mnt = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists()
    cmd_mnt = Path("/mnt/c/Windows/System32/cmd.exe").exists()
    st_mnt = Path("/mnt/c/Windows/System32/schtasks.exe").exists()
    return bool(ps_mnt or cmd_mnt or st_mnt)


def _win_file_exists(win_path: str) -> bool:
    """Check existence using Windows PowerShell (source of truth for Windows apps)."""
    win_path = (win_path or "").strip()
    if not win_path:
        return False

    cmd = f"if (Test-Path -LiteralPath {_ps_quote_literal(win_path)}) {{ exit 0 }} else {{ exit 1 }}"

    try:
        ps = _powershell_exe()
        r = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if int(r.returncode) in (0, 1):
            return int(r.returncode) == 0
    except Exception:
        pass

    resp = _agent_run_powershell(cmd, timeout_s=15)
    if isinstance(resp, dict):
        rc = _agent_exitcode(resp)
        if rc in (0, 1):
            return rc == 0

    return False


def _win_read_text(win_path: str) -> str:
    """Read text from Windows path using PowerShell (local or agent)."""
    win_path = (win_path or "").strip()
    if not win_path:
        return ""

    ps_cmd = f"[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-Content -LiteralPath {_ps_quote_literal(win_path)} -Raw"
    try:
        ps = _powershell_exe()
        r = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if int(r.returncode) == 0:
            return r.stdout
    except Exception:
        pass

    resp = _agent_run_powershell(ps_cmd, timeout_s=20)
    if isinstance(resp, dict):
        out = resp.get("stdout") or resp.get("output") or ""
        if out:
            return str(out)
        raw = resp.get("_raw")
        if raw:
            return str(raw)
    return ""


def _win_delete(win_path: str, retries: int = 8, sleep_s: float = 0.25) -> bool:
    """Delete a Windows path (local PS or agent)."""
    win_path = (win_path or "").strip()
    if not win_path:
        return False

    cmd = f"Remove-Item -LiteralPath {_ps_quote_literal(win_path)} -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath {_ps_quote_literal(win_path)}) {{ exit 1 }} else {{ exit 0 }}"

    for _ in range(max(1, retries)):
        try:
            ps = _powershell_exe()
            r = subprocess.run(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if int(r.returncode) in (0, 1):
                return int(r.returncode) == 0
        except Exception:
            pass

        resp = _agent_run_powershell(cmd, timeout_s=15)
        if isinstance(resp, dict):
            rc = _agent_exitcode(resp)
            if rc in (0, 1):
                if rc == 0:
                    return True

        time.sleep(sleep_s)

    return False


# -----------------------------
# Premiere force close (HARD KILL)
# -----------------------------
def _premiere_is_running() -> bool:
    """Return True if any Premiere process is running (robust on Windows/WSL)."""
    probe = r"""
    $ErrorActionPreference = 'SilentlyContinue'
    $hits = @()
    try {
      $hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          ($_.Name -as [string]) -like '*Premiere*' -or
          ($_.CommandLine -as [string]) -like '*Premiere*' -or
          ($_.ExecutablePath -as [string]) -like '*Premiere*'
        }
    } catch {}
    if ($hits -and $hits.Count -gt 0) { exit 0 } else { exit 1 }
    """

    try:
        resp = _run_ps_local_or_agent(probe, timeout_s=15)
        rc = _agent_exitcode(resp) if isinstance(resp, dict) else 9999
        if rc in (0, 1):
            return rc == 0
    except Exception:
        pass

    return True


def force_close_premiere(
    max_wait_s: int = 10,
    attempts: int = 3,
    use_schtask_fallback: bool = False,
    schtask_name: str = "KillPremiere",
) -> None:
    """HARD FORCE CLOSE Premiere (WSL compatible), exactly 3 tries, 5s interval.

    - Kills process trees via taskkill /F /T (image-name + PID sweep)
    - Also calls Stop-Process + CIM Terminate as reinforcement
    - No extra retries beyond 3 attempts
    """

    if (not _local_windows_bridge_available()) and (not _agent_url()):
        print("⚠️  Cannot force-close Premiere: no Windows bridge (/mnt/c) and WIN_AGENT_URL not set.")
        return

    ps_hard_kill = r"""
    $ErrorActionPreference = 'SilentlyContinue'

    function Kill-PidTree([int]$pid) {
      try { cmd /c ("taskkill /F /T /PID " + $pid) | Out-Null } catch {}
      try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
      try {
        $c = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $pid) -ErrorAction SilentlyContinue
        if ($c) { Invoke-CimMethod -InputObject $c -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
      } catch {}
    }

    # 1) Sweep by image name first
    $ims = @('Adobe Premiere Pro.exe','PremierePro.exe','AdobePremierePro.exe','Adobe Premiere Pro (Beta).exe')
    foreach ($im in $ims) { 
      try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
    }

    # 2) PID sweep via CIM (most reliable)
    $cims = @()
    try {
      $cims = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          ($_.Name -as [string]) -like '*Premiere*' -or
          ($_.CommandLine -as [string]) -like '*Premiere*' -or
          ($_.ExecutablePath -as [string]) -like '*Premiere*'
        }
    } catch {}

    $pids = @()
    foreach ($p in $cims) { try { $pids += [int]$p.ProcessId } catch {} }
    $pids = $pids | Sort-Object -Unique
    foreach ($pid in $pids) { Kill-PidTree $pid }

    # 3) Final sweep again by image name
    foreach ($im in $ims) {
      try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
    }
    """

    # Exactly 3 attempts, 5 seconds between attempts.
    for _ in range(3):
        _run_ps_local_or_agent(ps_hard_kill, timeout_s=60)
        time.sleep(30)
        if not _premiere_is_running():
            return

    print("⚠️  Premiere still appears to be running after 3 hard-kill attempts.")


# -----------------------------
# Re-run after timeout
# -----------------------------
def _resolve_rerun_script_path(script_arg: str) -> Path:
    """Resolve rerun script path.

    - If absolute unix path -> use directly
    - If Windows path (C:\\...) -> convert to /mnt/c/...
    - If relative -> resolve next to THIS validation script
    """
    s = str(script_arg or "").strip()
    if not s:
        return Path()

    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m:
        return win_to_wsl_path(s)

    p = Path(s).expanduser()
    if p.is_absolute():
        return p

    return (Path(__file__).resolve().parent / p).resolve()


def _run_rerun_script(script_path: Path) -> int:
    """Run rerun script and stream FULL output (stdout+stderr) to console."""
    if not script_path or not script_path.exists():
        print(f"⚠️ Rerun script not found: {script_path}")
        return 127

    cmd = ["python3", str(script_path)]
    print("\n" + "=" * 60)
    print(f"🔁 Re-running: {script_path}")
    print("=" * 60)

    try:
        r = subprocess.run(
            cmd,
            cwd=str(script_path.parent),
            capture_output=True,
            text=True,
            check=False,
        )

        if r.stdout:
            print(r.stdout.rstrip("\n"))
        if r.stderr:
            print(r.stderr.rstrip("\n"))

        print("=" * 60)
        print(f"🔁 Rerun exit code: {r.returncode}")
        print("=" * 60 + "\n")
        return int(r.returncode)

    except Exception as e:
        print(f"⚠️ Failed to execute rerun script: {e}")
        return 128



# -----------------------------
# Step 2/2 — Encoder validation
# -----------------------------
def _sanitize_json_text(raw: str) -> str:
    s = "" if raw is None else str(raw)
    s = s.lstrip("\ufeff")
    if "\x00" in s:
        s = s.replace("\x00", "")
    return s


def _read_text_best_effort(path: Path) -> str | None:
    try:
        b = path.read_bytes()
        if b is None:
            return None
        if len(b) == 0:
            return ""

        # --- Fast BOM detection ---
        if b.startswith(b"\xff\xfe") or b.startswith(b"\xfe\xff"):
            # UTF-16 with BOM
            try:
                return b.decode("utf-16")
            except Exception:
                pass

        # --- Heuristic: lots of NUL bytes => likely UTF-16LE without BOM ---
        nul_ratio = b.count(b"\x00") / max(1, len(b))
        if nul_ratio > 0.10:
            try:
                return b.decode("utf-16le")
            except Exception:
                pass

        # --- Normal UTF-8 / UTF-8-SIG ---
        for enc in ("utf-8-sig", "utf-8"):
            try:
                return b.decode(enc)
            except Exception:
                continue

        return b.decode("utf-8", errors="replace")
    except Exception:
        return None


def _try_parse_json_loose(raw: str):
    """
    Try to parse JSON even if raw has junk before/after.
    Uses JSONDecoder.raw_decode scanning from potential starts.
    """
    if raw is None:
        return None
    s = str(raw)

    dec = json.JSONDecoder()

    # scan for likely JSON starts
    starts = []
    for i, ch in enumerate(s):
        if ch == "{" or ch == "[":
            starts.append(i)
            if len(starts) >= 2000:  # safety cap
                break

    for i in starts:
        try:
            obj, end = dec.raw_decode(s, idx=i)
            return obj
        except Exception:
            continue

    return None


def read_json_any(path_wsl: Path, path_win: str) -> dict | None:
    raw = None

    # 1) Prefer WSL view
    if path_wsl and path_wsl.exists():
        raw = _read_text_best_effort(path_wsl)

    # 2) Windows fallback
    if (raw is None) and path_win and _win_file_exists(path_win):
        raw = _win_read_text(path_win)

    if raw is None:
        return None

    raw = _sanitize_json_text(raw)

    # If empty/whitespace, treat as not-ready yet
    if not raw.strip():
        return None

    # 1) strict parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # 2) loose parse (handles junk around JSON)
    obj = _try_parse_json_loose(raw)
    if isinstance(obj, dict):
        return obj

    return None


def normalize_status(v) -> str:
    s = str(v or "").strip().lower()
    s = s.replace("-", "_").replace(" ", "_")
    return s


def _tail_text(path: Path, max_bytes: int = 16384) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes), 0)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def watchdog_last_status(log_path: Path) -> str:
    if not log_path or (not log_path.exists()):
        return ""
    tail = _tail_text(log_path, max_bytes=16384)
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if "HB" in ln and "status=" in ln:
            m = re.search(r"status\s*=\s*([a-zA-Z_\-]+)", ln)
            if m:
                return m.group(1).strip().lower()
    return ""


def wait_for_watchdog_stopped(log_path: Path, timeout_s: int, poll_s: float = 2.0) -> bool:
    deadline = time.time() + max(1, int(timeout_s))
    while time.time() < deadline:
        if watchdog_last_status(log_path) == "stopped":
            return True
        time.sleep(max(0.5, float(poll_s)))
    return False


def wait_for_watchdog_stopped_stable(
    log_path: Path,
    stable_seconds: int = 180,
    poll_s: float = 2.0,
    progress_every_s: int = 30,
) -> bool:
    """
    Two-phase wait:
      (A) Wait indefinitely until watchdog_last_status() becomes 'stopped'
      (B) After first 'stopped', require it to remain 'stopped' continuously
          for stable_seconds. If it flips, reset the stability timer.

    Returns True only when stability window is satisfied.
    Never returns False (unless caller interrupts process).
    """
    stable_seconds = max(1, int(stable_seconds))
    poll_s = max(0.5, float(poll_s))
    progress_every_s = max(5, int(progress_every_s))

    first_seen_ts = None
    stable_start_ts = None
    last_progress_ts = 0.0

    while True:
        st = watchdog_last_status(log_path)

        now = time.time()
        if now - last_progress_ts >= progress_every_s:
            if stable_start_ts is None:
                print(f"… wait_and_kill: waiting for watchdog status=stopped (current='{st or 'unknown'}')")
            else:
                elapsed = int(now - stable_start_ts)
                remaining = max(0, stable_seconds - elapsed)
                print(f"… wait_and_kill: stopped seen; stability {elapsed}/{stable_seconds}s (remaining {remaining}s)")
            last_progress_ts = now

        if st == "stopped":
            if first_seen_ts is None:
                first_seen_ts = now
                stable_start_ts = now
            elif stable_start_ts is None:
                stable_start_ts = now

            # Check stability window
            if (now - stable_start_ts) >= stable_seconds:
                return True
        else:
            # Not stopped: reset stability timer (but keep waiting forever)
            stable_start_ts = None

        time.sleep(poll_s)


def force_close_media_encoder(max_wait_s: int = 10, attempts: int = 3) -> None:
    if (not _local_windows_bridge_available()) and (not _agent_url()):
        print("⚠️  Cannot force-close Media Encoder: no Windows bridge (/mnt/c) and WIN_AGENT_URL not set.")
        return

    ps = r'''
function Kill-PidTree([int]$pid) {
  if ($pid -le 0) { return }
  try { cmd /c ("taskkill /F /T /PID " + $pid) | Out-Null } catch {}
  try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
  try {
    $c = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $pid) -ErrorAction SilentlyContinue
    if ($c) { Invoke-CimMethod -InputObject $c -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
  } catch {}
}

$ims = @('Adobe Media Encoder.exe','MediaEncoder.exe','AdobeMediaEncoder.exe','Adobe Media Encoder (Beta).exe')
foreach ($im in $ims) {
  try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
}

$cims = @()
try {
  $cims = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      ($_.Name -as [string]) -like '*Media Encoder*' -or
      ($_.CommandLine -as [string]) -like '*Media Encoder*' -or
      ($_.ExecutablePath -as [string]) -like '*Media Encoder*' -or
      ($_.Name -as [string]) -like '*MediaEncoder*' -or
      ($_.CommandLine -as [string]) -like '*MediaEncoder*' -or
      ($_.ExecutablePath -as [string]) -like '*MediaEncoder*'
    }
} catch {}

$pids = @()
foreach ($p in $cims) { try { $pids += [int]$p.ProcessId } catch {} }
$pids = $pids | Sort-Object -Unique
foreach ($pid in $pids) { Kill-PidTree $pid }
'''
    for i in range(max(1, attempts)):
        try:
            _run_ps_local_or_agent(ps, timeout_s=max_wait_s)
        except Exception as e:
            print(f"⚠️  Media Encoder kill attempt {i+1}/{attempts} error: {e}")
        if i < attempts - 1:
            time.sleep(5)


def _resolve_media_encoder_agent_path() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[2] if len(here.parents) >= 3 else here.parent
    return repo_root / "_media_encoder_agent__.py"


def _run_media_encoder_agent(agent_path: Path) -> int:
    try:
        if not agent_path.exists():
            print(f"⚠️  Media Encoder agent not found: {agent_path}")
            return 127
        print(f"▶ RUNNING Media Encoder agent: {sys.executable} {agent_path}")
        r = subprocess.run([sys.executable, str(agent_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if r.stdout.strip():
            print(r.stdout.strip())
        if r.stderr.strip():
            print(r.stderr.strip())
        return int(r.returncode)
    except Exception as e:
        print(f"⚠️  Failed to run Media Encoder agent: {e}")
        return 128
    

def _hard_fail_if_rerun_failed(rc: int, context: str = "rerun_script") -> int:
    """
    Hard-fail gate for rerun script failures.
    Returns 0 if OK, otherwise returns the rerun exit code (or 22 as fallback).
    """
    try:
        rc_i = int(rc)
    except Exception:
        rc_i = 22

    if rc_i != 0:
        print(f"❌ HARD FAIL: {context} failed with exit code {rc_i}. Aborting to prevent false success.")
        return rc_i
    return 0


def run_encoder_validation_step(
    encoder_wsl: Path,
    encoder_win: str,
    poll_s: int,
    timeout_s: int,
    delete_after_read: bool,
    watchdog_log_wsl: Path,
    wait_and_kill_s: int,
    rerun_script_path: Path,
    schtask_enabled: bool,
    schtask_name: str,
    step1_mtime: float,
    step2_start_ts: float,
) -> int:
    print(f"📌 Step 2/2 waiting for: {encoder_wsl}")
    if encoder_win:
        print(f"📌 Step 2/2 Windows fallback: {encoder_win}")
    print(f"⏳ Step 2/2 Timeout: {timeout_s}s | Poll: {poll_s}s")

    # Treat files older than this as stale (prevents “old kill” from hours ago)
    STALE_AGE_S = 10 * 60  # 10 minutes

    def _wsl_mtime(p: Path) -> float:
        try:
            return float(p.stat().st_mtime)
        except Exception:
            return 0.0

    def _win_mtime_epoch(win_path: str) -> float:
        win_path = (win_path or "").strip()
        if not win_path:
            return 0.0
        ps = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            f"$p={_ps_quote_literal(win_path)};"
            "if (Test-Path -LiteralPath $p) { "
            "(Get-Item -LiteralPath $p).LastWriteTimeUtc.ToString('o') "
            "} else { '' }"
        )
        resp = _run_ps_local_or_agent(ps, timeout_s=15)
        try:
            out = (resp.get("stdout") or "").strip()
        except Exception:
            out = ""
        if not out:
            return 0.0
        # Parse ISO8601 (UTC) without extra imports; fall back to 0 on failure.
        try:
            # example: 2026-02-13T10:10:14.1234567Z
            out = out.replace("Z", "+00:00")
            import datetime
            dt = datetime.datetime.fromisoformat(out)
            return dt.timestamp()
        except Exception:
            return 0.0

    # 1) If file already exists, try to READ it first (do NOT delete blindly)
    #    Only delete it if it is truly stale (very old).
    now0 = time.time()

    exists_wsl = bool(encoder_wsl and encoder_wsl.exists())
    exists_win = _win_file_exists(encoder_win) if encoder_win else False

    if exists_wsl or exists_win:
        m = _wsl_mtime(encoder_wsl) if exists_wsl else _win_mtime_epoch(encoder_win)
        age = (now0 - m) if m > 0 else 0.0

        if m > 0 and age > STALE_AGE_S:
            print(f"⚠️ Step 2/2: encoder_validation.json is stale (age={int(age)}s) → deleting once.")
            try:
                delete_with_retries(encoder_wsl, retries=12, sleep_s=0.25)
            except Exception:
                pass
            if encoder_win:
                _win_delete(encoder_win, retries=12, sleep_s=0.25)
        else:
            # Not stale → parse immediately (this is the key fix)
            data = read_json_any(encoder_wsl, encoder_win)
            if isinstance(data, dict):
                st = normalize_status(data.get("status", data.get("Status", data.get("state", ""))))

                if delete_after_read:
                    try:
                        delete_with_retries(encoder_wsl, retries=12, sleep_s=0.25)
                    except Exception:
                        pass
                    if encoder_win:
                        _win_delete(encoder_win, retries=12, sleep_s=0.25)

                if st in ("fine", "success", "ok", "true", "1", "yes", "passed", "pass"):
                    print("✅ Two-step validation SUCCESS (encoder status=fine).")
                    return 0

                if st == "kill":
                    print("❌ Encoder status=kill → killing Premiere + Media Encoder.")
                    force_close_premiere(use_schtask_fallback=schtask_enabled, schtask_name=schtask_name)
                    force_close_media_encoder()
                    return 20

                if st in ("wait_and_kill", "wait_kill", "waitandkill"):
                    print("⚠️ Encoder status=wait_and_kill → waiting for watchdog status=stopped, then require 3 minutes stable stopped.")
                
                    stable_ok = wait_for_watchdog_stopped_stable(
                        watchdog_log_wsl,
                        stable_seconds=int(wait_and_kill_s),  # 180
                        poll_s=2.0,
                        progress_every_s=30.0,
                    )
                
                    if stable_ok:
                        print("✅ Watchdog status stayed 'stopped' for required stability window. Proceeding to restart.")
                    else:
                        print("⚠️ Could not confirm stable 'stopped' (log missing/parse error). Proceeding to restart anyway.")
                
                    force_close_premiere(use_schtask_fallback=schtask_enabled, schtask_name=schtask_name)
                    force_close_media_encoder()
                
                    rerun_rc = _run_rerun_script(rerun_script_path)
                    hard = _hard_fail_if_rerun_failed(rerun_rc, context="rerun_script(wait_and_kill)")
                    if hard != 0:
                        return hard
                
                    _run_media_encoder_agent(_resolve_media_encoder_agent_path())
                    return 21
                

                print(f"❌ Encoder validation unexpected status='{st}'. Treating as FAIL.")
                return 22
            # If parse failed, fall through to polling (don’t delete)

    # 2) Poll for file to appear and become parseable
    deadline = time.time() + max(1, int(timeout_s))
    last_debug = 0.0

    while time.time() < deadline:
        data = read_json_any(encoder_wsl, encoder_win)
        if data is None:
            now = time.time()
            if now - last_debug > 10:
                last_debug = now
                try:
                    ew = encoder_wsl.exists()
                    sz = encoder_wsl.stat().st_size if ew else -1
                except Exception:
                    ew, sz = False, -1
                ww = _win_file_exists(encoder_win) if encoder_win else False
                print(f"… Step 2/2: waiting (exists_wsl={ew}, size_wsl={sz}, exists_win={ww})")
            time.sleep(max(1, int(poll_s)))
            continue

        if delete_after_read:
            try:
                delete_with_retries(encoder_wsl, retries=12, sleep_s=0.25)
            except Exception:
                pass
            if encoder_win:
                _win_delete(encoder_win, retries=12, sleep_s=0.25)

        st = normalize_status(data.get("status", data.get("Status", data.get("state", ""))))

        if st in ("fine", "success", "ok", "true", "1", "yes", "passed", "pass"):
            print("✅ Two-step validation SUCCESS (encoder status=fine).")
            return 0

        if st == "kill":
            print("❌ Encoder status=kill → killing Premiere + Media Encoder.")
            force_close_premiere(use_schtask_fallback=schtask_enabled, schtask_name=schtask_name)
            force_close_media_encoder()
            return 20

        if st in ("wait_and_kill", "wait_kill", "waitandkill"):
            print("⚠️ Encoder status=wait_and_kill → waiting for watchdog status=stopped, then require 3 minutes stable stopped.")
            # Wait indefinitely for 'stopped', then require it stays 'stopped' for wait_and_kill_s seconds
            wait_for_watchdog_stopped_stable(
                watchdog_log_wsl,
                stable_seconds=int(wait_and_kill_s),  # keep your existing 180 default
                poll_s=2.0,
                progress_every_s=30,
            )
            print("✅ Watchdog status stayed 'stopped' for required stability window. Proceeding to restart.")
        
            force_close_premiere(use_schtask_fallback=schtask_enabled, schtask_name=schtask_name)
            force_close_media_encoder()
            
            rerun_rc = _run_rerun_script(rerun_script_path)
            _hard_fail_if_rerun_failed(rerun_rc, context="rerun_script(wait_and_kill)")
            
            _run_media_encoder_agent(_resolve_media_encoder_agent_path())
            return 21
        

        print(f"❌ Encoder validation unexpected status='{st}'. Treating as FAIL.")
        return 22

    print("⏰ Timeout: encoder_validation.json not found.")
    print("⚠️ Treating timeout as wait_and_kill → wait for watchdog status=stopped, then require stability window before restart.")
    
    # Wait indefinitely until watchdog reaches 'stopped' and stays stable for wait_and_kill_s seconds
    wait_for_watchdog_stopped_stable(
        watchdog_log_wsl,
        stability_s=int(wait_and_kill_s),  # your 180s stability window
        poll_s=2.0,
    )
    
    print("✅ Watchdog status stayed 'stopped' for required stability window. Proceeding to restart.")
    force_close_premiere(use_schtask_fallback=schtask_enabled, schtask_name=schtask_name)
    force_close_media_encoder()
    
    rerun_rc = _run_rerun_script(rerun_script_path)
    _hard_fail_if_rerun_failed(rerun_rc, context="rerun_script(timeout_wait_and_kill)")
    
    _run_media_encoder_agent(_resolve_media_encoder_agent_path())
    return 21
    
def _should_retry_step1_error(err: str) -> bool:
    e = str(err or "").lower()
    return (
        "ame queue not confirmed" in e
        or "onencoderjobqueued" in e
        or "not received within" in e
        or "retries exhausted" in e
    )



# -----------------------------
# Main polling logic
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/mnt/c/PPro_AutoRun/run_config.json", help="WSL path to run_config.json")
    ap.add_argument("--timeout-seconds", type=int, default=900, help="Total wait time in seconds")
    ap.add_argument("--poll-seconds", type=int, default=5, help="Polling interval seconds")

    # Step 2/2 encoder validation
    ap.add_argument("--encoder-timeout-seconds", type=int, default=300, help="Step 2/2 wait time for encoder_validation.json")
    ap.add_argument("--encoder-poll-seconds", type=int, default=2, help="Step 2/2 polling interval seconds")
    ap.add_argument("--no-delete-encoder-after-read", action="store_true", help="If set, do NOT delete encoder_validation.json after reading")

    # Watchdog log (keep your current default path as-is)
    ap.add_argument("--watchdog-log", default="", help="Watchdog log path (optional override)")
    ap.add_argument("--wait-and-kill-seconds", type=int, default=180, help="wait_and_kill: watchdog wait window in seconds (used by step-2 logic)")

    ap.add_argument(
        "--no-delete-after-read",
        action="store_true",
        help="If set, do NOT delete premiere_validation.json after reading",
    )

    ap.add_argument(
        "--no-schtask-fallback",
        action="store_true",
        help='Disable Scheduled Task fallback (task name default: "KillPremiere")',
    )
    ap.add_argument(
        "--schtask-name",
        default="KillPremiere",
        help='Windows Scheduled Task name to run as a last-resort close method (default: "KillPremiere")',
    )

    # Step-1 timeout retry + rerun behavior
    ap.add_argument(
        "--rerun-script",
        default="__run_premiere_after_xml__.py",
        help="Script to run after each timeout (default: __run_premiere_after_xml__.py)",
    )
    ap.add_argument(
        "--max-timeout-retries",
        type=int,
        default=3,
        help="How many Step-1 timeouts to retry before failing (default: 3)",
    )
    ap.add_argument(
        "--no-rerun-on-timeout",
        action="store_true",
        help="If set, do NOT rerun after timeout; fail immediately on the first timeout (old behavior).",
    )

    # NEW: full-run retries (encoder / step-2 failure)
    ap.add_argument(
        "--max-fullrun-retries",
        type=int,
        default=3,
        help="How many full-run retries if Step-2 fails (default: 3)",
    )

    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ run_config.json not found: {config_path}")
        force_close_premiere(
            use_schtask_fallback=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
        )
        return 3

    cfg = load_json(config_path)

    # --- Step 1/2 premiere validation path ---
    win_val = str(cfg.get("PremiereValidationPath") or cfg.get("premiereValidationPath") or "").strip()
    if win_val:
        validation_path = win_to_wsl_path(win_val)
    else:
        validation_path = config_path.parent / "premiere_validation.json"

    win_validation_path = wsl_to_win_path(validation_path)

    # --- Step 2/2 encoder validation path (same folder as premiere_validation.json) ---
    encoder_wsl = validation_path.parent / "encoder_validation.json"
    encoder_win = wsl_to_win_path(encoder_wsl)

    # KEEP YOUR CURRENT WATCHDOG DEFAULT EXACTLY AS-IS
    watchdog_log_wsl = win_to_wsl_path(r"C:\temp\AME_Watchdog\watchdog_per_item.log")
    if args.watchdog_log:
        watchdog_log_wsl = win_to_wsl_path(args.watchdog_log)

    timeout_s = int(args.timeout_seconds)
    timeout_min = round(timeout_s / 60.0, 2)

    print(f"📌 Config: {config_path}")
    print(f"📌 Waiting for: {validation_path}")
    if win_validation_path:
        print(f"📌 Windows fallback: {win_validation_path}")
    print(f"⏳ Timeout: {timeout_s}s (~{timeout_min} min) | Poll: {args.poll_seconds}s")

    # Resolve rerun script once (used for both Step-1 timeouts and Step-2 full retries)
    rerun_enabled = not args.no_rerun_on_timeout
    rerun_script_path = _resolve_rerun_script_path(args.rerun_script) if rerun_enabled else Path()

    max_full = max(1, int(args.max_fullrun_retries))
    max_step1_timeouts = max(1, int(args.max_timeout_retries))

    last_rc = 4

    # ----------------------------
    # FULL-RUN RETRY LOOP (Step-2 failures)
    # ----------------------------
    for full_try in range(1, max_full + 1):

        # ----------------------------
        # STEP 1/2 RETRY LOOP (timeouts waiting for premiere_validation.json)
        # ----------------------------
        step1_ok = False
        step1_mtime = 0.0

        for attempt in range(1, max_step1_timeouts + 1):
            attempt_start_ts = time.time()
            deadline = attempt_start_ts + timeout_s

            while time.time() < deadline:
                data = read_json_any(validation_path, win_validation_path)

                if data is None:
                    # If the file exists but isn't parseable yet (partial write), poll faster.
                    try:
                        if validation_path.exists() and validation_path.stat().st_size > 0:
                            time.sleep(1)
                            continue
                    except Exception:
                        pass
                    time.sleep(max(1, int(args.poll_seconds)))
                    continue

                # Guard: ignore stale Step-1 file from before THIS attempt started
                try:
                    if validation_path.exists():
                        mtime = float(validation_path.stat().st_mtime)
                        if mtime < (attempt_start_ts - 1.0):
                            delete_with_retries(validation_path, retries=12, sleep_s=0.25)
                            if win_validation_path:
                                _win_delete(win_validation_path, retries=12, sleep_s=0.25)
                            time.sleep(1)
                            continue
                        step1_mtime = mtime
                except Exception:
                    pass

                # Delete AFTER read (optional)
                if not args.no_delete_after_read:
                    deleted = delete_with_retries(validation_path, retries=12, sleep_s=0.25)
                    if (not deleted) and win_validation_path:
                        _win_delete(win_validation_path, retries=12, sleep_s=0.25)

                st = data.get("status", None)
                if isinstance(st, bool):
                    ok = (st is True)
                else:
                    s = str(st or "").strip().lower()
                    ok = s in ("true", "success", "ok", "fine", "1", "yes", "passed", "pass")

                if ok:
                    print("✅ Step 1/2 Premiere validation SUCCESS. Proceeding to encoder validation.")
                    step1_ok = True
                    break

                err = data.get("error", "(no error provided)")
                print(f"❌ Premiere validation FAIL: {err}")
                
                # NEW: retry certain Step-1 failures (like AME queue not confirmed)
                if rerun_enabled and _should_retry_step1_error(err) and (full_try < max_full):
                    print(f"🔁 Step 1/2 FAIL is retriable → restarting full run (full_try {full_try}/{max_full}).")
                
                    force_close_premiere(
                        use_schtask_fallback=not args.no_schtask_fallback,
                        schtask_name=args.schtask_name,
                    )
                    # Optional but recommended for THIS error class (queue/handshake issues)
                    force_close_media_encoder()
                
                    rc = _run_rerun_script(rerun_script_path)
                    hard = _hard_fail_if_rerun_failed(rc, context="rerun_script(step1_fail_retriable)")
                    if hard != 0:
                        return hard
                
                    _run_media_encoder_agent(_resolve_media_encoder_agent_path())
                
                    # Break out to outer FULL-RUN loop and try again
                    last_rc = 2
                    step1_ok = False
                    break
                
                # Default behavior (non-retriable step1 fail): exit immediately
                force_close_premiere(
                    use_schtask_fallback=not args.no_schtask_fallback,
                    schtask_name=args.schtask_name,
                )
                return 2
                
            if step1_ok:
                break

            # Step-1 timeout
            print(
                f"❌ Timeout: validation file not found within {timeout_s}s (~{timeout_min} min). "
                f"Attempt {attempt}/{max_step1_timeouts}."
            )

            force_close_premiere(
                use_schtask_fallback=not args.no_schtask_fallback,
                schtask_name=args.schtask_name,
            )

            if (not rerun_enabled) or (attempt >= max_step1_timeouts):
                last_rc = 4
                break

            rc = _run_rerun_script(rerun_script_path)
            hard = _hard_fail_if_rerun_failed(rc, context="rerun_script(main_timeout)")
            if hard != 0:
                return hard

        if not step1_ok:
            # exhausted step-1 timeouts in this full_try
            if full_try >= max_full:
                return int(last_rc)
            print(f"🔁 Full-run retry {full_try}/{max_full}: Step-1 never succeeded. Retrying full run...")
            continue

        # ----------------------------
        # STEP 2/2 — Encoder validation
        # ----------------------------
        step2_start_ts = time.time()
        rc2 = run_encoder_validation_step(
            encoder_wsl=encoder_wsl,
            encoder_win=encoder_win,
            poll_s=int(args.encoder_poll_seconds),
            timeout_s=int(args.encoder_timeout_seconds),
            delete_after_read=not args.no_delete_encoder_after_read,
            watchdog_log_wsl=watchdog_log_wsl,
            wait_and_kill_s=int(args.wait_and_kill_seconds),
            rerun_script_path=rerun_script_path,
            schtask_enabled=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
            step1_mtime=step1_mtime,
            step2_start_ts=step2_start_ts,
        )

        if int(rc2) == 0:
            return 0

        last_rc = int(rc2)

        # If Step-2 fails, FULL-RUN RETRY up to max_full
        if full_try >= max_full:
            print(f"❌ Encoder validation failed (rc={rc2}). Full-run retries exhausted ({max_full}/{max_full}).")
            return int(last_rc)

        print(f"🔁 Encoder validation failed (rc={rc2}). Full-run retry {full_try}/{max_full}...")

        # Safety close before restart
        force_close_premiere(
            use_schtask_fallback=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
        )
        force_close_media_encoder()

        # Restart pipeline
        if rerun_enabled:
            rc = _run_rerun_script(rerun_script_path)
            hard = _hard_fail_if_rerun_failed(rc, context="rerun_script(main_encoder_restart)")
            if hard != 0:
                return hard

        _run_media_encoder_agent(_resolve_media_encoder_agent_path())

    return int(last_rc)



if __name__ == "__main__":
    raise SystemExit(main())
