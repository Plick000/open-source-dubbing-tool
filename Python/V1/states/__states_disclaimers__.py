#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
__states_disclaimers__.py (FIXED)

- Read brand + language from inputs/config/config.json (WSL repo)
- Load languages config from Z drive (WSL):
    /mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json
- Resolve ONLY format for output path (your final spec):
    /mnt/z/Automated Dubbings/Disclaimers/{format}/{language}.mp4

SPECIAL OVERRIDE (RESTORED):
- If (format == "Format 3") AND (brand in {5,6}) AND (version == "V1"):
    /mnt/z/Automated Dubbings/Disclaimers/Format 3/{brand}.1/{language}.mp4

Notes:
- We do NOT include version folder in final_location.
- We DO read version from languages config ONLY to decide override (V1 requirement).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple


# -----------------------
# REPO PATHS (WSL)
# -----------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"
DISCLAIMER_JSON_FILE = PROJECT_ROOT / "output" / "JSON" / "A8__disclaimer__.json"

# -----------------------
# Z DRIVE PATHS (WSL)
# -----------------------
Z_ROOT = Path("/mnt/z/Automated Dubbings")
LANGUAGES_CFG_PATH_WSL = Z_ROOT / "admin" / "configs" / "metadata" / "__languages.json"

# FINAL DISCLAIMERS ROOT (YOUR FINAL SPEC)
DISCLAIMERS_ROOT = Z_ROOT / "Disclaimers"


# -----------------------
# Helpers
# -----------------------
def _norm(v: Any) -> str:
    return str(v or "").strip()

def ensure_z_mounted() -> None:
    if not Path("/mnt/z").exists():
        raise RuntimeError("Z drive is not mounted in WSL: /mnt/z does not exist.")
    if not Z_ROOT.exists():
        raise RuntimeError(f"Z root folder not found: {Z_ROOT}")
    if not LANGUAGES_CFG_PATH_WSL.is_file():
        raise FileNotFoundError(f"Languages config not found: {LANGUAGES_CFG_PATH_WSL}")

def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def resolve_brand_language(inputs_cfg: Dict[str, Any]) -> Tuple[int, str]:
    """
    Brand from config.video_brand, language from config.language (or equivalents)
    Returns:
      brand: int
      lang_key: lowercase underscore
    """
    brand_raw = (
        inputs_cfg.get("video_brand")
        or inputs_cfg.get("brand")
        or inputs_cfg.get("videoBrand")
        or inputs_cfg.get("brand_id")
        or inputs_cfg.get("brandId")
    )
    brand_raw = _norm(brand_raw)
    if not brand_raw:
        raise RuntimeError(f"Could not resolve BRAND from inputs config. Got: {brand_raw!r}")

    try:
        brand = int(brand_raw)
    except Exception:
        m = re.search(r"(\d+)", brand_raw)
        if not m:
            raise RuntimeError(f"Could not parse brand from inputs config value: {brand_raw!r}")
        brand = int(m.group(1))

    if brand < 1:
        raise RuntimeError(f"Invalid brand parsed: {brand}")

    lang_raw = (
        inputs_cfg.get("language")
        or inputs_cfg.get("targetLanguage")
        or inputs_cfg.get("target_lang")
        or (inputs_cfg.get("dubbing", {}) or {}).get("language")
        or (inputs_cfg.get("dubbing", {}) or {}).get("targetLanguage")
        or (inputs_cfg.get("dubbing", {}) or {}).get("target_lang")
        or ""
    )
    lang_raw = _norm(lang_raw)
    if not lang_raw:
        raise RuntimeError("Could not resolve LANGUAGE from inputs config.")

    # normalize: lowercase, hyphens/spaces -> underscore
    lang_key = " ".join(lang_raw.replace("-", " ").split()).strip().lower()
    lang_key = lang_key.replace(" ", "_")

    return brand, lang_key

