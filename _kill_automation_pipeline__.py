#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
_kill_automation_pipeline__.py

Purpose (existing):
- Watch the newest status .txt under the worker's status directory.
- When a completion marker is detected, kill the VV-3 pipeline processes.

Added (requested):
After killing the VV-3 pipeline:
1) Poll AME watchdog log (C:\temp\AME_Watchdog\watchdog_per_item.log) until status becomes "stopped".
2) Validate "stopped" remains stable for 60s, then hard-kill Adobe Media Encoder.
3) Run _media_encoder_agent__.py
4) Scan *this worker's* status files modified within the last N hours (default 15),
   extract languages marked DONE, and verify rendered outputs exist under:
     Z:\Automated Dubbings\Projects\{video_name}\RenderedVideos
   If missing, run _queue_again_launcher__.py with best-effort inputs.

Hard rule followed:
- Existing CLI args and core logic (watch -> match -> pkill) are preserved.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_STOP_LINE_REGEX = r"^(DONE|FAIL)\b|JOB\s+FINISHED|VIDEO\s+INCOMPLETE|Waiting\s+\d+s\s+before\s+next\s+language"
DEFAULT_KILL_PATTERN = r"(_pipeline_orchestrator__\.py|__run__\.py)"
DEFAULT_START_TIME = "06:30"

# New defaults (WSL paths)
DEFAULT_WATCHDOG_LOG = r"/mnt/c/temp/AME_Watchdog/watchdog_per_item.log"
DEFAULT_WORKERS_ROOT = r"/mnt/z/Automated Dubbings/JOBS/workers"
DEFAULT_PROJECTS_ROOT = r"/mnt/z/Automated Dubbings/Projects"


# ---------------------------
# Existing helpers (preserved)
# ---------------------------

def parse_hhmm(s: str) -> Tuple[int, int]:
    s = s.strip()
    hh, mm = s.split(":")
    hh = int(hh)
    mm = int(mm)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError
    return hh, mm


def next_start_time(hh: int, mm: int, grace_seconds: int) -> datetime:
    """
    If we are slightly late (within grace_seconds), start immediately.
    Else, schedule for next occurrence of HH:MM.
    """
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= target:
        late = (now - target).total_seconds()
        if late <= grace_seconds:
            return now  # start immediately (grace window)
        return target + timedelta(days=1)
    return target


def sleep_until(dt: datetime) -> None:
    while True:
        now = datetime.now()
        if now >= dt:
            return
        time.sleep(min(15, max(1, (dt - now).total_seconds())))


