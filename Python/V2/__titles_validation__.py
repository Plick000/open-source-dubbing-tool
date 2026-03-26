#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sys
import time
import subprocess
import shutil
from pathlib import Path

# -----------------------------
# CONFIG PATHS
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"
SEGMENTS_PATH = PROJECT_ROOT / "output" / "JSON" / "A13__titles_segments__.json"

BASE_FAST_CONTENT_WIN = "Z:/Automated Dubbings/Projects"
POLL_INTERVAL_SEC = 20
MIN_FILE_BYTES = 1024

FFPROBE_TIMEOUT_SEC = 8

# -----------------------------
# WATCHDOG CONFIG
# -----------------------------
WATCHDOG_LOG_WIN = r"C:\temp\AME_Watchdog\watchdog_per_item.log"
AGENT_SCRIPT_REL_PATH = "../../_media_encoder_agent__.py" 

# -----------------------------
# RETRY / TIME LIMITS
# -----------------------------
MAX_VALIDATION_WINDOW_SEC = 60 * 30  # 30 Minutes
MAX_BATCH_RENDER_RETRIES = 3
BATCH_TITLES_SCRIPT_NAME = "__batch_titles_rendering__.py"

# -----------------------------
# Helpers
# -----------------------------

def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def clean_video_name(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^Video\s*\d+\s*-\s*", "", s, flags=re.IGNORECASE).strip()
    return s

def cap_language(raw: str) -> str:
    s = (raw or "").strip()
    return s.capitalize() if s else s

def windows_drive_to_wsl_path(p: str) -> Path:
    p = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m and os.name != "nt":
        drive = m.group(1).lower()
        rest = m.group(2)
        return Path("/mnt") / drive / rest
    return Path(p)

def expected_title_filenames(segments) -> list[str]:
    if not isinstance(segments, list):
        raise ValueError("Segments JSON must be a list of objects.")
    ids: list[int] = []
    for obj in segments:
        if isinstance(obj, dict) and "id" in obj:
            try:
                v = int(obj["id"])
                if v > 0:
                    ids.append(v)
            except Exception:
                pass
    if not ids:
        return []
    ids = sorted(set(ids))
    return [f"title_{i:03d}.mp4" for i in ids]

def is_rendered_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_FILE_BYTES
    except FileNotFoundError:
        return False

def safe_delete_file(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass

def is_media_corrupted_ffprobe(p: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=FFPROBE_TIMEOUT_SEC)
        if r.returncode != 0: return True
        out = (r.stdout or "").strip()
        return float(out) <= 0.05
    except Exception:
        return True

def resolve_batch_titles_script() -> Path:
    candidates = [Path(__file__).resolve().with_name(BATCH_TITLES_SCRIPT_NAME)]
    for c in candidates:
        if c.exists() and c.is_file(): return c
    raise FileNotFoundError(f"Could not locate '{BATCH_TITLES_SCRIPT_NAME}'")

def run_batch_titles_rendering() -> int:
    script = resolve_batch_titles_script()
    cmd = [sys.executable, str(script)]
    print(f"\n[RENDER] Running batch titles renderer: {script}")
    return subprocess.call(cmd)

# -----------------------------
# AME Watchdog & Recovery
# -----------------------------

def get_watchdog_log_path() -> Path:
    return windows_drive_to_wsl_path(WATCHDOG_LOG_WIN)

def resolve_agent_script() -> Path:
    current_dir = Path(__file__).resolve().parent
    return (current_dir / AGENT_SCRIPT_REL_PATH).resolve()

def check_log_for_stopped(log_path: Path) -> bool:
    """Case-insensitive check for 'stopped' status in log."""
    if not log_path.exists():
        return False
    try:
        # Lowercase content to handle 'Stopped', 'STOPPED', or 'stopped'
        content = log_path.read_text(encoding="utf-8", errors="ignore").lower()
        return "stopped" in content
    except Exception as e:
        print(f"[DEBUG] Error reading log file: {e}")
        return False

def kill_media_encoder():
    print("[WATCHDOG] 🔪 Hard killing Adobe Media Encoder...")
    cmd = ["taskkill", "/F", "/IM", "Adobe Media Encoder.exe"]
    if os.name != 'nt': cmd[0] = "taskkill.exe"
    subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

def run_agent_script():
    agent_script = resolve_agent_script()
    if not agent_script.exists():
        print(f"[WATCHDOG] ❌ Agent script not found at: {agent_script}")
        return
    print(f"[WATCHDOG] 🚀 Running Agent: {agent_script.name}")
    # Run agent and move forward
    subprocess.Popen([sys.executable, str(agent_script)]) 

def perform_ame_recovery_sequence():
    """Recovery sequence: 60s wait -> Kill -> Run Agent -> 30s wait -> Run Batch Rendering."""
    print("[WATCHDOG] ⏳ Waiting 60 seconds for 'Stopped' state stability...")
    time.sleep(60)
    
    kill_media_encoder()
    
    print("[WATCHDOG] Starting Media Encoder Agent...")
    run_agent_script()
    
    print("[WATCHDOG] ⏳ Waiting 30 seconds after Agent start...")
    time.sleep(30)
    
    print("[WATCHDOG] 🔄 Re-running Batch Titles Rendering to re-queue items...")
    run_batch_titles_rendering()

def poll_watchdog_for_stopped(log_path: Path, duration_sec: int) -> bool:
    """Polls the log for 'stopped' for up to duration_sec."""
    print(f"[WATCHDOG] 🔍 Polling {log_path.name} for 'Stopped' status (Max {duration_sec}s)...")
    end_time = time.time() + duration_sec
    while time.time() < end_time:
        if check_log_for_stopped(log_path):
            return True
        time.sleep(10)
    return False

# -----------------------------
# Main Validation Logic
# -----------------------------

def validate_titles_within_window(expected_files, target_folder, window_sec):
    last_known_sizes = {}
    t0 = time.time()
    deadline = t0 + window_sec
    prev_poll_sizes_for_corruption = {}
    
    next_watchdog_check_allowed = 0 
    watchdog_log_path = get_watchdog_log_path()

    while True:
        missing = []
        current_sizes = {}
        for fname in expected_files:
            fpath = target_folder / fname
            if is_rendered_file(fpath):
                sz = fpath.stat().st_size
                if prev_poll_sizes_for_corruption.get(fname) == sz:
                    if is_media_corrupted_ffprobe(fpath):
                        print(f"\n🧨 CORRUPTED: {fpath} -> deleting")
                        safe_delete_file(fpath)
                        missing.append(fname)
                        continue
                current_sizes[fname] = sz
            else:
                missing.append(fname)
        
        # Condition: Missing Ratio > 90%
        missing_ratio = (len(missing) / len(expected_files)) if expected_files else 0
        current_time = time.time()

        if missing_ratio > 0.90 and current_time >= next_watchdog_check_allowed:
            print(f"\n[WATCHDOG] ⚠️ High missing ratio ({missing_ratio:.1%}). Checking logs...")
            # Poll for 5 minutes (300s)
            if poll_watchdog_for_stopped(watchdog_log_path, duration_sec=300):
                print("\n[WATCHDOG] 🛑 'Stopped' status CONFIRMED.")
                perform_ame_recovery_sequence()
                
                # Block watchdog for 30 minutes
                next_watchdog_check_allowed = time.time() + (30 * 60)
                
                # Reset 30-minute validation window
                print("[WATCHDOG] 🔄 Resetting validation timer.")
                t0 = time.time()
                deadline = t0 + window_sec
                last_known_sizes, prev_poll_sizes_for_corruption = {}, {}
            else:
                print("\n[WATCHDOG] ℹ️ 'Stopped' not found in log after 5 mins. Resuming validation.")
                # Wait 5 minutes before checking log again
                next_watchdog_check_allowed = time.time() + 300
        
        if not missing:
            if last_known_sizes and current_sizes == last_known_sizes:
                print("\n✅ All clips found and stable!")
                return True
            last_known_sizes = current_sizes
        else:
            last_known_sizes = {}
            remaining = max(0, int(deadline - time.time()))
            print(f"⏳ Missing {len(missing)}/{len(expected_files)} titles. Remaining: {remaining}s")

        if time.time() >= deadline: return False
        prev_poll_sizes_for_corruption = dict(current_sizes)
        time.sleep(POLL_INTERVAL_SEC)

def main():
    cfg = load_json(CONFIG_PATH)
    video_name = clean_video_name(cfg.get("video_name", ""))
    lang_cap = cap_language(cfg.get("language", ""))
    segments = load_json(SEGMENTS_PATH)
    expected_files = expected_title_filenames(segments)
    
    if not expected_files:
        print("\n✅ No titles to verify.")
        return 0
    
    target_folder = windows_drive_to_wsl_path(f"{BASE_FAST_CONTENT_WIN}/{video_name}/{lang_cap}/Titles/VideoClips")
    cycle, render_runs = 1, 0

    while True:
        print(f"\n[WINDOW] Validation cycle #{cycle}")
        if validate_titles_within_window(expected_files, target_folder, MAX_VALIDATION_WINDOW_SEC):
            return 0
        if render_runs >= MAX_BATCH_RENDER_RETRIES: return 2
        render_runs += 1
        run_batch_titles_rendering()
        cycle += 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)