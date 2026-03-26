#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import json
import sys
from pathlib import Path

# ===== EASY SETTINGS =====
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XML = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"
DEFAULT_OUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A21__transitions_V5_V6_timestamps__.json"

# Alternate simple paths if you want:
# DEFAULT_XML = Path("output/XML/V1__dubbing__.xml")
# DEFAULT_OUT_JSON = Path("output/JSON/A21__transitions_V5_V6_timestamps__.json")

FPS = 23.976

# Only these tracks will be scanned
TARGET_TRACKS = ["V7", "V8"]

# Count/extract all of these item types
TARGET_ITEM_TAGS = {"clipitem", "generatoritem"}


def _safe_int(text):
    if text is None:
        return None
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def extract_v5_v6_items(xml_path: Path, target_tracks, fps: float = FPS):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("No <sequence> element found in XML.")

    video = seq.find("./media/video")
    if video is None:
        raise RuntimeError("No <video> section found in <media>.")

    result = []
    item_id = 1

    # Walk each video track in order: first track = V1, second = V2, etc.
    for idx, track in enumerate(video.findall("track"), start=1):
        track_name = f"V{idx}"
        if track_name not in target_tracks:
            continue

        children = list(track)
        n = len(children)

        for i, node in enumerate(children):
            # We want every clipitem + generatoritem on V5/V6
            if node.tag not in TARGET_ITEM_TAGS:
                continue

            start_val = _safe_int(node.findtext("start"))
            end_val = _safe_int(node.findtext("end"))

            # Must have valid start/end
            if start_val is None or end_val is None:
                continue

            eff_start = start_val
            eff_end = end_val

            # Optional recovery for invalid ranges:
            # if item has weird negative/invalid range, try surrounding transitionitems
            if eff_start < 0 or eff_end <= eff_start:
                prev_trans = None
                next_trans = None

                # Search backward until previous clip/generator boundary
                for j in range(i - 1, -1, -1):
                    prev_node = children[j]
                    if prev_node.tag == "transitionitem":
                        prev_trans = prev_node
                        break
                    if prev_node.tag in TARGET_ITEM_TAGS:
                        break

                # Search forward until next clip/generator boundary
                for k in range(i + 1, n):
                    next_node = children[k]
                    if next_node.tag == "transitionitem":
                        next_trans = next_node
                        break
                    if next_node.tag in TARGET_ITEM_TAGS:
                        break

                if prev_trans is not None:
                    prev_start = _safe_int(prev_trans.findtext("start"))
                    if prev_start is not None:
                        eff_start = prev_start

                if next_trans is not None:
                    next_end = _safe_int(next_trans.findtext("end"))
                    if next_end is not None:
                        eff_end = next_end

            # Final validity check
            if eff_end <= eff_start:
                continue

            dur_frames = eff_end - eff_start
            dur_sec = round(dur_frames / fps, 3)

            result.append({
                "id": item_id,
                "sequence_start_time_sec": round(eff_start / fps, 3),
                "sequence_end_time_sec": round(eff_end / fps, 3),
                "sequence_start_frames": eff_start,
                "sequence_end_frames": eff_end,
                "sentence_duration_sec": dur_sec,
                "sentence_duration_frames": dur_frames,
                "track": track_name,
                "xml_tag": node.tag,          # tells you if it was clipitem or generatoritem
                "type": "transition"
            })

            item_id += 1

    return result


def main():
    if len(sys.argv) >= 2:
        xml_path = Path(sys.argv[1])
    else:
        xml_path = DEFAULT_XML

    if len(sys.argv) >= 3:
        out_path = Path(sys.argv[2])
    else:
        out_path = DEFAULT_OUT_JSON

    out_path.parent.mkdir(parents=True, exist_ok=True)

    items = extract_v5_v6_items(xml_path, TARGET_TRACKS, fps=FPS)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=4)

    print(f"✓ Done! Extracted {len(items)} items from V7/V8")
    print("Included tags: clipitem + generatoritem")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()