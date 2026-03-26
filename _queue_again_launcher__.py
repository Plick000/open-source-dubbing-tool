#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import argparse
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# -----------------------------
# Defaults (override via args if needed)
# -----------------------------
DEFAULT_PREMIERE_EXE_WIN = r"C:\Program Files\Adobe\Adobe Premiere Pro 2025\Adobe Premiere Pro.exe"

DEFAULT_RUNNER_DIR_WSL = Path("/mnt/c/VideoRenderAgain/_queue_again_runner")
DEFAULT_RUNNER_DIR_WIN = r"C:\VideoRenderAgain\_queue_again_runner"

DEFAULT_PREMIERE_BUSY_WAIT_SEC = 600   # 10 minutes
DEFAULT_TASKLIST_TIMEOUT_SEC = 20

PREMIERE_PROCESS_NAMES = [
    "Adobe Premiere Pro.exe",
    "Adobe Premiere Pro (Beta).exe",
]


# -----------------------------
# Helpers
# -----------------------------
def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def win_to_wsl_path(win_path: str) -> str:
    """
    Convert a Windows absolute path like:
      C:\\Users\\IT\\Documents\\file.txt
    to WSL path:
      /mnt/c/Users/IT/Documents/file.txt
    If it's not a drive path, returns a forward-slash normalized string.
    """
    p = sanitize_drive_path(win_path or "")
    if re.match(r"^[A-Za-z]:\\", p):
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return p.replace("\\", "/")


def get_userprofile_win(timeout_sec: int = 10) -> str:
    """
    Returns Windows user profile directory, e.g. C:\\Users\\IT
    Uses PowerShell so we don't hardcode username.
    """
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('UserProfile')"],
            timeout=timeout_sec,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace").strip()
        return sanitize_drive_path(out)
    except Exception:
        # Fallback: still avoid hardcoding. Empty means caller can skip or log.
        return ""

def ensure_run_config(
    runner_dir_wsl: Path,
    runner_dir_win: str,
    send_jsx_src_wsl: Path,
    premiere_exe_win: str,
    output_mp4_win: str,
    project_win: str,   # <-- ADD THIS
) -> None:
    """Create/update run_config.json with keys required by _SendToAME.jsx."""
    cfg_path = runner_dir_wsl / "run_config.json"

    # Normalize output path
    output_mp4_win = sanitize_drive_path(output_mp4_win or "")

    # Resolve Windows user profile dynamically (no hardcoding)
    userprofile = get_userprofile_win()
    preset_dir = (userprofile + r"\Documents\Adobe\Adobe Media Encoder\25.0\Presets") if userprofile else ""
    preset_epr_file = (preset_dir + r"\Video Render Preset.epr") if preset_dir else ""

    ame_log_win = (userprofile + r"\Documents\Adobe\Adobe Media Encoder\25.0\AMEEncodingErrorLog.txt") if userprofile else ""
    ame_log_wsl = win_to_wsl_path(ame_log_win) if ame_log_win else ""

    cfg = {
        "repo_root_wsl": "/home/vvruntime/viralverse-dubbing-automation",
        "drop_dir_wsl": "/mnt/c/VideoRenderAgain",
        "drop_dir_win": r"C:\VideoRenderAgain",
        "runner_dir_wsl": str(runner_dir_wsl),
        "runner_dir_win": runner_dir_win,
        "premiere_exe_win": sanitize_drive_path(premiere_exe_win),

        "send_jsx_src_wsl": str(send_jsx_src_wsl),
        "send_jsx_dst_wsl": str(runner_dir_wsl / "_SendToAME.jsx"),
        "send_jsx_dst_win": rf"{runner_dir_win}\_SendToAME.jsx",

        "launcher_jsx_wsl": str(runner_dir_wsl / "launcher.jsx"),
        "launcher_jsx_win": rf"{runner_dir_win}\launcher.jsx",

        "allowed_projects_root_win": r"Z:\Automated Dubbings\Projects\\",

        # Dynamic user (no hardcoding)
        "ame_log_win": ame_log_win,
        "ame_log_wsl": ame_log_wsl,

        # ✅ Required by your JSX logs
        "OutputMp4Path": output_mp4_win,

        # ✅ MUST be full .epr file path (not folder) to avoid "Unknown error exception"
        "PresetEprPath": preset_epr_file,
        "ProjectWinPath": sanitize_drive_path(project_win),
    }

    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cfg_path)
    

