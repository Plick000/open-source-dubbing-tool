#!/usr/bin/env python3
import json
import re
from decimal import Decimal, getcontext
from pathlib import Path

# ============================================================
# ✅ EASY CONFIG (FILENAMES ONLY)
# Put the JSON files in the SAME folder as this .py script
# ============================================================
# =========================
# PATH CONFIG (PROJECT ROOT)
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

GROUPED_JSON  = PROJECT_ROOT / "output" / "JSON" / "A7__segments__.json"
FINAL_JSON    = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"
OUT_ALL_JSON  = PROJECT_ROOT / "output" / "JSON" / "A9__absolute_interviews__.json"

TRACK         = "V1"
REQUIRE_TYPE  = "interview"   # or None
WRITE_PER_INTERVIEW_FILES = False
SEC_DECIMALS  = 4

# ============================================================

def script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()

def load_json(path: Path):
    # Check if the file exists
    if not path.exists():
        print(f"⚠️ Missing file: {path}. Returning empty list.")
        return []  # Return an empty list if file is missing
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dec(x) -> Decimal:
    return Decimal(str(x))

def quantize_seconds(x: Decimal, places: int) -> float:
    q = Decimal("1").scaleb(-places)  # e.g. 4 => 0.0001
    return float(x.quantize(q))

def build_final_index(final_data, track: str, require_type: str | None):
    idx = {}
    for item in final_data:
        if not isinstance(item, dict):
            continue
        if item.get("track") != track:
            continue
        if require_type and item.get("type") != require_type:
            continue
        if "id" not in item:
            continue
        try:
            idx[int(item["id"])] = item
        except Exception:
            continue
    return idx

