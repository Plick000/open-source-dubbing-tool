#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

# =========================
# FILES (same folder as script)
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FINAL_TITLES_FILE   = PROJECT_ROOT / "output" / "JSON" / "A12__final_segments__.json"
LT_LIST_FILE        = PROJECT_ROOT / "output" / "JSON" / "A17__extracted_lowerthirds__.json"
ULTRA_LIST_FILE     = PROJECT_ROOT / "output" / "JSON" / "A26__extracted_ultra_texts__.json"
ES_CHUNKS_FILE      = PROJECT_ROOT / "output" / "JSON" / "A3__dubbing__.json"
FINAL_SEQ_FILE      = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"

OUT_FILE_LT         = PROJECT_ROOT / "output" / "JSON" / "A18__computed_lowerthirds__.json"
OUT_FILE_ULTRA      = PROJECT_ROOT / "output" / "JSON" / "A27__computed_ultra_texts__.json"

# =========================
# SETTINGS
# =========================
DEFAULT_FPS         = 23.976
START_PAD_FRAMES    = 10
END_PAD_FRAMES      = 10
CHUNK_INDEX_TO_USE  = 0   # if source_chunk_numbers_dubbed has multiple, pick first

# Character-based wrapping settings
LT_CHAR_LIMIT       = 20
ULTRA_CHAR_LIMIT    = 25
ULTRA_NEXT_WORD_MAX = 5

# Ultra tracks must be processed in this exact order
ULTRA_TRACKS_IN_ORDER = ["V5", "V6"]

# =========================
# Helpers
# =========================
def die(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}\n")

def read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"File not found: {p}\nPut all JSON files in the SAME folder as this script.")
    except json.JSONDecodeError as e:
        die(f"Invalid JSON in {p}: {e}")

def write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def to_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None

def to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def normalize_type(it: Dict[str, Any]) -> str:
    raw = it.get("type")
    if raw is None:
        return "normal"
    t = str(raw).strip().lower()
    if t in ("", "none", "null", "unknown"):
        return "normal"
    return t

def normalize_label_raw(it: Dict[str, Any]) -> str:
    raw = it.get("label_raw")
    if raw is None:
        return "Iris"
    s = str(raw).strip()
    return s if s else "Iris"

def seconds_from_frames(fr: int, fps: float) -> float:
    return fr / fps

def frames_from_seconds(sec: float, fps: float) -> int:
    return int(round(sec * fps))

def norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ'-]", "", s, flags=re.UNICODE)
    return s

def norm_track(track: Any) -> str:
    return str(track or "").strip().upper()

def source_match_text(src: Dict[str, Any]) -> str:
    return (src.get("exact_script_text") or src.get("text_translated") or src.get("text") or "").strip()

def source_output_text(src: Dict[str, Any], fallback: str = "") -> str:
    return (src.get("text_translated") or src.get("text") or fallback or "").strip()

