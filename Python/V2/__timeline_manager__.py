import json
from pathlib import Path

# ==========================================================
# CONFIG
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIO_TIMESTAMPS_FILE = PROJECT_ROOT / "output" / "JSON" / "A3__dubbing__.json"
CLIPS_FILE = PROJECT_ROOT / "output" / "JSON" / "A2__dubbing__.json"

# Main output → NO LIMITS values, but STARTS anchored to WITH LIMITS timeline
OUTPUT_FILE_NO_LIMITS = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__no_limits__.json"

# Secondary output → actual WITH LIMITS version
OUTPUT_FILE_WITH_LIMITS = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"

FPS = 23.976
NON_NORMAL = {"interview", "disclaimer"}

DISCLAIMER_FIXED_SECONDS = 2.5
DISCLAIMER_FIXED_FRAMES = int(round(DISCLAIMER_FIXED_SECONDS * FPS))

MIN_SPEED = 0.4   # 40%
MAX_SPEED = 1.8   # 180%

# ==========================================================
# LOADERS
# ==========================================================
def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ==========================================================
# BUILD SENTENCE TIMINGS
# ==========================================================
def build_sentence_timings_from_audio(audio_ts):
    audio_ts_sorted = sorted(audio_ts, key=lambda x: x["id"])
    final_sentences = []

    for i, a in enumerate(audio_ts_sorted):
        sid = int(a["id"])

        start_sec = float(a["start_time_seconds"])
        end_sec = float(a["end_time_seconds"])

        duration_sec = end_sec - start_sec

        start_frames = int(a.get("start_frame", round(start_sec * FPS)))
        end_frames = int(a.get("end_frame", round(end_sec * FPS)))
        duration_frames = end_frames - start_frames

        if "gap_after_seconds" in a:
            gap_sec = float(a["gap_after_seconds"])
            gap_frames = int(a.get("gap_after_frames", round(gap_sec * FPS)))
        else:
            if i < len(audio_ts_sorted) - 1:
                next_start_sec = float(audio_ts_sorted[i + 1]["start_time_seconds"])
                gap_sec = max(0.0, next_start_sec - end_sec)
            else:
                gap_sec = 0.0
            gap_frames = int(round(gap_sec * FPS))

        playout_frames = duration_frames + gap_frames
        playout_sec = playout_frames / FPS if FPS > 0 else duration_sec + gap_sec

        final_sentences.append({
            "id": sid,
            "sentence": a.get("sentenceText") or a.get("sentence", ""),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start_frames": start_frames,
            "end_frames": end_frames,
            "duration_sec": duration_sec,
            "duration_frames": duration_frames,
            "gap_after_seconds": gap_sec,
            "gap_after_frames": gap_frames,
            "playout_duration_sec": playout_sec,
            "playout_duration_frames": playout_frames,
        })

    return final_sentences


# ==========================================================
# VERSION 2 — WITH LIMITS (REAL CURRENT LOGIC)
# ==========================================================
def build_final_sequence_with_limits(refined, clips):
    final = []
    current_frames = 0

    audio_idx = 0
    total_sentences = len(refined)
    pending_speed_reduction = 0.0

    for clip in clips:
        cid = clip["id"]
        ctype = clip["type"].lower()
        eng_sec = clip["eng_duration_seconds"]
        eng_frames = clip["eng_duration_frames"]

        if ctype in NON_NORMAL:
            if ctype == "disclaimer":
                dur_frames = DISCLAIMER_FIXED_FRAMES
                sentence_duration_sec = DISCLAIMER_FIXED_SECONDS
            else:
                dur_frames = eng_frames
                sentence_duration_sec = eng_sec

            start = current_frames
            end = start + dur_frames
            speed = 1.0

        else:
            if audio_idx >= total_sentences:
                print(f"⚠ WARNING: Ran out of audio sentences for clip id={cid}. Using original English duration.")

                dur_frames = eng_frames
                sentence_duration_sec = dur_frames / FPS if FPS > 0 else eng_sec
                speed = 1.0
            else:
                s = refined[audio_idx]
                audio_idx += 1

                base_dur_frames = s.get("playout_duration_frames")
                if base_dur_frames is None:
                    base_dur_frames = s["end_frames"] - s["start_frames"]

                base_sentence_duration_sec = (
                    base_dur_frames / FPS if FPS > 0 else s.get("playout_duration_sec", 0.0)
                )

                raw_speed = eng_sec / base_sentence_duration_sec if base_sentence_duration_sec > 0 else 1.0
                effective_speed = raw_speed - pending_speed_reduction

                if effective_speed > MAX_SPEED:
                    effective_speed = MAX_SPEED

                if effective_speed < MIN_SPEED:
                    pending_speed_reduction = MIN_SPEED - effective_speed
                    speed = MIN_SPEED
                else:
                    pending_speed_reduction = 0.0
                    speed = effective_speed

                if speed > 0:
                    dur_frames = int(round(eng_frames / speed))
                    if dur_frames < 1:
                        dur_frames = 1
                else:
                    dur_frames = base_dur_frames

                sentence_duration_sec = dur_frames / FPS if FPS > 0 else base_sentence_duration_sec

            start = current_frames
            end = start + dur_frames

        final.append({
            "id": cid,
            "type": clip["type"],
            "label_raw": clip.get("label_raw"),
            "eng_duration_seconds": eng_sec,
            "eng_duration_frames": eng_frames,
            "seqeunce_start_time_sec": round(start / FPS, 3),
            "seqeunce_end_time_sec": round(end / FPS, 3),
            "seqeunce_start_frames": start,
            "seqeunce_end_frames": end,
            "sentence_duration_sec": round(sentence_duration_sec, 3),
            "speed": round(speed, 3),
            "track": clip.get("track")
        })

        current_frames = end

    if pending_speed_reduction > 0:
        print(
            f"⚠ WARNING: Remaining speed reduction {round(pending_speed_reduction, 3)} "
            f"could not be applied because no more NORMAL clips were available."
        )

    print(f"\n[WITH LIMITS] Total audio sentences: {total_sentences}")
    print(f"[WITH LIMITS] Sentences consumed:    {audio_idx}")
    print(f"[WITH LIMITS] Clips total:           {len(clips)}\n")

    return final


