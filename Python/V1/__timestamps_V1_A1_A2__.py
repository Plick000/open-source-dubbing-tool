import xml.etree.ElementTree as ET
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
XML_PATH = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"
OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A5__timestamps__.json"

DEFAULT_FPS = 24   # fallback if XML does not contain fps


def map_label_to_type(label):
    if not label:
        return "unknown"

    l = label.lower()

    if "violet" in l:
        return "interview"
    if "iris" in l:
        return "normal"
    if "rose" in l:
        return "ae_title"
    if "cerulean" in l:
        return "disclaimer"
    if "forest" in l:
        return "normal"

    return "unknown"


def extract_label(clip):
    labels_node = clip.find("labels")
    if labels_node is not None:
        label2 = labels_node.findtext("label2")
        if label2:
            return label2.strip()
    return None


def parse_fcp_xml(path):
    tree = ET.parse(path)
    root = tree.getroot()

    sequence = root.find("./sequence")
    if sequence is None:
        raise Exception("No <sequence> found in XML")

    fps_node = sequence.find("./rate/timebase")
    fps = int(fps_node.text) if fps_node is not None else DEFAULT_FPS

    clips = []

    # ================= VIDEO TRACK V1 =================
    video_tracks = sequence.findall("./media/video/track")
    if not video_tracks:
        raise Exception("No video tracks found")

    v1 = video_tracks[0]
    v1_id = 1  # 🔁 reset ID per track

    for clip in v1.findall("clipitem"):
        start = int(clip.findtext("start", "0"))
        end = int(clip.findtext("end", "0"))
        in_f = int(clip.findtext("in", "0"))
        out_f = int(clip.findtext("out", "0"))

        duration_frames = out_f - in_f
        label = extract_label(clip)
        clip_type = map_label_to_type(label)

        clips.append({
            "id": v1_id,
            "track_local_id": v1_id,
            "track": "V1",
            "type": clip_type,
            "label_raw": label,

            "eng_in_frames": in_f,
            "eng_out_frames": out_f,
            "eng_in_seconds": round(in_f / fps, 4),
            "eng_out_seconds": round(out_f / fps, 4),

            "eng_duration_frames": duration_frames,
            "eng_duration_seconds": round(duration_frames / fps, 4),

            "eng_timeline_start_frames": start,
            "eng_timeline_end_frames": end,
            "eng_timeline_start_seconds": round(start / fps, 4),
            "eng_timeline_end_seconds": round(end / fps, 4),
        })

        v1_id += 1

    # ================= AUDIO TRACKS A1 & A2 =================
    audio_tracks = sequence.findall("./media/audio/track")

    for a_index, a_track in enumerate(audio_tracks[:2], start=1):
        track_name = f"A{a_index}"
        a_id = 1  # 🔁 reset ID per audio track

        for clip in a_track.findall("clipitem"):
            start = int(clip.findtext("start", "0"))
            end = int(clip.findtext("end", "0"))
            in_f = int(clip.findtext("in", "0"))
            out_f = int(clip.findtext("out", "0"))

            duration_frames = out_f - in_f
            label = extract_label(clip)
            clip_type = map_label_to_type(label) if label else "audio"

            clips.append({
                "id": a_id,
                "track_local_id": a_id,
                "track": track_name,
                "type": clip_type,
                "label_raw": label,

                "eng_in_frames": in_f,
                "eng_out_frames": out_f,
                "eng_in_seconds": round(in_f / fps, 4),
                "eng_out_seconds": round(out_f / fps, 4),

                "eng_duration_frames": duration_frames,
                "eng_duration_seconds": round(duration_frames / fps, 4),

                "eng_timeline_start_frames": start,
                "eng_timeline_end_frames": end,
                "eng_timeline_start_seconds": round(start / fps, 4),
                "eng_timeline_end_seconds": round(end / fps, 4),
            })

            a_id += 1

    return clips


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


if __name__ == "__main__":
    clips = parse_fcp_xml(XML_PATH)
    save_json(clips, OUTPUT_JSON)
    print(f"✔ Extracted {len(clips)} clips (IDs reset per track)")
    print(f"✔ Saved JSON to {OUTPUT_JSON}")
