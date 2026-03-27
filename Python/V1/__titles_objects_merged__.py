#!/usr/bin/env python3
import json
import os
import random
import time
from pathlib import Path, PureWindowsPath
from openai import OpenAI

# ========= CONFIG =========

PROJECT_ROOT = Path(__file__).resolve().parents[2]
    
INPUT_FILE = PROJECT_ROOT / "output" / "JSON" / "A12__final_segments__.json"
CONFIG_FILE = PROJECT_ROOT / "inputs" / "config" / "config.json"

# INPUT_FILE = Path("output/JSON/A12__final_segments__.json")

# Placeholder: root folder name coming from your JS pipeline
# DO NOT ASK QUESTIONS: user will set this

# CONFIG_FILE = Path("inputs/config/config.json")

# These are now AUTO-BUILT inside main() after reading JSON
OUTPUT_FILE = Path("")  # will be set
OUTPUT_FILE_WIN = ""
IMAGES_BASE_DIR_WIN = ""  # will be set

IMAGE_PREFIX = "title_"
IMAGE_DIGITS = 3  # 001, 002, ...

# Try these extensions in this order until one exists
IMAGE_EXT_CANDIDATES = [".jpg", ".jpeg", ".png", ".webp", ".avif", ".tiff", ".tif", ".bmp"]

# DeepSeek API (PUT YOUR KEY HERE)
DEEPSEEK_API_KEY = ""  # <-- change this
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Word range per line (for the part AFTER the number line)
MIN_WORDS_PER_LINE = 2
MAX_WORDS_PER_LINE = 4
# =========================


def load_config_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"ERROR: Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError("ERROR: config.json must be a JSON object (dict).")
    return data


def strip_video_prefix(name: str) -> str:
    """
    Removes prefixes like:
      'Video 27 - ...'
      'Video 27: ...'
      'Video 27 – ...'
      'Video 27 — ...'
      'Video27 - ...'
    If no match, returns original.
    """
    if not isinstance(name, str):
        return ""
    s = name.strip()

    # Common dash variants: -, –, — plus optional ":".
    import re
    s2 = re.sub(r"^\s*Video\s*\d+\s*(?:[-:–—]\s*)", "", s, flags=re.IGNORECASE)
    return s2.strip()