# ==========================================================
# VERSION 1 — NO LIMITS, BUT STARTS FOLLOW WITH-LIMITS
# ==========================================================
def build_final_sequence_no_limits_with_limited_starts(refined, clips, limited_starts):
    """
    A4__dubbing__.json:
    - Uses OLD no-limits speed/duration logic
    - But each item's START frame is forced to match the WITH LIMITS version
    - End frame = limited_start + no_limits_duration
    """
    final = []

    audio_idx = 0
    total_sentences = len(refined)

    for idx, clip in enumerate(clips):
        cid = clip["id"]
        ctype = clip["type"].lower()
        eng_sec = clip["eng_duration_seconds"]
        eng_frames = clip["eng_duration_frames"]

        if idx >= len(limited_starts):
            raise ValueError("limited_starts length does not match clips length.")

        start = int(limited_starts[idx])

        if ctype in NON_NORMAL:
            if ctype == "disclaimer":
                dur_frames = DISCLAIMER_FIXED_FRAMES
                sentence_duration_sec = DISCLAIMER_FIXED_SECONDS
            else:
                dur_frames = eng_frames
                sentence_duration_sec = eng_sec

            end = start + dur_frames
            speed = 1.0

        else:
            if audio_idx >= total_sentences:
                print(f"⚠ WARNING: Ran out of audio sentences for clip id={cid}. Using original English duration.")

                dur_frames = eng_frames
                end = start + dur_frames
                sentence_duration_sec = dur_frames / FPS if FPS > 0 else eng_sec
                speed = 1.0
            else:
                s = refined[audio_idx]
                audio_idx += 1

                dur_frames = s.get("playout_duration_frames")
                if dur_frames is None:
                    dur_frames = s["end_frames"] - s["start_frames"]

                end = start + dur_frames
                sentence_duration_sec = dur_frames / FPS if FPS > 0 else s.get("playout_duration_sec", 0.0)
                speed = eng_sec / sentence_duration_sec if sentence_duration_sec > 0 else 1.0

        final.append({
            "id": cid,
            "type": clip["type"],
            "label_raw": clip.get("label_raw"),
            "eng_duration_seconds": eng_sec,
            "eng_duration_frames": eng_frames,
            "seqeunce_start_time_sec": round(start / FPS, 3),
            "seqeunce_end_time_sec": round(end / FPS, 3),
            "seqeunce_start_frames": start,
            "seqeunce_end_frames": end,
            "sentence_duration_sec": round(sentence_duration_sec, 3),
            "speed": round(speed, 3),
            "track": clip.get("track")
        })

    print(f"\n[NO LIMITS / LIMITED STARTS] Total audio sentences: {total_sentences}")
    print(f"[NO LIMITS / LIMITED STARTS] Sentences consumed:    {audio_idx}")
    print(f"[NO LIMITS / LIMITED STARTS] Clips total:           {len(clips)}\n")

    return final


# ==========================================================
# MAIN
# ==========================================================
def main():
    print("\n=== TIMELINE MANAGER — A4 STARTS FOLLOW WITH LIMITS ===\n")

    audio_ts = load_json(AUDIO_TIMESTAMPS_FILE)
    clips = load_json(CLIPS_FILE)

    refined = build_sentence_timings_from_audio(audio_ts)

    # First build current limited timeline
    final_with_limits = build_final_sequence_with_limits(refined, clips)

    Path(OUTPUT_FILE_WITH_LIMITS).write_text(
        json.dumps(final_with_limits, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"✔ SAVED (WITH LIMITS) → {OUTPUT_FILE_WITH_LIMITS}")

    # Extract starts from with-limits
    limited_starts = [item["seqeunce_start_frames"] for item in final_with_limits]

    # Then build A4 no-limits version using those exact starts
    final_no_limits = build_final_sequence_no_limits_with_limited_starts(refined, clips, limited_starts)

    Path(OUTPUT_FILE_NO_LIMITS).write_text(
        json.dumps(final_no_limits, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"✔ SAVED (A4 / LIMITED STARTS) → {OUTPUT_FILE_NO_LIMITS}\n")


if __name__ == "__main__":
    main()