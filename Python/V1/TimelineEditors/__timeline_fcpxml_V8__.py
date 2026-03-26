import xml.etree.ElementTree as ET
import json
import os
import copy
from pathlib import Path

# ================= CONFIGURATION =================
PROJECT_ROOT = Path(__file__).resolve().parents[3]

INPUT_XML = PROJECT_ROOT / "output" / "XML" / "V8__dubbing__.xml"
INPUT_DATA_JSON = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"
INPUT_CONFIG_JSON = PROJECT_ROOT / "inputs" / "config" / "config.json"
TITLES_SEGMENTS_JSON = PROJECT_ROOT / "output" / "JSON" / "A16__final_titles_segments__.json"

OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V9__dubbing__.xml"
OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A20__BGM__.json"

TARGET_TRACK_NAME = "A11"
NEW_SEQUENCE_NAME = "Automated Timeline"
# =================================================


def _safe_int(x, default=None):
    try:
        return int(str(x).strip())
    except Exception:
        return default


def _get_last_v1_end_frame(sequence):
    """
    Take timeline end from the LAST clip item on V1 (first video track),
    not from sequence duration / JSON duration.
    """
    v_tracks = sequence.findall('./media/video/track')
    if not v_tracks:
        return None

    v1 = v_tracks[0]
    max_end = None

    # Clipitems + generatoritems (some timelines use generator items for solids, etc.)
    for tag in ('clipitem', 'generatoritem'):
        for item in v1.findall(f'./{tag}'):
            end_f = _safe_int(item.findtext('end'))
            if end_f is None:
                continue
            max_end = end_f if max_end is None else max(max_end, end_f)

    return max_end


def _lookup_file_duration_frames(root, clipitem):
    """
    Try to find the referenced media file duration in frames.
    If not found, return None and we will assume we can extend without looping.
    """
    # Common case: <clipitem><file>...</file></clipitem>
    dur = _safe_int(clipitem.findtext('./file/duration'))
    if dur is not None:
        return dur

    file_elem = clipitem.find('file')
    if file_elem is None:
        return None

    fid = file_elem.get('id')
    if not fid:
        return None

    # Elsewhere in the document: <file id="..."><duration>...</duration></file>
    dur2 = _safe_int(root.findtext(f".//file[@id='{fid}']/duration"))
    return dur2