def newest_status_file(status_dir: Path) -> Optional[Path]:
    if not status_dir.exists():
        return None
    files = list(status_dir.glob("*.txt"))
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def tail_poll_until_match(
    status_file: Path,
    poll: int,
    stop_rx: re.Pattern,
) -> str:
    """
    Follow status_file for new lines. Return the matching line when found.
    """
    # Open and seek to end so we only react to NEW updates.
    with open(status_file, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                ln = line.strip()
                if ln:
                    # echo to watcher terminal for transparency
                    print(f"[WATCH] {ln}")
                if stop_rx.search(line):
                    return ln

            time.sleep(max(1, poll))


def pkill_pattern(pattern: str, grace: int = 6) -> None:
    """
    Kill processes matching pattern (regex in pkill -f sense).
    First TERM, then KILL after grace seconds if still running.
    """
    # TERM
    subprocess.run(["pkill", "-TERM", "-f", pattern], check=False)
    t0 = time.time()

    while time.time() - t0 < grace:
        # any still alive?
        r = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return
        time.sleep(0.2)

    # KILL
    subprocess.run(["pkill", "-KILL", "-f", pattern], check=False)


# ---------------------------
# New helpers (post-kill flow)
# ---------------------------

def _wsl_path_to_windows(path_str: str) -> str:
    """
    Best-effort WSL -> Windows path conversion for /mnt/<drive>/...
    If it doesn't look like a WSL mount path, return as-is.
    """
    p = path_str.replace("\\", "/")
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if not m:
        return path_str
    drive = m.group(1).upper()
    rest = m.group(2).replace("/", "\\")
    return f"{drive}:\\{rest}"


def tail_poll_watchdog_until_stopped(
    watchdog_log: Path,
    poll_seconds: int,
    stable_seconds: int,
    stopped_rx: re.Pattern,
    nonstopped_rx: re.Pattern,
    max_wait_seconds: int,
) -> bool:
    """
    Follow watchdog log until we see a "stopped" marker, then validate it stays stopped
    (no non-stopped markers) for stable_seconds.

    Returns True if confirmed stopped, else False.
    """
    if not watchdog_log.exists():
        print(f"[WARN] Watchdog log not found: {watchdog_log}")
        return False

    print(f"[INFO] Polling AME watchdog log for STOPPED: {watchdog_log}")
    start = time.time()

    # Seek to end so we only consider NEW watchdog updates from now on.
    with open(watchdog_log, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)

        saw_stopped_at: Optional[float] = None

        while True:
            if max_wait_seconds > 0 and (time.time() - start) > max_wait_seconds:
                print(f"[WARN] Timed out waiting for STOPPED in watchdog log (>{max_wait_seconds}s).")
                return False

            line = f.readline()
            if not line:
                # ✅ if we're already in STOPPED-validation, allow timer to complete
                if saw_stopped_at is not None and (time.time() - saw_stopped_at) >= stable_seconds:
                    print(f"[INFO] STOPPED validated stable for {stable_seconds}s.")
                    return True

                time.sleep(max(1, poll_seconds))
                continue


            ln = line.strip()
            if ln:
                print(f"[AME] {ln}")

            if stopped_rx.search(line):
                if saw_stopped_at is None:
                    saw_stopped_at = time.time()
                    print(f"[INFO] Watchdog indicates STOPPED. Validating stable for {stable_seconds}s...")
                # IMPORTANT: do NOT continue here.
                # We must fall through so the stable_seconds timer check can run.

            if saw_stopped_at is not None and nonstopped_rx.search(line):
                # Any sign of activity resets validation.
                saw_stopped_at = None
                print("[INFO] Watchdog indicates NOT-stopped activity. Resetting STOPPED validation...")

            if saw_stopped_at is not None:
                if (time.time() - saw_stopped_at) >= stable_seconds:
                    print(f"[INFO] STOPPED validated stable for {stable_seconds}s.")
                    return True


def hard_kill_media_encoder(proc_name: str) -> None:
    """
    Hard kill AME on Windows from WSL using taskkill.
    proc_name examples:
      - Adobe Media Encoder.exe
      - Adobe Media Encoder
    """
    # taskkill expects the .exe name typically.
    # We'll try a few variants, but keep it quiet and best-effort.
    candidates = []
    pn = proc_name.strip().strip('"')
    if pn:
        candidates.append(pn)
    # Common AME process names
    for extra in ["Adobe Media Encoder.exe", "Adobe Media Encoder", "AdobeMediaEncoder.exe"]:
        if extra not in candidates:
            candidates.append(extra)

    for name in candidates:
        try:
            cmd = ["cmd.exe", "/C", "taskkill", "/F", "/T", "/IM", name]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if r.returncode == 0:
                print(f"[INFO] taskkill OK: {name}")
                return
        except Exception as e:
            print(f"[WARN] taskkill failed for {name}: {e}")

    print("[WARN] Could not confirm taskkill success for AME (may already be closed).")


def hard_kill_premiere(proc_name: str) -> None:
    """
    Hard kill Adobe Premiere Pro on Windows from WSL using taskkill.
    """
    candidates = []
    pn = (proc_name or "").strip().strip('"')
    if pn:
        candidates.append(pn)

    # Common Premiere process names (add more if your environment differs)
    for extra in [
        "Adobe Premiere Pro.exe",
        "Adobe Premiere Pro 2025.exe",
        "Adobe Premiere Pro (Beta).exe",
        "Adobe Premiere Pro Beta.exe",
    ]:
        if extra not in candidates:
            candidates.append(extra)

    for name in candidates:
        try:
            cmd = ["cmd.exe", "/C", "taskkill", "/F", "/T", "/IM", name]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if r.returncode == 0:
                print(f"[INFO] taskkill OK: {name}")
                return
        except Exception as e:
            print(f"[WARN] taskkill failed for {name}: {e}")

    print("[WARN] Could not confirm taskkill success for Premiere (may already be closed).")


def run_python_script(script_path: Path, extra_args: Optional[List[str]] = None) -> int:
    """
    Run a python script with the current interpreter. Returns exit code.
    """
    if not script_path.exists():
        print(f"[ERROR] Script not found: {script_path}")
        return 2
    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)
    print(f"[INFO] Running: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    return r.returncode


_LANG_TITLE_TO_CODE: Dict[str, str] = {
    # Add more as needed (safe defaults).
    "korean": "KR",
    "polish": "PL",
    "french": "FR",
    "spanish": "ES",
    "czech": "CS",
    "russian": "RU",
    "portuguese": "PT",
    "portuguese-br": "PT-BR",
    "croatian": "HR",
    "german": "DE",
    "italian": "IT",
    "arabic": "AR",
    "turkish": "TR",
    "hindi": "HI",
    "urdu": "UR",
}


def _norm_lang_token(s: str) -> str:
    s0 = (s or "").strip()
    if not s0:
        return ""
    # If already looks like a language code (KR, PL, PT-BR, etc.)
    if re.fullmatch(r"[A-Z]{2,3}(?:-[A-Z]{2})?", s0):
        return s0
    k = re.sub(r"\s+", " ", s0).strip().lower()
    k = k.replace("portuguese br", "portuguese-br").replace("pt br", "portuguese-br")
    return _LANG_TITLE_TO_CODE.get(k, s0.upper()[:3])


def parse_done_languages_from_status_text(text: str, window_start: datetime, default_video_name: Optional[str] = None) -> List[Tuple[str, str, datetime]]:
    """
    Parse status text into items:
      (video_name, lang_code, done_timestamp)

    Best-effort parser:
    - Tracks current "Video => ..."
    - Tracks current "Language => ..." / "Language           => ..."
    - Detects DONE/✅DONE lines, optionally with timestamps at line start or nearby.
    """
    out: List[Tuple[str, str, datetime]] = []

    current_video: Optional[str] = None
    current_lang: Optional[str] = None

    # Timestamp formats observed in your logs
    # Examples:
    #   [2026-02-16 18:30:45] ...
    #   2026-02-16 18:30:45 ...
    ts_rx = re.compile(r"(?:\[\s*)?(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\s*\])?")

    video_rx = re.compile(r"^\s*Video\s*=>\s*(.+?)\s*$", re.IGNORECASE)
    # e.g., "Language           => Korean"
    lang_any_rx = re.compile(r"^\s*Language\s+.*=>\s*(.+?)\s*$", re.IGNORECASE)

    # DONE signals
    done_rx = re.compile(r"\b(DONE|✅DONE)\b", re.IGNORECASE)
    # Fallback format example:
    # DONE FR French 2026-02-17 12:43:35
    done_simple_rx = re.compile(
        r"^\s*(?:✅\s*)?(?:DONE|✅DONE)\s*:?\s*([A-Z]{2,3}(?:-[A-Z]{2})?)\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*$",
        re.IGNORECASE
    )


    lines = text.splitlines()
    last_ts: Optional[datetime] = None

    for raw in lines:
        line = raw.strip("\n")

        m_ts = ts_rx.search(line)
        if m_ts:
            try:
                last_ts = datetime.strptime(m_ts.group(1) + " " + m_ts.group(2), "%Y-%m-%d %H:%M:%S")
            except Exception:
                last_ts = None

        m_v = video_rx.search(line)
        if m_v:
            current_video = m_v.group(1).strip()
            current_lang = None
            continue

        m_l = lang_any_rx.search(line)
        if m_l:
            current_lang = m_l.group(1).strip()
            continue

        m_simple = done_simple_rx.match(line.strip())
        if m_simple:
            lang_code = _norm_lang_token(m_simple.group(1))
            try:
                ts = datetime.strptime(m_simple.group(3) + " " + m_simple.group(4), "%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = datetime.now()

            if ts >= window_start:
                vid = current_video or default_video_name or ""
                if vid:
                    out.append((vid, lang_code, ts))
            continue


        if done_rx.search(line):
            if current_video and current_lang:
                ts = last_ts or datetime.now()
                if ts >= window_start:
                    out.append((current_video, _norm_lang_token(current_lang), ts))

    return out


def collect_recent_status_files(status_dir: Path, window_hours: int) -> List[Path]:
    now = datetime.now()
    cutoff = now - timedelta(hours=window_hours)
    files: List[Path] = []
    if not status_dir.exists():
        return files
    for p in status_dir.glob("*.txt"):
        try:
            mt = datetime.fromtimestamp(p.stat().st_mtime)
            if mt >= cutoff:
                files.append(p)
        except Exception:
            continue
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def find_best_prproj(project_root: Path) -> Optional[Path]:
    """
    Best-effort: find newest .prproj under project_root.
    """
    if not project_root.exists():
        return None
    candidates = list(project_root.rglob("*.prproj"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_prproj_for_language(project_dir: Path, video_name: str, lang_code: str) -> Optional[Path]:
    """
    Prefer the exact project file for the missing language:
      "{LANG} - {VIDEO}.prproj"
    Fallback to find_best_prproj if not found.
    """
    target = f"{lang_code} - {video_name}.prproj".lower()
    matches = []
    for p in project_dir.rglob("*.prproj"):
        if p.name.lower() == target:
            matches.append(p)

    if matches:
        matches.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return matches[0]

    return find_best_prproj(project_dir)


def rendered_exists(rendered_dir: Path, video_name: str, lang_code: str) -> bool:
    """
    Check if rendered output exists in rendered_dir for a given language.
    Best-effort heuristic:
      - any media file present whose name contains lang_code OR language title token.
    """
    if not rendered_dir.exists():
        return False

    lang_code_u = (lang_code or "").upper()
    lang_code_l = (lang_code or "").lower()

    media_exts = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".wav"}
    for p in rendered_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in media_exts:
            continue
        name = p.name
        if lang_code_u:
            if re.search(rf"(^|[^A-Z0-9]){re.escape(lang_code_u)}([^A-Z0-9]|$)", name.upper()):
                return True
    # Fallback: if there's only one file in RenderedVideos, treat as rendered.
    media_files = [p for p in rendered_dir.iterdir() if p.is_file() and p.suffix.lower() in media_exts]
    if len(media_files) == 1:
        return True
    return False


def try_run_queue_again(
    launcher_path: Path,
    prproj_path: Path,
    rendered_dir: Path,
    video_name: str,
    lang_code: str,
    send_jsx_wsl: Path,
) -> int:
    """
    Run _queue_again_launcher__.py with the ACTUAL expected CLI:
      --project-win <Z:\...prproj>
      --output-win  <Z:\...\RenderedVideos\<something>.mp4>   (optional but recommended)
      --send-jsx-wsl <WSL path to SendToAME.jsx>              (REQUIRED)
    """
    if not launcher_path.exists():
        print(f"[ERROR] Queue-again launcher not found: {launcher_path}")
        return 2

    if not send_jsx_wsl or not Path(send_jsx_wsl).exists():
        print(f"[ERROR] send_jsx_wsl not found (required): {send_jsx_wsl}")
        return 2

    # launcher requires WINDOWS paths for project/output
    project_win = _wsl_path_to_windows(str(prproj_path))
    output_dir_win = _wsl_path_to_windows(str(rendered_dir))
   

    # Best-effort output filename (keeps it deterministic)
    # If your pipeline uses a different naming convention, adjust ONLY this line.
    out_mp4_win = os.path.join(output_dir_win, f"{lang_code} - {video_name}.mp4")

    cmd = [
        sys.executable, str(launcher_path),
        "--project-win", project_win,
        "--output-win", out_mp4_win,
        "--send-jsx-wsl", str(Path(send_jsx_wsl)),
    ]

    print(f"[INFO] Queue-again: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    
    if r.returncode == 0:
        print("[INFO] Queue-again SUCCESS.")
    
        # ✅ NEW: run post-queue re-execution validation
        try:
            validation_script = Path(__file__).resolve().parent / "__killing_re_execution_validation__.py"
            if validation_script.exists():
                print(f"[INFO] Running re-execution validation: {validation_script}")
                queue_launcher = Path(__file__).resolve().parent / "_queue_again_launcher__.py"
                run_config_wsl = "/mnt/c/VideoRenderAgain/_queue_again_runner/run_config.json"
                
                rc2 = run_python_script(
                    validation_script,
                    extra_args=[
                        "--config", run_config_wsl,
                        "--rerun-script", str(queue_launcher),
                    ],
                )
                print(f"[INFO] __killing_re_execution_validation__.py exit code: {rc2}")
            else:
                print(f"[WARN] Validation script not found: {validation_script}")
        except Exception as e:
            # Hard rule: never break existing flow
            print(f"[WARN] Validation script execution failed (continuing): {e}")
    
        return 0

    print(f"[WARN] Queue-again returned {r.returncode}")
    return r.returncode



def post_kill_recovery_flow(
    watchdog_log: Path,
    watchdog_poll: int,
    watchdog_stable: int,
    watchdog_max_wait: int,
    media_encoder_proc: str,
    premiere_proc: str,
    media_encoder_agent: Path,
    workers_root: Path,
    worker_name: str,
    recent_hours: int,
    projects_root: Path,
    queue_again_launcher: Path,
    send_jsx_wsl: Path,   # <-- ADD THIS
    dry_run: bool,
) -> None:
    """
    Executes requested post-kill logic.
    Does not raise; prints errors and continues (so original behavior remains robust).
    """
    try:
        stopped_rx = re.compile(r"\bstopped\b", re.IGNORECASE)
        # anything that suggests it's NOT stopped (best-effort)
        nonstopped_rx = re.compile(r"\b(encoding|rendering|running|active|processing|queueing|started|idle|paused|waiting|ready)\b", re.IGNORECASE)

        confirmed = tail_poll_watchdog_until_stopped(
            watchdog_log=watchdog_log,
            poll_seconds=watchdog_poll,
            stable_seconds=watchdog_stable,
            stopped_rx=stopped_rx,
            nonstopped_rx=nonstopped_rx,
            max_wait_seconds=watchdog_max_wait,
        )

        if confirmed:
            print("[INFO] STOPPED confirmed. Hard-killing Premiere + AME now...")
        else:
            print("[WARN] STOPPED not confirmed; proceeding with hard-kill + recovery anyway (per reliability).")

        # 1) Hard-kill Premiere first (prevents re-opening AME or re-queue side effects)
        print("[INFO] Hard-killing Adobe Premiere Pro now...")
        if not dry_run:
            try:
                hard_kill_premiere(premiere_proc)
            except Exception as e:
                print(f"[WARN] Premiere taskkill failed (continuing): {e}")
        else:
            print("[DRY-RUN] Would hard-kill Adobe Premiere Pro.")

        # 2) Hard-kill AME
        print("[INFO] Hard-killing Adobe Media Encoder now...")
        if not dry_run:
            try:
                hard_kill_media_encoder(media_encoder_proc)
            except Exception as e:
                print(f"[WARN] AME taskkill failed (continuing): {e}")
        else:
            print("[DRY-RUN] Would hard-kill Adobe Media Encoder.")

        # Small settle delay (Windows process teardown)
        time.sleep(3)

        # 3) Run Media Encoder agent script
        print("[INFO] Running Media Encoder agent script...")
        if not dry_run:
            rc = run_python_script(media_encoder_agent)
            print(f"[INFO] _media_encoder_agent__.py exit code: {rc}")
        else:
            print("[DRY-RUN] Would run _media_encoder_agent__.py")


        # Scan recent status files for this worker
        status_dir = workers_root / worker_name / "status"
        recent_files = collect_recent_status_files(status_dir, recent_hours)
        print(f"[INFO] Recent status files (last {recent_hours}h) in {status_dir}: {len(recent_files)}")

        window_start = datetime.now() - timedelta(hours=recent_hours)

        done_items: List[Tuple[str, str, datetime]] = []
        for sf in recent_files:
            try:
                txt = sf.read_text(encoding="utf-8", errors="ignore")
                done_items.extend(parse_done_languages_from_status_text(txt, window_start, default_video_name=sf.stem))
            except Exception as e:
                print(f"[WARN] Could not parse status file {sf}: {e}")

        if not done_items:
            print("[INFO] No DONE languages found in the last window. Nothing to verify/queue.")
            return

        # De-duplicate by (video, lang) keep most recent timestamp
        latest: Dict[Tuple[str, str], datetime] = {}
        for vid, lang, ts in done_items:
            k = (vid, lang)
            if k not in latest or ts > latest[k]:
                latest[k] = ts

        print(f"[INFO] Unique DONE items to verify: {len(latest)}")

        for (video_name, lang_code), ts in sorted(latest.items(), key=lambda kv: kv[1]):
            project_dir = projects_root / video_name
            rendered_dir = project_dir / "RenderedVideos"

            ok = rendered_exists(rendered_dir, video_name, lang_code)
            if ok:
                print(f"[OK] Render exists: video='{video_name}' lang='{lang_code}' at {rendered_dir}")
                continue

            print(f"[MISSING] Render NOT found: video='{video_name}' lang='{lang_code}'. Will queue again.")
            prproj = find_prproj_for_language(project_dir, video_name, lang_code)
            if not prproj:
                print(f"[ERROR] Could not locate .prproj under: {project_dir} (skipping queue-again).")
                continue

            if dry_run:
                print(f"[DRY-RUN] Would run queue-again for prproj={prproj}, rendered_dir={rendered_dir}, lang={lang_code}")
                continue

            try_run_queue_again(
                launcher_path=queue_again_launcher,
                prproj_path=prproj,
                rendered_dir=rendered_dir,
                video_name=video_name,
                lang_code=lang_code,
                send_jsx_wsl=send_jsx_wsl,   # <-- REQUIRED FIX
            )

    except Exception as e:
        print(f"[ERROR] post_kill_recovery_flow failed: {e}")


# ---------------------------
# Main (existing + extended)
# ---------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START_TIME, help="Start time HH:MM (24h). Default 07:30")
    ap.add_argument("--poll", type=int, default=5, help="Poll interval seconds (default 5)")
    ap.add_argument("--grace", type=int, default=120, help="Grace seconds if start time just passed (default 120)")
    ap.add_argument("--worker", default="", help="Worker PC folder name (e.g. Room-2-Sufiyan). If empty, uses hostname.")
    ap.add_argument("--status-dir", default="", help="Override full status dir path. If set, --worker ignored.")
    ap.add_argument("--stop-regex", default=DEFAULT_STOP_LINE_REGEX, help="Regex that means 'language/job complete'.")
    ap.add_argument("--kill-pattern", default=DEFAULT_KILL_PATTERN, help="pkill -f pattern to kill VV-3 pipeline.")

    # New args (optional; do not affect existing behavior unless you want the new post-kill features)
    ap.add_argument("--watchdog-log", default=DEFAULT_WATCHDOG_LOG, help="WSL path to AME watchdog log (default /mnt/c/temp/AME_Watchdog/watchdog_per_item.log)")
    ap.add_argument("--watchdog-poll", type=int, default=2, help="Poll interval (seconds) for watchdog log tailing (default 2)")
    ap.add_argument("--watchdog-stable", type=int, default=60, help="Seconds STOPPED must remain stable before killing AME (default 60)")
    ap.add_argument("--watchdog-max-wait", type=int, default=0, help="Max seconds to wait for STOPPED (0=wait forever).")
    ap.add_argument("--media-encoder-proc", default="Adobe Media Encoder.exe", help="Windows process name for AME (default 'Adobe Media Encoder.exe')")
    ap.add_argument("--media-encoder-agent", default="", help="Path to _media_encoder_agent__.py (default: alongside this script)")
    ap.add_argument("--premiere-proc", default="Adobe Premiere Pro.exe", help="Windows process name for Premiere (default 'Adobe Premiere Pro.exe')")
    ap.add_argument("--workers-root", default=DEFAULT_WORKERS_ROOT, help="Workers root folder (WSL path). Default /mnt/z/Automated Dubbings/JOBS/workers")
    ap.add_argument("--projects-root", default=DEFAULT_PROJECTS_ROOT, help="Projects root folder (WSL path). Default /mnt/z/Automated Dubbings/Projects")
    ap.add_argument("--recent-hours", type=int, default=15, help="Lookback window (hours) for DONE languages (default 15)")
    ap.add_argument("--queue-again-launcher", default="", help="Path to _queue_again_launcher__.py (default: alongside this script)")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without killing AME or queueing again.")

    ap.add_argument(
        "--send-jsx-wsl",
        default=str(Path(__file__).resolve().parent / "Javascript" / "_SendToAME.jsx"),
        help="WSL path to SendToAME.jsx (REQUIRED for queue-again launcher).",
    )
    
    args = ap.parse_args()

    hh, mm = parse_hhmm(args.start)

    # Worker name (preserve original behavior)
    worker = args.worker.strip() or os.uname().nodename

    # Status dir (preserve original behavior)
    if args.status_dir.strip():
        status_dir = Path(args.status_dir.strip()).expanduser()
    else:
        status_dir = Path(f"/mnt/z/Automated Dubbings/JOBS/workers/{worker}/status")

    stop_rx = re.compile(args.stop_regex, re.IGNORECASE)
    start_dt = next_start_time(hh, mm, args.grace)

    print(f"[INFO] Now: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[INFO] Will START WATCHING at: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} (start={args.start}, grace={args.grace}s)")
    print(f"[INFO] Status dir: {status_dir}")
    print(f"[INFO] Stop regex: {args.stop_regex}")
    print(f"[INFO] Kill pattern: {args.kill_pattern}")

    sleep_until(start_dt)

    # pick newest status file at start time
    sf = newest_status_file(status_dir)
    if not sf:
        print(f"[ERROR] No status .txt files found in: {status_dir}", file=sys.stderr)
        return 2

    print(f"[INFO] Watching status file: {sf}")
    matched = tail_poll_until_match(sf, args.poll, stop_rx)

    print("")
    print(f"[INFO] COMPLETE marker detected -> {matched}")
    print("[INFO] Killing VV-3 pipeline process now...")
    pkill_pattern(args.kill_pattern, grace=6)

    # -------- New: post-kill recovery flow --------
    script_dir = Path(__file__).resolve().parent
    media_encoder_agent = Path(args.media_encoder_agent).expanduser() if args.media_encoder_agent.strip() else (script_dir / "_media_encoder_agent__.py")
    queue_again_launcher = Path(args.queue_again_launcher).expanduser() if args.queue_again_launcher.strip() else (script_dir / "_queue_again_launcher__.py")

    post_kill_recovery_flow(
        watchdog_log=Path(args.watchdog_log).expanduser(),
        watchdog_poll=args.watchdog_poll,
        watchdog_stable=args.watchdog_stable,
        watchdog_max_wait=args.watchdog_max_wait,
        media_encoder_proc=args.media_encoder_proc,
        premiere_proc=args.premiere_proc,   # <-- ADD THIS
        media_encoder_agent=media_encoder_agent,
        workers_root=Path(args.workers_root).expanduser(),
        worker_name=worker,
        recent_hours=args.recent_hours,
        projects_root=Path(args.projects_root).expanduser(),
        queue_again_launcher=queue_again_launcher,
        dry_run=args.dry_run,
        send_jsx_wsl=Path(args.send_jsx_wsl),
    )
    

    print("[INFO] Done. Exiting watcher (VV-3 should stop; tab closes if Close-on-exit=Always).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
