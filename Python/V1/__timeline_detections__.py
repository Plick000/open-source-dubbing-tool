import xml.etree.ElementTree as ET
import json
import sys
from pathlib import Path

# ============ FOLDERS / DEFAULTS ============

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XML_PATH = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A2__dubbing__.json"

DEFAULT_FPS = 24
# ===========================================


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


def parse_fcp_xml(path: str):
    tree = ET.parse(path)
    root = tree.getroot()

    sequence = root.find("./sequence")
    if sequence is None:
        raise Exception("No <sequence> found in XML")

    fps_node = sequence.find("./rate/timebase")
    fps = int(fps_node.text) if fps_node is not None else DEFAULT_FPS

    video_tracks = sequence.findall("./media/video/track")
    if not video_tracks:
        raise Exception("No video tracks found")

    v1 = video_tracks[0]

    clips = []
    id_counter = 1
    timeline_position_frames = 0

    for clip in v1.findall("clipitem"):
        start = int(clip.findtext("start", "0"))
        end = int(clip.findtext("end", "0"))
        duration_frames = end - start

        label = extract_label(clip)
        clip_type = map_label_to_type(label)

        clip_start_frames = timeline_position_frames
        clip_end_frames = timeline_position_frames + duration_frames

        clips.append({
            "id": id_counter,
            "type": clip_type,
            "label_raw": label,
            "eng_duration_frames": duration_frames,
            "eng_duration_seconds": round(duration_frames / fps, 4),
            "eng_timeline_start_frames": clip_start_frames,
            "eng_timeline_end_frames": clip_end_frames,
            "eng_timeline_start_seconds": round(clip_start_frames / fps, 4),
            "eng_timeline_end_seconds": round(clip_end_frames / fps, 4),
            "track": "V1"
        })

        timeline_position_frames += duration_frames
        id_counter += 1

    return clips


def save_json(data, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _resolve_xml(p: Path) -> Path:
    if p.exists():
        return p
    if not p.is_absolute():
        candidate = OUTPUT_XML_DIR / p.name
        if candidate.exists():
            return candidate
    return p


def main():
    if len(sys.argv) >= 2:
        xml_path = _resolve_xml(Path(sys.argv[1]))
    else:
        xml_path = DEFAULT_XML_PATH

    if len(sys.argv) >= 3:
        out_json = Path(sys.argv[2])
    else:
        out_json = DEFAULT_OUTPUT_JSON

    if not xml_path.exists():
        raise FileNotFoundError(f"Input XML not found: {xml_path}")

    clips = parse_fcp_xml(str(xml_path))
    save_json(clips, str(out_json))

    print(f"✔ Extracted {len(clips)} clips")
    print(f"✔ Saved JSON to {out_json}")


if __name__ == "__main__":
    main()
