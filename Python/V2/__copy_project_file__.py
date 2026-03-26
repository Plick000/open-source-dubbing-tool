#!/usr/bin/env python3
import json
import re
import shutil
from pathlib import Path

# ----------------------------
# CONFIG
# ----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_CONFIG = PROJECT_ROOT / "inputs" / "config" / "config.json"
FALLBACK_CONFIG = PROJECT_ROOT / "config.json"

# Unified Network location - Using your verified /mnt/z mount
NETWORK_BASE = Path("/mnt/z/Automated Dubbings/Projects")

# ✅ NEW: Central language metadata (Z:\Automated Dubbings\admin\configs\metadata\__languages.json)
LANG_METADATA_PATH = Path("/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json")

# Fallback mapping (kept to avoid breaking anything if metadata file is missing)
LANG_PREFIX = {
  "korean": "KR",
  "polish": "PL",
  "french": "FR",
  "spanish": "ES",
  "czech": "CZ",
  "russian": "RU",
  "portuguese": "BR",
  "croatian": "HR",
  "greek": "EL",
  "ukrainian": "UK",
  "turkish": "TR",
  "japanese": "JA",
  "chinese": "ZH",
  "vietnamese": "VI",
  "filipino": "TL",
  "german": "DE",
  "italian": "IT",
  "arabic": "AR",
  "hebrew": "HE",
  "finnish": "FI",
  "thai": "TH",
  "hindi": "HI",
  "indonesian": "ID",
  "persian": "FA",
  "malay": "MS",
  "romanian": "RO",
  "hungarian": "HU",
  "serbian": "SR",
  "bulgarian": "BG",
  "albanian": "SQ",
  "amharic": "AM",
  "lithuanian": "LT",
  "latvian": "LV",
  "estonian": "ET",
  "kazakh": "KK",
  "uzbek": "UZ",
  "bangla": "BN",
  "telugu": "TE",
  "marathi": "MR",
  "gujarati": "GU",
  "hausa": "HA",
  "urdu": "UR",
  "dutch": "NL",
  "swedish": "SV",
  "danish": "DA",
  "slovak": "SK",
  "bhojpuri": "BH",
  "norwegian": "NO"
}

