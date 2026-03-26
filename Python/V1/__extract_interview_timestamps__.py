import json
from pathlib import Path

# ==========================================================
# CONFIG
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A5__timestamps__.json"
OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A6__interviews__.json"


# ==========================================================
# LOAD
# ==========================================================
def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ==========================================================
# FILTER INTERVIEWS
# ==========================================================
def extract_interview_clips(clips):
    """
    Returns only clips where type == 'interview'
    Works across all tracks (V1, A1, A2)
    """
    return [
        clip for clip in clips
        if clip.get("type") == "interview"
    ]


# ==========================================================
# SAVE
# ==========================================================
def save_json(data, path):
    Path(path).write_text(
        json.dumps(data, indent=4),
        encoding="utf-8"
    )


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    all_clips = load_json(INPUT_JSON)
    interview_clips = extract_interview_clips(all_clips)

    save_json(interview_clips, OUTPUT_JSON)

    print(f"✔ Loaded {len(all_clips)} total clips")
    print(f"✔ Extracted {len(interview_clips)} interview clips")
    print(f"✔ Saved interview-only JSON → {OUTPUT_JSON}")
