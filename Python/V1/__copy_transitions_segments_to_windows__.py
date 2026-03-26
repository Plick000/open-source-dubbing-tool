#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import shutil
import sys
from pathlib import Path



# ----------------------------
# USER PATHS (as you specified)
# ----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"
SRC_JSON = PROJECT_ROOT / "output" / "JSON" / "A22__transitions_segments__.json"


# CONFIG_PATH = Path("inputs/config/config.json")

# SRC_JSON = Path("output") / "JSON" / "A22__transitions_segments__.json"

DEST_FILENAME = "A22__transitions_segments__.json"

BASE_WIN = Path("Z:/Automated Dubbings/Projects")        # Windows style
BASE_WSL = Path("/mnt/z/Automated Dubbings/Projects")    # WSL style (optional auto-detect)


def clean_video_name(raw: str) -> str:
    """
    Removes prefixes like:
      "Video 27 - " or "video 2-"
    Keeps the rest exactly.
    """
    s = str(raw or "").strip()
    # Remove "Video <number> - " (case-insensitive), allow flexible spaces/dash
    s = re.sub(r"^\s*video\s*\d+\s*[-–—]\s*", "", s, flags=re.IGNORECASE)
    return s.strip()

def safe_copy(src: Path, dst: Path) -> None:
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        shutil.copyfile(src, dst)
        print("[WARN] Metadata copy not permitted on destination; copied file contents only.")


def cap_language(raw: str) -> str:
    """
    Convert lowercase language (e.g., 'polish') into a folder-friendly capitalized form.
    Uses title-case for multiword/hyphen/parentheses languages.
    """
    s = str(raw or "").strip()
    if not s:
        return s
    return s.lower().title()


def resolve_base_folder() -> Path:
    """
    Uses E:/Fast Content if it exists; otherwise uses /mnt/e/Fast Content if running on WSL.
    If neither exists, defaults to E:/Fast Content (and will attempt to create directories under it).
    """
    if BASE_WIN.exists():
        return BASE_WIN
    if BASE_WSL.exists():
        return BASE_WSL
    return BASE_WIN


def read_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    cfg = read_config(CONFIG_PATH)

    video_name_raw = cfg.get("video_name", "")
    language_raw = cfg.get("language", "")

    video_name_clean = clean_video_name(video_name_raw)
    language_cap = cap_language(language_raw)

    if not video_name_clean:
        raise ValueError("config.json is missing a valid 'video_name'.")
    if not language_cap:
        raise ValueError("config.json is missing a valid 'language'.")

    if not SRC_JSON.exists():
        raise FileNotFoundError(f"Source JSON not found: {SRC_JSON.resolve()}")

    base = resolve_base_folder()

    # Drop path you requested:
    # E:/Fast Content/{clean video name}/{languageCap}/output/JSON/A22__transitions_segments__.json
    dest_dir = base / video_name_clean / language_cap / "output" / "JSON"
    dest_path = dest_dir / DEST_FILENAME

    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_copy(SRC_JSON, dest_path)

    print("[OK] Config:", CONFIG_PATH.resolve())
    print("[OK] video_name_raw:  ", video_name_raw)
    print("[OK] video_name_clean:", video_name_clean)
    print("[OK] language_raw:    ", language_raw)
    print("[OK] language_cap:    ", language_cap)
    print("[OK] Source JSON:     ", SRC_JSON.resolve())
    print("[OK] Dest JSON:       ", dest_path)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise
