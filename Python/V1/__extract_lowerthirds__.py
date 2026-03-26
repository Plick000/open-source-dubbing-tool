import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# -------------------------
# CONFIG
# -------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FINAL_TITLES_FILE = PROJECT_ROOT / "output" / "JSON" / "A12__final_segments__.json"
ALIGNMENT_FILE = PROJECT_ROOT / "inputs" / "audios" / "__dubbed__audio__alignments_output__.json"

OUTPUT_FILE_LOWERTHIRDS = PROJECT_ROOT / "output" / "JSON" / "A17__extracted_lowerthirds__.json"
OUTPUT_FILE_ULTRA = PROJECT_ROOT / "output" / "JSON" / "A26__extracted_ultra_texts__.json"

SCRIPT_FILE = PROJECT_ROOT / "inputs" / "script.txt"

FPS = 23.976

ULTRA_TRACKS_IN_ORDER = ["V5", "V6"]


# -------------------------
# Helpers
# -------------------------

def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def normalize_char_keep_len(ch: str) -> str:
    replacements = {
        "\u2018": "'", "\u2019": "'",
        "\u201C": '"', "\u201D": '"',
        "\u2013": "-", "\u2014": "-",
        "\u00A0": " ",
        "\ufeff": "",
    }
    ch = replacements.get(ch, ch)
    decomp = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomp if not unicodedata.combining(c))
    return base[0] if base else ch


def normalize_text_keep_len(s: str) -> str:
    return "".join(normalize_char_keep_len(c) for c in (s or ""))


def frames_from_seconds(sec: float, fps: float) -> int:
    return int(round(sec * fps))


def resolve_input_path(primary: Path, local_fallback_name: str) -> Path:
    if primary.exists():
        return primary

    local_candidate = Path(__file__).resolve().parent / local_fallback_name
    if local_candidate.exists():
        return local_candidate

    return primary


def extract_alignment_payload(raw: Any) -> Dict[str, Any]:
    """
    Supports both:
      1) {"characters": ..., "character_start_times_seconds": ...}
      2) [{"alignment": {"characters": ..., ...}}]
      3) {"alignment": {"characters": ..., ...}}
    """
    if isinstance(raw, list):
        if not raw:
            raise ValueError("alignment JSON list is empty")
        first = raw[0]
        if isinstance(first, dict) and isinstance(first.get("alignment"), dict):
            return first["alignment"]
        raise ValueError("alignment JSON list must contain an object with an 'alignment' key")

    if isinstance(raw, dict) and isinstance(raw.get("alignment"), dict):
        return raw["alignment"]

    if isinstance(raw, dict):
        return raw

    raise ValueError("unsupported alignment JSON structure")


def is_searchable_char(ch: str) -> bool:
    ch = normalize_char_keep_len(ch)
    if not ch:
        return False
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat.startswith("N")


def build_compact_index(text: str) -> Tuple[str, List[int]]:
    """
    Builds a punctuation-insensitive, whitespace-insensitive search string while
    preserving a map back to the original character indices.
    """
    compact_chars: List[str] = []
    original_indices: List[int] = []

    for i, ch in enumerate(text or ""):
        base = normalize_char_keep_len(ch)
        if not base:
            continue
        if not is_searchable_char(base):
            continue
        compact_chars.append(base.casefold())
        original_indices.append(i)

    return "".join(compact_chars), original_indices


def find_all_occurrences(haystack: str, needle: str) -> List[int]:
    if not haystack or not needle:
        return []
    out: List[int] = []
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos == -1:
            break
        out.append(pos)
        start = pos + 1
    return out


def get_bracket_ranges(text: str) -> List[Tuple[int, int]]:
    """
    Returns inclusive ranges for every [...] block.
    Nested '[' before a closing ']' resets the start so malformed content
    still behaves safely.
    """
    ranges: List[Tuple[int, int]] = []
    start_idx: Optional[int] = None

    for i, ch in enumerate(text or ""):
        if ch == "[":
            start_idx = i
        elif ch == "]" and start_idx is not None:
            ranges.append((start_idx, i))
            start_idx = None

    return ranges


