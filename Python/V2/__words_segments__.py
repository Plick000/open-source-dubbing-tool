import json
import random
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import pycountry

# ================== CONFIG ==================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"

# Language metadata (speciality mode) config (Z drive)
# NOTE: In WSL, Z: is typically mounted at /mnt/z
METADATA_LANG_CONFIG_CANDIDATES = [
    Path("/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json"),
    Path("/mnt/z/Automated Dubbings/admin/configs/metadata/languages.json"),
    Path("Z:/Automated Dubbings/admin/configs/metadata/__languages.json"),
    Path("Z:/Automated Dubbings/admin/configs/metadata/languages.json"),
]

def _load_json_if_exists(path: Path) -> Optional[Any]:
    try:
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None

def _normalize_language_key(lang_name: str) -> str:
    """
    Converts config language name (e.g., 'Polish', 'Chinese', 'Brazilian Portuguese') to the keys used in __languages.json.
    """
    s = str(lang_name or "").strip().lower()
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()

    # Common aliases / fallbacks
    aliases = {
        "bengali": "bangla",
        "mandarin": "chinese",
        "mandarin chinese": "chinese",
        "chinese mandarin": "chinese",
        "farsi": "persian",
    }
    if s in aliases:
        return aliases[s]

    # Handle multi-word variants
    if "portuguese" in s:
        return "portuguese"
    if "chinese" in s:
        return "chinese"

    # Default: first token is usually enough (e.g., "arabic", "korean")
    return s.split(" ")[0] if s else s

def _load_brand_key(config_path: Path = CONFIG_PATH) -> Optional[str]:
    """
    Optional brand narrowing. Supports config values like:
      brand: 17  -> 'brand_17'
      brand: 'brand_17' -> 'brand_17'
      Brand: '17' -> 'brand_17'
    If missing, returns None (we'll scan all brands).
    """
    if not config_path.exists():
        return None
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    raw = (
        cfg.get("brand")
        or cfg.get("Brand")
        or cfg.get("brand_id")
        or cfg.get("brandId")
        or (cfg.get("dubbing", {}) or {}).get("brand")
        or (cfg.get("dubbing", {}) or {}).get("brand_id")
        or (cfg.get("dubbing", {}) or {}).get("brandId")
    )

    if raw is None:
        return None

    s = str(raw).strip().lower()
    if not s:
        return None

    if s.startswith("brand_"):
        return s

    # numeric?
    m = re.fullmatch(r"\d+", s)
    if m:
        return f"brand_{int(s)}"

    return None

def _lookup_speciality_mode_from_languages_config(
    languages_cfg: Any,
    lang_key: str,
    brand_key: Optional[str] = None,
) -> Optional[str]:
    """
    Searches __languages.json structure for a given language key, optionally within a specific brand.
    Returns speciality mode (e.g., 'CJK', 'RTL', 'INDIC', 'CYRILLIC', 'DEFAULT') or None if not found.
    """
    if not languages_cfg:
        return None

    # The file is a list with one big object
    if isinstance(languages_cfg, list) and languages_cfg:
        root_obj = languages_cfg[0]
    else:
        root_obj = languages_cfg

    if not isinstance(root_obj, dict):
        return None

    def _search_brand(brand_obj: Any) -> Optional[str]:
        if not isinstance(brand_obj, dict):
            return None
        langs = brand_obj.get("languages")
        if not isinstance(langs, dict):
            return None
        entry = langs.get(lang_key)
        if not isinstance(entry, dict):
            return None
        mode = entry.get("speciality mode") or entry.get("speciality_mode") or entry.get("specialityMode")
        return str(mode).strip() if mode is not None else None

    # If brand specified, try that first
    if brand_key and brand_key in root_obj:
        return _search_brand(root_obj.get(brand_key))

    # Otherwise scan all brands
    for k, v in root_obj.items():
        if not str(k).lower().startswith("brand_"):
            continue
        found = _search_brand(v)
        if found:
            return found

    return None