def strip_win_extended_prefix(p: str) -> str:
    p = (p or "").strip()
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[len("\\\\?\\UNC\\"):]
    if p.startswith("\\\\?\\"):
        return p[len("\\\\?\\"):]
    if p.startswith("\\??\\UNC\\"):
        return "\\\\" + p[len("\\??\\UNC\\"):]
    if p.startswith("\\??\\"):
        return p[len("\\??\\"):]
    return p


def sanitize_drive_path(p: str) -> str:
    p = (p or "").strip().strip('"')
    for _ in range(10):
        p2 = strip_win_extended_prefix(p)
        if p2 == p:
            break
        p = p2
    # fix Z:\!\ bug if ever appears
    p = re.sub(r"^([A-Za-z]:)\\!\\", r"\1\\", p)
    # normalize weird spaces
    p = p.replace("\u00A0", " ").strip()
    return p


def tasklist_has_process(name: str, timeout_sec: int) -> bool:
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", f'tasklist /FI "IMAGENAME eq {name}"'],
            timeout=timeout_sec,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
        return name.lower() in out.lower()
    except Exception:
        return False


def wait_until_premiere_closed(max_wait_sec: int, timeout_sec: int) -> None:
    start = time.time()
    while True:
        any_running = any(tasklist_has_process(n, timeout_sec) for n in PREMIERE_PROCESS_NAMES)
        if not any_running:
            return
        if (time.time() - start) >= max_wait_sec:
            # Keep behavior minimal: stop waiting and proceed anyway
            return
        time.sleep(3)


def launch_premiere_with_es_processfile(premiere_exe_win: str, launcher_win: str) -> None:
    """
    Launch Premiere and execute a JSX file ON STARTUP:
      "Premiere.exe" /C es.processFile "C:\path\launcher.jsx"
    """
    premiere_exe_win = sanitize_drive_path(premiere_exe_win)
    launcher_win = sanitize_drive_path(launcher_win)

    # Build the exact CLI Premiere expects (most reliable form)
    # NOTE: We pass ONE argument string so quoting stays intact.
    arg_str = f'/C es.processFile "{launcher_win}"'

    ps_cmd = (
        f"$exe = '{premiere_exe_win}'; "
        f"$args = '{arg_str}'; "
        f"Start-Process -FilePath $exe -ArgumentList $args"
    )

    subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", ps_cmd])


# -----------------------------
# launcher.jsx generator
# -----------------------------
def build_launcher_jsx(project_win: str, send_jsx_win: str, log_win: str) -> str:
    project_win = sanitize_drive_path(project_win)
    send_jsx_win = sanitize_drive_path(send_jsx_win)
    log_win = sanitize_drive_path(log_win)

    # Safely encode Windows paths as JavaScript string literals
    proj_js = json.dumps(project_win)
    jsx_js = json.dumps(send_jsx_win)
    log_js = json.dumps(log_win)

    return f"""/* launcher.jsx (generated)
   Opens project and evalFile() SendToAME.jsx.
*/

(function () {{
  function safeFile(p) {{
    try {{ return new File(p); }} catch(e) {{ return null; }}
  }}

  var logPath = {log_js};
  var logFileObj = safeFile(logPath);

  function logFile(s) {{
    try {{
      if (!logFileObj) return;
      logFileObj.open("a");
      logFileObj.writeln(new Date().toISOString() + " " + s);
      logFileObj.close();
    }} catch(e) {{}}
  }}

  // Premiere Events panel + file log (best effort)
  function ev(msg) {{
    try {{
      if (app && app.setSDKEventMessage) {{
        app.setSDKEventMessage(String(msg), "info");
      }}
    }} catch(e) {{}}
    logFile(String(msg));
  }}

  function sleep(ms) {{
    var end = (new Date()).getTime() + ms;
    while ((new Date()).getTime() < end) {{}}
  }}

  function waitReady(ms) {{
    var start = (new Date()).getTime();
    while (((new Date()).getTime() - start) < ms) {{
      try {{
        // isDocumentOpen() can be flaky; just touching app.project is a good sign
        if (app && app.project) return true;
      }} catch(e) {{}}
      sleep(500);
    }}
    return false;
  }}

  ev("✅ launcher.jsx started");
  var proj = {proj_js};
  var jsx = {jsx_js};

  try {{
    ev("📂 Opening project: " + proj);
    app.openDocument(proj);
  }} catch(e) {{
    ev("❌ openDocument failed: " + e);
    throw e;
  }}

  // Wait longer for project to fully initialize (reduces 'unknown error' in downstream scripts)
  var ok = waitReady(180000); // 3 minutes
  if (!ok) {{
    ev("⚠️ waitReady timed out (continuing anyway)");
  }}

  // Extra settle time (Premiere can still be initializing bins/sequences)
  sleep(3000);

  try {{
    ev("▶ Running SendToAME.jsx: " + jsx);
    $.evalFile(jsx);
    ev("✅ SendToAME.jsx returned (no exception)");
  }} catch(e) {{
    ev("❌ evalFile failed: " + e);
    throw e;
  }}

  ev("🎉 launcher.jsx finished OK");
}})();
"""