def is_index_in_ranges(idx: int, ranges: List[Tuple[int, int]]) -> bool:
    for a, b in ranges:
        if a <= idx <= b:
            return True
    return False


def pick_script_occurrence_rank(
    key_compact: str,
    script_compact: str,
    script_compact_to_orig: List[int],
    bracket_ranges: List[Tuple[int, int]],
    seen_rank_for_key: Dict[str, int],
) -> Optional[int]:
    """
    Returns the occurrence INDEX (in the full occurrences list) that corresponds
    to the next non-title match for this key in the script.

    Special case:
      - If the key exists in the script ONLY inside [...] title blocks,
        returns -1 to signal "do not match from alignment".
    """
    if not key_compact or not script_compact:
        return None

    occs = find_all_occurrences(script_compact, key_compact)
    if not occs:
        return None

    key_len = len(key_compact)

    # store indices into 'occs' that are NOT inside [...]
    valid_occ_indices: List[int] = []

    for idx_in_occs, occ in enumerate(occs):
        start_orig = script_compact_to_orig[occ]
        end_orig = script_compact_to_orig[occ + key_len - 1]

        fully_inside_brackets = (
            is_index_in_ranges(start_orig, bracket_ranges)
            and is_index_in_ranges(end_orig, bracket_ranges)
        )
        if fully_inside_brackets:
            continue

        valid_occ_indices.append(idx_in_occs)

    # Key exists, but only inside titles => block matching entirely
    if not valid_occ_indices:
        return -1

    used = seen_rank_for_key.get(key_compact, 0)
    if used >= len(valid_occ_indices):
        chosen = valid_occ_indices[-1]
    else:
        chosen = valid_occ_indices[used]

    seen_rank_for_key[key_compact] = used + 1
    return chosen


def choose_alignment_occurrence(
    full_compact: str,
    full_compact_to_orig: List[int],
    key_compact: str,
    desired_rank: Optional[int],
    cursor_compact: int,
) -> Optional[Tuple[int, int]]:
    occs = find_all_occurrences(full_compact, key_compact)
    if not occs:
        return None

    key_len = len(key_compact)

    # 1) Use occurrence rank from the script (outside bracketed titles only)
    if desired_rank is not None and desired_rank < len(occs):
        start_compact = occs[desired_rank]
        return start_compact, start_compact + key_len

    # 2) Otherwise use the first chronological match after cursor
    for occ in occs:
        if occ >= cursor_compact:
            return occ, occ + key_len

    # 3) Final fallback: use the first occurrence
    first = occs[0]
    return first, first + key_len


def get_segment_text(seg: Dict[str, Any]) -> str:
    return (
        (seg.get("exact_script_text") or "")
        or (seg.get("text_translated") or "")
        or (seg.get("text_es") or "")
        or (seg.get("text") or "")
    ).strip()


