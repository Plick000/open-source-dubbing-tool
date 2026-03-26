import xml.etree.ElementTree as ET
import json
import os
import re
from pathlib import Path
from collections import defaultdict

# ==========================================
#   CONFIGURATION VARIABLES
# ==========================================
_SELF_PATH = Path(__file__).resolve()
PROJECT_ROOT = _SELF_PATH.parents[3] if len(_SELF_PATH.parents) > 3 else _SELF_PATH.parent

INPUT_XML = PROJECT_ROOT / "output" / "XML" / "V5__dubbing__.xml"
INPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A24__final_SFX_timestamps__.json"
OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V8__dubbing__.xml"
# ==========================================


def parse_track_number(track_label: str):
    """
    Extract numeric part from labels like A6, A7, etc.
    Returns int or None.
    """
    if track_label is None:
        return None
    m = re.match(r"^A(\d+)$", str(track_label).strip(), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))



def build_user_to_xml_map(all_tracks):
    """
    Build mapping of user-facing audio labels (A1, A2, ...)
    to actual XML audio track indices.

    Premiere stereo tracks are represented as two XML tracks (L/R),
    so one user label can map to two XML indices.
    """
    user_to_xml_map = {}
    current_user_idx = 1
    xml_idx = 0

    while xml_idx < len(all_tracks):
        track_node = all_tracks[xml_idx]
        track_type = track_node.get('premiereTrackType', 'Mono')
        label = f"A{current_user_idx}"

        if track_type == "Stereo":
            pair = [xml_idx]
            if xml_idx + 1 < len(all_tracks):
                pair.append(xml_idx + 1)
            user_to_xml_map[label] = pair
            xml_idx += 2
        else:
            user_to_xml_map[label] = [xml_idx]
            xml_idx += 1

        current_user_idx += 1

    return user_to_xml_map



def group_json_by_shifted_xml_track(sfx_data):
    """
    JSON track labels must be shifted by +1 for XML mapping:
      A6 -> A7
      A7 -> A8
      A8 -> A9
      etc.

    Returns dict like:
      {
        'A7': [item1, item2, ...],
        'A8': [...]
      }

    Keeps original JSON order within each track, because clip matching
    is positional per track.
    """
    grouped = defaultdict(list)

    for item in sfx_data:
        src_track = item.get('track')
        src_num = parse_track_number(src_track)
        if src_num is None:
            print(f"Skipping item id={item.get('id')} because track is invalid: {src_track}")
            continue

        dst_track = f"A{src_num + 1}"
        grouped[dst_track].append(item)

    return grouped



def update_track_clips(track_node, json_items, track_label):
    """
    Update one XML track using the ordered JSON items for that track.
    Matching is by clip order inside that track, not by global JSON id.
    """
    clips = track_node.findall('clipitem')
    updated = 0

    if not clips:
        print(f"{track_label}: XML track has 0 clips. Skipping.")
        return 0

    if not json_items:
        print(f"{track_label}: No JSON items mapped. Skipping.")
        return 0

    if len(json_items) > len(clips):
        print(
            f"{track_label}: JSON has {len(json_items)} items but XML track has only {len(clips)} clips. "
            f"Only first {len(clips)} will be applied."
        )
    elif len(json_items) < len(clips):
        print(
            f"{track_label}: XML track has {len(clips)} clips but JSON has {len(json_items)} items. "
            f"Only first {len(json_items)} clips will be updated."
        )

    max_count = min(len(clips), len(json_items))

    for i in range(max_count):
        clip = clips[i]
        data = json_items[i]

        start_node = clip.find('start')
        end_node = clip.find('end')

        if start_node is None or end_node is None:
            print(f"{track_label}: clip #{i + 1} missing <start> or <end>. Skipping clip.")
            continue

        start_node.text = str(int(data['new_sequence_start_frames']))
        end_node.text = str(int(data['new_sequence_end_frames']))
        updated += 1

    return updated



def update_xml_stereo():
    if not os.path.exists(INPUT_XML) or not os.path.exists(INPUT_JSON):
        print("Error: Input files not found.")
        return

    # 1) Load JSON data
    with open(INPUT_JSON, 'r', encoding='utf-8') as f:
        sfx_data = json.load(f)

    if not isinstance(sfx_data, list):
        print("Error: JSON root must be a list.")
        return

    # 2) Parse XML
    tree = ET.parse(INPUT_XML)
    root = tree.getroot()

    audio_node = root.find('.//media/audio')
    if audio_node is None:
        print("Error: <media><audio> node not found in XML.")
        return

    all_tracks = audio_node.findall('track')
    if not all_tracks:
        print("Error: No audio tracks found in XML.")
        return

    # 3) Build user-facing track label -> XML index map
    user_to_xml_map = build_user_to_xml_map(all_tracks)

    # 4) Shift JSON track labels by +1 for XML targeting
    json_by_xml_track = group_json_by_shifted_xml_track(sfx_data)

    total_updates = 0
    touched_tracks = []

    # 5) Apply updates track-by-track
    for xml_track_label in sorted(
        json_by_xml_track.keys(),
        key=lambda x: parse_track_number(x) if parse_track_number(x) is not None else 999999
    ):
        if xml_track_label not in user_to_xml_map:
            print(f"{xml_track_label}: Not found in XML sequence mapping.")
            continue

        indices = user_to_xml_map[xml_track_label]
        json_items = json_by_xml_track[xml_track_label]

        print(
            f"Mapping JSON -> XML: {xml_track_label} receives {len(json_items)} item(s), "
            f"XML indices = {indices}"
        )

        for idx in indices:
            if idx >= len(all_tracks):
                continue

            track_node = all_tracks[idx]
            updated = update_track_clips(track_node, json_items, xml_track_label)
            total_updates += updated

        touched_tracks.append(xml_track_label)

    # 6) Save updated XML
    tree.write(OUTPUT_XML, encoding='UTF-8', xml_declaration=True)

    print("Sync Complete!")
    print(f"Updated {total_updates} clip instances across tracks: {', '.join(touched_tracks) if touched_tracks else 'None'}")
    print(f"File saved as: {OUTPUT_XML}")


if __name__ == "__main__":
    update_xml_stereo()
