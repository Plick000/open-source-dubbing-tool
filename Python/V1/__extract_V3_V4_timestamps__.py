#!/usr/bin/env python3
import json
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict
from pathlib import Path
# =========================
# CONFIG (EDIT THESE ONLY)
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_XML       = PROJECT_ROOT / "output" / "XML" / "V5__dubbing__.xml"
OUTPUT_JSON     = PROJECT_ROOT / "output" / "JSON" / "A15__V3_V4_clips_timestamps__.json"

# INPUT_XML   = "output/XML/V5__dubbing__.xml"
# OUTPUT_JSON = "output/JSON/A15__V3_V4_clips_timestamps__.json"

# V3 (track 3) → "Title"
# V4 (track 4) → "Lowerthird"
TRACK_NAME_OVERRIDE = {
    3: "Title",
    4: "Lowerthird",
    5: "UltraText",
    6: "UltraExtraText",
}

TARGET_VIDEO_TRACKS = [3, 4, 5, 6]   # V3 & V4 & V5 & V6
DEFAULT_FPS = 24.0
# =========================


def text_int(node: Optional[ET.Element], default: int = 0) -> int:
    if node is None or node.text is None:
        return default
    try:
        return int(str(node.text).strip())
    except Exception:
        return default

def text_str(node: Optional[ET.Element], default: str = "") -> str:
    if node is None or node.text is None:
        return default
    return str(node.text).strip()

def frames_to_seconds(frames: int, fps: float) -> float:
    return 0.0 if fps <= 0 else frames / fps

def round4(x: float) -> float:
    return float(f"{x:.4f}")

def map_label_to_type(label: str) -> str:
    if not label:
        return "unknown"
    l = label.lower()
    if "violet" in l:
        return "interview"
    if "iris" in l:
        return "normal"
    if "forest" in l:
        return "normal"
    if "rose" in l:
        return "ae_title"
    if "cerulean" in l:
        return "disclaimer"
    return "unknown"

def extract_label_raw(clipitem: ET.Element) -> str:
    labels = clipitem.find("labels")
    if labels is not None:
        v = text_str(labels.find("label2"))
        if v:
            return v
        for child in list(labels):
            s = text_str(child)
            if s:
                return s
    return ""

def detect_fps(root: ET.Element) -> float:
    tb = root.find(".//sequence/rate/timebase")
    if tb is not None:
        try:
            return float(text_str(tb))
        except Exception:
            pass

    tb2 = root.find(".//rate/timebase")
    if tb2 is not None:
        try:
            return float(text_str(tb2))
        except Exception:
            pass

    return float(DEFAULT_FPS)

def iter_video_tracks(root: ET.Element) -> List[ET.Element]:
    video = root.find(".//sequence/media/video")
    if video is None:
        video = root.find(".//video")
    if video is None:
        return []
    return video.findall("track")


def main():
    tree = ET.parse(INPUT_XML)
    root = tree.getroot()

    fps = detect_fps(root)

    # Convert target tracks V# -> zero-based index
    wanted_indices = {t - 1 for t in TARGET_VIDEO_TRACKS if t >= 1}

    track_nodes = iter_video_tracks(root)
    if not track_nodes:
        raise RuntimeError("Could not find any video tracks in XML (no .//sequence/media/video/track).")

    out: List[Dict] = []
    global_id = 1

    for track_index, track_node in enumerate(track_nodes):
        if track_index not in wanted_indices:
            continue

        track_num = track_index + 1        # 1-based (V1=1...)
        track_name = f"V{track_num}"
        local_id = 1

        # Decide name override based on track number
        forced_name = TRACK_NAME_OVERRIDE.get(track_num, "")

        for clipitem in track_node.findall("clipitem"):
            start = text_int(clipitem.find("start"), 0)
            end = text_int(clipitem.find("end"), start)
            if end < start:
                end = start

            dur_frames = end - start
            if dur_frames <= 0:
                dur_frames = 1

            label_raw = extract_label_raw(clipitem)
            clip_type = map_label_to_type(label_raw)

            # Original name in XML (fallback)
            xml_name = text_str(clipitem.find("name"), "")

            item = {
                "id": global_id,
                "track_local_id": local_id,
                "track": track_name,

                # ✅ V3 => Title, V4 => Lowerthird
                "name": forced_name or xml_name,

                "type": clip_type,
                "label_raw": label_raw,

                "eng_in_frames": start,
                "eng_out_frames": end,
                "eng_in_seconds": round4(frames_to_seconds(start, fps)),
                "eng_out_seconds": round4(frames_to_seconds(end, fps)),

                "eng_duration_frames": dur_frames,
                "eng_duration_seconds": round4(frames_to_seconds(dur_frames, fps)),

                "eng_timeline_start_frames": start,
                "eng_timeline_end_frames": end,
                "eng_timeline_start_seconds": round4(frames_to_seconds(start, fps)),
                "eng_timeline_end_seconds": round4(frames_to_seconds(end, fps)),
            }

            out.append(item)
            global_id += 1
            local_id += 1

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✔ Extracted {len(out)} clips from V{TARGET_VIDEO_TRACKS} → {OUTPUT_JSON} (fps={fps})")


if __name__ == "__main__":
    main()
