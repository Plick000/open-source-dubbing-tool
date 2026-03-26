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
SEGMENTS_PATH = PROJECT_ROOT / "output" / "JSON" / "A18__computed_lowerthirds__.json"

BASE_FAST_CONTENT_WIN = "Z:/Automated Dubbings/Projects"
POLL_INTERVAL_SEC = 20
MIN_FILE_BYTES = 1024

FFPROBE_TIMEOUT_SEC = 8
# -----------------------------
# RETRY / TIME LIMITS
# -----------------------------
# Poll for up to 1 hour. If validation does not succeed within this window,
# this script will run __batch_lowerthirds_rendering__.py and then start a new
# 5 Minutes validation window.
MAX_VALIDATION_WINDOW_SEC = 60 * 5  # 5 Minutes

# Maximum number of times we will re-run __batch_lowerthirds_rendering__.py after a
# 1-hour validation window fails.
MAX_BATCH_RENDER_RETRIES = 3

# Script that (re)renders the lowerthird clips.
# Expected to live alongside this file, but we also try safe fallbacks.
BATCH_lowerthirdS_SCRIPT_NAME = "__batch_lowerthirds_rendering__.py"


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
    p = p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m and os.name != "nt":
        drive = m.group(1).lower()
        rest = m.group(2)
        return Path("/mnt") / drive / rest
    return Path(p)


def expected_lowerthird_filenames(segments) -> list[str]:
    if not isinstance(segments, list):
        raise ValueError("Segments JSON must be a list of objects.")

    # NOTE:
    # - We treat id<=0 as "no valid id" (id=0 often means "no lowerthirds").
    # - If we end up with no valid ids, we return an empty list so the caller
    #   can treat validation as a successful no-op (do NOT raise).
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
    return [f"lowerthird_{i:03d}.mov" for i in ids]


def is_rendered_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_FILE_BYTES
    except FileNotFoundError:
        return False