def _pick_char_limits_from_speciality_mode(mode: Optional[str]) -> Tuple[int, int]:
    """
    Business rules:
      - CYRILLIC => 10-20
      - RTL      => 10-20
      - CJK      => 5-15
      - INDIC    => 10-15
      - DEFAULT/unknown => 10-25
    """
    m = str(mode or "").strip().upper()
    if m == "CYRILLIC":
        return (10, 20)
    if m == "RTL":
        return (10, 20)
    if m == "CJK":
        return (5, 15)
    if m == "INDIC":
        return (10, 15)
    return (10, 25)

def resolve_char_limits_for_language(lang_name: str) -> Tuple[int, int, str]:
    """
    Returns: (min_chars, max_chars, speciality_mode_used)
    Never raises; always returns a safe default.
    """
    lang_key = _normalize_language_key(lang_name)
    brand_key = _load_brand_key()

    cfg_obj = None
    for cand in METADATA_LANG_CONFIG_CANDIDATES:
        cfg_obj = _load_json_if_exists(cand)
        if cfg_obj is not None:
            break

    speciality = _lookup_speciality_mode_from_languages_config(cfg_obj, lang_key, brand_key=brand_key)
    min_c, max_c = _pick_char_limits_from_speciality_mode(speciality)
    return min_c, max_c, (str(speciality).strip().upper() if speciality else "DEFAULT")

def load_language_name(config_path: Path = CONFIG_PATH, default: str = "") -> str:
    if not config_path.exists():
        return default
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    lang = (
    cfg.get("language")
        or cfg.get("targetLanguage")
        or cfg.get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("language")
        or (cfg.get("dubbing", {}) or {}).get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("targetLanguage")
        or default
    )
    return str(lang).strip()

def language_name_to_code(language_name: str) -> str:
    name = " ".join(language_name.replace("-", " ").split()).strip()
    try:
        lang = pycountry.languages.lookup(name)
    except LookupError:
        # fallback: try title-cased (sometimes helps)
        lang = pycountry.languages.lookup(name.title())

    code = getattr(lang, "alpha_2", None) or getattr(lang, "alpha_3", None)
    if not code:
        raise ValueError(f"Could not derive ISO code from language name: {language_name}")
    return code.lower()

# ================== IMPORTS ==================

LANG_NAME = load_language_name()
if not LANG_NAME:
    raise RuntimeError(f"No language found in {CONFIG_PATH}. Set 'language' to a full name like 'Polish'.")

Language = " ".join(LANG_NAME.replace("-", " ").split()).title()   # Capitalized full name (folder)
shortLanguage = language_name_to_code(LANG_NAME)                  # ISO code (transcript filename)


INPUT_ROOT_DIR = PROJECT_ROOT / "output" / "samples" / f"{Language}"
OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A7__segments__.json"
TRANSCRIPT_FILENAME = f"transcript_{shortLanguage}.json"

print(f"[config] Language folder: {Language} | short code: {shortLanguage}")


FPS = 24
RANDOM_SEED = 42

# NEW: Character-based grouping (easy to change)
# Default remains 10-25 (existing behavior), but we override based on language speciality mode
# from Z:/Automated Dubbings/admin/configs/metadata/__languages.json (WSL: /mnt/z/...).
MIN_CHARS, MAX_CHARS, _SPECIALITY_MODE = resolve_char_limits_for_language(LANG_NAME)
print(f"[config] Speciality mode: {_SPECIALITY_MODE} | char range: {MIN_CHARS}-{MAX_CHARS}")

# Optional: if last segment becomes too short, try to merge into previous if it won't exceed MAX_CHARS
MERGE_LAST_IF_TOO_SHORT = True

# NEW: enforce no frame gaps between segments inside an interview
ENFORCE_CONTIGUOUS_FRAMES = True

# NEW: after frame-fix, recompute seconds from frames so they stay consistent
RECOMPUTE_SECONDS_FROM_FRAMES = True

# Prevent 0-frame segments due to rounding edge-cases
MIN_SEGMENT_FRAMES = 1
# ===========================================


