#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import json
import copy
import sys
from pathlib import Path
from typing import List, Dict, Any

# ================== FOLDERS ==================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_XML = PROJECT_ROOT / "inputs" / "XML" / "English.xml"
DEFAULT_INPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A1__dubbing__.json"
DEFAULT_OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"

# ============================================

VIDEO_TRACK_INDEX = 0
ADD_SEG_INDEX_TO_NAME = False


def _resolve_in_dir(p: Path, fallback_dir: Path) -> Path:
    if p.exists():
        return p
    if not p.is_absolute():
        candidate = fallback_dir / p.name
        if candidate.exists():
            return candidate
    return p


# ----------------------------------------------------------
# LOAD CUT FRAMES + COLOR LABEL SEGMENTS
# ----------------------------------------------------------
def load_cut_frames(json_path: Path):
    if not json_path.exists():
        raise FileNotFoundError(f"A1 JSON file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    frames_set = set()
    segments = []

    for item in data:
        start = item.get("sequence_start_frames")
        end = item.get("sequence_end_frames")
        label = item.get("label")

        if isinstance(label, str):
            label = label.lower()

        if isinstance(start, int):
            frames_set.add(start)
        if isinstance(end, int):
            frames_set.add(end)

        if isinstance(start, int) and isinstance(end, int):
            segments.append({"start": start, "end": end, "label": label})

    return sorted(frames_set), segments


# ----------------------------------------------------------
# WRITE COLOR LABEL INTO CLIPITEM
# ----------------------------------------------------------
def add_color_label(ci: ET.Element, label: str):
    if not label:
        return

    formatted = label.capitalize()

    labels_el = ci.find("labels")
    if labels_el is None:
        labels_el = ET.SubElement(ci, "labels")
    else:
        for child in list(labels_el):
            labels_el.remove(child)

    label_el = ET.SubElement(labels_el, "label2")
    label_el.text = formatted


# ----------------------------------------------------------
# SPLIT CLIPITEM AND APPLY COLOR LABEL BASED ON RANGE
# ----------------------------------------------------------
def split_clipitem_by_cuts(ci: ET.Element, cut_frames: List[int], segments: List[Dict[str, Any]]):
    start = int(ci.findtext("start"))
    end = int(ci.findtext("end"))

    if end <= start:
        return [ci]

    in_el = ci.find("in")
    out_el = ci.find("out")

    in_ = int(in_el.text) if in_el is not None else 0
    out_ = int(out_el.text) if out_el is not None else in_ + (end - start)

    internal_cuts = [c for c in cut_frames if start < c < end]
    if not internal_cuts:
        return [ci]

    internal_cuts = sorted(internal_cuts)
    boundaries = [start] + internal_cuts + [end]

    new_segments = []
    orig_name_el = ci.find("name")
    orig_name = orig_name_el.text if orig_name_el is not None else ""

    for idx in range(len(boundaries) - 1):
        seg_start = boundaries[idx]
        seg_end = boundaries[idx + 1]

        seg_ci = copy.deepcopy(ci)

        seg_ci.find("start").text = str(seg_start)
        seg_ci.find("end").text = str(seg_end)

        offset = seg_start - start
        seg_in = in_ + offset
        seg_out = seg_in + (seg_end - seg_start)

        if seg_ci.find("in") is not None:
            seg_ci.find("in").text = str(seg_in)
        if seg_ci.find("out") is not None:
            seg_ci.find("out").text = str(seg_out)

        if ADD_SEG_INDEX_TO_NAME and orig_name_el:
            seg_ci.find("name").text = f"{orig_name} [seg {idx+1}]"

        for s in segments:
            if s["start"] <= seg_start < s["end"]:
                add_color_label(seg_ci, s["label"])
                break

        new_segments.append(seg_ci)

    return new_segments


# ----------------------------------------------------------
# FIND VIDEO TRACK V1
# ----------------------------------------------------------
def find_video_track(root, index):
    paths = [
        ".//sequence/video/track",
        ".//sequence/media/video/track",
        ".//media/video/track",
        ".//video/track",
    ]

    for p in paths:
        tracks = root.findall(p)
        if tracks:
            if index < len(tracks):
                return tracks[index]
            raise IndexError(f"Only {len(tracks)} tracks, requested {index}")

    raise RuntimeError("No video <track> found")


# ----------------------------------------------------------
# APPLY CUTS + LABEL COLORS
# ----------------------------------------------------------
def apply_cuts_to_v1(xml_path, json_path, output_path, video_track_index=0):
    xml_path = Path(xml_path)
    json_path = Path(json_path)
    output_path = Path(output_path)

    cut_frames, segments = load_cut_frames(json_path)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    video_track = find_video_track(root, video_track_index)

    original = list(video_track.findall("clipitem"))
    new_items = []
    split_count = 0

    for ci in original:
        parts = split_clipitem_by_cuts(ci, cut_frames, segments)
        split_count += len(parts) - 1
        new_items.extend(parts)

    for ci in original:
        video_track.remove(ci)

    for ci in new_items:
        video_track.append(ci)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    print(f"✓ Done — {split_count} splits created")
    print(f"Saved to {output_path}")


def main():
    # Allow CLI, but default to folder structure.
    if len(sys.argv) > 1:
        input_xml = _resolve_in_dir(Path(sys.argv[1]), INPUT_XML_DIR)
    else:
        input_xml = DEFAULT_INPUT_XML

    if len(sys.argv) > 2:
        input_json = _resolve_in_dir(Path(sys.argv[2]), OUTPUT_JSON_DIR)
    else:
        input_json = DEFAULT_INPUT_JSON

    if len(sys.argv) > 3:
        output_xml = Path(sys.argv[3])
    else:
        output_xml = DEFAULT_OUTPUT_XML

    apply_cuts_to_v1(input_xml, input_json, output_xml, video_track_index=VIDEO_TRACK_INDEX)


if __name__ == "__main__":
    main()
