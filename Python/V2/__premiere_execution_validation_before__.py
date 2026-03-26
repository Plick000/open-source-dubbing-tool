#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from urllib import request


# -----------------------------
# Path helpers
# -----------------------------
def win_to_wsl_path(p: str) -> Path:
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
    for _ in range(max(1, retries)):
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception:
            time.sleep(sleep_s)
    return not path.exists()


# -----------------------------
# Windows Agent helpers
# -----------------------------
def _agent_url() -> str:
    return str(
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


def _local_windows_bridge_available() -> bool:
    ps_mnt = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists()
    cmd_mnt = Path("/mnt/c/Windows/System32/cmd.exe").exists()
    st_mnt = Path("/mnt/c/Windows/System32/schtasks.exe").exists()
    return bool(ps_mnt or cmd_mnt or st_mnt)


def _run_ps_local_or_agent(ps_script: str, timeout_s: int = 35) -> dict:
    """Run PowerShell via local WSL bridge (/mnt/c/...) if possible, else via WIN_AGENT_URL.

    Returns a dict that may include: exitCode, stdout, stderr.
    """
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

    return _agent_run_powershell(ps_script, timeout_s=timeout_s) or {}


def _win_file_exists(win_path: str) -> bool:
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
            if rc in (0, 1) and rc == 0:
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

    $ims = @('Adobe Premiere Pro.exe','PremierePro.exe','AdobePremierePro.exe','Adobe Premiere Pro (Beta).exe')
    foreach ($im in $ims) {
      try { cmd /c ("taskkill /F /T /IM `"" + $im + "`"") | Out-Null } catch {}
    }

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
# Re-run helper (WSL side)
# -----------------------------
def run_rerun_script(script_path: str) -> int:
    s = str(script_path or "").strip()
    if not s:
        print("⚠️  Rerun script path is empty; skipping rerun.")
        return 1

    p = Path(s).expanduser()
    if not p.is_absolute():
        p = (Path(__file__).resolve().parent / p)

    if not p.exists():
        print(f"⚠️  Rerun script not found: {p}")
        return 127

    print("\n" + "=" * 74)
    print(f"🔁 Re-running: {p}")
    print("=" * 74)

    try:
        r = subprocess.run(["python3", str(p)], check=False)
        print("=" * 74)
        print(f"🔁 Re-run finished (exit code: {r.returncode})")
        print("=" * 74 + "\n")
        return int(r.returncode or 0)
    except Exception as e:
        print(f"⚠️  Failed to run rerun script: {e}")
        return 2


# -----------------------------
# Main polling logic
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument("--config", default="/mnt/c/PPro_BeforeXML/run_config.json", help="WSL path to run_config.json")
    ap.add_argument("--timeout-seconds", type=int, default=600, help="Total wait time in seconds")
    ap.add_argument("--poll-seconds", type=int, default=5, help="Polling interval seconds")

    ap.add_argument(
        "--validation",
        default="",
        help=(
            "Windows or WSL path to premiere_validation.json. "
            "Use this if you don't have run_config.json. "
            r"Example: C:\PPro_BeforeXML\premiere_validation.json or /mnt/c/PPro_BeforeXML/premiere_validation.json"
        ),
    )

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

    ap.add_argument(
        "--rerun-script",
        default="__run_premiere_before_xml__.py",
        help=(
            "WSL path to the script to re-run when validation file is NOT found before timeout. "
            "If relative, it is resolved relative to this validation file."
        ),
    )
    ap.add_argument(
        "--max-timeout-retries",
        type=int,
        default=3,
        help="Max number of timeout cycles before failing (default: 5).",
    )
    ap.add_argument(
        "--no-rerun-on-timeout",
        action="store_true",
        help="If set, keep old behavior: fail immediately on timeout (no re-run).",
    )

    args = ap.parse_args()

    validation_arg = str(args.validation or "").strip()
    config_path = Path(args.config)

    cfg = None
    if config_path.exists():
        try:
            cfg = load_json(config_path)
        except Exception:
            cfg = None

    if validation_arg:
        validation_path = win_to_wsl_path(validation_arg)
    else:
        win_val = ""
        if isinstance(cfg, dict):
            win_val = str(cfg.get("PremiereValidationPath") or cfg.get("premiereValidationPath") or "").strip()

        if win_val:
            validation_path = win_to_wsl_path(win_val)
        else:
            validation_path = Path("/mnt/c/PPro_BeforeXML/premiere_validation.json")

    if not str(validation_path).strip():
        print("❌ No validation path resolved. Provide --validation.")
        force_close_premiere(
            use_schtask_fallback=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
        )
        return 3

    win_validation_path = wsl_to_win_path(validation_path)

    timeout_s = int(args.timeout_seconds)
    timeout_min = round(timeout_s / 60.0, 2)

    if config_path.exists():
        print(f"📌 Config: {config_path}")
    else:
        print("📌 Config: (none) using direct validation path")

    print(f"📌 Waiting for: {validation_path}")
    if win_validation_path:
        print(f"📌 Windows fallback: {win_validation_path}")
    print(f"⏳ Timeout: {timeout_s}s (~{timeout_min} min) | Poll: {args.poll_seconds}s")

    max_tries = max(1, int(args.max_timeout_retries))
    rerun_enabled = (not args.no_rerun_on_timeout)

    for attempt in range(1, max_tries + 1):
        if attempt > 1:
            print("\n" + "#" * 74)
            print(f"🔄 TIMEOUT RETRY ATTEMPT {attempt}/{max_tries}")
            print("#" * 74)

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

            if validation_path.exists():
                try:
                    raw = validation_path.read_text(encoding="utf-8")
                except Exception:
                    raw = None

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
                    if (not deleted) and win_validation_path:
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
                force_close_premiere(
                    use_schtask_fallback=not args.no_schtask_fallback,
                    schtask_name=args.schtask_name,
                )
                return 2

            time.sleep(args.poll_seconds)

        print(f"❌ Timeout: validation file not found within {timeout_s}s (~{timeout_min} min).")
        force_close_premiere(
            use_schtask_fallback=not args.no_schtask_fallback,
            schtask_name=args.schtask_name,
        )

        if (not rerun_enabled) or (attempt >= max_tries):
            return 4

        run_rerun_script(args.rerun_script)

    return 4


if __name__ == "__main__":
    raise SystemExit(main())