def load_transcript(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def collect_dubbed_words(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Flatten all 'word_type' == 'word' items from data['dubbed'][*]['words'].
    Keeps order across utterances.
    """
    words: List[Dict[str, Any]] = []
    dubbed = data.get("dubbed", [])

    for utt in dubbed:
        for w in utt.get("words", []):
            if w.get("word_type") != "word":
                continue
            words.append(
                {
                    "text": str(w.get("text", "")).strip(),
                    "start_s": float(w.get("start_s", 0.0)),
                    "end_s": float(w.get("end_s", 0.0)),
                }
            )

    # remove empty-text words safely
    words = [w for w in words if w["text"]]
    return words


def seconds_to_frame(t: float, fps: float) -> int:
    return int(round(t * fps))


def frame_to_seconds(f: int, fps: float) -> float:
    return f / fps


def extract_interview_number_from_name(name: str) -> Optional[int]:
    """
    interview_005_id5 -> 5
    interview_6 -> 6
    id12 -> 12
    Takes the LAST number in the string.
    """
    nums = re.findall(r"\d+", str(name))
    if not nums:
        return None
    try:
        return int(nums[-1])
    except Exception:
        return None


def resolve_interview_number(tpath: Path, data: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """
    Priority:
    1) parent folder name (your case: interview_005_id5 -> 5)
    2) filename stem
    3) optional JSON fields if present
    """
    n = extract_interview_number_from_name(tpath.parent.name)
    if n is not None:
        return n

    n = extract_interview_number_from_name(tpath.stem)
    if n is not None:
        return n

    if isinstance(data, dict):
        for key in ("interview_id", "id", "interviewNumber", "interview_index"):
            if key in data:
                try:
                    return int(data[key])
                except Exception:
                    pass

    return None


def segment_char_len(seg_words: List[Dict[str, Any]]) -> int:
    return len(" ".join(w["text"] for w in seg_words).strip())


def group_words_by_chars_random(
    words: List[Dict[str, Any]],
    rng: random.Random,
    min_chars: int,
    max_chars: int,
) -> List[List[Dict[str, Any]]]:
    """
    Randomly group consecutive words into segments with character length between [min_chars, max_chars],
    WITHOUT cutting words.

    Rule behavior:
    - pick a random target T in [min_chars, max_chars]
    - add words until reaching/exceeding T
    - if adding the next whole word would exceed max_chars, do NOT add it; it becomes first word of next segment
      (this matches your "if 40 exceeded, move that word to next chunk")

    Note:
    - If a SINGLE word itself is longer than max_chars, we still put it alone in its own segment.
    """
    if not words:
        return []

    segments: List[List[Dict[str, Any]]] = []
    i = 0
    n = len(words)

    while i < n:
        target = rng.randint(min_chars, max_chars)

        seg: List[Dict[str, Any]] = []
        cur_len = 0

        while i < n:
            w = words[i]
            wtxt = w["text"]

            add_len = len(wtxt) if cur_len == 0 else (1 + len(wtxt))  # include space
            new_len = cur_len + add_len

            # If segment empty and the word itself is longer than max_chars => allow it alone
            if not seg and len(wtxt) > max_chars:
                seg.append(w)
                i += 1
                break

            # If we can add word without exceeding max_chars, decide whether to add or stop
            if new_len <= max_chars:
                # Always add if we are still below target
                if cur_len < target:
                    seg.append(w)
                    cur_len = new_len
                    i += 1

                    # If we reached/exceeded target, we STOP here (word already complete)
                    if cur_len >= target:
                        break
                    continue

                # If we're already at/above target, stop (keep segment in range)
                break

            # If adding would exceed max_chars:
            # - If we have nothing yet (shouldn't happen because long-word handled), force-add one word
            # - Else: stop; this word goes to next segment
            if not seg:
                seg.append(w)
                i += 1
            break

        if seg:
            segments.append(seg)
        else:
            # safety: avoid infinite loop
            i += 1

    # Optional: fix last segment if too short by merging into previous (only if it doesn't exceed max_chars)
    if MERGE_LAST_IF_TOO_SHORT and len(segments) >= 2:
        last = segments[-1]
        if segment_char_len(last) < min_chars:
            prev = segments[-2]
            combined = prev + last
            if segment_char_len(combined) <= max_chars:
                segments[-2] = combined
                segments.pop()

    return segments


def build_interview_segments(
    groups: List[List[Dict[str, Any]]],
    fps: float,
    interview_name: str,
) -> Dict[str, Any]:
    """
    Build final JSON structure for ONE interview:
    {
      "id": "interview_5",
      "segments": [
        {"id":"seg_001", "start_s":..., "end_s":..., "start_frame":..., "end_frame":..., "text":"..."},
        ...
      ]
    }
    """
    segments_out: List[Dict[str, Any]] = []

    for idx, g in enumerate(groups, start=1):
        start_s = float(g[0]["start_s"])
        end_s = float(g[-1]["end_s"])
        start_f = seconds_to_frame(start_s, fps)
        end_f = seconds_to_frame(end_s, fps)

        seg_txt = " ".join(w["text"] for w in g).strip()

        segments_out.append(
            {
                "id": f"seg_{idx:03d}",
                "start_s": start_s,
                "end_s": end_s,
                "start_frame": start_f,
                "end_frame": end_f,
                "text": seg_txt,
            }
        )

    # Enforce contiguity (no gaps) by aligning start_frame/end_frame in sequence
    if ENFORCE_CONTIGUOUS_FRAMES and segments_out:
        for i in range(1, len(segments_out)):
            prev = segments_out[i - 1]
            cur = segments_out[i]
            # make current start exactly one frame after prev end
            cur["start_frame"] = prev["end_frame"] + 1
            # Ensure start <= end and at least MIN_SEGMENT_FRAMES
            if cur["end_frame"] < cur["start_frame"]:
                cur["end_frame"] = cur["start_frame"] + (MIN_SEGMENT_FRAMES - 1)

    # Optionally recompute seconds from frames after fixes
    if RECOMPUTE_SECONDS_FROM_FRAMES and segments_out:
        for seg in segments_out:
            seg["start_s"] = frame_to_seconds(int(seg["start_frame"]), fps)
            seg["end_s"] = frame_to_seconds(int(seg["end_frame"]), fps)

    return {"id": interview_name, "segments": segments_out}


def find_transcript_files(root: Path, filename: str) -> List[Path]:
    return list(root.rglob(filename))


def main():
    root = Path(INPUT_ROOT_DIR)
    if not root.exists():
        print(f"Input root folder does not exist: {root.resolve()}")
        return

    transcripts = find_transcript_files(root, TRANSCRIPT_FILENAME)

    if not transcripts:
        print(f"No '{TRANSCRIPT_FILENAME}' files found under: {root.resolve()}")
        return

    print(f"Found {len(transcripts)} transcript file(s):")
    for p in transcripts:
        print("  -", p)

    pairs: List[Tuple[int, Path]] = []
    fallback_counter = 1

    for tpath in transcripts:
        data_for_id: Optional[Dict[str, Any]] = None
        try:
            data_for_id = load_transcript(tpath)
        except Exception:
            data_for_id = None

        interview_num = resolve_interview_number(tpath, data_for_id)
        if interview_num is None:
            interview_num = fallback_counter
            fallback_counter += 1

        pairs.append((interview_num, tpath))

    pairs.sort(key=lambda x: x[0])

    out_list: List[Dict[str, Any]] = []
    used_ids = set()

    for interview_num, tpath in pairs:
        # Ensure unique interview keys if duplicates appear
        final_num = interview_num
        while final_num in used_ids:
            final_num += 1
        used_ids.add(final_num)

        interview_name = f"interview_{final_num}"
        print(f"\n--- Processing {tpath} as {interview_name} ---")

        try:
            data = load_transcript(tpath)
        except Exception as e:
            print(f"  ❌ Failed to load transcript {tpath}: {e}")
            continue

        words = collect_dubbed_words(data)
        print(f"  Collected {len(words)} words from 'dubbed' section.")

        if not words:
            print("  ⚠ No words found, skipping.")
            continue

        # Deterministic RNG per interview id
        rng = random.Random(RANDOM_SEED + int(final_num))

        groups = group_words_by_chars_random(words, rng, MIN_CHARS, MAX_CHARS)
        print(f"  Created {len(groups)} char-based segments (min={MIN_CHARS}, max={MAX_CHARS}).")

        interview_obj = build_interview_segments(groups, FPS, interview_name)
        out_list.append(interview_obj)

    if not out_list:
        print("\n⚠ No valid interviews processed. Nothing to save.")
        return

    out_path = Path(OUTPUT_JSON)
    out_path.write_text(
        json.dumps(out_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✔ Saved grouped interviews JSON → {out_path.resolve()}")
    print("Done.\n")


if __name__ == "__main__":
    main()
