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

SRC_XML_PRIMARY = PROJECT_ROOT / "output" / "XML" / "V9__dubbing__.xml"
SRC_XML_FALLBACK = PROJECT_ROOT / "output" / "XML" / "V9__dubbing__.xml"

# CONFIG_PATH = Path("inputs/config/config.json")

# SRC_XML_PRIMARY = Path("output") / "XML" / "V9__dubbing__.xml"   # pickup (your exact path)
# SRC_XML_FALLBACK = Path("output") / "XML" / "V9__dubbing__.xml"   # fallback if primary not found

DEST_FILENAME = "V9__dubbing__.xml"

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
    # Title-case is generally safest for folder naming ("Chinese (Traditional)", "Portuguese-Br")
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


def pick_source_xml() -> Path:
    if SRC_XML_PRIMARY.exists():
        return SRC_XML_PRIMARY
    if SRC_XML_FALLBACK.exists():
        return SRC_XML_FALLBACK
    raise FileNotFoundError(
        "Source XML not found. Checked:\n"
        f" - {SRC_XML_PRIMARY.resolve()}\n"
        f" - {SRC_XML_FALLBACK.resolve()}"
    )


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

    base = resolve_base_folder()

    dest_dir = base / video_name_clean / language_cap / "XML"
    dest_path = dest_dir / DEST_FILENAME

    src_path = pick_source_xml()

    # Ensure destination folder exists
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy file (preserve metadata)
    safe_copy(src_path, dest_path)

    print("[OK] Config:", CONFIG_PATH.resolve())
    print("[OK] video_name_raw:  ", video_name_raw)
    print("[OK] video_name_clean:", video_name_clean)
    print("[OK] language_raw:    ", language_raw)
    print("[OK] language_cap:    ", language_cap)
    print("[OK] Source XML:      ", src_path.resolve())
    print("[OK] Dest XML:        ", dest_path)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise
