import json
import subprocess
from pathlib import Path
import os

# ================== CONFIG ==================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIPS_JSON = PROJECT_ROOT / "output" / "JSON" / "A6__interviews__.json"
INPUT_VIDEO = PROJECT_ROOT / "inputs" / "video" / "EnglishRendered.mp4"
OUTPUT_DIR = PROJECT_ROOT / "output" / "samples" / "interviews"

# Filter
TARGET_TYPE = "interview"
TARGET_TRACKS = {"V1"}  # we use video track positions, but cut audio from the full file

# Audio encoding settings
AUDIO_CODEC = "libmp3lame"
QUALITY = "2"  # -qscale:a (2 = good VBR quality)

# Silence filter config
# If the mean volume of the clip is LOWER than this (e.g. -45 dB),
# we consider it "too silent" and skip exporting.
MIN_MEAN_VOLUME_DB = -45.0
# ============================================


def load_clips(json_path: str):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Clips JSON should contain a list.")
    return data


def ensure_output_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def trim_clip_ffmpeg_to_mp3(
    input_path: Path,
    start_sec: float,
    end_sec: float,
    output_path: Path,
):
    """
    Use ffmpeg to trim [start_sec, end_sec] from input_path and export as MP3.

    - We drop video (-vn)
    - Encode audio as MP3 (libmp3lame)
    - VBR quality controlled by -qscale:a
    """
    duration = end_sec - start_sec
    if duration <= 0:
        print(f"⚠ Skipping clip {output_path.name}: non-positive duration ({duration:.3f}s)")
        return

    cmd = [
        "ffmpeg",
        "-y",                      # overwrite output
        "-i", str(input_path),
        "-ss", f"{start_sec:.3f}",
        "-to", f"{end_sec:.3f}",
        "-vn",                     # no video
        "-acodec", AUDIO_CODEC,
        "-qscale:a", QUALITY,
        str(output_path),
    ]

    print("  ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def measure_clip_mean_volume(
    input_path: Path,
    start_sec: float,
    end_sec: float,
) -> float | None:
    """
    Measure mean volume (in dB) of the clip segment [start_sec, end_sec]
    using ffmpeg's volumedetect filter.

    Returns:
        mean_volume_db (float) or None if it cannot be measured.
    """
    duration = end_sec - start_sec
    if duration <= 0:
        return None

    # ffmpeg volumedetect writes stats to stderr
    # We trim the segment with -ss/-t and run volumedetect on it.
    null_sink = "NUL" if os.name == "nt" else "/dev/null"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(input_path),
        "-af", "volumedetect",
        "-f", "null",
        null_sink,
    ]

    print("  Analyzing volume:", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"  ❌ volumedetect failed: {e}")
        return None

    mean_db = None
    for line in result.stderr.splitlines():
        line = line.strip()
        # Look for a line like: "mean_volume: -23.0 dB"
        if "mean_volume:" in line:
            try:
                part = line.split("mean_volume:")[1]
                part = part.split("dB")[0].strip()
                mean_db = float(part)
                break
            except Exception:
                continue

    if mean_db is not None:
        print(f"  ▶ Measured mean volume: {mean_db:.2f} dB")
    else:
        print("  ⚠ Could not parse mean_volume from volumedetect output.")

    return mean_db


def main():
    print("\n=== TRIM INTERVIEW AUDIO CLIPS TO MP3 (FROM IN/OUT JSON) ===\n")

    clips = load_clips(CLIPS_JSON)
    input_video = Path(INPUT_VIDEO)
    out_dir = ensure_output_dir(OUTPUT_DIR)

    if not input_video.exists():
        raise FileNotFoundError(f"Input video not found: {input_video}")

    # Filter interview clips using V1 timing
    interview_clips = [
        c for c in clips
        if str(c.get("type", "")).lower() == TARGET_TYPE
        and c.get("track") in TARGET_TRACKS
    ]

    print(f"Loaded {len(clips)} clips from JSON.")
    print(f"Found {len(interview_clips)} interview clips on tracks: {sorted(TARGET_TRACKS)}\n")

    if not interview_clips:
        print("No interview clips found. Nothing to do.")
        return

    # Optional: sort by timeline start
    interview_clips.sort(
        key=lambda c: c.get("eng_timeline_start_frames", 0)
    )

    kept_count = 0
    skipped_silent = 0

    for idx, clip in enumerate(interview_clips, start=1):
        cid = clip.get("id")
        start_sec = float(clip.get("eng_in_seconds", 0.0))
        end_sec = float(clip.get("eng_out_seconds", 0.0))
    
        # ✅ NEW: 3-digit prefix must match the clip id (not the interview order)
        try:
            cid_int = int(str(cid).strip())
        except Exception:
            # Safety fallback: if id is missing/non-numeric, keep old behavior for naming only
            cid_int = idx
    
        out_name = f"interview_{cid_int:03d}_id{cid_int}.mp3"
        out_path = out_dir / out_name
    
        print(f"[{idx}/{len(interview_clips)}] Clip id={cid}")
        print(f"  Source In/Out: {start_sec:.3f}s -> {end_sec:.3f}s")
        print(f"  Output       : {out_path}")
    
        # ---- NEW: check mean volume to skip silent/too-quiet clips ----
        mean_db = measure_clip_mean_volume(input_video, start_sec, end_sec)
        if mean_db is not None and mean_db < MIN_MEAN_VOLUME_DB:
            print(
                f"  ⚠ Skipping clip id={cid} due to low volume "
                f"({mean_db:.2f} dB < {MIN_MEAN_VOLUME_DB:.2f} dB)"
            )
            skipped_silent += 1
            print()
            continue
        # --------------------------------------------------------------
    
        try:
            trim_clip_ffmpeg_to_mp3(input_video, start_sec, end_sec, out_path)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ ffmpeg failed for clip id={cid}: {e}")
        else:
            print("  ✔ Done.\n")
            kept_count += 1
    

    print("\n✔ All interview MP3 clips processed.")
    print(f"→ Saved in folder: {out_dir.resolve()}")
    print(f"  Exported clips: {kept_count}")
    print(f"  Skipped (too silent): {skipped_silent}")


if __name__ == "__main__":
    main()