# -----------------------------
# Main
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-win", required=True, help="Windows path to .prproj (Z:\\...)")
    ap.add_argument("--output-win", default="", help="Windows output mp4 path for AME (required by SendToAME.jsx)")
    ap.add_argument("--send-jsx-wsl", required=True, help="WSL path to SendToAME.jsx")
    ap.add_argument("--premiere-exe-win", default=DEFAULT_PREMIERE_EXE_WIN)
    ap.add_argument("--runner-dir-wsl", default=str(DEFAULT_RUNNER_DIR_WSL))
    ap.add_argument("--runner-dir-win", default=DEFAULT_RUNNER_DIR_WIN)
    ap.add_argument("--premiere-busy-wait-sec", type=int, default=DEFAULT_PREMIERE_BUSY_WAIT_SEC)
    ap.add_argument("--tasklist-timeout-sec", type=int, default=DEFAULT_TASKLIST_TIMEOUT_SEC)
    args = ap.parse_args()

    project_win = sanitize_drive_path(args.project_win)
    send_jsx_src_wsl = Path(args.send_jsx_wsl)
    runner_dir_wsl = Path(args.runner_dir_wsl)
    runner_dir_win = args.runner_dir_win

    ensure_dir(runner_dir_wsl)
    ensure_run_config(runner_dir_wsl, runner_dir_win, send_jsx_src_wsl, args.premiere_exe_win, args.output_win, project_win=args.project_win)

    # Copy SendToAME.jsx into C: runner dir so JSX can evalFile it reliably
    send_jsx_dst_wsl = runner_dir_wsl / "_SendToAME.jsx"
    shutil.copy2(str(send_jsx_src_wsl), str(send_jsx_dst_wsl))

    send_jsx_win = rf"{runner_dir_win}\_SendToAME.jsx"
    launcher_win = rf"{runner_dir_win}\launcher.jsx"
    launcher_wsl = runner_dir_wsl / "launcher.jsx"
    log_win = rf"{runner_dir_win}\launcher.log"

    jsx_text = build_launcher_jsx(project_win, send_jsx_win, log_win)
    launcher_wsl.write_text(jsx_text, encoding="utf-8")

    # Wait if Premiere already open (your requested behavior)
    wait_until_premiere_closed(args.premiere_busy_wait_sec, args.tasklist_timeout_sec)

    # Launch Premiere and run launcher.jsx at startup
    launch_premiere_with_es_processfile(args.premiere_exe_win, launcher_win)

    print("[launcher] ✅ Started Premiere with es.processFile")
    print("[launcher] Project:", project_win)
    print("[launcher] Launcher:", launcher_win)
    print("[launcher] Log:", log_win)
    print("[launcher] SendToAME copied to:", send_jsx_win)


if __name__ == "__main__":
    main()