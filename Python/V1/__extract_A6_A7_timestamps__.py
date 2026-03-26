import xml.etree.ElementTree as ET
import json
import os
from pathlib import Path

# ==========================================
#   CONFIGURATION VARIABLES
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"
OUTPUT_FILE = PROJECT_ROOT / "output" / "JSON" / "A23__A6_A7_timestamps__.json"

# INPUT_FILE = "output/XML/V1__dubbing__.xml"
# OUTPUT_FILE = "output/JSON/A23__A6_A7_timestamps__.json"
TARGET_FPS = 23.976  # Force calculations to 23.976 FPS
# ==========================================

def extract_timestamps(xml_path, json_path):
    if not os.path.exists(xml_path):
        print(f"Error: The input file '{xml_path}' was not found.")
        return

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Error parsing XML: {e}")
        return

    sequence = root.find('sequence')
    if sequence is None:
        print("Error: No <sequence> found in XML.")
        return

    # Find Audio Media
    media = sequence.find('media')
    if media is None: return
    audio = media.find('audio')
    if audio is None: return
    
    all_tracks = audio.findall('track')

    # 1. Dynamic Track Mapping (Stereo vs Mono)
    # Because explodedTracks="true", Stereo tracks occupy 2 XML track entries.
    # We need to map user track labels (A1, A2...) to actual XML track indices.
    
    track_map = {} # user_label: [xml_indices]
    current_user_index = 1
    xml_idx = 0
    
    while xml_idx < len(all_tracks):
        track_node = all_tracks[xml_idx]
        track_type = track_node.get('premiereTrackType', 'Mono')
        
        user_label = f"A{current_user_index}"
        
        if track_type == "Stereo":
            # Map both pair indices to this user label
            track_map[user_label] = [xml_idx, xml_idx + 1]
            xml_idx += 2
        else:
            # Map single index
            track_map[user_label] = [xml_idx]
            xml_idx += 1
            
        current_user_index += 1

    # 2. Identify target indices for A6 and A7
    targets = ["A6", "A7", "A8", "A9"]
    target_xml_indices = {}
    
    for label in targets:
        if label in track_map:
            # We take the first index of the pair/single for clip extraction
            target_xml_indices[track_map[label][0]] = label
            print(f"Mapped {label} to XML Track Index {track_map[label][0]}")

    # 3. Extract Clip Data
    extracted_data = []
    global_id_counter = 1
    fps = TARGET_FPS

    for xml_idx, user_label in target_xml_indices.items():
        if xml_idx >= len(all_tracks):
            continue
            
        track = all_tracks[xml_idx]
        clips = track.findall('clipitem')
        
        for clip in clips:
            try:
                start_frame = int(clip.find('start').text)
                end_frame = int(clip.find('end').text)
            except (AttributeError, ValueError):
                continue

            if start_frame < 0 or end_frame < 0:
                continue

            duration_frames = end_frame - start_frame
            
            # Label Extraction
            label_text = ""
            labels_node = clip.find('labels')
            if labels_node is not None:
                label2 = labels_node.find('label2')
                if label2 is not None:
                    label_text = label2.text

            extracted_data.append({
                "id": global_id_counter,
                "sequence_start_time_sec": round(start_frame / fps, 3),
                "sequence_end_time_sec": round(end_frame / fps, 3),
                "sequence_start_frames": start_frame,
                "sequence_end_frames": end_frame,
                "sentence_duration_sec": round(duration_frames / fps, 3),
                "sentence_duration_frames": duration_frames,
                "track": user_label,
                "label": label_text
            })
            global_id_counter += 1

    # 4. Save to JSON
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=4)
        print(f"\nSuccess! Extracted {len(extracted_data)} clips to {json_path}")
    except IOError as e:
        print(f"Error saving file: {e}")

if __name__ == "__main__":
    extract_timestamps(INPUT_FILE, OUTPUT_FILE)