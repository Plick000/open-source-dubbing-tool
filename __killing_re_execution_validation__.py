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
    max_wait_s: int = 0,
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
        _run_ps_local_or_agent(ps_hard_kill, timeout_s=40)
        time.sleep(5)
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


def _run_queue_again_from_config(queue_launcher_path: Path, config_path: Path) -> int:
    """
    Rerun _queue_again_launcher__.py using run_config.json produced by the launcher.
    Expects keys:
      - ProjectWinPath
      - OutputMp4Path (optional)
      - send_jsx_src_wsl
    """
    try:
        cfg = load_json(config_path)
    except Exception as e:
        print(f"⚠️ Failed to read config for queue-again rerun: {config_path} | {e}")
        return 2

    project_win = str(cfg.get("ProjectWinPath") or "").strip()
    output_win  = str(cfg.get("OutputMp4Path") or "").strip()
    send_jsx_wsl = str(cfg.get("send_jsx_src_wsl") or "").strip()

    if not project_win or not send_jsx_wsl:
        print("⚠️ run_config.json missing required keys for queue-again rerun "
              "(ProjectWinPath/send_jsx_src_wsl).")
        return 2

    cmd = [sys.executable, str(queue_launcher_path), "--project-win", project_win]

    # output is optional (launcher accepts it but may not always be present)
    if output_win:
        cmd += ["--output-win", output_win]

    cmd += ["--send-jsx-wsl", send_jsx_wsl]

    print(f"🔁 Rerun via queue-again config: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    return r.returncode



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
# Retriable error detection
# -----------------------------
def _is_retriable_ame_queue_error(err: str) -> bool:
    """
    Treat this specific validation failure as retriable:
      "AME queue not confirmed (onEncoderJobQueued not received within 60s). Retries exhausted: 5"
    We keep matching loose so minor wording changes still retry.
    """
    s = str(err or "").strip().lower()
    if not s:
        return False
    return ("ame queue not confirmed" in s) and ("onencoderjobqueued" in s)

def _tail_lines(text: str, n: int = 60) -> str:
    s = str(text or "")
    lines = s.splitlines()
    return "\n".join(lines[-max(1, n):])


def _watchdog_stopped_in_tail(text: str) -> bool:
    # Generic, safe matcher (until you re-upload the real log format)
    tail = _tail_lines(text, n=80).lower()
    return ("stopped" in tail)


def _watchdog_wait_for_stopped(
    win_log_path: str,
    max_wait_s: int = 300,       # 5 minutes
    poll_s: float = 2.0,
    confirm_wait_s: int = 30,    # confirm for 30 seconds
) -> bool:
    """
    Polls the Windows watchdog log for up to 5 minutes.
    If 'stopped' appears in the tail, waits 30 seconds and checks again to confirm.
    Returns True if confirmed stopped, else False.
    """
    start = time.time()
    last_text = ""

    while time.time() - start < max_wait_s:
        try:
            txt = _win_read_text(win_log_path) or ""
        except Exception:
            txt = ""

        # If file is missing/unreadable, just keep polling until timeout
        if txt:
            last_text = txt
            if _watchdog_stopped_in_tail(txt):
                time.sleep(confirm_wait_s)
                try:
                    txt2 = _win_read_text(win_log_path) or ""
                except Exception:
                    txt2 = ""
                if txt2 and _watchdog_stopped_in_tail(txt2):
                    return True
                # if not confirmed, continue polling (do not treat as stopped)
        time.sleep(poll_s)

    return False


def force_close_media_encoder() -> None:
    """
    HARD FORCE CLOSE Adobe Media Encoder (WSL compatible).
    Mirrors the style of force_close_premiere but only for AME processes.
    """
    if (not _local_windows_bridge_available()) and (not _agent_url()):
        print("⚠️  Cannot force-close Media Encoder: no Windows bridge (/mnt/c) and WIN_AGENT_URL not set.")
        return

    ps_kill_ame = r"""
    $ErrorActionPreference = 'SilentlyContinue'

    function Kill-PidTree([int]$pid) {
      try { cmd /c ("taskkill /F /T /PID " + $pid) | Out-Null } catch {}
      try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
      try {
        $c = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $pid) -ErrorAction SilentlyContinue
        if ($c) { Invoke-CimMethod -InputObject $c -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
      } catch {}
    }

    # 1) Sweep by image name
    $ims = @('Adobe Media Encoder.exe','Adobe Media Encoder (Beta).exe','Media Encoder.exe','AdobeMediaEncoder.exe')
    foreach ($im in $ims) {
      try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
    }

    # 2) PID sweep via CIM
    $cims = @()
    try {
      $cims = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          ($_.Name -as [string]) -like '*Media Encoder*' -or
          ($_.CommandLine -as [string]) -like '*Media Encoder*' -or
          ($_.ExecutablePath -as [string]) -like '*Media Encoder*'
        }
    } catch {}

    $pids = @()
    foreach ($p in $cims) { try { $pids += [int]$p.ProcessId } catch {} }
    $pids = $pids | Sort-Object -Unique
    foreach ($pid in $pids) { Kill-PidTree $pid }

    # 3) Sweep again
    foreach ($im in $ims) {
      try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
    }
    """

    _run_ps_local_or_agent(ps_kill_ame, timeout_s=40)


# -----------------------------
# Main polling logic
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/mnt/c/VideoRenderAgain/_queue_again_runner/run_config.json", help="WSL path to run_config.json")
    ap.add_argument("--timeout-seconds", type=int, default=900, help="Total wait time in seconds")
    ap.add_argument("--poll-seconds", type=int, default=5, help="Polling interval seconds")

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

    # timeout retry + rerun behavior
    ap.add_argument(
        "--rerun-script",
        default="__run_premiere_after_xml__.py",
        help="Script to run after each timeout (default: __run_premiere_after_xml__.py)",
    )
    ap.add_argument(
        "--max-timeout-retries",
        type=int,
        default=3,
        help="How many timeout cycles to attempt before failing (default: 5)",
    )
    ap.add_argument(
        "--no-rerun-on-timeout",
        action="store_true",
        help="If set, do NOT rerun after timeout; fail immediately on the first timeout (old behavior).",
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

    win_val = str(cfg.get("PremiereValidationPath") or cfg.get("premiereValidationPath") or "").strip()
    if win_val:
        validation_path = win_to_wsl_path(win_val)
    else:
        validation_path = config_path.parent / "premiere_validation.json"

    win_validation_path = wsl_to_win_path(validation_path)

    timeout_s = int(args.timeout_seconds)
    timeout_min = round(timeout_s / 60.0, 2)

    print(f"📌 Config: {config_path}")
    print(f"📌 Waiting for: {validation_path}")
    if win_validation_path:
        print(f"📌 Windows fallback: {win_validation_path}")
    print(f"⏳ Timeout: {timeout_s}s (~{timeout_min} min) | Poll: {args.poll_seconds}s")

    max_retries = max(1, int(args.max_timeout_retries))
    rerun_enabled = not args.no_rerun_on_timeout
    rerun_script_path = _resolve_rerun_script_path(args.rerun_script) if rerun_enabled else Path()

    MAX_AME_QUEUE_RETRIES = 3
    ame_queue_retry_count = 0

    WIN_AME_WATCHDOG_LOG = r"C:\temp\AME_Watchdog\watchdog_per_item.log"
    MEDIA_ENCODER_AGENT_SCRIPT = "_media_encoder_agent__.py"
    media_encoder_agent_path = _resolve_rerun_script_path(MEDIA_ENCODER_AGENT_SCRIPT) if rerun_enabled else Path()
    

    for attempt in range(1, max_retries + 1):
        # Remove stale file before each waiting cycle (prevents old results)
        try:
            if validation_path.exists():
                delete_with_retries(validation_path, retries=12, sleep_s=0.25)
        except Exception:
            pass
        if win_validation_path:
            _win_delete(win_validation_path, retries=6, sleep_s=0.2)

        deadline = time.time() + timeout_s

        while time.time() < deadline:
            raw = None

            # 1) Prefer WSL view
            if validation_path.exists():
                try:
                    raw = validation_path.read_text(encoding="utf-8")
                except Exception:
                    raw = None

            # 2) If WSL doesn't see it, ask Windows (local PS or agent)
            if raw is None and win_validation_path and _win_file_exists(win_validation_path):
                try:
                    raw = _win_read_text(win_validation_path)
                except Exception:
                    raw = None

            if raw is not None:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    time.sleep(args.poll_seconds)
                    continue
                except Exception as e:
                    print(f"❌ Error reading validation file: {e}")
                    force_close_premiere(
                        use_schtask_fallback=not args.no_schtask_fallback,
                        schtask_name=args.schtask_name,
                    )
                    return 2

                delete_after_read = not args.no_delete_after_read
                if delete_after_read:
                    deleted = delete_with_retries(validation_path, retries=12, sleep_s=0.25)
                    if not deleted and win_validation_path:
                        deleted = _win_delete(win_validation_path, retries=12, sleep_s=0.25)
                    if not deleted:
                        print("⚠️ Could not delete validation file (locked). Continuing anyway.")

                st = data.get("status", None)

                if isinstance(st, bool):
                    ok = (st is True)
                else:
                    s = str(st or "").strip().lower()
                    ok = s in ("true", "success", "ok", "1", "yes")

                if ok:
                    print("✅ Premiere validation SUCCESS. Not forcing close.")
                    return 0

                err = data.get("error", "(no error provided)")
                print(f"❌ Premiere validation FAIL: {err}")

                # ✅ AME queue-not-confirmed: retry exactly 3 times, then WARN + PASS (exit 0)
                if _is_retriable_ame_queue_error(err):
                    ame_queue_retry_count += 1
                
                    if rerun_enabled and ame_queue_retry_count <= MAX_AME_QUEUE_RETRIES and attempt < max_retries:
                        print(
                            f"🔁 Retriable validation error (AME queue not confirmed). "
                            f"AME retry {ame_queue_retry_count}/{MAX_AME_QUEUE_RETRIES} | "
                            f"cycle {attempt}/{max_retries}."
                        )
                    
                        # NEW: On first occurrence of this AME issue, watch watchdog log for 5 minutes
                        if ame_queue_retry_count == 1:
                            print(f"📄 Watching AME watchdog log for STOPPED: {WIN_AME_WATCHDOG_LOG} (max 5 min)...")
                            stopped = _watchdog_wait_for_stopped(WIN_AME_WATCHDOG_LOG, max_wait_s=300, poll_s=2.0, confirm_wait_s=30)
                    
                            if stopped:
                                print("⚠️ AME watchdog indicates STOPPED (confirmed). Closing Premiere + Media Encoder.")
                                force_close_premiere(
                                    use_schtask_fallback=not args.no_schtask_fallback,
                                    schtask_name=args.schtask_name,
                                )
                                force_close_media_encoder()
                    
                                # Run AME recovery agent (DO NOT start AME directly)
                                print(f"🔁 Running Media Encoder agent: {media_encoder_agent_path}")
                                _run_rerun_script(media_encoder_agent_path)
                    
                                # Now rerun Premiere flow same as current logic
                                if rerun_script_path.name.lower() == "_queue_again_launcher__.py":
                                    _run_queue_again_from_config(rerun_script_path, config_path)
                                else:
                                    _run_rerun_script(rerun_script_path)
                                break
                    
                            else:
                                print("ℹ️ AME watchdog did NOT show STOPPED within 5 minutes. Proceeding with Premiere-only retry.")
                    
                        # Default behavior: Premiere-only retry (same as current)
                        force_close_premiere(
                            use_schtask_fallback=not args.no_schtask_fallback,
                            schtask_name=args.schtask_name,
                        )
                        
                        # IMPORTANT: if rerun-script is queue launcher, rerun using config so required CLI args exist
                        if rerun_script_path.name.lower() == "_queue_again_launcher__.py":
                            _run_queue_again_from_config(rerun_script_path, config_path)
                        else:
                            _run_rerun_script(rerun_script_path)
                        
                        break

                
                    # If AME issue repeated 3 times (or no retries left), WARN but PASS successfully
                    print(
                        f"⚠️ WARN: AME queue not confirmed repeated "
                        f"{ame_queue_retry_count}/{MAX_AME_QUEUE_RETRIES}. "
                        f"Allowing pipeline to continue (validation forced PASS)."
                    )
                    return 0
                

            time.sleep(args.poll_seconds)

        # Timeout
        print(
            f"❌ Timeout: validation file not found within {timeout_s}s (~{timeout_min} min). "
            f"Attempt {attempt}/{max_retries}."
        )

        # Preserve original behavior: force close on timeout
        force_close_premiere(
            use_schtask_fallback=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
        )

        if not rerun_enabled:
            return 4

        if attempt >= max_retries:
            return 4

        # Re-run the execution script, then loop again
        if rerun_script_path.name.lower() == "_queue_again_launcher__.py":
            _run_queue_again_from_config(rerun_script_path, config_path)
        else:
            _run_rerun_script(rerun_script_path)

    return 4


if __name__ == "__main__":
    raise SystemExit(main())
