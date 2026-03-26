#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =======================
# Auto-pick video + language + brand from config.json
# Brand is taken ONLY from config.video_brand
# Builds LowerThird AEP project location
#
# UPDATED:
# - NO hardcoded languages
# - format (and version for output compatibility) resolved from:
#     /mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json
# - Folder naming follows Titles rule:
#     "Format 18 RTL" -> folder "format 18 RTL"
# - File naming follows format string:
#     "Format 18 RTL LowerThird.aep"
# - aep_missing check is done on WSL path (correct)
# =======================

import re
import json
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Tuple


# -----------------------
# CONFIG (repo local)
# -----------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"

# Base folder (Windows display + WSL real path)
LOWERTHIRDS_BASE_WIN = PureWindowsPath(r"Z:\Automated Dubbings\LowerThirds")
LOWERTHIRDS_BASE_WSL = Path("/mnt/z/Automated Dubbings/LowerThirds")

# Output JSON (unchanged)
OUTPUT_JSON_FILE = PROJECT_ROOT / "output" / "JSON" / "A19__lowerthird__.json"

# Z-drive languages config (WSL path)
LANGUAGES_CFG_PATH_WSL = Path("/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json")


# -----------------------
# Helpers
# -----------------------
def _norm(v: Any) -> str:
    return str(v or "").strip()

def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def _ensure_z_mounted() -> None:
    if not Path("/mnt/z").exists():
        raise RuntimeError("Z drive is not mounted in WSL: /mnt/z does not exist.")
    if not LANGUAGES_CFG_PATH_WSL.is_file():
        raise FileNotFoundError(f"Languages config not found on Z drive: {LANGUAGES_CFG_PATH_WSL}")

def _read_brand_from_config(cfg: dict) -> int:
    """
    Reads brand from cfg['video_brand'] ONLY.
    Accepts: 7, "7", "Brand 7", "brand-10", "Brand 19", etc.
    Returns int >= 1
    """
    raw = _norm(cfg.get("video_brand"))
    if not raw:
        raise RuntimeError(f"No brand found in {CONFIG_PATH}. Expected key: video_brand")

    try:
        b = int(raw)
    except ValueError:
        m = re.search(r"(\d+)", raw)
        if not m:
            raise RuntimeError(f"Invalid video_brand in config: {raw!r}")
        b = int(m.group(1))

    if b < 1:
        raise RuntimeError(f"Invalid brand parsed: {b}")
    return b

def _normalize_lang_key(language_raw: str) -> Tuple[str, str]:
    """
    Returns:
      - language_display (Title Case)
      - language_key (lowercase underscore) for matching __languages.json keys
    """
    lr = _norm(language_raw)
    language_display = " ".join(lr.replace("-", " ").split()).title()

    lk = lr.lower()
    lk = " ".join(lk.replace("-", " ").split())
    lk = lk.replace(" ", "_")
    return language_display, lk

def load_video_language_and_brand() -> Tuple[str, str, str, int, Dict[str, Any]]:
    cfg = _read_json(CONFIG_PATH)

    video_name = (
        cfg.get("video_name")
        or cfg.get("videoName")
        or cfg.get("video_title")
        or cfg.get("title")
        or (cfg.get("video", {}) or {}).get("name")
        or (cfg.get("video", {}) or {}).get("title")
        or ""
    )
    video_name = _norm(video_name)
    if not video_name:
        raise RuntimeError(
            f"No video name found in {CONFIG_PATH}. "
            "Expected one of: video_name, videoName, video_title, title, video.name, video.title"
        )

    language_raw = (
        cfg.get("language")
        or cfg.get("targetLanguage")
        or cfg.get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("language")
        or (cfg.get("dubbing", {}) or {}).get("targetLanguage")
        or (cfg.get("dubbing", {}) or {}).get("target_lang")
        or ""
    )
    language_raw = _norm(language_raw)
    if not language_raw:
        raise RuntimeError(
            f"No language found in {CONFIG_PATH}. "
            "Expected one of: language, targetLanguage, target_lang, dubbing.language, dubbing.target_lang"
        )

    brand = _read_brand_from_config(cfg)
    language_display, lang_key = _normalize_lang_key(language_raw)

    return video_name, language_display, lang_key, brand, cfg

def _normalize_languages_cfg(raw_cfg: Any) -> Dict[str, Any]:
    """
    __languages.json can be:
      - dict
      - list of dict(s)
    Normalize to a single dict with brand_* keys.
    """
    if isinstance(raw_cfg, dict):
        return raw_cfg
    if isinstance(raw_cfg, list):
        merged: Dict[str, Any] = {}
        for el in raw_cfg:
            if isinstance(el, dict):
                merged.update(el)
        if merged:
            return merged
    raise TypeError(f"Unexpected __languages.json top-level type: {type(raw_cfg).__name__}")