def extract_segments(
    segments: List[Dict[str, Any]],
    full_compact: str,
    full_compact_to_orig: List[int],
    start_times: List[Any],
    end_times: Optional[List[Any]],
    script_compact: str,
    script_compact_to_orig: List[int],
    bracket_ranges: List[Tuple[int, int]],
    alignment_bracket_ranges: Optional[List[Tuple[int, int]]] = None,
    starting_id: int = 1,
    ultra_conflict_map: Optional[Dict[Tuple[int, int], "set[str]"]] = None,
    max_conflict_skips: int = 20,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cursor_compact = 0
    new_id = starting_id
    seen_script_rank_for_key: Dict[str, int] = {}

    # Track how many alignment occurrences we've already used per key,
    # so repeated LowerThird/Ultra with same text doesn't reuse the same hit.
    used_alignment_occ_index_for_key: Dict[str, int] = {}

    for seg in segments:
        key = get_segment_text(seg)

        # RULE #1:
        # Keep items with sentenceText == "empty" (case-insensitive) OR missing/blank/null.
        # Do not search alignments. Set timestamps/frames to null.
        key_stripped = (key or "").strip()
        if (not key_stripped) or (key_stripped.casefold() == "empty"):
            out.append({
                "id": new_id,
                "sentenceText": key if key_stripped else None,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        key_compact, _ = build_compact_index(key)
        if not key_compact:
            # Still keep the item, because it's effectively non-searchable text.
            out.append({
                "id": new_id,
                "sentenceText": key,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        desired_rank: Optional[int] = None
        if script_compact:
            desired_rank = pick_script_occurrence_rank(
                key_compact=key_compact,
                script_compact=script_compact,
                script_compact_to_orig=script_compact_to_orig,
                bracket_ranges=bracket_ranges,
                seen_rank_for_key=seen_script_rank_for_key,
            )

        # If the text only exists inside [...] titles in the script, do NOT match from alignment.
        if desired_rank == -1:
            out.append({
                "id": new_id,
                "sentenceText": key,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        # Find ALL occurrences in alignment compact string
        occs = find_all_occurrences(full_compact, key_compact)
        if not occs:
            # Not found in alignment: keep it, timestamps null, do not search further
            out.append({
                "id": new_id,
                "sentenceText": key,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        # Decide initial occurrence index (preserves existing priority rules)
        occ_index: int = 0

        # 1) Use NON-TITLE occurrence ordinal from the script (titles do NOT shift this)
        if desired_rank is not None and desired_rank >= 0 and desired_rank < len(occs):
            occ_index = desired_rank
        else:
            # 2) Otherwise use the first chronological match after cursor
            found = False
            for i, occ in enumerate(occs):
                if occ >= cursor_compact:
                    occ_index = i
                    found = True
                    break
            # 3) Final fallback: first occurrence (already 0)
            if not found:
                occ_index = 0

        # RULE #2:
        # If the same text comes again, force moving to the next alignment occurrence.
        occ_index = max(occ_index, used_alignment_occ_index_for_key.get(key_compact, 0))

        # If LowerThird lands exactly on an UltraText timing (same frames) AND same text,
        # skip to the next occurrence for LowerThird (so it doesn't steal UltraText).
        skips = 0
        chosen: Optional[Tuple[int, int]] = None
        chosen_occ_index: Optional[int] = None

        while occ_index < len(occs):
            start_compact = occs[occ_index]
            end_compact_excl = start_compact + len(key_compact)

            start_orig_idx = full_compact_to_orig[start_compact]
            end_orig_idx = full_compact_to_orig[end_compact_excl - 1]

            # NEW: Never match occurrences that come from inside [...] in the ALIGNMENT text (titles).
            if alignment_bracket_ranges:
                inside_title = (
                    is_index_in_ranges(start_orig_idx, alignment_bracket_ranges)
                    and is_index_in_ranges(end_orig_idx, alignment_bracket_ranges)
                )
                if inside_title:
                    occ_index += 1
                    continue            

            try:
                start_sec = float(start_times[start_orig_idx])
            except Exception:
                occ_index += 1
                continue

            if end_times is not None:
                try:
                    end_sec = float(end_times[end_orig_idx])
                except Exception:
                    end_sec = float(start_times[end_orig_idx]) + (1.0 / FPS)
            else:
                next_idx = end_orig_idx + 1
                if next_idx < len(start_times):
                    end_sec = float(start_times[next_idx])
                else:
                    end_sec = float(start_times[end_orig_idx]) + (1.0 / FPS)

            if end_sec < start_sec:
                end_sec = start_sec

            start_frame = frames_from_seconds(start_sec, FPS)
            end_frame = frames_from_seconds(end_sec, FPS)

            conflict = False
            if ultra_conflict_map:
                frame_key = (start_frame, end_frame)
                ultra_keys = ultra_conflict_map.get(frame_key)
                if ultra_keys and key_compact in ultra_keys:
                    conflict = True

            if conflict and skips < max_conflict_skips:
                skips += 1
                occ_index += 1
                continue

            chosen = (start_compact, end_compact_excl)
            chosen_occ_index = occ_index
            break

        if chosen is None or chosen_occ_index is None:
            # Could not choose a valid occurrence: keep it with null timestamps
            out.append({
                "id": new_id,
                "sentenceText": key,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        start_compact, end_compact_excl = chosen
        start_orig_idx = full_compact_to_orig[start_compact]
        end_orig_idx = full_compact_to_orig[end_compact_excl - 1]

        try:
            start_sec = float(start_times[start_orig_idx])
        except Exception:
            out.append({
                "id": new_id,
                "sentenceText": key,
                "track": seg.get("track"),
                "start_time_seconds": None,
                "end_time_seconds": None,
                "duration_seconds": None,
                "start_frame": None,
                "end_frame": None,
                "duration_frames": None,
                "fps": FPS,
            })
            new_id += 1
            continue

        if end_times is not None:
            try:
                end_sec = float(end_times[end_orig_idx])
            except Exception:
                end_sec = float(start_times[end_orig_idx]) + (1.0 / FPS)
        else:
            next_idx = end_orig_idx + 1
            if next_idx < len(start_times):
                end_sec = float(start_times[next_idx])
            else:
                end_sec = float(start_times[end_orig_idx]) + (1.0 / FPS)

        if end_sec < start_sec:
            end_sec = start_sec

        start_frame = frames_from_seconds(start_sec, FPS)
        end_frame = frames_from_seconds(end_sec, FPS)

        out.append({
            "id": new_id,
            "sentenceText": key,
            "track": seg.get("track"),
            "start_time_seconds": round(start_sec, 3),
            "end_time_seconds": round(end_sec, 3),
            "duration_seconds": round(end_sec - start_sec, 3),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "duration_frames": max(0, end_frame - start_frame),
            "fps": FPS,
        })

        # Advance: record that this key has consumed this occurrence index, so next time it moves forward.
        used_alignment_occ_index_for_key[key_compact] = max(
            used_alignment_occ_index_for_key.get(key_compact, 0),
            chosen_occ_index + 1
        )

        new_id += 1
        cursor_compact = max(cursor_compact, end_compact_excl)

    return out


def track_sort_key(seg: Dict[str, Any]) -> Tuple[int, float]:
    track = str(seg.get("track") or "").strip().upper()
    try:
        track_index = ULTRA_TRACKS_IN_ORDER.index(track)
    except ValueError:
        track_index = len(ULTRA_TRACKS_IN_ORDER)

    try:
        start_sec = float(seg.get("start_sec", 0.0) or 0.0)
    except Exception:
        start_sec = 0.0

    return (track_index, start_sec)


# -------------------------
# Main
# -------------------------

def main() -> None:
    final_titles_path = resolve_input_path(FINAL_TITLES_FILE, "A12__final_segments__.json")
    alignment_path = resolve_input_path(ALIGNMENT_FILE, "__dubbed__audio__alignments__.json")
    output_path_lowerthirds = resolve_input_path(OUTPUT_FILE_LOWERTHIRDS, "A17__extracted_lowerthirds__.json")
    output_path_ultra = resolve_input_path(OUTPUT_FILE_ULTRA, "A26__extracted_ultra_texts__.json")
    script_path = resolve_input_path(SCRIPT_FILE, "script.txt")

    if not final_titles_path.exists():
        print(f"❌ Missing: {final_titles_path}")
        sys.exit(1)
    if not alignment_path.exists():
        print(f"❌ Missing: {alignment_path}")
        sys.exit(1)

    final_titles = read_json(final_titles_path)
    raw_alignment = read_json(alignment_path)

    if not isinstance(final_titles, list):
        print("❌ final_titles JSON must be a JSON list.")
        sys.exit(1)

    try:
        alignment = extract_alignment_payload(raw_alignment)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    chars = alignment.get("characters")
    start_times = alignment.get("character_start_times_seconds")
    end_times = alignment.get("character_end_times_seconds")

    if not isinstance(chars, list) or not isinstance(start_times, list) or len(chars) != len(start_times):
        print("❌ alignment must have 'characters' and 'character_start_times_seconds' with same length.")
        sys.exit(1)

    if end_times is not None and (not isinstance(end_times, list) or len(end_times) != len(chars)):
        print("❌ alignment 'character_end_times_seconds' must match the characters length when present.")
        sys.exit(1)

    full_text = "".join(str(c) for c in chars)
    full_compact, full_compact_to_orig = build_compact_index(full_text)
    alignment_bracket_ranges = get_bracket_ranges(full_text)

    script_text = ""
    script_compact = ""
    script_compact_to_orig: List[int] = []
    bracket_ranges: List[Tuple[int, int]] = []

    if script_path.exists():
        script_text = read_text(script_path)
        script_compact, script_compact_to_orig = build_compact_index(script_text)
        bracket_ranges = get_bracket_ranges(script_text)

    # Existing output: LowerThird only
    lowerthird_segments = [
        x for x in final_titles
        if isinstance(x, dict) and x.get("type") == "LowerThird"
    ]
    lowerthird_segments.sort(key=lambda x: float(x.get("start_sec", 0.0) or 0.0))

    # Updated ultra output: both V5 and V6 in a single file
    # Order must be: all V5 first, then all V6
    ultra_segments = [
        x for x in final_titles
        if isinstance(x, dict) and str(x.get("track") or "").strip().upper() in ULTRA_TRACKS_IN_ORDER
    ]
    ultra_segments.sort(key=track_sort_key)
    
    ultra_out = extract_segments(
        segments=ultra_segments,
        full_compact=full_compact,
        full_compact_to_orig=full_compact_to_orig,
        start_times=start_times,
        end_times=end_times,
        script_compact=script_compact,
        script_compact_to_orig=script_compact_to_orig,
        bracket_ranges=bracket_ranges,
        alignment_bracket_ranges=alignment_bracket_ranges,
        starting_id=1,
    )

    # Build a quick lookup so LowerThird doesn't "steal" an UltraText match
    # when BOTH share the exact same timing (frames) and same normalized text.
    ultra_conflict_map: Dict[Tuple[int, int], set[str]] = {}
    for u in ultra_out:
        try:
            sk = (int(u.get("start_frame")), int(u.get("end_frame")))
        except Exception:
            continue
        u_text = str(u.get("sentenceText") or "")
        u_key_compact, _ = build_compact_index(u_text)
        if not u_key_compact:
            continue
        ultra_conflict_map.setdefault(sk, set()).add(u_key_compact)

    lowerthird_out = extract_segments(
        segments=lowerthird_segments,
        full_compact=full_compact,
        full_compact_to_orig=full_compact_to_orig,
        start_times=start_times,
        end_times=end_times,
        script_compact=script_compact,
        script_compact_to_orig=script_compact_to_orig,
        bracket_ranges=bracket_ranges,
        alignment_bracket_ranges=alignment_bracket_ranges,
        starting_id=1,
        ultra_conflict_map=ultra_conflict_map,
    )

    write_json(output_path_ultra, ultra_out)
    write_json(output_path_lowerthirds, lowerthird_out)

    print("============================================")
    print("✅ DONE — extracted both outputs")
    print(f"Saved Ultra Texts: {output_path_ultra}")
    print(f"Matched Ultra Texts: {len(ultra_out)}")
    print(f"Saved LowerThirds: {output_path_lowerthirds}")
    print(f"Matched LowerThirds: {len(lowerthird_out)}")
    print(f"Ultra Tracks Order: {', '.join(ULTRA_TRACKS_IN_ORDER)}")
    print("============================================")


if __name__ == "__main__":
    main()