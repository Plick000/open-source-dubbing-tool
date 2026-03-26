import json
import os
from pathlib import Path

# ==========================================
#   CONFIGURATION VARIABLES
# ==========================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

FILE_SFX_INPUT = PROJECT_ROOT / "output" / "JSON" / "A23__A6_A7_timestamps__.json"
FILE_A2_ENGLISH = PROJECT_ROOT / "output" / "JSON" / "A2__dubbing__.json"
FILE_A4_DUBBED = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"
FILE_OUTPUT = PROJECT_ROOT / "output" / "JSON" / "A24__final_SFX_timestamps__.json"

# FILE_SFX_INPUT = "output/JSON/A23__A6_A7_timestamps__.json"
# FILE_A2_ENGLISH = "output/JSON/A2__dubbing__.json"
# FILE_A4_DUBBED = "output/JSON/A4__dubbing__.json"
# FILE_OUTPUT = "output/JSON/A24__final_SFX_timestamps__.json"

FPS = 24.0  # Timebase for seconds calculation
# ==========================================

def load_data(file_name):
    if not os.path.exists(file_name):
        print(f"Error: {file_name} not found.")
        return None
    with open(file_name, 'r', encoding='utf-8') as f:
        return json.load(f)
    
def write_output(data):
    FILE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def process_sfx_alignment():
    # Load all files
    sfx_data = load_data(FILE_SFX_INPUT)
    a2_data = load_data(FILE_A2_ENGLISH)
    a4_data = load_data(FILE_A4_DUBBED)

    # Always generate the output file, even if inputs are missing/empty.
    # Only treat None as "missing file/failed load". Empty [] is allowed.
    if sfx_data is None or a2_data is None or a4_data is None:
        write_output([])
        print("[WARN] One or more required input JSON files were missing/failed to load. Wrote empty output.")
        return
    
    # If SFX list is empty, still write output as []
    if isinstance(sfx_data, list) and len(sfx_data) == 0:
        write_output([])
        print("Magic Complete! 0 SFX clips processed (no SFX/lowerthird items).")
        print(f"Saved to: {FILE_OUTPUT}")
        return
    
    # Create lookup for A4 (Dubbed) by ID
    a4_lookup = {item['id']: item for item in a4_data}
    
    final_output = []

    for sfx in sfx_data:
        sfx_start = sfx['sequence_start_frames']
        sfx_duration = sfx['sentence_duration_frames']
        
        # 1. Find the corresponding range in A2 (English reference)
        matched_a2 = None
        for a2_item in a2_data:
            if a2_item['eng_timeline_start_frames'] <= sfx_start <= a2_item['eng_timeline_end_frames']:
                matched_a2 = a2_item
                break
        
        if matched_a2:
            match_id = matched_a2['id']
            
            # 2. Find matching ID in A4 (Dubbed target)
            if match_id in a4_lookup:
                matched_a4 = a4_lookup[match_id]
                
                # --- CALCULATION LOGIC AS REQUESTED ---
                
                # A. Offset = SFX Start - English Start
                offset = sfx_start - matched_a2['eng_timeline_start_frames']
                
                # B. Intermediate Offset = (Offset / Speed) then rounded
                speed = matched_a4.get('speed', 1.0)
                if speed == 0: speed = 1.0
                intermediate_offset = round(offset / speed)
                
                # C. New Start = Intermediate Offset + Dubbed Start
                a4_seq_start = matched_a4.get('seqeunce_start_frames', 0)
                new_start_frames = intermediate_offset + a4_seq_start
                
                # D. New End = New Start + SFX Original Duration
                new_end_frames = new_start_frames + sfx_duration
                
                # E. Calculate Seconds (24 FPS)
                new_start_sec = round(new_start_frames / FPS, 3)
                new_end_sec = round(new_end_frames / FPS, 3)

                # 3. Format the JSON object
                final_output.append({
                    "id": sfx['id'],
                    "new_sequence_start_time_sec": new_start_sec,
                    "new_sequence_end_time_sec": new_end_sec,
                    "new_sequence_start_frames": int(new_start_frames),
                    "new_sequence_end_frames": int(new_end_frames),
                    "sentence_duration_sec": sfx['sentence_duration_sec'],
                    "sentence_duration_frames": sfx_duration,
                    "track": sfx['track'],
                    "label": sfx['label']
                })
            else:
                print(f"Warning: ID {match_id} not found in A4 dubbing file.")
        else:
            print(f"Warning: SFX Frame {sfx_start} (ID {sfx['id']}) did not match any English range in A2.")

    # Save final JSON
    write_output(final_output)
    
    print(f"Magic Complete! {len(final_output)} SFX clips processed.")
    print(f"Saved to: {FILE_OUTPUT}")

if __name__ == "__main__":
    process_sfx_alignment()