def load_json_list(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def init_deepseek_client():
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "YOUR_DEEPSEEK_API_KEY_HERE":
        raise RuntimeError("Please set DEEPSEEK_API_KEY at the top of the script.")
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def wrap_words(text: str,
               min_words: int = MIN_WORDS_PER_LINE,
               max_words: int = MAX_WORDS_PER_LINE) -> str:
    """Split text into multiple lines of 2–4 words (random)."""
    words = text.split()
    n = len(words)
    if n <= min_words:
        return text

    lines = []
    i = 0
    while i < n:
        remaining = n - i
        if remaining <= max_words:
            count = remaining
        else:
            count = random.randint(min_words, max_words)

        lines.append(" ".join(words[i:i + count]))
        i += count

    return "\n".join(lines)


def llm_split_number_line(client, text: str) -> dict:
    if not text or not text.strip():
        return {"number_line": "", "rest_text": text}

    system_msg = (
        "You are a text analyser. The user will give you a short title in any language "
        "that usually starts with a phrase meaning something like 'Number X' "
        "(for example: 'Número cuatro.', 'Número seis.', 'Nummer vier.', etc.).\n\n"
        "Your job is to split this title into TWO parts:\n"
        "  1) NUMBER_LINE: ONLY the part that expresses the listing number, "
        "     including punctuation.\n"
        "     IMPORTANT: NUMBER_LINE MUST use digits, not spelled-out numbers.\n"
        "     Convert number-words into numerics.\n"
        "     Examples:\n"
        "       - 'Número cuatro.' -> 'Número 4.'\n"
        "       - 'Número seis.' -> 'Número 6.'\n"
        "       - 'Nummer vier.' -> 'Nummer 4.'\n"
        "  2) REST_TEXT: everything else in the title, with no leading/trailing spaces.\n\n"
        "RULES:\n"
        "- NUMBER_LINE must contain digits (0–9). Spelled-out numbers are NOT allowed there.\n"
        "- Do NOT rewrite, translate, or paraphrase any non-number words.\n"
        "- Keep original language, punctuation, accents, and casing exactly the same.\n"
        "- ONLY convert the number itself from words to digits.\n\n"
        "If you cannot find any clear number phrase, set NUMBER_LINE to empty "
        "and put the entire text in REST_TEXT.\n\n"
        "Respond ONLY in this EXACT format (no extra text):\n"
        "NUMBER_LINE: <...>\n"
        "BODY: <...>\n"
)

    user_msg = f'Title: "{text}"'

    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        
        number_line = ""
        body_text = ""
        
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("NUMBER_LINE:"):
                number_line = line[len("NUMBER_LINE:"):].strip()
            elif line.startswith("BODY:"):
                body_text = line[len("BODY:"):].strip()
        
        if not number_line:
            return {"number_line": "", "body_text": text.strip()}
        
        return {"number_line": number_line, "body_text": body_text or ""}
        
        
    except Exception as e:
        print(f"[WARN] DeepSeek split failed for text: {text!r} -> {e}")
        return {"number_line": "", "rest_text": text.strip()}


def format_title_with_deepseek(client, text: str) -> dict:
    if not text:
        return {"text": "", "number": "", "body": ""}

    split = llm_split_number_line(client, text)
    number_line = split.get("number_line", "") or ""
    body_raw = split.get("body_text", "") or ""

    body_wrapped = wrap_words(body_raw) if body_raw else ""

    # If there is no number, keep number="" but still return body/text
    if not number_line:
        return {"text": body_wrapped, "number": "", "body": body_wrapped}

    # If number exists but body is empty
    if not body_wrapped:
        return {"text": number_line, "number": number_line, "body": ""}

    return {"text": number_line + "\n" + body_wrapped, "number": number_line, "body": body_wrapped}



# ---------- PATH FIX (Windows -> WSL) ----------
def win_dir_to_check_path(win_dir: str) -> Path:
    """
    Convert Windows base dir like:
      E:\\Folder\\Sub
    into WSL path:
      /mnt/e/Folder/Sub
    when running on Linux/WSL.
    On Windows, returns Path(win_dir) unchanged.
    """
    if os.name == "nt":
        return Path(win_dir)

    # Linux/WSL
    p = PureWindowsPath(win_dir)
    drive = p.drive.rstrip(":").lower()
    parts = list(p.parts)[1:]  # skip "E:\\"
    return Path("/mnt") / drive / Path(*parts)


def win_file_to_os_path(win_path: str) -> Path:
    """
    Same idea as win_dir_to_check_path, but for a full file path.
    On Windows, returns Path(win_path).
    On WSL/Linux, maps E:\\... -> /mnt/e/...
    """
    if os.name == "nt":
        return Path(win_path)
    p = PureWindowsPath(win_path)
    drive = p.drive.rstrip(":").lower()
    parts = list(p.parts)[1:]
    return Path("/mnt") / drive / Path(*parts)


def _first_str(v):
    return v.strip() if isinstance(v, str) and v.strip() else ""


def sanitize_for_windows_folder(name: str) -> str:
    """
    Minimal safety: replace forbidden Windows filename chars.
    Keeps your title largely intact.
    """
    if not isinstance(name, str):
        return ""
    bad = '<>:"/\\|?*'
    out = name
    for ch in bad:
        out = out.replace(ch, "_")
    return out.strip().rstrip(".")


def detect_video_name_from_json(items) -> str:
    """
    Robustly find the "video name from json" from common keys anywhere in the list.
    Raises clear error if missing.
    """
    keys = (
        "videoName", "VideoName", "video_name", "videoTitle", "video_title",
        "videoUploadTitle", "uploadTitle", "title", "video"
    )

    # If the JSON is actually a dict
    if isinstance(items, dict):
        for k in keys:
            v = _first_str(items.get(k))
            if v:
                return sanitize_for_windows_folder(v)
        # Try nested
        meta = items.get("meta") if isinstance(items.get("meta"), dict) else {}
        for k in keys:
            v = _first_str(meta.get(k))
            if v:
                return sanitize_for_windows_folder(v)

        raise RuntimeError(
            "ERROR: Could not detect VideoName from JSON (dict). "
            "Add one of these keys at top-level: "
            + ", ".join(keys)
        )

    # List of dicts (your current structure)
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in keys:
                v = _first_str(it.get(k))
                if v:
                    return sanitize_for_windows_folder(v)
            # Try nested meta/config inside item
            meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
            for k in keys:
                v = _first_str(meta.get(k))
                if v:
                    return sanitize_for_windows_folder(v)

        raise RuntimeError(
            "ERROR: Could not detect VideoName from JSON (list). "
            "Ensure at least one item contains one of these keys: "
            + ", ".join(keys)
        )

    raise RuntimeError("ERROR: Unsupported JSON structure for detecting VideoName.")


def normalize_lang_cap(lang: str) -> str:
    """
    Convert language input to the folder style you use (LanguageCap).
    - If it's a 2-letter code: map to common names (es->Spanish, fr->French, etc.)
    - Otherwise title-case.
    """
    raw = (lang or "").strip()
    if not raw:
        return ""

    low = raw.lower().replace("_", "-")

    code_map = {
         "ko": "Korean",
         "pl": "Polish",
         "fr": "French",
         "es": "Spanish",
         "cs": "Czech",
         "ru": "Russian",
         "br": "Portuguese",
         "hr": "Croatian",
         "el": "Greek",
         "uk": "Ukrainian",
         "tr": "Turkish",
         "ja": "Japanese",
         "zh": "Chinese",
         "vi": "Vietnamese",
         "tl": "Filipino",
         "de": "German",
         "it": "Italian",
         "ar": "Arabic",
         "he": "Hebrew",
         "fi": "Finnish",
         "th": "Thai",
         "hi": "Hindi",
         "id": "Indonesian",
         "fa": "Persian",
         "ms": "Malay",
         "ro": "Romanian",
         "hu": "Hungarian",
         "sr": "Serbian",
         "bg": "Bulgarian",
         "sq": "Albanian",
         "am": "Amharic",
         "lt": "Lithuanian",
         "lv": "Latvian",
         "et": "Estonian",
         "kk": "Kazakh",
         "uz": "Uzbek",
         "bn": "Bangla",
         "te": "Telugu",
         "mr": "Marathi",
         "gu": "Gujarati",
         "ha": "Hausa",
         "ur": "Urdu",
         "nl": "Dutch",
         "sv": "Swedish",
         "da": "Danish",
         "sk": "Slovak",
         "bh": "Bhojpuri",
         "no": "Norwegian"
    }

    if low in code_map:
        return code_map[low]

    # If they already pass "Spanish" or "Portuguese-BR" etc.
    parts = raw.replace("_", " ").split()
    return " ".join(p[:1].upper() + p[1:].lower() for p in parts)


def detect_language_from_json(items) -> str:
    """
    Prefer explicit language fields if present.
    Fallback to infer from your current pipeline field: exact_script_text -> 'es'
    """
    lang_keys = ("language", "lang", "Language", "LANG", "target_language", "targetLang")

    if isinstance(items, dict):
        for k in lang_keys:
            v = _first_str(items.get(k))
            if v:
                return v
        meta = items.get("meta") if isinstance(items.get("meta"), dict) else {}
        for k in lang_keys:
            v = _first_str(meta.get(k))
            if v:
                return v
        # Fallback inference
        if any(k.startswith("text_") for k in items.keys()):
            # If exact_script_text exists, keep your pipeline assumption
            if "exact_script_text" in items:
                return "es"
        return ""

    if isinstance(items, list):
        # Search explicit first
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in lang_keys:
                v = _first_str(it.get(k))
                if v:
                    return v
            meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
            for k in lang_keys:
                v = _first_str(meta.get(k))
                if v:
                    return v

        # Fallback inference from the first Title item keys (keeps your current behavior)
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("type") == "Title":
                if "exact_script_text" in it:
                    return "es"
                # If you ever switch fields later, this will adapt
                for k in it.keys():
                    if k.startswith("text_") and _first_str(it.get(k)):
                        return k.split("_", 1)[1]  # suffix after "text_"
        return ""

    return ""


def build_output_file_win(video_name_json: str, lang_cap: str) -> str:
    return rf"Z:\Automated Dubbings\Projects\{video_name_json}\{lang_cap}\Titles\__titles_segments__.json"


def build_images_base_dir_win(video_name_json: str) -> str:
    return rf"Z:\Automated Dubbings\Projects\{video_name_json}\English\Project File\Copied_{video_name_json}"


def find_existing_image_for_title_index(title_index_1based: int) -> tuple[str, str]:
    """
    For Title #1: try title_001.jpg, title_001.jpeg, title_001.png, title_001.webp
    Check existence using OS path (WSL-compatible), but output Windows path for Premiere.
    """
    base = f"{IMAGE_PREFIX}{title_index_1based:0{IMAGE_DIGITS}d}"

    # For checking: use real filesystem path
    check_dir = win_dir_to_check_path(IMAGES_BASE_DIR_WIN)

    for ext in IMAGE_EXT_CANDIDATES:
        name = base + ext
        disk_path = check_dir / name  # real path on current OS
        if disk_path.exists():
            # Output Windows path (Premiere)
            win_path = str(PureWindowsPath(IMAGES_BASE_DIR_WIN) / name)
            return name, win_path

    return "", ""


def main():
    global OUTPUT_FILE, OUTPUT_FILE_WIN, IMAGES_BASE_DIR_WIN

    if not INPUT_FILE.exists():
        print(f"ERROR: Input file not found: {INPUT_FILE}")
        return

    items = load_json_list(INPUT_FILE)

    # ---- AUTO PATH BUILD (NEW) ----
    cfg = load_config_json(CONFIG_FILE)
    
    video_name_raw = (cfg.get("video_name") or "").strip()
    lang_raw = (cfg.get("language") or "").strip()
    
    if not video_name_raw:
        raise RuntimeError("ERROR: Missing 'video_name' in inputs/config/config.json")
    if not lang_raw:
        raise RuntimeError("ERROR: Missing 'language' in inputs/config/config.json")
    
    video_name_json = sanitize_for_windows_folder(strip_video_prefix(video_name_raw))
    lang_cap = normalize_lang_cap(lang_raw)
    
    if not video_name_json:
        raise RuntimeError("ERROR: VideoName became empty after prefix removal/sanitizing.")
    if not lang_cap:
        raise RuntimeError("ERROR: LanguageCap is empty after normalization.")

    # OUTPUT_FILE = Path("output/JSON/A13__titles_segments__.json")
    OUTPUT_FILE = PROJECT_ROOT / "output" / "JSON" / "A13__titles_segments__.json"
    OUTPUT_FILE_WIN = str(OUTPUT_FILE)

    IMAGES_BASE_DIR_WIN = build_images_base_dir_win(video_name_json)

    # Ensure OUTPUT folder exists
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    # --------------------------------

    # Show where we're checking images (helps debug)
    check_dir = win_dir_to_check_path(IMAGES_BASE_DIR_WIN)
    print(f"[INFO] VideoNameFromJson: {video_name_json}")
    print(f"[INFO] LanguageCap: {lang_cap}")
    print(f"[INFO] OUTPUT_FILE: {OUTPUT_FILE}")
    print(f"[INFO] IMAGES_BASE_DIR_WIN: {IMAGES_BASE_DIR_WIN}")
    print(f"[INFO] Image existence check dir: {check_dir}")

    if not check_dir.exists():
        print("[WARN] That check directory does not exist on this machine.")
        print("       If you are on WSL, confirm your drive is mounted under /mnt/<drive>/ ...")

    try:
        client = init_deepseek_client()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    # ONLY TITLES
    titles = [it for it in items if (isinstance(it, dict) and it.get("type") == "Title")]

    total = len(titles)
    if total == 0:
        print("No Title items found (type == 'Title'). Writing empty output JSON(s) and exiting.")
    
        merged = []
    
        # Always write the normal OUTPUT_FILE (so pipeline doesn't break)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT_FILE.open("w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    
        # Also write the project Titles JSON (Z:\...\Titles\__titles_segments__.json)
        # (safe: won't crash if Z: not mounted)
        try:
            secondary_win = build_output_file_win(video_name_json, lang_cap)
            secondary_os  = win_file_to_os_path(secondary_win)
    
            secondary_os.parent.mkdir(parents=True, exist_ok=True)
            with secondary_os.open("w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
    
            print(f"[INFO] Wrote empty project Titles JSON to: {secondary_win}")
        except Exception as e:
            print(f"[WARN] Could not write empty project Titles JSON: {e}")
    
        return
        

    merged = []
    start_time = time.time()
    print(f"\nStarting processing of {total} titles (auto-detecting image extension)...\n")

    missing_images = 0

    for idx, title in enumerate(titles, start=1):
        tid = title.get("id")
        original_text = title.get("exact_script_text") or ""

        fmt = format_title_with_deepseek(client, original_text)

        image_name, image_path = find_existing_image_for_title_index(idx)
        if not image_name:
            missing_images += 1

        merged.append({
            "id": tid,
            "text": fmt["text"],
            "number": fmt["number"],  # always present ("" if missing)
            "body": fmt["body"],      # always present
            "start_sec": title.get("start_sec"),
            "end_sec": title.get("end_sec"),
            "start_frame": title.get("start_frame"),
            "end_frame": title.get("end_frame"),
            "duration_sec": title.get("duration_sec"),
            "duration_frames": title.get("duration_frames"),
            "image_name": image_name,
            "image_path": image_path,
        })

        elapsed = time.time() - start_time
        pct = (idx / total) * 100
        avg_per_item = elapsed / idx
        est_total = avg_per_item * total
        remaining = max(est_total - elapsed, 0.0)

        print(
            f"[{idx}/{total}] {pct:6.2f}% done | "
            f"elapsed: {elapsed:6.1f}s | "
            f"est total: {est_total:6.1f}s | "
            f"remaining: {remaining:6.1f}s | "
            f"missing_images: {missing_images}"
        )

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    total_elapsed = time.time() - start_time
    print("\n===================================")
    print(f"Wrote {len(merged)} title entries into {OUTPUT_FILE_WIN}")
    print(f"(Disk path used: {OUTPUT_FILE})")
    print(f"Missing images: {missing_images} (image_name/image_path left empty)")
    print(f"Total time: {total_elapsed:.1f} seconds")
    print("===================================")


if __name__ == "__main__":
    main()
