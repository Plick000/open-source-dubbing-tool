import xml.etree.ElementTree as ET
import json
import os
import time
import math
from pathlib import Path

# -------------------------------------------------
# CONFIG
# -------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ORIGINAL_XML = PROJECT_ROOT / "output" / "XML" / "V1__dubbing__.xml"
SEGMENTS_JSON = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"
OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V2__dubbing__.xml"

# new_file_duration = English(original FILE duration) / 24 * 30
SRC_FPS_FOR_FILE_DURATION = 24.0
DST_FPS_FOR_FILE_DURATION = 30.0

# 5-frame boundary snap tolerance
FRAME_GLITCH_FIX_RANGE = 5

t0 = time.time()

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def extract_language_from_json(path):
    base = os.path.basename(path)
    lang = base.split("_")[0]
    return lang.upper()

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))

def remove_timeremap(clip):
    for flt in list(clip.findall("filter")):
        eff = flt.find("effect")
        if eff is None:
            continue
        eff_id_text = eff.findtext("effectid")
        eff_id_attr = eff.get("id")
        if eff_id_text == "timeremap" or eff_id_attr == "timeremap":
            clip.remove(flt)

def get_file_duration_frames(root, clip, fallback_out):
    """
    English(original file duration) in frames:
      1) inline <file><duration>
      2) referenced <file id="..."><duration> in root
      3) fallback: use fallback_out
    """
    file_ref = clip.find("file")
    file_duration = None

    if file_ref is not None:
        d = file_ref.findtext("duration")
        if d and d.isdigit():
            file_duration = int(d)

    if file_duration is None and file_ref is not None:
        file_id = file_ref.get("id")
        if file_id:
            for f in root.findall(".//file"):
                if f.get("id") == file_id:
                    d = f.findtext("duration")
                    if d and d.isdigit():
                        file_duration = int(d)
                        break

    if file_duration is None:
        file_duration = int(fallback_out) if fallback_out is not None else 1

    return max(1, int(file_duration))

def get_segment_frames(seg):
    if "seqeunce_start_frames" in seg and "seqeunce_end_frames" in seg:
        return int(seg["seqeunce_start_frames"]), int(seg["seqeunce_end_frames"])
    if "sequence_start_frames" in seg and "sequence_end_frames" in seg:
        return int(seg["sequence_start_frames"]), int(seg["sequence_end_frames"])
    if "seqeunce start frames" in seg and "seqeunce end frames" in seg:
        return int(seg["seqeunce start frames"]), int(seg["seqeunce end frames"])
    raise KeyError("No *_start_frames / *_end_frames keys found in segment JSON.")

def get_segment_speed(seg):
    if "speed" not in seg:
        return 1.0
    sp = float(seg["speed"])
    if sp <= 0:
        raise RuntimeError(f"Invalid speed <= 0 in JSON for segment id={seg.get('id')}: speed={sp}")
    return sp

def calc_new_file_duration(english_file_duration_frames: int) -> int:
    val = (english_file_duration_frames / SRC_FPS_FOR_FILE_DURATION) * DST_FPS_FOR_FILE_DURATION
    return round_half_up(val)

def snap_start_to_previous_end(current_start: int, previous_end: int, max_range: int = FRAME_GLITCH_FIX_RANGE) -> int:
    """
    If current start is within +/- max_range frames of prev end, snap start to prev end.
    Eliminates tiny overlaps and tiny gaps.
    """
    if previous_end is None:
        return int(current_start)

    current_start = int(current_start)
    previous_end = int(previous_end)
    delta = current_start - previous_end

    if abs(delta) <= int(max_range):
        return previous_end

    return current_start

# ----------------------------------------------------------
# CRITICAL: enforce (out-in) == (end-start)
# ----------------------------------------------------------
def enforce_slot_len_via_out(clip, target_len_frames: int):
    """
    Force the trim length (out-in) to exactly equal the timeline slot length (end-start).
    This prevents Premiere from dropping/duplicating frames (the "glitch").
    """
    target_len_frames = int(target_len_frames)
    if target_len_frames < 1:
        target_len_frames = 1

    in_f = int(clip.findtext("in") or "0")
    out_f = int(clip.findtext("out") or str(in_f + 1))

    if (out_f - in_f) != target_len_frames:
        clip.find("out").text = str(in_f + target_len_frames)

def patch_timeremap_kf3_when_to_out(clip):
    """
    If we changed <out>, update timeremap keyframe #3 "when" to match the new out.
    """
    out_f = int(clip.findtext("out") or "0")

    for flt in clip.findall("filter"):
        eff = flt.find("effect")
        if eff is None:
            continue
        if (eff.findtext("effectid") or "").strip() != "timeremap":
            continue

        for p in eff.findall("parameter"):
            if (p.findtext("parameterid") or "").strip() != "graphdict":
                continue
            kfs = p.findall("keyframe")
            if len(kfs) < 4:
                continue
            # KF3 (index 2)
            when_node = kfs[2].find("when")
            if when_node is not None:
                when_node.text = str(out_f)

