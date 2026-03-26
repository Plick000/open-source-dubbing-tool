#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import time
import glob
import subprocess
from pathlib import Path
from typing import Optional, Tuple

# ---------------- CONFIG ----------------
RETRIES = 5
VERIFY_WAIT_SECONDS = 25

# Your JSX in repo (WSL)
JSX_REL_PATH = Path("Javascript") / "_MediaEncoderPolling.jsx"

# Where to copy JSX on Windows (avoid UNC)
WIN_JSX_DST = r"C:\temp\AME_Watchdog\_MediaEncoderPolling.jsx"

# Must match what your JSX writes
WIN_LOG_PATH = r"C:\temp\AME_Watchdog\watchdog_per_item.log"

# OPTIONAL: force a specific AME folder
# e.g. "Adobe Media Encoder 2024"
FORCE_AME_FOLDER_NAME: Optional[str] = None

AME_GLOB_CANDIDATES = [
    "/mnt/c/Program Files/Adobe/Adobe Media Encoder 2025/Adobe Media Encoder.exe",
]
# ----------------------------------------


def run(cmd: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wslpath_to_win(linux_path: str) -> str:
    cp = run(["wslpath", "-w", linux_path])
    out = (cp.stdout or "").strip()
    if cp.returncode != 0 or not out:
        raise RuntimeError(f"wslpath -w failed for: {linux_path}\n{cp.stdout}")
    return out


def winpath_to_wsl(win_path: str) -> str:
    cp = run(["wslpath", "-u", win_path])
    out = (cp.stdout or "").strip()
    if cp.returncode != 0 or not out:
        raise RuntimeError(f"wslpath -u failed for: {win_path}\n{cp.stdout}")
    return out


def find_ame_exe_wsl() -> str:
    matches: list[str] = []
    for pat in AME_GLOB_CANDIDATES:
        matches.extend(glob.glob(pat))
    matches = sorted(set(matches))

    if not matches:
        raise FileNotFoundError(
            "Could not find Adobe Media Encoder.exe.\nChecked:\n" + "\n".join(AME_GLOB_CANDIDATES)
        )

    if FORCE_AME_FOLDER_NAME:
        for m in matches:
            if FORCE_AME_FOLDER_NAME.lower() in m.lower():
                return m
        raise FileNotFoundError(
            f"FORCE_AME_FOLDER_NAME={FORCE_AME_FOLDER_NAME} set but not found.\nMatches:\n" + "\n".join(matches)
        )

    return matches[-1]  # newest


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def ensure_windows_dir(win_dir: str) -> None:
    # Create Windows dir via cmd (safe), but WITHOUT launching AME through cmd
    run(["cmd.exe", "/c", f'if not exist "{win_dir}" mkdir "{win_dir}"'])


def copy_jsx_to_windows(src_jsx_wsl: Path, dst_jsx_win: str) -> str:
    """
    Copies JSX from WSL filesystem -> Windows filesystem.
    Returns the WINDOWS path of the destination (C:\...).
    """
    dst_wsl = Path(winpath_to_wsl(dst_jsx_win))
    dst_wsl.parent.mkdir(parents=True, exist_ok=True)
    dst_wsl.write_bytes(src_jsx_wsl.read_bytes())
    return dst_jsx_win


def get_log_mtime(win_log_path: str) -> Optional[float]:
    try:
        log_wsl = Path(winpath_to_wsl(win_log_path))
        if not log_wsl.exists():
            return None
        return log_wsl.stat().st_mtime
    except Exception:
        return None


def verify_started(prev_mtime: Optional[float]) -> Tuple[bool, str]:
    mtime = get_log_mtime(WIN_LOG_PATH)
    if mtime is None:
        return False, f"log missing: {WIN_LOG_PATH}"
    if prev_mtime is None:
        return True, f"log created: {WIN_LOG_PATH}"
    if mtime > prev_mtime:
        return True, f"log updated: {WIN_LOG_PATH}"
    return False, f"log exists but not updated yet: {WIN_LOG_PATH}"


def launch_ame_direct_wsl(ame_exe_wsl: str, jsx_win_path: str) -> subprocess.Popen:
    """
    IMPORTANT: Launch the Windows .exe DIRECTLY from WSL (no cmd.exe).
    This avoids all quoting and UNC current-dir issues.
    """
    args = [
        ame_exe_wsl,
        "--console",
        "es.processFile",
        jsx_win_path,   # pass Windows path; AME expects it
    ]
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    root = repo_root()
    jsx_src = (root / JSX_REL_PATH).resolve()

    if not jsx_src.exists():
        print(f"[ERROR] JSX not found: {jsx_src}")
        return 2

    try:
        ame_exe = find_ame_exe_wsl()
    except Exception as e:
        print(f"[ERROR] {e}")
        return 3

    ensure_windows_dir(r"C:\temp\AME_Watchdog")
    jsx_dst_win = copy_jsx_to_windows(jsx_src, WIN_JSX_DST)

    print(f"[INFO] Repo root: {root}")
    print(f"[INFO] AME exe (WSL): {ame_exe}")
    print(f"[INFO] JSX src (WSL): {jsx_src}")
    print(f"[INFO] JSX dst (WIN): {jsx_dst_win}")
    print(f"[INFO] Verify log (WIN): {WIN_LOG_PATH}")
    if FORCE_AME_FOLDER_NAME:
        print(f"[INFO] Forced AME folder: {FORCE_AME_FOLDER_NAME}")
    print("")

    base_mtime = get_log_mtime(WIN_LOG_PATH)

    for attempt in range(1, RETRIES + 1):
        print(f"[INFO] Attempt {attempt}/{RETRIES}: launching AME (direct from WSL) + running JSX...")

        p = launch_ame_direct_wsl(ame_exe, jsx_dst_win)

        # Drain a tiny bit of stdout (non-blocking-ish)
        time.sleep(1)
        try:
            if p.stdout:
                chunk = p.stdout.read(300)  # small peek
                if chunk:
                    print("[DEBUG] AME stdout peek:\n" + chunk.strip() + "\n")
        except Exception:
            pass

        start = time.time()
        ok = False
        reason = ""
        while time.time() - start < VERIFY_WAIT_SECONDS:
            ok, reason = verify_started(base_mtime)
            if ok:
                print(f"[OK] Watchdog started: {reason}")
                return 0
            time.sleep(1)

        print(f"[WARN] Verify failed: {reason}")

        # Backoff before retry
        time.sleep(min(5 * attempt, 20))
        base_mtime = get_log_mtime(WIN_LOG_PATH)

    print("[ERROR] Failed to start watchdog after retries.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())