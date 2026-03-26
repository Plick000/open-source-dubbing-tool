#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


# =========================
# SETTINGS
# =========================

TRIES = 180
SLEEP_SECONDS = 5

# --- Stabilization settings (post-copy) ---
# We consider the copied file "stable" if size+mtime_ns stay unchanged
# for N consecutive checks, within timeout seconds.
STABLE_CONSECUTIVE_CHECKS = 3
STABLE_CHECK_INTERVAL_SEC = 1.0
STABLE_TIMEOUT_SEC = 30.0

# Validate that the copied file is well-formed XML before we report success.
VALIDATE_XML_PARSE = True


# Define base path for Automated Dubbings Projects on WSL
BASE_DUBBING_FLASK = "/mnt/z/Automated Dubbings/Projects/"  # WSL path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_ROOT / "inputs" / "config" / "config.json"
DEST_XML = PROJECT_ROOT / "inputs" / "XML" / "English.xml"


# =========================

time.sleep(1)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Could not find config.json at: {CONFIG_FILE}")


def clean_video_name(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^\s*video\s*\d+\s*-\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^\s*video\s*\d+\s*[:\-]\s*", "", s, flags=re.IGNORECASE).strip()
    if not s:
        raise ValueError(f"After cleaning, video name became empty. Raw was: {raw!r}")
    return s


def build_source_dir(video_clean: str) -> Path:
    base = Path(BASE_DUBBING_FLASK)
    return base / video_clean / "English" / "XML"


def pick_xml_file(src_dir: Path) -> Path | None:
    # Preferred exact filename
    exact = src_dir / "english.xml"
    if exact.exists() and exact.is_file():
        return exact

    # Case-variation fallback (Linux is case-sensitive)
    # e.g., English.xml / ENGLISH.XML etc.
    if src_dir.exists() and src_dir.is_dir():
        for p in src_dir.glob("*.xml"):
            if p.name.lower() == "english.xml":
                return p

        # Last fallback: any .xml in folder
        any_xml = sorted(src_dir.glob("*.xml"))
        if any_xml:
            return any_xml[0]

    return None


def _file_sig(p: Path) -> tuple[int, int]:
    """
    Return a lightweight signature for stability checks:
    (size_bytes, mtime_ns)
    """
    st = p.stat()
    return int(st.st_size), int(st.st_mtime_ns)


def wait_until_stable(p: Path,
                      consecutive: int = STABLE_CONSECUTIVE_CHECKS,
                      interval: float = STABLE_CHECK_INTERVAL_SEC,
                      timeout: float = STABLE_TIMEOUT_SEC) -> tuple[bool, str]:
    """
    Wait until file's (size, mtime_ns) remains unchanged for `consecutive` checks.
    """
    if not p.exists() or not p.is_file():
        return False, "missing"

    deadline = time.time() + float(timeout)

    try:
        last = _file_sig(p)
    except Exception as e:
        return False, f"stat_failed:{e}"

    stable_hits = 0
    while time.time() <= deadline:
        time.sleep(float(interval))
        try:
            cur = _file_sig(p)
        except Exception as e:
            return False, f"stat_failed:{e}"

        if cur == last:
            stable_hits += 1
            if stable_hits >= int(consecutive):
                return True, "stable"
        else:
            stable_hits = 0
            last = cur

    return False, "timeout"


def xml_is_well_formed(p: Path) -> tuple[bool, str]:
    """
    Validate XML parsing. This catches truncated / malformed XML early.
    """
    try:
        ET.parse(p)
        return True, "xml_ok"
    except ET.ParseError as e:
        return False, f"parse_error:{e}"
    except Exception as e:
        return False, f"xml_check_failed:{e}"


def validate_copied_xml(dest: Path) -> tuple[bool, str]:
    """
    1) Wait for stability (dest not changing)
    2) Optionally validate XML is well-formed
    """
    ok, reason = wait_until_stable(dest)
    if not ok:
        return False, f"not_stable:{reason}"

    if VALIDATE_XML_PARSE:
        ok2, reason2 = xml_is_well_formed(dest)
        if not ok2:
            return False, reason2

    return True, "ok"


def main() -> int:
    cfg = load_config()
    raw_name = cfg.get("video_name", "")
    if not raw_name:
        raise KeyError("config.json missing 'video_name'")

    video_clean = clean_video_name(raw_name)

    src_dir = build_source_dir(video_clean)
    DEST_XML.parent.mkdir(parents=True, exist_ok=True)

    print(f"[CHECK  ] source dir:         {src_dir}")
    print(f"[COPY   ] destination path:   {DEST_XML}")
    print("")

    for attempt in range(1, TRIES + 1):
        picked = pick_xml_file(src_dir)
        if picked:
            # Keep your original wait before copying (do not break current condition)
            time.sleep(5)

            shutil.copy2(picked, DEST_XML)

            ok, reason = validate_copied_xml(DEST_XML)
            if ok:
                print(
                    f"✅ Found on attempt {attempt}. Copied & validated:\n"
                    f"   FROM: {picked}\n"
                    f"   TO:   {DEST_XML}"
                )
                return 0

            # If not stable/valid, do NOT return success. Keep retrying.
            print(
                f"⚠️ Copied on attempt {attempt} but NOT stable/valid yet ({reason}). "
                f"Will retry..."
            )

            # Optional: remove bad/incomplete dest to prevent downstream reading it.
            try:
                if DEST_XML.exists():
                    DEST_XML.unlink()
            except Exception:
                pass

            time.sleep(SLEEP_SECONDS)
            continue

        print(f"⏳ Not found (attempt {attempt}/{TRIES}). Sleeping {SLEEP_SECONDS}s...")
        time.sleep(SLEEP_SECONDS)

    print(f"❌ Not found after {TRIES} attempts.")
    print(f"Last checked directory:\n{src_dir}")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️ Stopped by user.")
        sys.exit(130)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        sys.exit(1)