def remove_duplicate_kf2_if_same_as_kf1(clip):
    """
    If new_in==0, your generator makes KF1 when=0 and KF2 when=0.
    Remove KF2 duplicate to avoid Premiere curve weirdness.
    """
    for flt in clip.findall("filter"):
        eff = flt.find("effect")
        if eff is None:
            continue
        if (eff.findtext("effectid") or "").strip() != "timeremap":
            continue

        for p in eff.findall("parameter"):
            if (p.findtext("parameterid") or "").strip() != "graphdict":
                continue
            kfs = p.findall("keyframe")
            if len(kfs) < 4:
                continue

            k1_when = (kfs[0].findtext("when") or "").strip()
            k2_when = (kfs[1].findtext("when") or "").strip()
            if k1_when and k2_when and k1_when == k2_when:
                p.remove(kfs[1])

# ----------------------------------------------------------
# APPLY YOUR EXACT MODEL C MATH (video + audio)
# ----------------------------------------------------------
def apply_timeremap_model_c_exact(clip, eng_in, eng_out, english_file_duration_frames, speed_factor, mediatype):
    """
    Your math:

    new_duration(clipitem) = English(original FILE duration) / speed_factor
    new_in  = English(original in)  / speed_factor
    new_out = English(original out) / speed_factor

    new_file_duration = English(original FILE duration) / 24 * 30

    Keyframes:
      KF1: when=0 value=0
      KF2: when=new_in  value=English in
      KF3: when=new_out value=English out
      KF4: when=new_duration value=new_file_duration
    """
    eng_in = int(eng_in)
    eng_out = int(eng_out)
    english_file_duration_frames = int(english_file_duration_frames)

    sp = float(speed_factor)
    if sp <= 0:
        raise RuntimeError("speed_factor must be > 0 (division).")

    new_in = round_half_up(eng_in / sp)
    new_out = round_half_up(eng_out / sp)

    new_duration = round_half_up(english_file_duration_frames / sp)
    new_file_duration = calc_new_file_duration(english_file_duration_frames)

    if new_out <= new_in:
        new_out = new_in + 1
    if new_duration < 1:
        new_duration = 1
    if new_file_duration < 1:
        new_file_duration = 1

    clip.find("in").text = str(new_in)
    clip.find("out").text = str(new_out)
    clip.find("duration").text = str(new_duration)

    speed_percent = sp * 100.0

    filter_tag = ET.Element("filter")
    effect = ET.SubElement(filter_tag, "effect")

    ET.SubElement(effect, "name").text = "Time Remap"
    ET.SubElement(effect, "effectid").text = "timeremap"
    ET.SubElement(effect, "effectcategory").text = "motion"
    ET.SubElement(effect, "effecttype").text = "motion"
    ET.SubElement(effect, "mediatype").text = mediatype

    p1 = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p1, "parameterid").text = "variablespeed"
    ET.SubElement(p1, "value").text = "0"

    p2 = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p2, "parameterid").text = "speed"
    ET.SubElement(p2, "value").text = f"{speed_percent:.6f}"

    p3 = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p3, "parameterid").text = "reverse"
    ET.SubElement(p3, "value").text = "FALSE"

    p4 = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p4, "parameterid").text = "frameblending"
    ET.SubElement(p4, "value").text = "FALSE"

    graph = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(graph, "parameterid").text = "graphdict"
    ET.SubElement(graph, "valuemin").text = "0"
    ET.SubElement(graph, "valuemax").text = str(new_file_duration)
    ET.SubElement(graph, "value").text = "0"

    # KF1
    k1 = ET.SubElement(graph, "keyframe")
    ET.SubElement(k1, "when").text = "0"
    ET.SubElement(k1, "value").text = "0"

    # KF2
    k2 = ET.SubElement(graph, "keyframe")
    ET.SubElement(k2, "when").text = str(new_in)
    ET.SubElement(k2, "value").text = str(eng_in)

    # KF3
    k3 = ET.SubElement(graph, "keyframe")
    ET.SubElement(k3, "when").text = str(new_out)
    ET.SubElement(k3, "value").text = str(eng_out)

    # KF4
    k4 = ET.SubElement(graph, "keyframe")
    ET.SubElement(k4, "when").text = str(new_duration)
    ET.SubElement(k4, "value").text = str(new_file_duration)

    clip.append(filter_tag)