def _find_brand_block(cfg: Dict[str, Any], brand: int) -> Dict[str, Any]:
    key = f"brand_{brand}"
    if key in cfg and isinstance(cfg[key], dict):
        return cfg[key]

    # fallback: any key containing the same digits
    for k, v in cfg.items():
        if not isinstance(v, dict):
            continue
        m = re.search(r"(\d+)", str(k))
        if m and int(m.group(1)) == brand:
            return v

    raise KeyError(f"Brand '{key}' not found in languages config: {LANGUAGES_CFG_PATH_WSL}")

def _find_language_entry(brand_block: Dict[str, Any], lang_key: str) -> Dict[str, Any]:
    langs = brand_block.get("languages") or {}
    if not isinstance(langs, dict) or not langs:
        raise KeyError("Brand block has no 'languages' dictionary in __languages.json")

    if lang_key in langs and isinstance(langs[lang_key], dict):
        return langs[lang_key]

    want = lang_key.replace("-", "_").replace(" ", "_")
    for k, v in langs.items():
        kk = _norm(k).lower().replace("-", "_").replace(" ", "_")
        if kk == want and isinstance(v, dict):
            return v

    available = sorted([str(k) for k in langs.keys()])
    raise KeyError(
        f"Language '{lang_key}' not found under this brand in __languages.json. "
        f"Available (sample): {available[:40]}{'...' if len(available) > 40 else ''}"
    )

def resolve_format_and_version(brand: int, lang_key: str) -> Tuple[str, int, str]:
    """
    Returns:
      format_raw (string exactly as in json, e.g. "Format 18 RTL")
      version_num (1/2)
      version_str ("V1"/"V2")
    """
    _ensure_z_mounted()
    raw_cfg = _read_json(LANGUAGES_CFG_PATH_WSL)
    cfg = _normalize_languages_cfg(raw_cfg)

    brand_block = _find_brand_block(cfg, brand)
    entry = _find_language_entry(brand_block, lang_key)

    fmt = _norm(entry.get("format"))
    if not fmt:
        raise KeyError(f"Missing 'format' for brand_{brand}.{lang_key} in __languages.json")

    ver_str = _norm(entry.get("version")).upper() or "V1"
    if not ver_str.startswith("V"):
        ver_str = f"V{ver_str}"
    if ver_str not in ("V1", "V2"):
        raise ValueError(f"Invalid version '{ver_str}' for brand_{brand}.{lang_key} (expected V1 or V2).")
    ver_num = 1 if ver_str == "V1" else 2

    return fmt, ver_num, ver_str

def make_folder_name_from_format(fmt: str) -> str:
    """
    Required by your Titles rule:
      "Format 18 RTL" -> "format 18 RTL"
      "Format 19"     -> "format 19"
      "Format RTL"    -> "format RTL"
    """
    s = _norm(fmt)
    if not s:
        raise ValueError("Empty format string")
    if s.lower().startswith("format"):
        return "format" + s[6:]  # replace only the first word casing
    return s[:1].lower() + s[1:]


# =======================
# MAIN
# =======================
video_name, language, lang_key, brand, inputs_cfg = load_video_language_and_brand()

format_raw, version_num, version_str = resolve_format_and_version(brand, lang_key)
format_key = make_folder_name_from_format(format_raw)

# Folder + file naming (same style as Titles)
lowerthirds_folder_win = LOWERTHIRDS_BASE_WIN / format_key
lowerthirds_folder_wsl = LOWERTHIRDS_BASE_WSL / format_key

aep_file = f"{format_raw} LowerThird.aep"

aep_path_win = str(lowerthirds_folder_win / aep_file)
final_location = str((lowerthirds_folder_wsl / aep_file).resolve())

# IMPORTANT: check existence using WSL path
aep_missing = not Path(final_location).is_file()

result = {
    "video_name": video_name,
    "language": language,
    "language_key": lang_key,

    "brand": brand,
    "brand_source": "config.video_brand",

    # kept for backward compatibility, but not hardcoded
    "version": version_num,
    "version_str": version_str,

    # keep format exactly (supports "Format 18 RTL")
    "format": format_raw,
    "format_key": format_key,

    "lowerthirds_folder": str(lowerthirds_folder_win),

    "aep_file": aep_file,
    "aep_path_win": aep_path_win,
    "aep_missing": aep_missing,

    "final_location": final_location,
    "status": "OK" if not aep_missing else "MISSING_AEP",
}

print(json.dumps(result, indent=2, ensure_ascii=False))

OUTPUT_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_JSON_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"✅ {OUTPUT_JSON_FILE} written successfully")
