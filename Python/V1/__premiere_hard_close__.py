# Python/V1/__premiere_hard_close__.py
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import List, Tuple


def _run(cmd: List[str], timeout: int = 40) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError as e:
        return 127, "", f"FileNotFoundError: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"TimeoutExpired: {' '.join(cmd)}"


def _which(x: str) -> str | None:
    return shutil.which(x)


def _pick_pwsh() -> str | None:
    for c in ["powershell.exe", "powershell", "pwsh.exe", "pwsh"]:
        if _which(c):
            rc, out, _ = _run([c, "-NoProfile", "-Command", "echo ok"])
            if rc == 0 and out.strip().lower() == "ok":
                return c
    return None


def _cmd_exe() -> str:
    return "cmd.exe" if _which("cmd.exe") else "cmd"


def _find_premiere_processes(pwsh: str) -> List[dict]:
    ps = r"""
$ErrorActionPreference="SilentlyContinue";

$procs = Get-CimInstance Win32_Process |
  Where-Object {
    ($_.Name -like "*Premiere*") -or
    ($_.Name -like "*Adobe Premiere Pro*") -or
    ($_.ExecutablePath -like "*Adobe Premiere Pro*")
  } |
  Select-Object ProcessId, Name, ExecutablePath;

if ($procs) {
  $procs | ForEach-Object {
    "$($_.ProcessId)`t$($_.Name)`t$($_.ExecutablePath)"
  }
}
"""
    rc, out, _ = _run([pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    if rc != 0:
        return []

    procs: List[dict] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            procs.append(
                {
                    "pid": int(parts[0].strip()),
                    "name": parts[1].strip(),
                    "path": parts[2].strip() if len(parts) >= 3 else "",
                }
            )

    # de-dupe by pid
    seen = set()
    uniq = []
    for p in procs:
        if p["pid"] not in seen:
            seen.add(p["pid"])
            uniq.append(p)
    return uniq


def _stop_process_powershell(pwsh: str, pids: List[int]) -> Tuple[int, str, str]:
    pid_list = ",".join(str(x) for x in pids)
    ps = rf"""
$ErrorActionPreference="Continue";
$pids = @({pid_list});

foreach ($pid in $pids) {{
  try {{
    Stop-Process -Id $pid -Force -ErrorAction Stop
    "OK Stop-Process PID=$pid"
  }} catch {{
    "ERR Stop-Process PID=$pid :: $($_.Exception.Message)"
  }}
}}
"""
    return _run([pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])


def _taskkill_pid(cmdexe: str, pid: int) -> Tuple[int, str, str]:
    return _run([cmdexe, "/c", "taskkill", "/F", "/T", "/PID", str(pid)])


def _taskkill_image(cmdexe: str, image: str) -> Tuple[int, str, str]:
    return _run([cmdexe, "/c", "taskkill", "/F", "/T", "/IM", image])


def hard_close_premiere(retries: int = 10, sleep_seconds: float = 1.0) -> Tuple[bool, str]:
    pwsh = _pick_pwsh()
    if not pwsh or not (_which("cmd.exe") or _which("cmd")):
        return True, "No Windows interop available (expected in Linux Docker)."

    cmdexe = _cmd_exe()
    last_logs: List[str] = []

    image_names = [
        "Adobe Premiere Pro.exe",
        "Adobe Premiere Pro Beta.exe",
        "Adobe Premiere Pro (Beta).exe",
    ]

    for attempt in range(1, retries + 1):
        procs = _find_premiere_processes(pwsh)
        if not procs:
            return True, "Premiere not running."

        pids = [p["pid"] for p in procs]
        last_logs.append(f"[Attempt {attempt}] Found PIDs: {pids}")

        # Layer 1: Stop-Process
        rc1, out1, err1 = _stop_process_powershell(pwsh, pids)
        if out1:
            last_logs.append(out1)
        if err1:
            last_logs.append(err1)

        # Layer 2: taskkill per PID (tree)
        for pid in pids:
            rc2, out2, err2 = _taskkill_pid(cmdexe, pid)
            if out2:
                last_logs.append(f"taskkill PID {pid}: {out2}")
            if err2:
                last_logs.append(f"taskkill PID {pid} ERR: {err2}")

        # Layer 3: taskkill by image name
        for img in image_names:
            rc3, out3, err3 = _taskkill_image(cmdexe, img)
            if out3:
                last_logs.append(f"taskkill IM {img}: {out3}")
            if err3 and "not found" not in err3.lower():
                last_logs.append(f"taskkill IM {img} ERR: {err3}")

        time.sleep(sleep_seconds)

        if not _find_premiere_processes(pwsh):
            return True, "Premiere killed."

    remaining = _find_premiere_processes(pwsh)
    rem_text = "\n".join(
        [f"PID={p['pid']} NAME={p['name']} PATH={p['path']}" for p in remaining]
    ) or "(none)"

    diag = "\n".join(last_logs[-40:])
    return False, f"Still running after retries.\nRemaining:\n{rem_text}\n\nLast logs:\n{diag}"


def main() -> int:
    retries = int(os.getenv("PREMIERE_KILL_RETRIES", "10"))
    sleep_s = float(os.getenv("PREMIERE_KILL_SLEEP", "1.0"))

    pwsh = _pick_pwsh()
    if not pwsh or not (_which("cmd.exe") or _which("cmd")):
        print("✅ PREMIERE_GUARD: No Windows interop (PowerShell/cmd not found). PASS.")
        return 0

    print("ℹ️ PREMIERE_GUARD: Windows interop detected. Managing Premiere processes if any.")

    # Not running -> pass
    if not _find_premiere_processes(pwsh):
        print("✅ PREMIERE_GUARD: Premiere is NOT running. PASS.")
        return 0

    print("⚠️ PREMIERE_GUARD: Premiere IS running. Initiating HARD KILL...")

    ok, msg = hard_close_premiere(retries=retries, sleep_seconds=sleep_s)
    if ok:
        print("✅ PREMIERE_GUARD: Premiere hard-killed successfully. PASS.")
        return 0

    print("❌ PREMIERE_GUARD: FAILED to kill Premiere (still running after retries).")
    print(msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