def safe_delete_file(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except TypeError:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    except Exception:
        pass


def is_media_corrupted_ffprobe(p: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False

    try:
        cmd = [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(p),
        ]
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FFPROBE_TIMEOUT_SEC,
        )
        if r.returncode != 0:
            return True

        out = (r.stdout or "").strip()
        dur = float(out)
        return dur <= 0.05
    except Exception:
        return True


def resolve_batch_lowerthirds_script() -> Path:
    """Find __batch_lowerthirds_rendering__.py without breaking existing path logic."""
    candidates = [
        Path(__file__).resolve().with_name(BATCH_lowerthirdS_SCRIPT_NAME),
    ]

    for c in candidates:
        if c.exists() and c.is_file():
            return c

    raise FileNotFoundError(
        "Could not locate '__batch_lowerthirds_rendering__.py'. Tried:\n- "
        + "\n- ".join(str(x) for x in candidates)
    )


def run_batch_lowerthirds_rendering() -> int:
    """Run __batch_lowerthirds_rendering__.py (full execution) and return its exit code."""
    script = resolve_batch_lowerthirds_script()
    cmd = [sys.executable, str(script)]
    print(f"\n[RENDER] Running batch lowerthirds renderer: {script}")
    print(f"[RENDER] Command: {' '.join(cmd)}")
    return subprocess.call(cmd)


def validate_lowerthirds_within_window(
    expected_files: list[str],
    target_folder: Path,
    window_sec: int,
) -> bool:
    """Poll until all expected files exist and become size-stable, or until the window expires."""

    last_known_sizes: dict[str, int] = {}
    t0 = time.time()
    deadline = t0 + window_sec
    prev_poll_sizes_for_corruption: dict[str, int] = {}  # <-- ADD

    while True:
        missing: list[str] = []
        current_sizes: dict[str, int] = {}

        # 1) Check existence and collect current sizes
        for fname in expected_files:
            fpath = target_folder / fname
            if is_rendered_file(fpath):
                sz = fpath.stat().st_size

                # Corruption check ONLY when size is stable for 1 poll (avoids deleting in-progress renders)
                if prev_poll_sizes_for_corruption.get(fname) == sz:
                    if is_media_corrupted_ffprobe(fpath):
                        print(f"\n🧨 CORRUPTED FILE DETECTED: {fpath}  -> deleting and re-queuing")
                        safe_delete_file(fpath)
                        missing.append(fname)
                        continue

                current_sizes[fname] = sz
            else:
                missing.append(fname)


        # 2) Stability logic
        if not missing:
            # If sizes have stopped changing, we treat as success
            if last_known_sizes and current_sizes == last_known_sizes:
                elapsed = time.time() - t0
                mins = int(elapsed // 60)
                secs = round(elapsed % 60, 2)
                print("\n✅ All clips found and stable! (Successfully verified)")
                print(f"Total time taken: {mins} minutes {secs} seconds.")
                return True

            # Either first time finding all files, or they are still growing
            if not last_known_sizes:
                print("\n📂 All files found! Checking stability (waiting for sizes to stop changing)...")
            else:
                print("\n⏳ Files are still being written (sizes changing). Polling again...")

            last_known_sizes = current_sizes
        else:
            # Reset stability tracking if files go missing or haven't appeared yet
            last_known_sizes = {}
            elapsed = time.time() - t0
            remaining = max(0, int(deadline - time.time()))
            print(
                f"\n⏳ Missing {len(missing)}/{len(expected_files)} lowerthirds. "
                f"Polling in {POLL_INTERVAL_SEC}s... (elapsed {int(elapsed)}s, remaining {remaining}s)"
            )

        # 3) Hard stop for this validation window
        if time.time() >= deadline:
            elapsed = time.time() - t0
            mins = int(elapsed // 60)
            secs = round(elapsed % 60, 2)
            print(f"\n⏱️ Validation window expired after {mins} minutes {secs} seconds.")
            return False

        prev_poll_sizes_for_corruption = dict(current_sizes)  # <-
        time.sleep(POLL_INTERVAL_SEC)


# -----------------------------
# Main
# -----------------------------

def main():
    cfg = load_json(CONFIG_PATH)
    raw_video_name = cfg.get("video_name", "")
    raw_language = cfg.get("language", "")

    if not raw_video_name or not raw_language:
        raise ValueError("config.json must contain 'video_name' and 'language'.")

    video_name = clean_video_name(raw_video_name)
    lang_cap = cap_language(raw_language)

    segments = load_json(SEGMENTS_PATH)
    expected_files = expected_lowerthird_filenames(segments)

    # If no valid ids exist (or ids are 0), there are no lowerthird clips to verify.
    if not expected_files:
        print("\n✅ No valid lowerthird 'id' entries found (or id<=0). Skipping lowerthirds validation.")
        return 0


    target_folder_win = f"{BASE_FAST_CONTENT_WIN}/{video_name}/{lang_cap}/LowerThirds/VideoClips"
    target_folder = windows_drive_to_wsl_path(target_folder_win)

    print(f"[CHECK ] Target: {target_folder}")
    print(f"[CHECK ] Expected count: {len(expected_files)}")

    cycle = 1
    render_runs = 0

    while True:
        print(f"\n[WINDOW] Validation cycle #{cycle} (max window: {MAX_VALIDATION_WINDOW_SEC}s)")
        ok = validate_lowerthirds_within_window(expected_files, target_folder, MAX_VALIDATION_WINDOW_SEC)
        if ok:
            return 0

        # Validation window expired / not successful.
        # Run __batch_lowerthirds_rendering__.py again (up to MAX_BATCH_RENDER_RETRIES).
        if render_runs >= MAX_BATCH_RENDER_RETRIES:
            print(
                "\n❌ Validation failed after maximum re-render attempts. "
                f"Reached MAX_BATCH_RENDER_RETRIES={MAX_BATCH_RENDER_RETRIES}."
            )
            return 2

        render_runs += 1
        print(
            "\n[WINDOW] lowerthirds not fully verified within 5 Minutes. "
            f"Triggering batch re-render (attempt {render_runs}/{MAX_BATCH_RENDER_RETRIES})..."
        )

        rc = run_batch_lowerthirds_rendering()
        if rc != 0:
            print(
                f"[RENDER] __batch_lowerthirds_rendering__.py exited with code {rc}. "
                "Starting a new validation window anyway..."
            )
        else:
            print("[RENDER] Batch lowerthirds rendering completed (exit code 0). Starting a new validation window...")

        cycle += 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)