# ----------------------------------------------------------
# UNIQUE IDS + FIX LINKCLIPREF (as in your existing file)
# ----------------------------------------------------------
def make_clipitems_unique_and_fix_links(root):
    video_tracks = root.findall(".//sequence/media/video/track")
    audio_tracks = root.findall(".//sequence/media/audio/track")

    lookup = {}
    next_id_num = 1

    for ti, tr in enumerate(video_tracks, start=1):
        clips = tr.findall("clipitem")
        for ci, clip in enumerate(clips, start=1):
            new_id = f"clipitem-{next_id_num}"
            next_id_num += 1
            clip.set("id", new_id)
            lookup[("video", ti, ci)] = new_id

    for ti, tr in enumerate(audio_tracks, start=1):
        clips = tr.findall("clipitem")
        for ci, clip in enumerate(clips, start=1):
            new_id = f"clipitem-{next_id_num}"
            next_id_num += 1
            clip.set("id", new_id)
            lookup[("audio", ti, ci)] = new_id

    for clip in root.findall(".//clipitem"):
        for link in clip.findall("link"):
            mtype = (link.findtext("mediatype") or "").strip().lower()
            try:
                tindex = int(link.findtext("trackindex") or "0")
                cindex = int(link.findtext("clipindex") or "0")
            except ValueError:
                continue

            key = (mtype, tindex, cindex)
            if key in lookup:
                ref = link.find("linkclipref")
                if ref is not None:
                    ref.text = lookup[key]

# ----------------------------------------------------------
# MAIN — Apply on V1 + A1 (A1 is explicitly stretched to match V1)
# ----------------------------------------------------------
def process_v1_and_a1_stretch_a1():
    tree = ET.parse(ORIGINAL_XML)
    root = tree.getroot()

    language = extract_language_from_json(SEGMENTS_JSON)
    seq_node = root.find(".//sequence/name")
    if seq_node is not None:
        seq_node.text = f"{language} Automated Timeline"

    segments = load_json(SEGMENTS_JSON)
    seg_list = sorted(segments, key=lambda s: int(s.get("id", 0)))

    # V1 = first video track
    vtrack1 = root.find(".//sequence/media/video/track")
    if vtrack1 is None:
        raise RuntimeError("No V1 video track found.")
    vclips = vtrack1.findall("clipitem")

    # A1 = first audio track
    atracks = root.findall(".//sequence/media/audio/track")
    if not atracks:
        raise RuntimeError("No audio tracks found (need A1).")
    atrack1 = atracks[0]
    a1clips = atrack1.findall("clipitem")

    if len(vclips) != len(seg_list):
        raise RuntimeError(
            f"Mismatch: V1 clips={len(vclips)} but JSON segments={len(seg_list)}. "
            f"JSON must include ALL clips in the same order."
        )

    prev_final_end = None

    # Process by index: V1 is the master; A1 follows the same index (if present)
    for i, vclip in enumerate(vclips):
        seg = seg_list[i]
        original_start, new_end = get_segment_frames(seg)
        speed = get_segment_speed(seg)

        # snap tiny overlap/gap to previous end
        new_start = snap_start_to_previous_end(original_start, prev_final_end, FRAME_GLITCH_FIX_RANGE)
        if new_end <= new_start:
            new_start = int(original_start)

        target_len = int(new_end) - int(new_start)
        if target_len < 1:
            target_len = 1

        # ---------------- VIDEO ----------------
        eng_in_v = int(vclip.findtext("in") or "0")
        eng_out_v = int(vclip.findtext("out") or str(eng_in_v + 1))
        eng_file_dur_v = get_file_duration_frames(root, vclip, fallback_out=eng_out_v)

        vclip.find("start").text = str(new_start)
        vclip.find("end").text = str(new_end)

        remove_timeremap(vclip)
        apply_timeremap_model_c_exact(
            clip=vclip,
            eng_in=eng_in_v,
            eng_out=eng_out_v,
            english_file_duration_frames=eng_file_dur_v,
            speed_factor=speed,
            mediatype="video",
        )

        # critical glitch prevention
        enforce_slot_len_via_out(vclip, target_len)
        patch_timeremap_kf3_when_to_out(vclip)
        remove_duplicate_kf2_if_same_as_kf1(vclip)

        # ---------------- A1 (STRETCH TO MATCH VIDEO) ----------------
        if i < len(a1clips):
            aclip = a1clips[i]

            eng_in_a = int(aclip.findtext("in") or "0")
            eng_out_a = int(aclip.findtext("out") or str(eng_in_a + 1))
            eng_file_dur_a = get_file_duration_frames(root, aclip, fallback_out=eng_out_a)

            # Force same timeline slot as video
            aclip.find("start").text = str(new_start)
            aclip.find("end").text = str(new_end)

            remove_timeremap(aclip)
            apply_timeremap_model_c_exact(
                clip=aclip,
                eng_in=eng_in_a,
                eng_out=eng_out_a,
                english_file_duration_frames=eng_file_dur_a,
                speed_factor=speed,
                mediatype="audio",
            )

            # Force audio trim length to equal timeline slot length
            enforce_slot_len_via_out(aclip, target_len)
            patch_timeremap_kf3_when_to_out(aclip)
            remove_duplicate_kf2_if_same_as_kf1(aclip)

        prev_final_end = int(new_end)

    make_clipitems_unique_and_fix_links(root)

    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)
    print("\n✔ DONE (V1 + A1 stretched to V1 + anti-glitch invariant) →", OUTPUT_XML)
    print(f"Total Time taken: {round(time.time() - t0, 2)}s")

if __name__ == "__main__":
    process_v1_and_a1_stretch_a1()