def is_empty_sentence_item(it: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(it, dict):
        return False
    return str(it.get("sentenceText") or "").strip().lower() == "empty"

def item_uid(it: Dict[str, Any]) -> str:
    _id = it.get("id")
    if _id is not None:
        return f"id:{_id}"
    return f"py:{id(it)}"

def index_by_id(items: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        _id = to_int(it.get("id"))
        if _id is None:
            continue
        out[_id] = it
    return out

def get_item_fps(it: Dict[str, Any], fallback: float) -> float:
    fps = to_float(it.get("fps"))
    return fps if fps and fps > 0 else fallback

def get_start_end_seconds(it: Dict[str, Any], fps_fallback: float) -> Optional[Tuple[float, float]]:
    fps = get_item_fps(it, fps_fallback)

    s_sec = to_float(it.get("start_time_seconds"))
    e_sec = to_float(it.get("end_time_seconds"))
    if s_sec is not None and e_sec is not None:
        return float(s_sec), float(e_sec)

    s_fr = to_int(it.get("start_frame"))
    e_fr = to_int(it.get("end_frame"))
    if s_fr is None or e_fr is None:
        return None

    return seconds_from_frames(s_fr, fps), seconds_from_frames(e_fr, fps)

def ultra_track_sort_key(it: Dict[str, Any]) -> Tuple[int, float]:
    track = str(it.get("track") or "").strip().upper()
    try:
        track_index = ULTRA_TRACKS_IN_ORDER.index(track)
    except ValueError:
        track_index = len(ULTRA_TRACKS_IN_ORDER)

    start_sec = to_float(it.get("start_sec"))
    if start_sec is None:
        start_sec = 0.0

    return (track_index, float(start_sec))

# =========================
# Character-based wrapping
# =========================
def tokenize_words(text: str) -> List[str]:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return []
    return s.split(" ")

def join_words(words: List[str]) -> str:
    return " ".join(words)

def wrap_text_lowerthird(text: str, char_limit: int = LT_CHAR_LIMIT) -> str:
    words = tokenize_words(text)
    if not words:
        return ""

    lines: List[str] = []
    current: List[str] = []

    for word in words:
        candidate = join_words(current + [word]) if current else word
        current = candidate.split(" ")

        if len(join_words(current)) >= char_limit:
            lines.append(join_words(current))
            current = []

    if current:
        lines.append(join_words(current))

    return "\n".join(lines)

def wrap_text_ultra(
    text: str,
    char_limit: int = ULTRA_CHAR_LIMIT,
    next_word_max: int = ULTRA_NEXT_WORD_MAX
) -> str:
    words = tokenize_words(text)
    if not words:
        return ""

    lines: List[str] = []
    current: List[str] = []

    for word in words:
        current_text = join_words(current)
        current_len = len(current_text)

        if current:
            next_start_len = current_len + 1

            if current_len >= char_limit:
                lines.append(current_text)
                current = []
                current_text = ""
                current_len = 0
            else:
                if next_start_len >= char_limit and len(word) > next_word_max:
                    lines.append(current_text)
                    current = []
                    current_text = ""
                    current_len = 0

        if current:
            current.append(word)
        else:
            current = [word]

    if current:
        lines.append(join_words(current))

    return "\n".join(lines)

def wrap_output_text(text: str, mode: str) -> str:
    s = (text or "").strip()
    if not s:
        return s

    mode = str(mode or "").strip().lower()
    if mode == "ultra":
        return wrap_text_ultra(s, ULTRA_CHAR_LIMIT, ULTRA_NEXT_WORD_MAX)

    return wrap_text_lowerthird(s, LT_CHAR_LIMIT)

# =========================
# Source indexing
# =========================
def build_source_indexes(
    source_items: List[Dict[str, Any]]
) -> Tuple[
    Dict[str, List[Dict[str, Any]]],
    Dict[Tuple[str, str], List[Dict[str, Any]]],
    Dict[str, List[Dict[str, Any]]],
    List[Dict[str, Any]]
]:
    """
    Build source indexes from FINAL TITLES items.

    Returns:
      1) text_global_buckets
      2) text_track_buckets
      3) blank_track_buckets
      4) blank_global_list
    """
    text_global: Dict[str, List[Dict[str, Any]]] = {}
    text_track: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    blank_track: Dict[str, List[Dict[str, Any]]] = {}
    blank_global: List[Dict[str, Any]] = []

    for src in source_items:
        if not isinstance(src, dict):
            continue

        track = norm_track(src.get("track"))
        key_text = source_match_text(src)

        if key_text:
            text_key = norm_text(key_text)
            if not text_key:
                continue

            text_global.setdefault(text_key, []).append(src)
            if track:
                text_track.setdefault((track, text_key), []).append(src)
        else:
            blank_global.append(src)
            if track:
                blank_track.setdefault(track, []).append(src)

    return text_global, text_track, blank_track, blank_global

def pick_next_unconsumed_source(
    candidates: List[Dict[str, Any]],
    consumed_source_uids: Set[str]
) -> Optional[Dict[str, Any]]:
    for src in candidates:
        uid = item_uid(src)
        if uid in consumed_source_uids:
            continue
        consumed_source_uids.add(uid)
        return src
    return None

def pick_matching_source_for_extracted(
    extracted_item: Dict[str, Any],
    text_global: Dict[str, List[Dict[str, Any]]],
    text_track: Dict[Tuple[str, str], List[Dict[str, Any]]],
    blank_track: Dict[str, List[Dict[str, Any]]],
    blank_global: List[Dict[str, Any]],
    consumed_source_uids: Set[str]
) -> Optional[Dict[str, Any]]:
    """
    Match extracted item to next source item.
    Normal text items match by extracted sentenceText.
    Empty items try blank source item first, but can still output even if none found.
    """
    ext_track = norm_track(extracted_item.get("track"))

    if is_empty_sentence_item(extracted_item):
        if ext_track:
            picked = pick_next_unconsumed_source(blank_track.get(ext_track, []), consumed_source_uids)
            if picked is not None:
                return picked
        return pick_next_unconsumed_source(blank_global, consumed_source_uids)

    key_text = str(extracted_item.get("sentenceText") or "").strip()
    if not key_text:
        return None

    text_key = norm_text(key_text)
    if not text_key:
        return None

    if ext_track:
        picked = pick_next_unconsumed_source(text_track.get((ext_track, text_key), []), consumed_source_uids)
        if picked is not None:
            return picked

    return pick_next_unconsumed_source(text_global.get(text_key, []), consumed_source_uids)

# =========================
# Core computation
# =========================
def build_computed_output(
    *,
    final_titles: List[Dict[str, Any]],
    extracted_list: List[Dict[str, Any]],
    es_chunks: List[Dict[str, Any]],
    final_seq: List[Dict[str, Any]],
    mode: str
) -> List[Dict[str, Any]]:
    """
    IMPORTANT:
    Output is driven by EXTRACTED LIST ORDER.
    This guarantees all extracted items, including all 'empty' rows,
    get their own output row.
    """

    es_by_id = index_by_id([x for x in es_chunks if isinstance(x, dict)])

    normal_seq: List[Dict[str, Any]] = []
    for x in final_seq:
        if not isinstance(x, dict):
            continue
        if normalize_type(x) != "normal":
            continue

        if x.get("label_raw") is None or (isinstance(x.get("label_raw"), str) and not x.get("label_raw").strip()):
            x["label_raw"] = "Iris"

        normal_seq.append(x)

    if mode == "ultra":
        source_items = [
            x for x in final_titles
            if isinstance(x, dict) and str(x.get("track") or "").strip().upper() in ULTRA_TRACKS_IN_ORDER
        ]
        source_items.sort(key=ultra_track_sort_key)
    else:
        source_items = [
            x for x in final_titles
            if isinstance(x, dict) and str(x.get("type", "")).strip().lower() in ["lowerthird", "lower third", "lower_third"]
        ]

    text_global, text_track, blank_track, blank_global = build_source_indexes(source_items)
    consumed_source_uids: Set[str] = set()

    out: List[Dict[str, Any]] = []
    new_id = 1

    for ext in extracted_list:
        if not isinstance(ext, dict):
            continue

        seq_fps = DEFAULT_FPS
        ext_is_empty = is_empty_sentence_item(ext)
        matched_source = pick_matching_source_for_extracted(
            extracted_item=ext,
            text_global=text_global,
            text_track=text_track,
            blank_track=blank_track,
            blank_global=blank_global,
            consumed_source_uids=consumed_source_uids
        )

        # -------------------------
        # EMPTY extracted item
        # -------------------------
        if ext_is_empty:
            N: Optional[int] = None

            if matched_source:
                match = matched_source.get("match") or {}
                nums = match.get("source_chunk_numbers_dubbed")
                if isinstance(nums, list) and nums and CHUNK_INDEX_TO_USE < len(nums):
                    N = to_int(nums[CHUNK_INDEX_TO_USE])

            row = {
                "id": new_id,
                "sentenceText": "empty",
                "Final LT Timeline Start seconds": None,
                "Final LT Timeline End seconds": None,
                "duration_seconds": None,
                "Final LT Timeline Start Frame": None,
                "Final LT Timeline End Frame": None,
                "duration_frames": None,
                "fps": seq_fps,
                "N": N,
                "track": ext.get("track")
            }

            out.append(row)
            new_id += 1
            continue

        # -------------------------
        # NORMAL extracted item
        # -------------------------
        if not matched_source:
            # No matched source item. Skip rather than invent timestamps.
            continue

        match = matched_source.get("match") or {}
        nums = match.get("source_chunk_numbers_dubbed")
        if not isinstance(nums, list) or not nums:
            continue
        if CHUNK_INDEX_TO_USE >= len(nums):
            continue

        N = to_int(nums[CHUNK_INDEX_TO_USE])
        if N is None or N <= 0:
            continue

        es = es_by_id.get(N)
        if not es:
            continue

        # Timing calculation stays anchored to EXTRACTED item + ES chunk
        word_times = get_start_end_seconds(ext, DEFAULT_FPS)
        es_times = get_start_end_seconds(es, DEFAULT_FPS)
        if not word_times or not es_times:
            continue

        word_start_sec, word_end_sec = word_times
        es_start_sec, es_end_sec = es_times

        start_offset_sec = word_start_sec - es_start_sec
        end_offset_sec   = word_end_sec   - es_end_sec

        idx = N - 1
        if idx < 0 or idx >= len(normal_seq):
            continue

        seq_item = normal_seq[idx]

        seq_start_sec = to_float(seq_item.get("seqeunce_start_time_sec"))
        seq_end_sec   = to_float(seq_item.get("seqeunce_end_time_sec"))

        if seq_start_sec is None or seq_end_sec is None:
            seq_start_fr = to_int(seq_item.get("seqeunce_start_frames"))
            seq_end_fr   = to_int(seq_item.get("seqeunce_end_frames"))
            if seq_start_fr is None or seq_end_fr is None:
                continue
            seq_start_sec = seconds_from_frames(seq_start_fr, seq_fps)
            seq_end_sec   = seconds_from_frames(seq_end_fr, seq_fps)

        start_pad_sec = START_PAD_FRAMES / seq_fps
        end_pad_sec   = END_PAD_FRAMES / seq_fps

        final_start_sec = (seq_start_sec + start_offset_sec) - start_pad_sec
        final_end_sec   = (seq_end_sec   + end_offset_sec)   + end_pad_sec

        final_start_fr = frames_from_seconds(final_start_sec, seq_fps)
        final_end_fr   = frames_from_seconds(final_end_sec, seq_fps)

        if final_end_fr <= final_start_fr:
            continue

        raw_sentence = source_output_text(matched_source, str(ext.get("sentenceText") or "").strip())
        wrapped_sentence = wrap_output_text(raw_sentence, mode)

        row = {
            "id": new_id,
            "sentenceText": wrapped_sentence,
            "Final LT Timeline Start seconds": round(final_start_sec, 4),
            "Final LT Timeline End seconds": round(final_end_sec, 4),
            "duration_seconds": round(final_end_sec - final_start_sec, 4),
            "Final LT Timeline Start Frame": final_start_fr,
            "Final LT Timeline End Frame": final_end_fr,
            "duration_frames": final_end_fr - final_start_fr,
            "fps": seq_fps,
            "N": N,
            "track": ext.get("track")
        }

        out.append(row)
        new_id += 1

    return out

# =========================
# Main
# =========================
def main() -> None:
    final_titles = read_json(FINAL_TITLES_FILE)
    lt_list      = read_json(LT_LIST_FILE)
    ultra_list   = read_json(ULTRA_LIST_FILE)
    es_chunks    = read_json(ES_CHUNKS_FILE)
    final_seq    = read_json(FINAL_SEQ_FILE)

    if not isinstance(final_titles, list):
        die(f"{FINAL_TITLES_FILE} must be a JSON list.")
    if not isinstance(lt_list, list):
        die(f"{LT_LIST_FILE} must be a JSON list.")
    if not isinstance(ultra_list, list):
        die(f"{ULTRA_LIST_FILE} must be a JSON list.")
    if not isinstance(es_chunks, list):
        die(f"{ES_CHUNKS_FILE} must be a JSON list.")
    if not isinstance(final_seq, list):
        die(f"{FINAL_SEQ_FILE} must be a JSON list.")

    lt_out = build_computed_output(
        final_titles=final_titles,
        extracted_list=lt_list,
        es_chunks=es_chunks,
        final_seq=final_seq,
        mode="lowerthird"
    )

    ultra_out = build_computed_output(
        final_titles=final_titles,
        extracted_list=ultra_list,
        es_chunks=es_chunks,
        final_seq=final_seq,
        mode="ultra"
    )

    write_json(OUT_FILE_LT, lt_out)
    write_json(OUT_FILE_ULTRA, ultra_out)

    print("\n==============================")
    print("✅ DONE (Computed LowerThirds + Ultra Texts)")
    print(f"Matched LowerThirds: {len(lt_out)}")
    print(f"Saved:              {OUT_FILE_LT}")
    print(f"Matched UltraTexts: {len(ultra_out)}")
    print(f"Saved:              {OUT_FILE_ULTRA}")
    print(f"Ultra Tracks Order: {', '.join(ULTRA_TRACKS_IN_ORDER)}")
    print("==============================\n")

if __name__ == "__main__":
    main()