def _extract_brand_number(value):
    """
    Accepts:
      12
      "12"
      "Brand 12"
      "brand13"
    Returns int or None
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    return _safe_int(digits, None) if digits else None


def _load_config_json():
    """
    Load config.json safely. If missing/invalid, return {} so current logic does not break.
    """
    try:
        if not INPUT_CONFIG_JSON.exists():
            return {}
        with open(INPUT_CONFIG_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_brand12plus_titles_start_frame():
    """
    If config video_brand >= 12:
      - load output/JSON/__titles_segments__.json
      - return the first item's start_frame
    Else:
      - return None

    Any failure returns None so the old disclaimer logic remains unchanged.
    """
    try:
        config = _load_config_json()
        brand_num = _extract_brand_number(
            config.get('video_brand')
            or config.get('brand')
            or config.get('Brand')
            or config.get('brand_number')
        )

        if brand_num is None or brand_num < 12:
            return None

        if not TITLES_SEGMENTS_JSON.exists():
            return None

        with open(TITLES_SEGMENTS_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, list) or not data:
            return None

        first_item = data[0]
        if not isinstance(first_item, dict):
            return None

        return _safe_int(first_item.get('seqeunce_start_frames'), None)
    except Exception:
        return None


def process_bgm_logic():
    # Ensure output directories exist
    OUTPUT_XML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. LOAD INPUT JSON DATA
        with open(INPUT_DATA_JSON, 'r', encoding='utf-8') as f:
            ref_data = json.load(f)

        # Find disclaimer and final timestamps (fallback only)
        disclaimer = next((item for item in ref_data if item.get('type') == 'disclaimer'), None)
        if not disclaimer:
            print("Error: 'disclaimer' type not found in input JSON.")
            return

        disc_end_sec = disclaimer.get('seqeunce_end_time_sec')
        final_end_sec_fallback = ref_data[-1].get('seqeunce_end_time_sec')

        # 2. PARSE XML
        tree = ET.parse(INPUT_XML)
        root = tree.getroot()

        # --- Change Sequence Name ---
        sequence = root.find('.//sequence')
        if sequence is not None:
            name_tag = sequence.find('name')
            if name_tag is not None:
                name_tag.text = NEW_SEQUENCE_NAME

        if sequence is None:
            print("Error: <sequence> not found in XML.")
            return

        # Calculate FPS/Timebase
        rate_elem = sequence.find('rate')
        timebase = float(rate_elem.find('timebase').text)
        ntsc = rate_elem.find('ntsc').text.upper() == 'TRUE'
        fps = timebase / 1.001 if ntsc else timebase

        # Convert disclaimer end to frames (existing logic)
        disc_end_f = int(round(float(disc_end_sec) * fps))

        # NEW: For Brand 12+, override split boundary from titles JSON first item start_frame
        override_split_f = _get_brand12plus_titles_start_frame()
        split_boundary_f = override_split_f if override_split_f is not None else disc_end_f

        # ===== NEW: TAKE FINAL END FROM V1 LAST CLIP END (PRIMARY) =====
        v1_end_f = _get_last_v1_end_frame(sequence)

        if v1_end_f is None:
            # Fallback to JSON if V1 couldn't be read
            final_end_f = int(round(float(final_end_sec_fallback) * fps))
        else:
            final_end_f = int(v1_end_f)

        # Guard: never let final end be before split boundary
        if final_end_f < split_boundary_f:
            final_end_f = split_boundary_f

        # 3. TRACK MAPPING (A1, A2... logic)
        audio_tracks = sequence.findall('./media/audio/track')
        logical_tracks = []
        xml_idx = 0
        logical_num = 1

        while xml_idx < len(audio_tracks):
            current_track = audio_tracks[xml_idx]
            is_stereo = False

            if (xml_idx + 1) < len(audio_tracks):
                has_stereo_attr = current_track.get('totalExplodedTrackCount') == '2'
                first_clip = current_track.find('.//clipitem')
                has_link = False
                if first_clip is not None:
                    links = first_clip.findall('link')
                    next_idx_str = str(xml_idx + 2)
                    has_link = any(
                        l.find('trackindex') is not None and l.find('trackindex').text == next_idx_str
                        for l in links
                    )
                if has_stereo_attr or has_link:
                    is_stereo = True

            logical_tracks.append({
                "name": f"A{logical_num}",
                "xml_elements": [audio_tracks[xml_idx], audio_tracks[xml_idx + 1]] if is_stereo else [audio_tracks[xml_idx]]
            })
            xml_idx += 2 if is_stereo else 1
            logical_num += 1

        # 4. MODIFY TARGET TRACK (A11)
        target = next((t for t in logical_tracks if t['name'] == TARGET_TRACK_NAME), None)
        if not target:
            print(f"Error: {TARGET_TRACK_NAME} not found. Max track is {logical_tracks[-1]['name']}")
            return

        final_json_data = []

        # We'll only build JSON metadata from the first element of the stereo pair (same as before)
        json_built = False

        for track_elem in target["xml_elements"]:
            clips = track_elem.findall('clipitem')
            if len(clips) < 2:
                print("Skipping track part: Less than 2 clips found.")
                continue

            # ===== NEW: idempotency / prevent 'one more clip' on re-run =====
            # Keep only first 2 clipitems on this track; remove any extras from previous runs.
            for extra in clips[2:]:
                track_elem.remove(extra)
            clips = track_elem.findall('clipitem')
            c1, c2 = clips[0], clips[1]

            # --- Modify Clip 1 (End at split boundary OR Final if shorter) ---
            c1_start_f = _safe_int(c1.findtext('start'), 0)
            clip1_end_f = min(split_boundary_f, final_end_f)

            c1.find('end').text = str(clip1_end_f)
            c1_in = _safe_int(c1.findtext('in'), 0)
            c1.find('out').text = str(c1_in + (clip1_end_f - c1_start_f))

            # If timeline ends inside first segment, we stop here (do not create/modify later clips)
            if final_end_f <= split_boundary_f:
                # Remove clip2 (and any extras already removed above)
                track_elem.remove(c2)

                if not json_built:
                    final_json_data = [{
                        "id": 1,
                        "start_time_seconds": round(c1_start_f / fps, 3),
                        "end_time_seconds": round(final_end_f / fps, 3),
                        "duration_seconds": round((final_end_f - c1_start_f) / fps, 3),
                        "start_frame": c1_start_f,
                        "end_frame": final_end_f,
                        "duration_frames": final_end_f - c1_start_f
                    }]
                    json_built = True

                continue

            # --- Modify Clip 2 (Start at split boundary) ---
            c2_old_start = _safe_int(c2.findtext('start'), 0)
            c2_old_in = _safe_int(c2.findtext('in'), 0)
            c2_old_out = _safe_int(c2.findtext('out'), 0)
            
            c2.find('start').text = str(split_boundary_f)
            
            # Adjust 'in' to maintain audio content continuity (preserve your existing logic)
            c2_new_in = c2_old_in + (split_boundary_f - c2_old_start)
            c2.find('in').text = str(c2_new_in)

            # ===== FIXED: Clip 2 must repeat its original used source section, not continue forward =====
            # Keep your continuity adjustment for c2_new_in, but when more duration is needed than the
            # original clip2 source window allows, duplicate that same section again and again.
            
            file_dur_f = _lookup_file_duration_frames(root, c2)
            
            desired_len_f = final_end_f - split_boundary_f
            
            # Original source window length of clip2 before any modification
            orig_seg_len_f = max(0, c2_old_out - c2_old_in)
            
            # After shifting the clip start to split_boundary_f, the new usable source window starts at c2_new_in.
            # Its max natural end is limited by BOTH:
            #   1) the original clip2 source-window length
            #   2) actual media file duration if known
            natural_seg_len_f = orig_seg_len_f
            if file_dur_f is not None:
                natural_seg_len_f = min(natural_seg_len_f, max(0, file_dur_f - c2_new_in))
            
            # Safety fallback: if original out/in is broken, fall back to media remainder
            if natural_seg_len_f <= 0 and file_dur_f is not None:
                natural_seg_len_f = max(0, file_dur_f - c2_new_in)
            
            if natural_seg_len_f <= 0:
                # Nothing valid to play from clip2, so collapse it safely.
                c2.find('end').text = str(split_boundary_f)
                c2.find('out').text = str(c2_new_in)
            else:
                # First clip2 instance uses only its own repeated segment length, not unlimited forward extension.
                first_seg_len_f = min(natural_seg_len_f, desired_len_f)
                c2_end_f = split_boundary_f + first_seg_len_f
            
                c2.find('end').text = str(c2_end_f)
                c2.find('out').text = str(c2_new_in + first_seg_len_f)
            
                remaining_f = final_end_f - c2_end_f
                cur_start = c2_end_f
                loop_idx = 1
            
                # Repeat the SAME source region that clip2 originally represents after continuity shift
                loop_in_f = c2_new_in
                loop_out_f = c2_new_in + natural_seg_len_f
            
                while remaining_f > 0:
                    seg_len = min(natural_seg_len_f, remaining_f)
            
                    cN = copy.deepcopy(c2)
                    cN.set('id', f"{c2.get('id', 'clip')}_loop{loop_idx}")
            
                    cN.find('start').text = str(cur_start)
                    cN.find('end').text = str(cur_start + seg_len)
                    cN.find('in').text = str(loop_in_f)
                    cN.find('out').text = str(loop_in_f + seg_len)
            
                    track_elem.append(cN)
            
                    cur_start += seg_len
                    remaining_f -= seg_len
                    loop_idx += 1

            # ------------- JSON metadata (first element only) -------------
            if not json_built:
                def meta(item_id, start_f, end_f):
                    s_s, e_s = start_f / fps, end_f / fps
                    return {
                        "id": item_id,
                        "start_time_seconds": round(s_s, 3),
                        "end_time_seconds": round(e_s, 3),
                        "duration_seconds": round(e_s - s_s, 3),
                        "start_frame": start_f,
                        "end_frame": end_f,
                        "duration_frames": end_f - start_f
                    }

                # Collect all BGM clipitems on this track in timeline order
                clip_items = track_elem.findall('clipitem')
                clip_items_sorted = sorted(
                    clip_items,
                    key=lambda x: _safe_int(x.findtext('start'), 0)
                )

                final_json_data = []
                for i, ci in enumerate(clip_items_sorted, start=1):
                    sf = _safe_int(ci.findtext('start'), 0)
                    ef = _safe_int(ci.findtext('end'), 0)
                    final_json_data.append(meta(i, sf, ef))

                json_built = True

        # 5. SAVE OUTPUTS
        tree.write(OUTPUT_XML, encoding='UTF-8', xml_declaration=True)
        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(final_json_data, f, indent=2)

        print(f"Done! Updated sequence name to '{NEW_SEQUENCE_NAME}'.")
        if override_split_f is not None:
            print(
                f"BGM split overridden by titles JSON first item start_frame: "
                f"frame={split_boundary_f}, seconds={round(split_boundary_f / fps, 3)}"
            )
        else:
            print(
                f"BGM split aligned to disclaimer end: "
                f"frame={split_boundary_f}, seconds={round(split_boundary_f / fps, 3)}"
            )
        print(f"BGM end aligned to V1 last clip end: frame={final_end_f}, seconds={round(final_end_f / fps, 3)}")
        print(f"XML saved to: {OUTPUT_XML}")
        print(f"JSON saved to: {OUTPUT_JSON}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    process_bgm_logic()
