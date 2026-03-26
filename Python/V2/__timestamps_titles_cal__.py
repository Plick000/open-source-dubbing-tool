#!/usr/bin/env python3
"""
Extract Final Dubbed Sequence items that correspond to AE TITLES (V3/V4 timestamps),
using FUZZY matching against clips_output.json (because start frames can be off by a few frames).

New matching strategy (per your requirement):
1) Prefer matching by END frame (exact or within MAX_END_DELTA),
2) Then allow START frame mismatch within +/- MAX_START_DELTA (default 5 frames),
3) If still not found, fallback to RANGE/OVERLAP matching and pick best by scoring.

Expected files (in same folder):
- V3_V4_clips_timestamps.json
- clips_output.json
- Final_Dubbed_Sequence.json

Outputs:
- ae_titles__final_dubbed_subset.json
- ae_titles__match_report.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -------------------------
# EASY VARIABLES (edit these)
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

V3V4_TIMESTAMPS_FILENAME    = PROJECT_ROOT / "output" / "JSON" / "A15__V3_V4_clips_timestamps__.json"
CLIPS_OUTPUT_FILENAME       = PROJECT_ROOT / "output" / "JSON" / "A2__dubbing__.json"
FINAL_DUBBED_FILENAME       = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"

OUT_FINAL_ONLY_FILENAME     = PROJECT_ROOT / "output" / "JSON" / "A16__final_titles_segments__.json"
OUT_REPORT_FILENAME         = PROJECT_ROOT / "output" / "Reports" / "JSON" / "Titles" / "__final_titles_segments_report__.json"

# V3V4_TIMESTAMPS_FILENAME = "output/JSON/A15__V3_V4_clips_timestamps__.json"
# CLIPS_OUTPUT_FILENAME    = "output/JSON/A2__dubbing__.json"
# FINAL_DUBBED_FILENAME    = "output/JSON/A4__dubbing__.json"

# OUT_FINAL_ONLY_FILENAME  = "output/JSON/A16__final_titles_segments__.json"
# OUT_REPORT_FILENAME      = "output/Reports/JSON/Titles/__final_titles_segments_report__.json"

# If True: exclude "Lowerthird" AE titles (recommended)
SKIP_LOWERTHIRD = True
NEGATIVE_BUFFER_FRAME = 3

# ---- NEW FUZZY SETTINGS ----
# Allow AE start vs clips_output start mismatch by up to N frames (both directions).
MAX_START_DELTA = 5

# Allow AE end vs clips_output end mismatch by up to N frames (usually 0).
MAX_END_DELTA = 0

# Fallback overlap requirement (frames). If 0, any overlap counts.
MIN_OVERLAP_FRAMES = 1

# When scoring candidates, prefer V1 + normal
PREFER_TRACK = "V1"
PREFER_TYPE  = "normal"


# -------------------------
# Helpers
# -------------------------
def die(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}\n")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die(f"File not found: {path}\nPut it in the SAME folder as this script, or change the filename variables.")
    except json.JSONDecodeError as e:
        die(f"Invalid JSON in {path}: {e}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)   # ✅ create folders
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_real_title_card(v: Dict[str, Any]) -> bool:
    """
    Decide if an entry from V3_V4_clips_timestamps.json is a TITLE card (keep),
    or a lowerthird (skip) etc.
    """
    if v.get("type") != "ae_title":
        return False

    name = str(v.get("name", "")).strip().lower()
    track = str(v.get("track", "")).strip().upper()

    if SKIP_LOWERTHIRD and name == "lowerthird":
        return False

    # Keep "Title" OR track V3 (your current dataset)
    if name == "title":
        return True
    if track == "V3":
        return True

    return False


def safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return default


def build_index_by_end_frame(items: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    idx: Dict[int, List[Dict[str, Any]]] = {}
    for it in items:
        endf = safe_int(it.get("eng_timeline_end_frames"))
        if endf is None:
            continue
        idx.setdefault(endf, []).append(it)
    return idx


def overlap_frames(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """
    Inclusive/exclusive doesn't matter much for matching; treat as [start, end].
    """
    left = max(a_start, b_start)
    right = min(a_end, b_end)
    return max(0, right - left)


def preference_bonus(it: Dict[str, Any]) -> int:
    """
    Higher is better.
    """
    bonus = 0
    if str(it.get("track", "")).upper() == str(PREFER_TRACK).upper():
        bonus += 50
    if str(it.get("type", "")).lower() == str(PREFER_TYPE).lower():
        bonus += 20
    return bonus


def score_candidate(
    cand: Dict[str, Any],
    want_start: int,
    want_end: int,
    want_dur: int
) -> Tuple[int, int, int, int]:
    """
    Lower is better in tuple ordering.

    Priority:
      1) end_diff (very strong)
      2) start_diff
      3) duration_diff
      4) negative bonus (so higher bonus becomes "smaller"/better)
    """
    c_start = safe_int(cand.get("eng_timeline_start_frames"), 10**18) or 10**18
    c_end   = safe_int(cand.get("eng_timeline_end_frames"),   -10**18) or -10**18
    c_dur   = safe_int(cand.get("eng_duration_frames"), 0) or 0

    end_diff = abs(c_end - want_end)
    start_diff = abs(c_start - want_start)
    dur_diff = abs(c_dur - want_dur)

    # End diff dominates heavily
    return (end_diff, start_diff, dur_diff, -preference_bonus(cand))


def gather_end_candidates(
    clips_by_end: Dict[int, List[Dict[str, Any]]],
    want_end: int,
    max_end_delta: int
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in range(-max_end_delta, max_end_delta + 1):
        out.extend(clips_by_end.get(want_end + d, []))
    return out


def find_best_match_for_title(
    title_start: int,
    title_end: int,
    title_dur: int,
    clips_by_end: Dict[int, List[Dict[str, Any]]],
    all_clips: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    """
    Returns: (best_clip_or_None, match_method, debug_info)

    match_method:
      - "end+start_within_delta"
      - "end+overlap_fallback"
      - "global_best_overlap_fallback"
      - "none"
    """
    debug: Dict[str, Any] = {}

    # 1) strict-ish: end within delta + start within delta
    end_cands = gather_end_candidates(clips_by_end, title_end, MAX_END_DELTA)
    strict = []
    for c in end_cands:
        c_start = safe_int(c.get("eng_timeline_start_frames"))
        c_end   = safe_int(c.get("eng_timeline_end_frames"))
        if c_start is None or c_end is None:
            continue
        if abs(c_start - title_start) <= MAX_START_DELTA and abs(c_end - title_end) <= MAX_END_DELTA:
            strict.append(c)

    if strict:
        best = sorted(strict, key=lambda x: score_candidate(x, title_start, title_end, title_dur))[0]
        debug["candidates_count"] = len(strict)
        return best, "end+start_within_delta", debug

    # 2) fallback: end within delta + overlap range
    overlap_ok = []
    for c in end_cands:
        c_start = safe_int(c.get("eng_timeline_start_frames"))
        c_end   = safe_int(c.get("eng_timeline_end_frames"))
        if c_start is None or c_end is None:
            continue
        ov = overlap_frames(title_start, title_end, c_start, c_end)
        if ov >= MIN_OVERLAP_FRAMES:
            overlap_ok.append(c)

    if overlap_ok:
        best = sorted(overlap_ok, key=lambda x: score_candidate(x, title_start, title_end, title_dur))[0]
        debug["candidates_count"] = len(overlap_ok)
        return best, "end+overlap_fallback", debug

    # 3) last fallback: global overlap search (in case end indexing fails)
    global_overlap = []
    for c in all_clips:
        c_start = safe_int(c.get("eng_timeline_start_frames"))
        c_end   = safe_int(c.get("eng_timeline_end_frames"))
        if c_start is None or c_end is None:
            continue
        ov = overlap_frames(title_start, title_end, c_start, c_end)
        if ov >= MIN_OVERLAP_FRAMES:
            global_overlap.append(c)

    if global_overlap:
        best = sorted(global_overlap, key=lambda x: score_candidate(x, title_start, title_end, title_dur))[0]
        debug["candidates_count"] = len(global_overlap)
        return best, "global_best_overlap_fallback", debug

    return None, "none", debug


# -------------------------
# Main
# -------------------------
def main() -> None:
    base = Path(__file__).resolve().parent

    v3v4_path  = base / V3V4_TIMESTAMPS_FILENAME
    clips_path = base / CLIPS_OUTPUT_FILENAME
    final_path = base / FINAL_DUBBED_FILENAME

    v3v4 = read_json(v3v4_path)
    clips_output = read_json(clips_path)
    final_dubbed = read_json(final_path)

    if not isinstance(v3v4, list):
        die(f"{V3V4_TIMESTAMPS_FILENAME} must be a JSON list.")
    if not isinstance(clips_output, list):
        die(f"{CLIPS_OUTPUT_FILENAME} must be a JSON list.")
    if not isinstance(final_dubbed, list):
        die(f"{FINAL_DUBBED_FILENAME} must be a JSON list.")

    # Filter v3v4 to only real title cards
    title_cards = [x for x in v3v4 if isinstance(x, dict) and is_real_title_card(x)]

    # Index clips_output by end frame (more reliable for you)
    clips_by_end = build_index_by_end_frame(clips_output)

    # Build final_dubbed index by id
    final_by_id: Dict[int, Dict[str, Any]] = {}
    for it in final_dubbed:
        if not isinstance(it, dict):
            continue
        _id = safe_int(it.get("id"))
        if _id is not None:
            final_by_id[_id] = it

    matches: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    missing_in_clips: List[Dict[str, Any]] = []
    missing_in_final: List[Dict[str, Any]] = []

    for t in title_cards:
        start_f = safe_int(t.get("eng_timeline_start_frames"))
        end_f   = safe_int(t.get("eng_timeline_end_frames"))
        dur_f   = safe_int(t.get("eng_duration_frames"))

        if start_f is None or end_f is None:
            skipped.append({"reason": "missing_start_or_end", "v3v4": t})
            continue

        if dur_f is None:
            # compute from range if missing
            dur_f = max(0, end_f - start_f)

        picked, method, dbg = find_best_match_for_title(
            title_start=start_f,
            title_end=end_f,
            title_dur=dur_f,
            clips_by_end=clips_by_end,
            all_clips=clips_output,
        )

        if not picked:
            missing_in_clips.append({
                "reason": "no_match_in_clips_output",
                "match_method": method,
                "eng_timeline_start_frames": start_f,
                "eng_timeline_end_frames": end_f,
                "v3v4": t,
                "debug": dbg,
            })
            continue

        clip_id = safe_int(picked.get("id"))
        if clip_id is None:
            skipped.append({"reason": "matched_clip_missing_id", "v3v4": t, "clips_output": picked})
            continue

        final_item = final_by_id.get(clip_id)
        if not final_item:
            missing_in_final.append({
                "reason": "id_not_found_in_final_dubbed_sequence",
                "id": clip_id,
                "match_method": method,
                "v3v4": t,
                "clips_output": picked,
                "debug": dbg,
            })
            continue

        # diffs / overlap info (for report)
        c_start = safe_int(picked.get("eng_timeline_start_frames"), start_f) or start_f
        c_end   = safe_int(picked.get("eng_timeline_end_frames"), end_f) or end_f
        ov = overlap_frames(start_f, end_f, c_start, c_end)

        matches.append({
            "match_method": method,
            "diffs": {
                "start_diff_frames": abs(c_start - start_f),
                "end_diff_frames": abs(c_end - end_f),
                "overlap_frames": ov,
            },
            "ae_title": {
                "v3v4_id": t.get("id"),
                "track": t.get("track"),
                "name": t.get("name"),
                "eng_timeline_start_frames": start_f,
                "eng_timeline_end_frames": end_f,
                "eng_duration_frames": dur_f,
            },
            "matched_clip_output": {
                "id": clip_id,
                "track": picked.get("track"),
                "type": picked.get("type"),
                "eng_timeline_start_frames": c_start,
                "eng_timeline_end_frames": c_end,
                "eng_duration_frames": picked.get("eng_duration_frames"),
            },
            "final_dubbed_item": final_item,
        })

    # Output files
    out_final = base / OUT_FINAL_ONLY_FILENAME
    out_report = base / OUT_REPORT_FILENAME

    final_only = [m["final_dubbed_item"] for m in matches]
    
    for it in final_only:
        if isinstance(it, dict):
            if "seqeunce_start_frames" in it:
                it["seqeunce_start_frames"] = int(it["seqeunce_start_frames"]) - NEGATIVE_BUFFER_FRAME
    
            if "seqeunce_end_frames" in it:
                it["seqeunce_end_frames"] = int(it["seqeunce_end_frames"]) + NEGATIVE_BUFFER_FRAME
    

    write_json(out_final, final_only)

    report = {
        "input_files": {
            "v3v4_timestamps": str(v3v4_path),
            "clips_output": str(clips_path),
            "final_dubbed_sequence": str(final_path),
        },
        "settings": {
            "SKIP_LOWERTHIRD": SKIP_LOWERTHIRD,
            "MAX_START_DELTA": MAX_START_DELTA,
            "MAX_END_DELTA": MAX_END_DELTA,
            "MIN_OVERLAP_FRAMES": MIN_OVERLAP_FRAMES,
            "PREFER_TRACK": PREFER_TRACK,
            "PREFER_TYPE": PREFER_TYPE,
        },
        "counts": {
            "v3v4_total": len(v3v4),
            "title_cards_kept": len(title_cards),
            "matches": len(matches),
            "missing_in_clips_output": len(missing_in_clips),
            "missing_in_final_dubbed": len(missing_in_final),
            "skipped_other": len(skipped),
        },
        "missing_in_clips_output": missing_in_clips,
        "missing_in_final_dubbed": missing_in_final,
        "skipped": skipped,
        "matches_preview": matches[:20],
    }
    write_json(out_report, report)

    print("\n==============================")
    print("✅ DONE")
    print(f"Title cards kept: {len(title_cards)}")
    print(f"Matched:          {len(matches)}")
    print(f"Output (final):   {out_final.name}")
    print(f"Report:           {out_report.name}")
    print("==============================\n")


if __name__ == "__main__":
    main()