# ----------------------------
# HELPERS
# ----------------------------
def load_config() -> dict:
    p = PRIMARY_CONFIG if PRIMARY_CONFIG.exists() else FALLBACK_CONFIG
    if not p.exists():
        raise FileNotFoundError(f"Could not find config.json at {PRIMARY_CONFIG}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def clean_video_name(video_name: str) -> str:
    s = (video_name or "").strip()
    s = re.sub(r"^\s*Video\s*\d+\s*[-–—]\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*Video\s*\d+\s*", "", s, flags=re.IGNORECASE)
    return s.strip()

def capitalize_language_folder(lang: str) -> str:
    s = (lang or "").strip().lower()
    parts = re.split(r"([\-_\s]+)", s)
    out = []
    for part in parts:
        if re.fullmatch(r"[\-_\s]+", part or ""):
            out.append(part)
        else:
            out.append(part[:1].upper() + part[1:] if part else "")
    return "".join(out).strip()

def _normalize_lang_key(lang: str) -> str:
    return (lang or "").strip().lower()

def _normalize_brand_key(brand_value) -> str | None:
    """
    Accepts many possible brand formats:
      - 17
      - "17"
      - "brand_17"
      - "Brand 17"
      - "brand-17"
    Returns "brand_17" or None if not parseable.
    """
    if brand_value is None:
        return None
    s = str(brand_value).strip().lower()
    if not s:
        return None
    # Extract the first integer found
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    return f"brand_{int(m.group(1))}"

def load_language_metadata() -> list | dict | None:
    """
    Loads the __languages.json file which appears to be a JSON array
    containing one object (based on your attached file).
    """
    if not LANG_METADATA_PATH.exists():
        return None
    try:
        with LANG_METADATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def get_lang_prefix_from_metadata(lang: str, cfg: dict) -> str | None:
    """
    Reads langcode from Z:/Automated Dubbings/admin/configs/metadata/__languages.json.

    Strategy:
    1) If brand is present in config.json -> use that brand block first.
    2) Else (or if not found) -> scan all brands for that language and use first match.
    """
    meta = load_language_metadata()
    if not meta:
        return None

    # __languages.json in your upload is a list with one dict; support both list/dict just in case
    meta_obj = None
    if isinstance(meta, list) and meta:
        meta_obj = meta[0]
    elif isinstance(meta, dict):
        meta_obj = meta
    else:
        return None

    lang_key = _normalize_lang_key(lang)

    # Try brand-first lookup
    brand_key = _normalize_brand_key(
        cfg.get("brand") or cfg.get("Brand") or cfg.get("brand_id") or cfg.get("brand_number")
    )

    if brand_key and isinstance(meta_obj.get(brand_key), dict):
        langs = meta_obj.get(brand_key, {}).get("languages", {})
        if isinstance(langs, dict):
            entry = langs.get(lang_key)
            if isinstance(entry, dict):
                code = (entry.get("langcode") or "").strip()
                if code:
                    return code.upper()

    # Fallback: scan all brands for the language
    for bkey, bval in (meta_obj or {}).items():
        if not isinstance(bval, dict):
            continue
        langs = bval.get("languages", {})
        if not isinstance(langs, dict):
            continue
        entry = langs.get(lang_key)
        if isinstance(entry, dict):
            code = (entry.get("langcode") or "").strip()
            if code:
                return code.upper()

    return None

def get_lang_prefix(lang: str, cfg: dict | None = None) -> str:
    """
    ✅ UPDATED:
    - Prefer __languages.json (langcode)
    - Fallback to hardcoded LANG_PREFIX (existing behavior)
    - Final fallback: first 2 letters
    """
    s = _normalize_lang_key(lang)

    # 1) Metadata-based (new desired behavior)
    if cfg is not None:
        meta_code = get_lang_prefix_from_metadata(s, cfg)
        if meta_code:
            return meta_code

    # 2) Existing hardcoded mapping (kept so nothing breaks)
    if s in LANG_PREFIX:
        return LANG_PREFIX[s]

    # 3) Existing generic fallback
    letters = re.sub(r"[^a-z]", "", s)
    return (letters[:2] or "XX").upper()

def find_pickup_prproj(pickup_dir: Path, clean_name: str) -> Path:
    """Finds the .prproj file inside the folder."""
    exact = pickup_dir / f"{clean_name}.prproj"
    if exact.exists():
        return exact

    prprojs = sorted(pickup_dir.glob("*.prproj"))
    if not prprojs:
        prprojs = sorted(pickup_dir.rglob("*.prproj"))

    if not prprojs:
        raise FileNotFoundError(f"No .prproj found inside: {pickup_dir}")

    clean_low = clean_name.lower()
    for p in prprojs:
        if clean_low in p.stem.lower():
            return p
    return prprojs[0]

# ----------------------------
# MAIN LOGIC
# ----------------------------
def main():
    try:
        cfg = load_config()
    except Exception as e:
        print(f"❌ CONFIG ERROR: {e}")
        return

    raw_video_name = (cfg.get("video_name") or "").strip()
    raw_language = (cfg.get("language") or "").strip()

    if not raw_video_name or not raw_language:
        print("❌ ERROR: config.json is missing 'video_name' or 'language'")
        return

    clean_name = clean_video_name(raw_video_name)
    language_cap = capitalize_language_folder(raw_language)

    # ✅ UPDATED: get prefix from __languages.json (with safe fallback)
    lang_prefix = get_lang_prefix(raw_language, cfg)

    # 1. Define Search Base
    search_base = NETWORK_BASE / clean_name / "English" / "Project File"

    print(f"[CONFIG] Video: {clean_name}")
    print(f"[CONFIG] Lang:  {language_cap} ({lang_prefix})")

    # Optional visibility: warn if metadata path missing (does not break anything)
    if not LANG_METADATA_PATH.exists():
        print(f"⚠️  LANG METADATA not found at: {LANG_METADATA_PATH} (using fallback mapping)")

    if not search_base.exists():
        print(f"❌ ERROR: Search path does not exist: {search_base}")
        return

    # 2. DISCOVERY LOGIC
    pickup_dir = None
    folders = [f for f in search_base.iterdir() if f.is_dir() and f.name.startswith("Copied_")]

    if folders:
        pickup_dir = folders[0]
        print(f"[FOUND]  Folder: {pickup_dir.name}")
    else:
        print(f"⚠️  No 'Copied_' folder found. Checking search base directly.")
        pickup_dir = search_base

    # 3. Define Destination
    dest_dir = NETWORK_BASE / clean_name / language_cap / "Project File"
    dest_file = dest_dir / f"{lang_prefix} - {clean_name}.prproj"

    # 4. Find the file and Copy
    try:
        pickup_file = find_pickup_prproj(pickup_dir, clean_name)
        print(f"[FOUND]  File:   {pickup_file.name}")

        # Create destination directory if it doesn't exist
        dest_dir.mkdir(parents=True, exist_ok=True)

        print(f"[ACTION] Copying to target...")

        # ULTIMATE FIX: copyfile does not touch permissions/metadata
        # This prevents the [Errno 1] Operation not permitted on network drives
        shutil.copyfile(pickup_file, dest_file)

        print("\n✅ PROCESS COMPLETE: Successfully copied to viralserver!")

    except Exception as e:
        # If it still throws a permission error but the file exists, we ignore it
        if "Operation not permitted" in str(e) and dest_file.exists():
            print("\n✅ PROCESS COMPLETE: File copied (Note: Metadata sync skipped).")
        else:
            print(f"\n❌ ERROR during copy: {e}")

if __name__ == "__main__":
    main()