def normalize_languages_cfg(raw_cfg: Any) -> Dict[str, Any]:
    """
    __languages.json can be dict or list-of-dicts. Normalize to dict.
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

def find_brand_block(cfg: Dict[str, Any], brand: int) -> Dict[str, Any]:
    key = f"brand_{brand}"
    if key in cfg and isinstance(cfg[key], dict):
        return cfg[key]

    # fallback search
    for k, v in cfg.items():
        if not isinstance(v, dict):
            continue
        m = re.search(r"(\d+)", str(k))
        if m and int(m.group(1)) == brand:
            return v

    raise KeyError(f"Brand '{key}' not found in languages config: {LANGUAGES_CFG_PATH_WSL}")

def find_language_entry(brand_block: Dict[str, Any], lang_key: str) -> Dict[str, Any]:
    langs = brand_block.get("languages") or {}
    if not isinstance(langs, dict) or not langs:
        raise KeyError("Brand block has no 'languages' dictionary.")

    if lang_key in langs and isinstance(langs[lang_key], dict):
        return langs[lang_key]

    # variant matching
    want = lang_key.replace("-", "_").replace(" ", "_")
    for k, v in langs.items():
        kk = _norm(k).lower().replace("-", "_").replace(" ", "_")
        if kk == want and isinstance(v, dict):
            return v

    available = sorted([str(k) for k in langs.keys()])
    raise KeyError(
        f"Language '{lang_key}' not found under this brand. "
        f"Available: {available[:40]}{'...' if len(available) > 40 else ''}"
    )

def parse_format_number(fmt: str) -> int:
    """
    Returns numeric part if format contains a number (e.g. "Format 3" -> 3)
    Raises if none found.
    """
    m = re.search(r"(\d+)", _norm(fmt))
    if not m:
        raise ValueError(f"Format has no number: {fmt!r}")
    return int(m.group(1))

def build_state(inputs_cfg: Dict[str, Any], languages_cfg_raw: Any) -> Dict[str, Any]:
    ensure_z_mounted()

    brand, lang_key = resolve_brand_language(inputs_cfg)
    lang_cfg = normalize_languages_cfg(languages_cfg_raw)

    brand_block = find_brand_block(lang_cfg, brand)
    entry = find_language_entry(brand_block, lang_key)

    # format is the folder name EXACTLY (your final spec)
    format_raw = _norm(entry.get("format"))
    if not format_raw:
        raise KeyError(f"Missing 'format' for brand_{brand}.{lang_key} in __languages.json")

    # read version only for override condition
    version_str = _norm(entry.get("version")).upper() or "V1"
    if not version_str.startswith("V"):
        version_str = f"V{version_str}"

    # --- SPECIAL OVERRIDE (RESTORED): Format 3 + brand 5/6 + V1 => /Format 3/{brand}.1/{lang}.mp4
    override_applied = False
    final_path = DISCLAIMERS_ROOT / format_raw / f"{lang_key.capitalize()}.mp4"

    try:
        fmt_num = parse_format_number(format_raw)
    except Exception:
        fmt_num = None

    if fmt_num == 3 and brand in (5, 6) and version_str == "V1":
        override_applied = True
        final_path = DISCLAIMERS_ROOT / format_raw / f"{brand}.1" / f"{lang_key.capitalize()}.mp4"

    final_location = str(final_path.resolve())
    final_exists = final_path.is_file()

    return {
        "video_name": _norm(inputs_cfg.get("video_name") or inputs_cfg.get("videoName") or ""),
        "brand": brand,
        "brand_source": "config.video_brand",
        "language_key": lang_key,

        # from __languages.json
        "format_key": format_raw,
        "version_str": version_str,  # kept for debugging/traceability

        # override state
        "override_applied": override_applied,

        # final output
        "final_location": final_location,
        "final_exists": final_exists,

        # traceability
        "languages_cfg_path": str(LANGUAGES_CFG_PATH_WSL),
        "inputs_config": inputs_cfg,
    }

def main() -> None:
    ensure_z_mounted()

    inputs_cfg = read_json(CONFIG_PATH)
    languages_cfg_raw = read_json(LANGUAGES_CFG_PATH_WSL)

    state = build_state(inputs_cfg, languages_cfg_raw)

    print(json.dumps(state, indent=2, ensure_ascii=False))

    DISCLAIMER_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCLAIMER_JSON_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
