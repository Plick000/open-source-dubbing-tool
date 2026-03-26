import json
from pathlib import Path


FPS = 24

# ================================
# LOADER FUNCTION
# ================================
def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

# ================================
# LOAD ALL 3 FILES
# ================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
transitions = load(PROJECT_ROOT / "output" / "JSON" / "A21__transitions_V5_V6_timestamps__.json")
clips_output = load(PROJECT_ROOT / "output" / "JSON" / "A2__dubbing__.json")
final_dubbed = load(PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json")
Output_JSON = PROJECT_ROOT / "output" / "JSON" / "A22__transitions_segments__.json"


# transitions = load("output/JSON/A21__transitions_V5_V6_timestamps__.json")  # :contentReference[oaicite:0]{index=0}
# clips_output = load("output/JSON/A2__dubbing__.json")  # :contentReference[oaicite:1]{index=1}
# final_dubbed = load("output/JSON/A4__dubbing__.json")  # :contentReference[oaicite:2]{index=2}
# Output_JSON = "output/JSON/A22__transitions_segments__.json"

# Convert final dubbed to dict by ID for fast lookup
final_by_id = {item["id"]: item for item in final_dubbed}

# ================================
# HELPER
# ================================
def frames_to_seconds(fr):
    return fr / FPS


def get_speed_ratio(dubbed):
    """
    Returns a usable speed ratio.
    Supports:
      - 1.25  -> treated as 1.25x
      - 125   -> treated as 125% -> 1.25x
      - missing/invalid/0 -> fallback 1.0
    """
    raw = dubbed.get("speed", 1.0)  # change only this key if your A4 field name is different

    try:
        ratio = float(raw)
    except (TypeError, ValueError):
        return 1.0

    if ratio <= 0:
        return 1.0

    # If value looks like percent (e.g. 80, 125, 200), convert to x-speed
    if ratio > 10:
        ratio = ratio / 100.0

    return ratio


def adjust_transition_overlap(overlap_frames, speed_ratio, fixed_frames=24):
    """
    Stretch only positive overlap beyond fixed_frames.
    Negative/zero values stay unchanged.
    """
    overlap_frames = int(round(overlap_frames))

    if overlap_frames <= 0:
        return overlap_frames

    if overlap_frames <= fixed_frames:
        return overlap_frames

    extra_frames = overlap_frames - fixed_frames
    stretched_extra = int(round(extra_frames / speed_ratio))

    return fixed_frames + stretched_extra


# Check if transition lies inside clip boundaries
def transition_matches_clip(t_start, t_end, c_start, c_end):
    return (t_start >= c_start and t_start <= c_end) or \
           (t_end >= c_start and t_end <= c_end)

# ================================
# MAIN LOGIC
# ================================
final_results = []

for t in transitions:
    t_id = t["id"]
    t_start = t["sequence_start_frames"]
    t_end = t["sequence_end_frames"]

    # ---------------------------------------
    # 1) FIND MATCHING CLIP IN clips_output
    # ---------------------------------------
    matched_clip = None
    for clip in clips_output:
        c_start = clip["eng_timeline_start_frames"]
        c_end = clip["eng_timeline_end_frames"]

        if transition_matches_clip(t_start, t_end, c_start, c_end):
            matched_clip = clip
            break

    if not matched_clip:
        print(f"WARNING: No clip match for transition ID {t_id}")
        continue

    clip_id = matched_clip["id"]
    clip_eng_end = matched_clip["eng_timeline_end_frames"]

    # ---------------------------------------
    # 2) CALCULATE OFFSETS
    # ---------------------------------------
    after_cut_end = t_end - clip_eng_end
    after_cut_start = clip_eng_end - t_start

    # ---------------------------------------
    # 3) GET FINAL DUBBED CLIP
    # ---------------------------------------
    dubbed = final_by_id.get(clip_id)
    if not dubbed:
        print(f"WARNING: ID {clip_id} not found in final dubbed sequence")
        continue

    seq_end_frames = dubbed["seqeunce_end_frames"]
    speed_ratio = get_speed_ratio(dubbed)

    # ---------------------------------------
    # 4) COMPUTE NEW TRANSITION POSITIONS
    # ---------------------------------------
    # Default: use the original mapped cut point
    adjusted_seq_end_frames = seq_end_frames

    # Distance from transition END to the English clip cut
    # (positive only when the transition end is before the cut, i.e. inside the clip)
    before_cut_distance_end = clip_eng_end - t_end

    # Special rule:
    # If transition END is more than 24 frames before the cut,
    # shift the mapped cut point using only the excess beyond 24 frames.
    if before_cut_distance_end > 24:
        extra = before_cut_distance_end - 24
        adjusted_extra = int(round(extra / speed_ratio))
        cut_shift = adjusted_extra - extra
        adjusted_seq_end_frames = seq_end_frames - cut_shift

    # Always calculate start/end from the adjusted cut point
    # using the ORIGINAL raw offsets
    new_start_frames = adjusted_seq_end_frames - after_cut_start
    new_end_frames = adjusted_seq_end_frames + after_cut_end

    # ---------------------------------------
    # 5) BUILD RESULT OBJECT
    # ---------------------------------------
    result = {
        "id": t_id,
        "track": t["track"],
        "type": "transition",
        "new_sequence_start_frames": new_start_frames,
        "new_sequence_end_frames": new_end_frames,
        "new_sequence_start_time_sec": frames_to_seconds(new_start_frames),
        "new_sequence_end_time_sec": frames_to_seconds(new_end_frames)
    }

    final_results.append(result)

# ================================
# SAVE OUTPUT
# ================================
Path(Output_JSON).write_text(
    json.dumps(final_results, indent=4),
    encoding="utf-8"
)

print(f"Final transitions JSON created: {Output_JSON}")