def _extract_interview_num(interview_id_val) -> int | None:
    """
    Accepts:
      - "interview_2"
      - "2"
      - 2
    Returns int interview number or None.
    """
    if interview_id_val is None:
        return None
    s = str(interview_id_val).strip()
    if not s:
        return None
    m = re.match(r"^interview_(\d+)$", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    return None

def _normalize_grouped_interviews(grouped_data):
    """
    Supports BOTH formats:

    OLD:
      [ { "interview_2": [ ...segments... ] }, ... ]
      where segments have timeline_start_sec / timeline_end_sec / timeline_start_frame / timeline_end_frame

    NEW:
      [ { "id": "interview_2", "segments": [ ... ] }, ... ]
      where segments have start_s / end_s / start_frame / end_frame
    """
    out = []  # list of tuples: (interview_key_str, interview_num, segments_list)

    # Case: not a list -> reject
    if not isinstance(grouped_data, list):
        raise ValueError("A7__segments__.json must be a list.")

    # If it looks like NEW format
    if grouped_data and isinstance(grouped_data[0], dict) and "id" in grouped_data[0] and "segments" in grouped_data[0]:
        for obj in grouped_data:
            if not isinstance(obj, dict):
                continue
            interview_id = obj.get("id")
            interview_num = _extract_interview_num(interview_id)
            if interview_num is None:
                continue
            segs = obj.get("segments")
            if not isinstance(segs, list):
                continue
            out.append((f"interview_{interview_num}", interview_num, segs))
        return out

    # Otherwise assume OLD format
    interview_key_re = re.compile(r"^interview_(\d+)$", re.IGNORECASE)
    for obj in grouped_data:
        if not isinstance(obj, dict):
            continue
        for k, segments in obj.items():
            m = interview_key_re.match(str(k))
            if not m:
                continue
            interview_num = int(m.group(1))
            if not isinstance(segments, list):
                print(f"⚠️ Skipping {k}: value is not a list.")
                continue
            out.append((f"interview_{interview_num}", interview_num, segments))
    return out

def _get_local_times_and_frames(seg: dict):
    """
    Accepts both schemas:
      OLD: timeline_start_sec, timeline_end_sec, timeline_start_frame, timeline_end_frame
      NEW: start_s, end_s, start_frame, end_frame
    """
    # Seconds
    if "timeline_start_sec" in seg or "timeline_end_sec" in seg:
        local_start_sec = dec(seg["timeline_start_sec"])
        local_end_sec   = dec(seg["timeline_end_sec"])
    else:
        local_start_sec = dec(seg["start_s"])
        local_end_sec   = dec(seg["end_s"])

    # Frames
    if "timeline_start_frame" in seg or "timeline_end_frame" in seg:
        local_start_frame = int(seg["timeline_start_frame"])
        local_end_frame   = int(seg["timeline_end_frame"])
    else:
        local_start_frame = int(seg["start_frame"])
        local_end_frame   = int(seg["end_frame"])

    return local_start_sec, local_end_sec, local_start_frame, local_end_frame

def main():
    grouped_path = GROUPED_JSON
    final_path   = FINAL_JSON
    out_all_path = OUT_ALL_JSON

    track        = TRACK
    require_type = REQUIRE_TYPE
    sec_decimals = SEC_DECIMALS

    # Gracefully handle missing files, log and proceed
    grouped_data = load_json(grouped_path)
    final_data   = load_json(final_path)

    # If final_data is not a list, raise an error
    if not isinstance(final_data, list):
        raise ValueError("Final dubbed sequence JSON must be a list of items.")

    # Build the index
    final_index = build_final_index(final_data, track=track, require_type=require_type)

    # Normalize grouped interviews
    interview_groups = _normalize_grouped_interviews(grouped_data)

    out_all = {}

    for interview_key, interview_num, segments in interview_groups:
        seq_item = final_index.get(interview_num)
        if not seq_item:
            print(f"⚠️ Missing in FINAL for {interview_key} (id={interview_num}) on track={track} (type={require_type}). Skipping.")
            continue

        # Processing logic for valid segments
        seq_start_sec    = dec(seq_item["seqeunce_start_time_sec"])
        seq_end_sec      = dec(seq_item["seqeunce_end_time_sec"])
        seq_start_frames = int(seq_item["seqeunce_start_frames"])
        seq_end_frames   = int(seq_item["seqeunce_end_frames"])

        converted = []
        max_end_frame = -1
        max_end_sec = Decimal("0")

        cursor_end_sec = None
        cursor_end_frame = None
        first_valid = True

        for s in segments:
            if not isinstance(s, dict):
                continue

            seg_id = s.get("id")
            seg_text = s.get("text")
            if not seg_id or not isinstance(seg_text, str):
                continue

            try:
                local_start_sec, local_end_sec, local_start_frame, local_end_frame = _get_local_times_and_frames(s)
            except Exception:
                continue

            dur_sec = (local_end_sec - local_start_sec)
            dur_frames = (local_end_frame - local_start_frame)

            if first_valid:
                abs_start_sec   = seq_start_sec + local_start_sec
                abs_start_frame = seq_start_frames + local_start_frame
                first_valid = False
            else:
                abs_start_sec   = cursor_end_sec
                abs_start_frame = cursor_end_frame

            abs_end_sec   = abs_start_sec + dur_sec
            abs_end_frame = abs_start_frame + dur_frames

            cursor_end_sec = abs_end_sec
            cursor_end_frame = abs_end_frame

            max_end_frame = max(max_end_frame, abs_end_frame)
            max_end_sec = max(max_end_sec, abs_end_sec)

            converted.append({
                "id": seg_id,
                "text": seg_text,
                "timeline_start_sec": quantize_seconds(abs_start_sec, sec_decimals),
                "timeline_end_sec": quantize_seconds(abs_end_sec, sec_decimals),
                "timeline_start_frame": abs_start_frame,
                "timeline_end_frame": abs_end_frame,
            })

        if max_end_frame > seq_end_frames:
            print(f"⚠️ WARNING {interview_key}: end frame {max_end_frame} > seqeunce_end_frames {seq_end_frames}")
        if max_end_sec > seq_end_sec:
            print(f"⚠️ WARNING {interview_key}: end sec {float(max_end_sec)} > seqeunce_end_time_sec {float(seq_end_sec)}")

        out_all[interview_key] = converted

        if WRITE_PER_INTERVIEW_FILES:
            per_path = PROJECT_ROOT / "output" / "JSON" / f"{interview_key}_absolute_{track}.json"
            per_path.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write final output even if no valid data
    out_all_path.write_text(json.dumps(out_all, ensure_ascii=False, indent=2), encoding="utf-8")

    print("============================================")
    print("✔ DONE")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Track: {track}")
    print(f"Input grouped: {grouped_path}")
    print(f"All interviews output: {out_all_path}")
    print(f"Total interviews converted: {len(out_all)}")
    print("============================================")


if __name__ == "__main__":
    getcontext().prec = 28
    main()
