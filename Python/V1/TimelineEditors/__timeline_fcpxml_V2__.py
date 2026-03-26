import os
import re
import json
import math
import shlex
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
import platform
import shutil
import pycountry
from pathlib import Path


def is_wsl() -> bool:
    return (
        "WSL_DISTRO_NAME" in os.environ
        or "microsoft" in platform.release().lower()
        or "microsoft" in platform.version().lower()
    )

def resolve_fast_content_base() -> Path:
    """
    If running in WSL, use /mnt/e/...
    If running in Windows Python, use E:\...
    """
    return Path("/mnt/z/Automated Dubbings/Projects") if is_wsl() else Path(r"Z:\Automated Dubbings\Projects")


def windows_to_wsl_path(win_path: str) -> str:
    """
    Convert Windows path like:
    E:\\Fast Content\\Video Name\\Polish\\PL - Video Name.mp3
    to:
    /mnt/e/Fast Content/Video Name/Polish/PL - Video Name.mp3
    """
    p = str(win_path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2)
        return f"/mnt/{drive}/{rest}"
    return p


def wsl_to_windows_path(p: str) -> str:
    p = p.strip()
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}"
    return p

def to_pathurl_windows(win_path: str) -> str:
    p = win_path.replace("\\", "/")
    quoted = urllib.parse.quote(p, safe="/:")
    quoted = quoted.replace(":", "%3a")
    return "file://localhost/" + quoted

# =======================
# CONFIG (edit these)
# =======================

PROJECT_ROOT = Path(__file__).resolve().parents[3]

INPUT_XML = PROJECT_ROOT / "output" / "XML" / "V2__dubbing__.xml"
SEGMENTS_JSON = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__no_limits__.json"
OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V3__dubbing__.xml"

# Your dubbed narration audio (MP3/WAV)
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"

# Source audio to copy from
SOURCE_DUBBED_AUDIO = PROJECT_ROOT / "inputs" / "audios" / "__dubbed__audio__.mp3"

# INPUT_XML = "output/XML/V2__dubbing__.xml"
# SEGMENTS_JSON = "output/JSON/A4__dubbing__.json"

# Your dubbed narration audio (MP3/WAV)
# CONFIG_PATH = Path("inputs/config/config.json")

# Source audio to copy from
# SOURCE_DUBBED_AUDIO = Path("inputs/audios/__dubbed__audio__.mp3")

FAST_CONTENT_BASE = resolve_fast_content_base()


# OUTPUT_XML = "output/XML/V3__dubbing__.xml"

# Premiere "A2" in *this XML style* is usually trackindex 3 & 4 (exploded stereo).
# If your timeline has different layout, change these:
TARGET_TRACKINDEX_LEFT = 3
TARGET_TRACKINDEX_RIGHT = 4

# Remove any existing clipitems from those target tracks first
CLEAR_TARGET_TRACKS = True

DISCLAIMER_A3_LEFT = 5
DISCLAIMER_A3_RIGHT = 6

# Segment types to skip (no audio placed, and source cursor does NOT advance)
SKIP_TYPES = {"interview"}

# Premiere ticks constant: 1 second = 254016000000 ticks
PPRO_TICKS_PER_SECOND = 254016000000

DISCLAIMER_LABEL = "cerulean"


# -----------------------
# Helpers
# -----------------------


def load_disclaimer_location(json_path=PROJECT_ROOT / "output" / "JSON" / "A8__disclaimer__.json") -> str:
    json_path = Path(json_path)
    if not json_path.exists():
        raise RuntimeError(f"Disclaimer state file not found: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    final_location = (data.get("final_location") or "").strip()
    if not final_location:
        raise RuntimeError("final_location missing in A8__disclaimer__.json")

    # Try the path as-is, then common folder-name variants
    candidates = [
        final_location,
        final_location.replace("/Automated Dubbing/", "/Automated Dubbings/"),
        final_location.replace("/Automated Dubbings/", "/Automated Dubbing/"),
    ]

    for c in candidates:
        if Path(c).exists():
            return c

    # If nothing exists, give a precise error
    raise RuntimeError(
        "Disclaimer video not found at any expected location.\n"
        "Tried:\n- " + "\n- ".join(candidates)
    )


DISCLAIMER_VIDEO_PATH = load_disclaimer_location(PROJECT_ROOT / "output" / "JSON" / "A8__disclaimer__.json")

def is_disclaimer(seg) -> bool:
    # type OR label_raw
    t = (str(seg.get("type", "")).strip().lower())
    lab = (str(seg.get("label_raw", "")).strip().lower())
    return (t == "disclaimer") or (lab == DISCLAIMER_LABEL)

def ffprobe_duration_seconds(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed:\n{p.stderr}\nCMD={' '.join(cmd)}")
    return float(p.stdout.strip())


def ffprobe_audio_info(path: str):
    """
    Returns (duration_seconds: float, sample_rate: int, channels: int)
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-show_entries", "format=duration",
        "-of", "json",
        path
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{p.stderr}\nCMD={' '.join(cmd)}")

    data = json.loads(p.stdout)
    duration = float(data["format"]["duration"])
    stream = data["streams"][0]
    sr = int(stream.get("sample_rate", 48000))
    ch = int(stream.get("channels", 2))
    return duration, sr, ch


def get_video_tracks(sequence_elem):
    video = sequence_elem.find("./media/video")
    if video is None:
        raise RuntimeError("No <media><video> found in this XML.")
    tracks = video.findall("./track")
    if not tracks:
        raise RuntimeError("No <track> nodes found under <media><video>.")
    return video, tracks

def find_disclaimer_file_elem(root):
    # finds the <file> that currently points to the English disclaimer
    for f in root.iter("file"):
        pu = (f.findtext("pathurl") or "").lower()
        if "/disclaimers/" in pu or "\\disclaimers\\" in pu:
            return f
    return None

def update_file_elem(file_elem, new_path: str, fps_seq: int) -> int:
    """
    Update an existing <file> element to point to a new media path.

    Accepts:
      - WSL path: /mnt/e/.../file.mp4
      - Windows path: E:\\...\\file.mp4  or  E:/.../file.mp4

    Updates:
      <name>, <pathurl>, <duration>, <rate><timebase>, <timecode><rate><timebase> (if present)

    Returns:
      duration_frames (int) computed at sequence fps
    """
    if file_elem is None:
        raise RuntimeError("update_file_elem(): file_elem is None")

    if not new_path or not str(new_path).strip():
        raise RuntimeError("update_file_elem(): new_path is empty")

    new_path = str(new_path).strip()

    # ---- Normalize input path ----
    # If it's a Windows path, convert to WSL for ffprobe; and keep Windows for pathurl/name.
    is_windows = bool(re.match(r"^[A-Za-z]:[\\/]", new_path))
    if is_windows:
        win_path = new_path.replace("/", "\\")
        wsl_path = windows_to_wsl_path(win_path)
    else:
        # Assume WSL input
        wsl_path = new_path
        win_path = wsl_to_windows_path(wsl_path)

    # ---- Build name + pathurl for XML ----
    new_name = os.path.basename(win_path)
    new_url = to_pathurl_windows(win_path)

    # ---- Compute duration (frames at sequence fps) ----
    probe_path = wsl_path if is_wsl() else win_path
    dur_s = ffprobe_duration_seconds(probe_path)
    dur_frames = int(math.ceil(dur_s * int(fps_seq)))

    # ---- Write <name> ----
    n = file_elem.find("name")
    if n is None:
        n = ET.SubElement(file_elem, "name")
    n.text = new_name

    # ---- Write <pathurl> ----
    pu = file_elem.find("pathurl")
    if pu is None:
        pu = ET.SubElement(file_elem, "pathurl")
    pu.text = new_url

    # ---- Write <duration> ----
    d = file_elem.find("duration")
    if d is None:
        d = ET.SubElement(file_elem, "duration")
    d.text = str(dur_frames)

    # ---- Update <rate><timebase> inside <file> if present ----
    rt_tb = file_elem.find("rate/timebase")
    if rt_tb is not None:
        rt_tb.text = str(int(fps_seq))

    # ---- Update <timecode><rate><timebase> if present ----
    tc_tb = file_elem.find("timecode/rate/timebase")
    if tc_tb is not None:
        tc_tb.text = str(int(fps_seq))

    return dur_frames


def remove_timeremap_filters(clipitem):
    for f in list(clipitem.findall("filter")):
        eff = f.find("effect")
        if eff is None:
            continue
        if (eff.findtext("effectid") or "").strip().lower() == "timeremap":
            clipitem.remove(f)

def add_constant_timeremap(clipitem, mediatype: str, speed_percent: float, source_dur_frames: int):
    """
    Minimal constant-speed Time Remap (works for both video & audio in your XML)
    """
    flt = ET.Element("filter")
    eff = ET.SubElement(flt, "effect")

    ET.SubElement(eff, "name").text = "Time Remap"
    ET.SubElement(eff, "effectid").text = "timeremap"
    ET.SubElement(eff, "effectcategory").text = "motion"
    ET.SubElement(eff, "effecttype").text = "motion"
    ET.SubElement(eff, "mediatype").text = mediatype

    p1 = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p1, "parameterid").text = "variablespeed"
    ET.SubElement(p1, "value").text = "0"

    p2 = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p2, "parameterid").text = "speed"
    ET.SubElement(p2, "value").text = f"{speed_percent:.6f}"

    p3 = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p3, "parameterid").text = "reverse"
    ET.SubElement(p3, "value").text = "FALSE"

    p4 = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(p4, "parameterid").text = "frameblending"
    ET.SubElement(p4, "value").text = "FALSE"

    # graphdict (needed in your XML — all your timeremaps have it)
    g = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(g, "parameterid").text = "graphdict"
    ET.SubElement(g, "valuemin").text = "0"
    ET.SubElement(g, "valuemax").text = str(source_dur_frames)
    ET.SubElement(g, "value").text = "0"

    # linear mapping endpoints (Premiere reads these)
    k0 = ET.SubElement(g, "keyframe")
    ET.SubElement(k0, "when").text = "0"
    ET.SubElement(k0, "value").text = "0"

    # end keyframe: use source_dur_frames-1 like your exports
    k1 = ET.SubElement(g, "keyframe")
    timeline_len = int(clipitem.findtext("end")) - int(clipitem.findtext("start"))
    ET.SubElement(k1, "when").text = str(max(1, timeline_len))
    ET.SubElement(k1, "value").text = str(max(0, source_dur_frames - 1))

    clipitem.append(flt)



def set_track_enabled(track_elem, enabled: bool):
    """
    Ensures the track has <enabled>TRUE/FALSE</enabled> and sets it.
    """
    enabled_node = track_elem.find("./enabled")
    if enabled_node is None:
        enabled_node = ET.SubElement(track_elem, "enabled")
    enabled_node.text = "TRUE" if enabled else "FALSE"


def get_sequence_fps(seq_elem):
    rate = seq_elem.find("./rate")
    if rate is None:
        return 24, True
    tb = rate.findtext("./timebase")
    ntsc = rate.findtext("./ntsc")
    fps = int(tb) if tb else 24
    ntsc_bool = (str(ntsc).strip().upper() == "TRUE")
    return fps, ntsc_bool


def ticks_from_frames(frames, fps):
    # frames / fps seconds * ticks_per_second
    return int(round((frames / float(fps)) * PPRO_TICKS_PER_SECOND))


def find_max_numeric_id(root, prefix):
    """
    Finds max N for ids like prefix-N
    """
    mx = 0
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for el in root.iter():
        _id = el.get("id")
        if not _id:
            continue
        m = pat.match(_id)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def find_max_masterclip(root):
    mx = 0
    pat = re.compile(r"^masterclip-(\d+)$")
    for el in root.iter("masterclipid"):
        t = (el.text or "").strip()
        m = pat.match(t)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def normalize_seg_type(seg):
    for k in ("type", "clip_type", "segment_type", "kind"):
        if k in seg and seg[k] is not None:
            return str(seg[k]).strip().lower()
    return "normal"


def seg_start_end_frames(seg, fps):
    """
    Returns (start_frame, end_frame) in SEQUENCE FRAMES.
    Supports your JSON keys (including the 'seqeunce_*' typo).
    """

    # ---- prefer explicit frame keys ----
    frame_pairs = [
        ("seqeunce_start_frames", "seqeunce_end_frames"),   # <-- your JSON (typo)
        ("sequence_start_frames", "sequence_end_frames"),
        ("new_sequence_start_frame", "new_sequence_end_frame"),
        ("sequence_start_frame", "sequence_end_frame"),
        ("start_frame", "end_frame"),
        ("start", "end"),
    ]
    for a, b in frame_pairs:
        if seg.get(a) is not None and seg.get(b) is not None:
            return int(round(float(seg[a]))), int(round(float(seg[b])))

    # ---- seconds keys ----
    sec_pairs = [
        ("seqeunce_start_time_sec", "seqeunce_end_time_sec"),  # <-- your JSON (typo)
        ("sequence_start_time_sec", "sequence_end_time_sec"),
        ("new_sequence_start_seconds", "new_sequence_end_seconds"),
        ("start_seconds", "end_seconds"),
        ("start_sec", "end_sec"),
    ]
    for a, b in sec_pairs:
        if seg.get(a) is not None and seg.get(b) is not None:
            s = float(seg[a]); e = float(seg[b])
            return int(round(s * fps)), int(round(e * fps))

    # ---- fallback: start + duration ----
    # If you have start frames but not end frames, compute end.
    if seg.get("seqeunce_start_frames") is not None and seg.get("eng_duration_frames") is not None:
        st = int(round(float(seg["seqeunce_start_frames"])))
        dur = int(round(float(seg["eng_duration_frames"])))
        return st, st + dur

    raise KeyError(f"Could not find start/end fields in segment keys={list(seg.keys())}")



def get_audio_tracks(sequence_elem):
    audio = sequence_elem.find("./media/audio")
    if audio is None:
        raise RuntimeError("No <media><audio> found in this XML.")
    tracks = audio.findall("./track")
    if not tracks:
        raise RuntimeError("No <track> nodes found under <media><audio>.")
    return audio, tracks


def strip_clipitems(track_elem):
    for child in list(track_elem):
        if child.tag == "clipitem":
            track_elem.remove(child)


def preserve_tail_nodes(track_elem):
    """
    Many tracks end with:
      <enabled>TRUE</enabled>
      <locked>FALSE</locked>
      <outputchannelindex>1</outputchannelindex>
    We want to keep them at the end after we insert clipitems.
    """
    keep = []
    for child in list(track_elem):
        if child.tag in ("enabled", "locked", "outputchannelindex"):
            keep.append(child)
            track_elem.remove(child)
    return keep


def append_tail_nodes(track_elem, tail_nodes):
    for n in tail_nodes:
        track_elem.append(n)

def ensure_output_channel(track_elem, idx: int):
    n = track_elem.find("./outputchannelindex")
    if n is None:
        n = ET.SubElement(track_elem, "outputchannelindex")
    n.text = str(idx)


def make_file_def(file_id, name, pathurl, duration_frames, fps_for_file, ntsc_for_file, sample_rate, channels):
    f = ET.Element("file", {"id": file_id})
    ET.SubElement(f, "name").text = name
    ET.SubElement(f, "pathurl").text = pathurl

    rate = ET.SubElement(f, "rate")
    ET.SubElement(rate, "timebase").text = str(fps_for_file)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc_for_file else "FALSE"

    ET.SubElement(f, "duration").text = str(duration_frames)

    # Minimal timecode block (Premiere accepts this)
    tc = ET.SubElement(f, "timecode")
    tc_rate = ET.SubElement(tc, "rate")
    ET.SubElement(tc_rate, "timebase").text = str(fps_for_file)
    ET.SubElement(tc_rate, "ntsc").text = "TRUE" if ntsc_for_file else "FALSE"
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(f, "media")
    a = ET.SubElement(media, "audio")
    sc = ET.SubElement(a, "samplecharacteristics")
    ET.SubElement(sc, "depth").text = "16"
    ET.SubElement(sc, "samplerate").text = str(sample_rate)
    ET.SubElement(a, "channelcount").text = str(channels)

    return f


def make_audio_clipitem(clipitem_id, masterclip_id, clip_name, file_ref_id,
                        fps_seq, ntsc_seq,
                        timeline_start, timeline_end,
                        src_in, src_out,
                        sourcetrack_index,
                        link_other_clipitem_id, this_trackindex, other_trackindex,
                        clipindex_in_track, groupindex=1,
                        include_full_file_def=None):
    """
    include_full_file_def: optional ET.Element (<file ...> full def) inserted as child
    """
    ci = ET.Element("clipitem", {"id": clipitem_id, "premiereChannelType": "stereo"})
    ET.SubElement(ci, "masterclipid").text = masterclip_id
    ET.SubElement(ci, "name").text = clip_name
    ET.SubElement(ci, "enabled").text = "TRUE"

    # IMPORTANT: clipitem <duration> in this XML typically represents FULL SOURCE duration (not slice length)
    # We'll set it later by caller after we compute full duration frames.
    ET.SubElement(ci, "duration").text = "0"

    rate = ET.SubElement(ci, "rate")
    ET.SubElement(rate, "timebase").text = str(fps_seq)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc_seq else "FALSE"

    ET.SubElement(ci, "start").text = str(timeline_start)
    ET.SubElement(ci, "end").text = str(timeline_end)
    ET.SubElement(ci, "in").text = str(src_in)
    ET.SubElement(ci, "out").text = str(src_out)

    ET.SubElement(ci, "pproTicksIn").text = str(ticks_from_frames(src_in, fps_seq))
    ET.SubElement(ci, "pproTicksOut").text = str(ticks_from_frames(src_out, fps_seq))

    if include_full_file_def is not None:
        # Full definition
        ci.append(include_full_file_def)
    else:
        ET.SubElement(ci, "file", {"id": file_ref_id})

    st = ET.SubElement(ci, "sourcetrack")
    ET.SubElement(st, "mediatype").text = "audio"
    ET.SubElement(st, "trackindex").text = str(sourcetrack_index)

    # links: mimic style in your XML (self + other, with groupindex)
    l1 = ET.SubElement(ci, "link")
    ET.SubElement(l1, "linkclipref").text = clipitem_id
    ET.SubElement(l1, "mediatype").text = "audio"
    ET.SubElement(l1, "trackindex").text = str(this_trackindex)
    ET.SubElement(l1, "clipindex").text = str(clipindex_in_track)
    ET.SubElement(l1, "groupindex").text = str(groupindex)

    l2 = ET.SubElement(ci, "link")
    ET.SubElement(l2, "linkclipref").text = link_other_clipitem_id
    ET.SubElement(l2, "mediatype").text = "audio"
    ET.SubElement(l2, "trackindex").text = str(other_trackindex)
    ET.SubElement(l2, "clipindex").text = str(clipindex_in_track)
    ET.SubElement(l2, "groupindex").text = str(groupindex)

    # optional blocks to match typical Premiere exports (safe)
    li = ET.SubElement(ci, "logginginfo")
    ET.SubElement(li, "description")
    ET.SubElement(li, "scene")
    ET.SubElement(li, "shottake")
    ET.SubElement(li, "lognote")
    ET.SubElement(li, "good")
    ET.SubElement(li, "originalvideofilename")
    ET.SubElement(li, "originalaudiofilename")

    ci_info = ET.SubElement(ci, "colorinfo")
    ET.SubElement(ci_info, "lut")
    ET.SubElement(ci_info, "lut1")
    ET.SubElement(ci_info, "asc_sop")
    ET.SubElement(ci_info, "asc_sat")
    ET.SubElement(ci_info, "lut2")

    return ci

def load_config_values(config_path: Path = CONFIG_PATH) -> dict:
    if not config_path.exists():
        raise RuntimeError(f"Config not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))

def get_config_video_name(cfg: dict) -> str:
    # try common keys (adjust if your config uses different)
    val = (
        cfg.get("video_name")
        or cfg.get("videoName")
        or cfg.get("video_title")
        or cfg.get("title")
        or (cfg.get("video", {}) or {}).get("name")
        or (cfg.get("video", {}) or {}).get("title")
    )
    if not val or not str(val).strip():
        raise RuntimeError("video name missing in config.json (expected keys: video_name/videoName/title/...)")
    return str(val).strip()

def get_config_language_name(cfg: dict) -> str:
    # supports your earlier style
    val = (
        cfg.get("language")
        or cfg.get("targetLanguage")
        or cfg.get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("language")
        or (cfg.get("dubbing", {}) or {}).get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("targetLanguage")
    )
    if not val or not str(val).strip():
        raise RuntimeError("language missing in config.json (expected keys: language/target_lang/...)")
    return str(val).strip()

def normalize_language_caps(lang_name: str) -> str:
    # "polish" / "polish-language" -> "Polish"
    return " ".join(lang_name.replace("-", " ").split()).title()

def language_name_to_upper_iso(lang_name: str) -> str:
    # "Polish" -> "PL" (ElevenLabs style)
    name = " ".join(lang_name.replace("-", " ").split()).strip()
    try:
        lang = pycountry.languages.lookup(name)
    except LookupError:
        lang = pycountry.languages.lookup(name.title())

    code = getattr(lang, "alpha_2", None) or getattr(lang, "alpha_3", None)
    if not code:
        raise ValueError(f"Could not derive ISO code from language name: {lang_name}")
    return code.upper()

def build_fastcontent_audio_destination(video_name: str, language_cap: str, iso_upper: str) -> Path:
    # E:\Fast Content\{Video Name}\{LanguageCap}\{ISO} - {Video Name}.mp3
    base_folder = FAST_CONTENT_BASE / video_name / language_cap / "Audio"
    filename = f"{iso_upper} - {video_name}.mp3"
    return base_folder / filename

def ensure_audio_at_destination(src: Path, dst: Path) -> None:
    if not src.exists():
        raise RuntimeError(f"Source dubbed audio not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Copy file contents first (works reliably on drvfs mounts)
    shutil.copyfile(src, dst)

    # Best-effort metadata copy (may fail on /mnt/<drive>; ignore safely)
    try:
        shutil.copystat(src, dst)
    except PermissionError:
        # drvfs often blocks utime/chmod metadata writes
        pass


def main():
    # -----------------------
    # NEW: derive video+language from config + copy audio into Fast Content structure
    # -----------------------
    def normalize_video_name(video_name: str) -> str:
        s = (video_name or "").strip()
        # Remove: "Video 26", "Video26", "V 26" with or without separators
        s = re.sub(r"^\s*(?:video|v)\s*\d+\s*(?:[-–—:|]\s*)?", "", s, flags=re.IGNORECASE)
        return s.strip()

    cfg = load_config_values(CONFIG_PATH)
    
    video_name = get_config_video_name(cfg)
    video_name = normalize_video_name(video_name)   # ✅ APPLY HERE
    
    language_raw = get_config_language_name(cfg)
    language_cap = normalize_language_caps(language_raw)
    iso_upper = language_name_to_upper_iso(language_cap)
    
    dst_audio = build_fastcontent_audio_destination(video_name, language_cap, iso_upper)
    ensure_audio_at_destination(SOURCE_DUBBED_AUDIO, dst_audio)
    
    # IMPORTANT:
    # - In WSL, dst_audio is already /mnt/e/... so use it directly for ffprobe
    # - In Windows, dst_audio is E:\... and ffprobe will use Windows path
    global DUBBED_AUDIO_PATH
    DUBBED_AUDIO_PATH = str(dst_audio)
    
    # Safety check (so you immediately see if copy went wrong)
    if not Path(DUBBED_AUDIO_PATH).exists():
        raise RuntimeError(f"Copied audio not found at: {DUBBED_AUDIO_PATH}")
    

    print(f"[config] video_name={video_name}")
    print(f"[config] language={language_cap} | iso={iso_upper}")
    print(f"[audio] copied from: {SOURCE_DUBBED_AUDIO}")
    print(f"[audio] using DUBBED_AUDIO_PATH: {DUBBED_AUDIO_PATH}")

    tree = ET.parse(INPUT_XML)
    root = tree.getroot()

    seq = root.find("./sequence")
    if seq is None:
        raise RuntimeError("No <sequence> found at root. Is this a Premiere XML (xmeml)?")

    fps_seq, ntsc_seq = get_sequence_fps(seq)

    with open(SEGMENTS_JSON, "r", encoding="utf-8") as f:
        segments = json.load(f)

    # allow {"segments":[...]}
    if isinstance(segments, dict) and "segments" in segments:
        segments = segments["segments"]

    if not isinstance(segments, list):
        raise RuntimeError("SEGMENTS_JSON must be a list or {segments:[...]}.")

    # Sort by timeline start
    seg_rows = []
    for seg in segments:
        st, en = seg_start_end_frames(seg, fps_seq)
        seg_rows.append((st, en, seg))
    seg_rows.sort(key=lambda x: (x[0], x[1]))

    # Determine new IDs
    next_clipitem_num = find_max_numeric_id(root, "clipitem") + 1
    next_file_num = find_max_numeric_id(root, "file") + 1
    next_masterclip_num = find_max_masterclip(root) + 1

    new_file_id = f"file-{next_file_num}"
    new_masterclip = f"masterclip-{next_masterclip_num}"

    # Audio info (duration in seconds, SR, channels)
    probe_audio_path = windows_to_wsl_path(DUBBED_AUDIO_PATH) if is_wsl() else DUBBED_AUDIO_PATH
    dur_s, sr, ch = ffprobe_audio_info(probe_audio_path)

    # IMPORTANT: Your sequence is 24fps NTSC TRUE in this XML style.
    # We compute source duration frames at SEQUENCE fps for clipitem slicing.
    total_audio_frames = int(math.ceil(dur_s * fps_seq))
    # Many exports have file.duration slightly >= clipitem.duration; we keep it safe.
    file_duration_frames = total_audio_frames

    audio_win_path = wsl_to_windows_path(DUBBED_AUDIO_PATH)
    audio_name = os.path.basename(audio_win_path)
    pathurl = to_pathurl_windows(audio_win_path)

    # Get audio tracks (exploded stereo)
    audio_elem, tracks = get_audio_tracks(seq)
    disc_file = find_disclaimer_file_elem(root)
    if disc_file is None:
        raise RuntimeError("Could not find disclaimer <file> in XML (searches for /Disclaimers/ in pathurl).")

    disclaimer_source_frames = update_file_elem(disc_file, DISCLAIMER_VIDEO_PATH, fps_seq)
    # A1 mute stays (your existing code)
    if len(tracks) >= 1:
        set_track_enabled(tracks[0], False)
    
    # Validate A3 indices exist
    if DISCLAIMER_A3_LEFT > len(tracks) or DISCLAIMER_A3_RIGHT > len(tracks):
        raise ValueError(f"XML has only {len(tracks)} audio tracks; A3 (tracks {DISCLAIMER_A3_LEFT}/{DISCLAIMER_A3_RIGHT}) not available.")


    if TARGET_TRACKINDEX_LEFT < 1 or TARGET_TRACKINDEX_RIGHT < 1:
        raise ValueError("Trackindex must be >= 1.")
    if TARGET_TRACKINDEX_LEFT > len(tracks) or TARGET_TRACKINDEX_RIGHT > len(tracks):
        raise ValueError(f"XML has only {len(tracks)} audio tracks; your target indexes are out of range.")

    left_track = tracks[TARGET_TRACKINDEX_LEFT - 1]
    right_track = tracks[TARGET_TRACKINDEX_RIGHT - 1]

    a3_left_track = tracks[DISCLAIMER_A3_LEFT - 1]
    a3_right_track = tracks[DISCLAIMER_A3_RIGHT - 1]

    # Optional: clear only clipitems, keep enabled/locked/outputchannelindex
    left_tail = preserve_tail_nodes(left_track)
    right_tail = preserve_tail_nodes(right_track)

    a3_left_tail = preserve_tail_nodes(a3_left_track)
    a3_right_tail = preserve_tail_nodes(a3_right_track)

    if CLEAR_TARGET_TRACKS:
        strip_clipitems(left_track)
        strip_clipitems(right_track)
        strip_clipitems(a3_left_track)
        strip_clipitems(a3_right_track)
        

    # Build full <file> definition once (insert only in FIRST new clipitem)
    file_def = make_file_def(
        file_id=new_file_id,
        name=audio_name,
        pathurl=pathurl,
        duration_frames=file_duration_frames,
        fps_for_file=fps_seq,
        ntsc_for_file=False,   # matches your existing file blocks like file-2 having ntsc FALSE
        sample_rate=sr,
        channels=ch
    )

    source_cursor = 0
    clipindex_counter = 1
    
    # IMPORTANT:
    # clipindex_counter is shared across disclaimers + dubbed clips.
    # We need a dedicated flag for whether the main dubbed audio <file> definition
    # has been inserted yet, otherwise a leading disclaimer can skip it entirely.
    main_file_def_inserted = False

    for st, en, seg in seg_rows:
        seg_type = normalize_seg_type(seg)
        length = max(0, en - st)
        if length <= 0:
            continue

        if seg_type in SKIP_TYPES:
            # Skip placing audio, and DO NOT advance source cursor
            continue

        if is_disclaimer(seg):
            required_len = max(0, en - st)
            if required_len <= 0:
                continue

            src_in_d = 0
            src_out_d = min(required_len, disclaimer_source_frames)

            # If disclaimer file shorter than needed, slow it down to fill the segment
            speed_percent = 100.0
            if disclaimer_source_frames > 0 and disclaimer_source_frames < required_len:
                speed_percent = (disclaimer_source_frames / float(required_len)) * 100.0

            a3_left_id = f"clipitem-{next_clipitem_num}"
            a3_right_id = f"clipitem-{next_clipitem_num + 1}"
            next_clipitem_num += 2

            disc_file_id = disc_file.get("id")
            disc_name = disc_file.findtext("name") or "Disclaimer"

            a3L = make_audio_clipitem(
                clipitem_id=a3_left_id,
                masterclip_id="masterclip-2",
                clip_name=disc_name,
                file_ref_id=disc_file_id,
                fps_seq=fps_seq, ntsc_seq=ntsc_seq,
                timeline_start=st, timeline_end=en,
                src_in=src_in_d, src_out=src_out_d,
                sourcetrack_index=1,
                link_other_clipitem_id=a3_right_id,
                this_trackindex=DISCLAIMER_A3_LEFT,
                other_trackindex=DISCLAIMER_A3_RIGHT,
                clipindex_in_track=clipindex_counter,
                groupindex=3,
                include_full_file_def=None
            )

            a3R = make_audio_clipitem(
                clipitem_id=a3_right_id,
                masterclip_id="masterclip-2",
                clip_name=disc_name,
                file_ref_id=disc_file_id,
                fps_seq=fps_seq, ntsc_seq=ntsc_seq,
                timeline_start=st, timeline_end=en,
                src_in=src_in_d, src_out=src_out_d,
                sourcetrack_index=2,
                link_other_clipitem_id=a3_left_id,
                this_trackindex=DISCLAIMER_A3_RIGHT,
                other_trackindex=DISCLAIMER_A3_LEFT,
                clipindex_in_track=clipindex_counter,
                groupindex=3,
                include_full_file_def=None
            )

            # Duration should match SOURCE media duration for that clip's file
            a3L.find("duration").text = str(disclaimer_source_frames)
            a3R.find("duration").text = str(disclaimer_source_frames)

            if speed_percent != 100.0:
                remove_timeremap_filters(a3L)
                remove_timeremap_filters(a3R)
                add_constant_timeremap(a3L, "audio", speed_percent, disclaimer_source_frames)
                add_constant_timeremap(a3R, "audio", speed_percent, disclaimer_source_frames)

            a3_left_track.append(a3L)
            a3_right_track.append(a3R)

            clipindex_counter += 1
            continue


        src_in = source_cursor
        src_out = source_cursor + length

        if src_in >= total_audio_frames:
            break
        if src_out > total_audio_frames:
            src_out = total_audio_frames

        # Create paired clipitems (L/R)
        left_id = f"clipitem-{next_clipitem_num}"
        right_id = f"clipitem-{next_clipitem_num + 1}"
        next_clipitem_num += 2

        # First segment includes full file def; others use <file id="..."/>
        include_file = file_def if not main_file_def_inserted else None

        left_ci = make_audio_clipitem(
            clipitem_id=left_id,
            masterclip_id=new_masterclip,
            clip_name=audio_name,
            file_ref_id=new_file_id,
            fps_seq=fps_seq, ntsc_seq=ntsc_seq,
            timeline_start=st, timeline_end=en,
            src_in=src_in, src_out=src_out,
            sourcetrack_index=1,  # left channel
            link_other_clipitem_id=right_id,
            this_trackindex=TARGET_TRACKINDEX_LEFT,
            other_trackindex=TARGET_TRACKINDEX_RIGHT,
            clipindex_in_track=clipindex_counter,
            groupindex=1,
            include_full_file_def=include_file
        )

        right_ci = make_audio_clipitem(
            clipitem_id=right_id,
            masterclip_id=new_masterclip,
            clip_name=audio_name,
            file_ref_id=new_file_id,
            fps_seq=fps_seq, ntsc_seq=ntsc_seq,
            timeline_start=st, timeline_end=en,
            src_in=src_in, src_out=src_out,
            sourcetrack_index=2,  # right channel
            link_other_clipitem_id=left_id,
            this_trackindex=TARGET_TRACKINDEX_RIGHT,
            other_trackindex=TARGET_TRACKINDEX_LEFT,
            clipindex_in_track=clipindex_counter,
            groupindex=1,
            include_full_file_def=None
        )

        # Set clipitem duration to FULL source duration (this matches your XML convention)
        left_ci.find("duration").text = str(file_duration_frames)
        right_ci.find("duration").text = str(file_duration_frames)

        left_track.append(left_ci)
        right_track.append(right_ci)
        
        if not main_file_def_inserted:
            main_file_def_inserted = True        
        
        clipindex_counter += 1
        source_cursor += length



    append_tail_nodes(left_track, left_tail)
    append_tail_nodes(right_track, right_tail)

    ensure_output_channel(left_track, 1)
    ensure_output_channel(right_track, 2)

    append_tail_nodes(a3_left_track, a3_left_tail)
    append_tail_nodes(a3_right_track, a3_right_tail)

    ensure_output_channel(a3_left_track, 1)
    ensure_output_channel(a3_right_track, 2)


    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)
    print(f"✅ DONE: wrote {OUTPUT_XML}")
    print(f"Sequence FPS used: {fps_seq} (ntsc={ntsc_seq})")
    print(f"Dubbed audio frames: {total_audio_frames} (file duration set to {file_duration_frames})")
    print(f"Inserted segments: {clipindex_counter - 1} into tracks {TARGET_TRACKINDEX_LEFT}/{TARGET_TRACKINDEX_RIGHT}")


if __name__ == "__main__":
    main()
    
