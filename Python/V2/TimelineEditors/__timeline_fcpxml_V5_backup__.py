#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from urllib.parse import quote

# ============================================================
# CONFIG
# ============================================================

@dataclass
class Config:
    # Same folder as this script

    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    XML_IN  = PROJECT_ROOT / "output" / "XML" / "V5__dubbing__.xml"
    JSON_IN  = PROJECT_ROOT / "output" / "JSON" / "A16__final_titles_segments__.json"
    XML_OUT  = PROJECT_ROOT / "output" / "XML" / "V6__dubbing__.xml"

    INPUTS_CONFIG_JSON: str = PROJECT_ROOT / "inputs" / "config" / "config.json"


    # XML_IN: str = "output/XML/V5__dubbing__.xml"
    # JSON_IN: str = "output/JSON/A16__final_titles_segments__.json"
    # XML_OUT: str = "output/XML/V6__dubbing__.xml"

    # Read video_name + language from here
    # INPUTS_CONFIG_JSON: str = "inputs/config/config.json"

    # "V3" in your request = 1-based 3rd <track> under sequence/media/video
    TARGET_VIDEO_TRACK_INDEX_1BASED: int = 3

    # Title clips source folder on Windows (AUTO-BUILT at runtime)
    TITLES_FOLDER_WIN: str = ""  # will be set in main()

    # Title filenames (title_001.mp4, title_002.mp4 ...)
    TITLE_NAME_FMT: str = "title_{:03d}.mp4"

    # IMPORTANT: You said calculate total video frames on 24 (NOT 25)
    VIDEO_FRAMES_FPS: int = 24  # for file duration

    # If ffprobe can't read your Windows path from Linux/WSL, set override frames here:
    TOTAL_FRAMES_OVERRIDE: Optional[int] = None

    VERBOSE: bool = True


TICKS_PER_SECOND = 254016000000  # Premiere ticks constant


# ============================================================
# SMALL HELPERS
# ============================================================

def log(msg: str, cfg: Config) -> None:
    if cfg.VERBOSE:
        print(msg)

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def preserve_header(xml_text: str) -> str:
    """
    Preserve the exact top header lines (XML decl + DOCTYPE) from input.
    Premiere is picky; missing <!DOCTYPE xmeml> can cause 'damaged' import.
    """
    lines = xml_text.splitlines(True)
    header_lines: List[str] = []
    for line in lines[:30]:
        if line.lstrip().startswith("<xmeml"):
            break
        header_lines.append(line)
    header = "".join(header_lines)
    if "<!DOCTYPE xmeml" not in header:
        # Force it if missing
        if not header.strip().startswith("<?xml"):
            header = '<?xml version="1.0" encoding="UTF-8"?>\n' + header
        header = header.rstrip("\n") + "\n<!DOCTYPE xmeml>\n"
    return header

def indent(elem: ET.Element, level: int = 0) -> None:
    """
    Pretty indentation for ElementTree output.
    """
    i = "\n" + level * "\t"
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = i + "\t"
        for child in elem:
            indent(child, level + 1)
        if not (elem.tail or "").strip():
            elem.tail = i
    else:
        if level and not (elem.tail or "").strip():
            elem.tail = i

def windows_to_wsl_path(win_path: str) -> Optional[Path]:
    """
    E:\Folder\File.mp4 -> /mnt/e/Folder/File.mp4 (for probing in WSL/Linux).
    """
    p = win_path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.+)$", p)
    if not m:
        cand = Path(p)
        return cand if cand.exists() else None
    drive = m.group(1).lower()
    rest = m.group(2)
    wsl = Path("/mnt") / drive / rest
    return wsl if wsl.exists() else None


def ffprobe_duration_seconds(local_path: Path) -> Optional[float]:
    """
    Fallback: duration seconds -> frames.
    """
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(local_path)
            ],
            stderr=subprocess.STDOUT
        ).decode("utf-8", "replace").strip()
        return float(out) if out else None
    except Exception:
        return None


def compute_total_frames(cfg: Config, win_path: str) -> int:
    if cfg.TOTAL_FRAMES_OVERRIDE is not None:
        return int(cfg.TOTAL_FRAMES_OVERRIDE)

    local = windows_to_wsl_path(win_path)
    if local is None:
        raise FileNotFoundError(
            f"Cannot access file for probing from this machine:\n{win_path}\n"
            f"Mount drive in WSL (/mnt/e/...) OR set TOTAL_FRAMES_OVERRIDE."
        )

    dur = ffprobe_duration_seconds(local)
    if dur is None:
        raise RuntimeError(
            f"ffprobe failed to read duration for:\n{local}\n"
            f"Install ffprobe/ffmpeg OR set TOTAL_FRAMES_OVERRIDE."
        )

    # ✅ Your rule: compute frames at 24fps (NOT real frame count)
    frames = int(round(dur * cfg.VIDEO_FRAMES_FPS))
    return max(frames, 2)


def pathurl_for_premiere(win_path: str) -> str:
    """
    Must look like:
    file://localhost/E%3a/.../LowerThird%20&amp;%20Title/...
    We MUST keep '&' as '&' (XML serializer will escape to &amp;),
    and URL-encode spaces etc.
    """
    p = win_path.replace("\\", "/")
    encoded = quote(p, safe="/&")          # keep '&' unencoded
    encoded = encoded.replace("%3A", "%3a")  # match Premiere style
    return "file://localhost/" + encoded

def parse_json_items(p: Path) -> List[Dict[str, Any]]:
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of objects.")
    out: List[Dict[str, Any]] = []
    for obj in data:
        if isinstance(obj, dict) and "seqeunce_start_frames" in obj and "seqeunce_end_frames" in obj:
            out.append(obj)
    if not out:
        raise ValueError("No usable JSON items (need seqeunce_start_frames and seqeunce_end_frames).")
    return out

def is_ae_graphic_clipitem(clip: ET.Element) -> bool:
    name = (clip.findtext("name") or "").strip().lower()
    if name == "graphic":
        return True
    f = clip.find("file")
    if f is None:
        return False
    ms = (f.findtext("mediaSource") or "").strip()
    if ms == "GraphicAndType":
        return True
    pu = (f.findtext("pathurl") or "").strip().lower()
    if pu.endswith(".aegraphic"):
        return True
    fn = (f.findtext("name") or "").strip().lower()
    if ".aegraphic" in fn:
        return True
    return False

def set_text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = str(value)
    return el

def remove_children(parent: ET.Element, tag: str) -> None:
    for el in list(parent.findall(tag)):
        parent.remove(el)

def find_max_file_id_number(root: ET.Element) -> int:
    mx = 0
    for f in root.findall(".//file"):
        fid = f.get("id") or ""
        m = re.match(r"file-(\d+)$", fid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx

def ticks_for_frames_at_23976(frames: int) -> int:
    """
    clipitem rate is timebase 24 ntsc TRUE => 23.976 fps.
    ticks_per_frame = TICKS_PER_SECOND / 23.976 = TICKS_PER_SECOND*1001/(24*1000)
    """
    ticks_per_frame = (TICKS_PER_SECOND * 1001) / (24 * 1000)
    return int(round(frames * ticks_per_frame))

def build_timeremap_filter(speed_percent: float, timeline_len: int, file_total_frames: int) -> ET.Element:
    """
    Matches your working example exactly (2 keyframes).
    graphdict valuemax/value uses file_total_frames.
    """
    flt = ET.Element("filter")
    eff = ET.SubElement(flt, "effect")

    ET.SubElement(eff, "name").text = "Time Remap"
    ET.SubElement(eff, "effectid").text = "timeremap"
    ET.SubElement(eff, "effectcategory").text = "motion"
    ET.SubElement(eff, "effecttype").text = "motion"
    ET.SubElement(eff, "mediatype").text = "video"

    def add_param(pid: str, name: str, value: str, vmin: Optional[str] = None, vmax: Optional[str] = None) -> ET.Element:
        p = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
        ET.SubElement(p, "parameterid").text = pid
        ET.SubElement(p, "name").text = name
        if vmin is not None:
            ET.SubElement(p, "valuemin").text = vmin
        if vmax is not None:
            ET.SubElement(p, "valuemax").text = vmax
        ET.SubElement(p, "value").text = value
        return p

    add_param("variablespeed", "variablespeed", "0", "0", "1")
    add_param("speed", "speed", f"{speed_percent:.3f}".rstrip("0").rstrip("."), "-100000", "100000")
    add_param("reverse", "reverse", "FALSE")
    add_param("frameblending", "frameblending", "FALSE")

    gd = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(gd, "parameterid").text = "graphdict"
    ET.SubElement(gd, "name").text = "graphdict"
    ET.SubElement(gd, "valuemin").text = "0"
    ET.SubElement(gd, "valuemax").text = str(file_total_frames)
    ET.SubElement(gd, "value").text = "0"

    k1 = ET.SubElement(gd, "keyframe")
    ET.SubElement(k1, "when").text = "0"
    ET.SubElement(k1, "value").text = "0"
    ET.SubElement(k1, "speedvirtualkf").text = "TRUE"
    ET.SubElement(k1, "speedkfin").text = "TRUE"

    k2 = ET.SubElement(gd, "keyframe")
    ET.SubElement(k2, "when").text = str(timeline_len)
    ET.SubElement(k2, "value").text = str(file_total_frames)
    ET.SubElement(k2, "speedvirtualkf").text = "TRUE"
    ET.SubElement(k2, "speedkfout").text = "TRUE"

    interp = ET.SubElement(gd, "interpolation")
    ET.SubElement(interp, "name").text = "FCPCurve"

    return flt


def build_file_block(file_id: str, win_path: str, file_total_frames: int) -> ET.Element:
    """
    Builds <file> exactly like your working example:
    - rate 24 / ntsc FALSE everywhere in file
    - includes audio samplecharacteristics + channelcount
    """
    f = ET.Element("file", {"id": file_id})
    ET.SubElement(f, "name").text = Path(win_path.replace("\\", "/")).name
    ET.SubElement(f, "pathurl").text = pathurl_for_premiere(win_path)

    r = ET.SubElement(f, "rate")
    ET.SubElement(r, "timebase").text = "24"
    ET.SubElement(r, "ntsc").text = "FALSE"

    ET.SubElement(f, "duration").text = str(file_total_frames)

    tc = ET.SubElement(f, "timecode")
    tcr = ET.SubElement(tc, "rate")
    ET.SubElement(tcr, "timebase").text = "24"
    ET.SubElement(tcr, "ntsc").text = "FALSE"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(f, "media")

    # video
    v = ET.SubElement(media, "video")
    sc = ET.SubElement(v, "samplecharacteristics")
    scr = ET.SubElement(sc, "rate")
    ET.SubElement(scr, "timebase").text = "24"
    ET.SubElement(scr, "ntsc").text = "FALSE"
    ET.SubElement(sc, "width").text = "1920"
    ET.SubElement(sc, "height").text = "1080"
    ET.SubElement(sc, "anamorphic").text = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"

    # audio (to match your example)
    a = ET.SubElement(media, "audio")
    asc = ET.SubElement(a, "samplecharacteristics")
    ET.SubElement(asc, "depth").text = "16"
    ET.SubElement(asc, "samplerate").text = "48000"
    ET.SubElement(a, "channelcount").text = "2"

    return f


# ============================================================
# NEW: Dynamic TITLES_FOLDER_WIN builder (ONLY CHANGE YOU ASKED)
# ============================================================

_LANG_MAP = {
    # codes
    "en": "English",
    "es": "Spanish",
    "pl": "Polish",
    "fr": "French",
    "cz": "Czech",
    "cs": "Czech",
    "ru": "Russian",
    "br": "Portuguese-BR",
    "pt-br": "Portuguese-BR",
    "ptbr": "Portuguese-BR",
    "hr": "Croatian",
    "da": "Danish",
    "de": "German",
    "it": "Italian",
    "tr": "Turkish",
    "ko": "Korean",
    # names (common)
    "english": "English",
    "spanish": "Spanish",
    "polish": "Polish",
    "french": "French",
    "czech": "Czech",
    "russian": "Russian",
    "croatian": "Croatian",
    "danish": "Danish",
    "german": "German",
    "italian": "Italian",
    "turkish": "Turkish",
    "korean": "Korean",
    "portuguese-br": "Portuguese-BR",
    "portuguese br": "Portuguese-BR",
    "brazilian": "Portuguese-BR",
    "brazilian portuguese": "Portuguese-BR",
    "portuguese": "Portuguese",  # if you ever pass plain pt/portuguese
}

def language_to_folder_cap(lang_raw: str) -> str:
    key = (lang_raw or "").strip().lower()
    if not key:
        return "English"
    key = key.replace("_", "-")
    return _LANG_MAP.get(key) or key.title()

def clean_video_name(video_name: str) -> str:
    """
    Input:  Video 11 - Something
            Video 11: Something
            Video 11 – Something
            Video 11 — Something
    Output: Something
    """
    s = (video_name or "").strip()

    # Normalize common dash variants so the regex is robust
    # (we still accept real – — directly in the regex too)
    # Do NOT change content beyond prefix removal.
    prefix_re = re.compile(r"^\s*Video\s*\d+\s*[-–—:]\s*", re.IGNORECASE)
    s = prefix_re.sub("", s).strip()

    # Also handle weird spacing like "Video 11  -  "
    s = re.sub(r"^\s*Video\s*\d+\s*[-–—:]\s*", "", s, flags=re.IGNORECASE).strip()
    return s

def build_titles_folder_win_from_config(base_dir: Path, cfg: Config) -> str:
    """
    Builds exactly:
    E:\Fast Content\<VideoNameClean>\<LanguageCap>\Titles
    """
    cfg_path = (base_dir / cfg.INPUTS_CONFIG_JSON)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    video_name = (data.get("video_name") or "").strip()
    language = (data.get("language") or "").strip()

    if not video_name:
        raise ValueError(f"'video_name' missing/empty in {cfg_path}")
    if not language:
        raise ValueError(f"'language' missing/empty in {cfg_path}")

    video_clean = clean_video_name(video_name)
    lang_cap = language_to_folder_cap(language)

    # MUST be Windows path string with backslashes
    return r"Z:\Automated Dubbings\Projects" + "\\" + video_clean + "\\" + lang_cap + "\\Titles" + "\\VideoClips"


def main() -> int:
    cfg = Config()

    base = Path(__file__).resolve().parent

    # ✅ ONLY CHANGE: auto-build TITLES_FOLDER_WIN from inputs/config/config.json
    cfg.TITLES_FOLDER_WIN = build_titles_folder_win_from_config(base, cfg)
    log(f"TITLES_FOLDER_WIN = {cfg.TITLES_FOLDER_WIN}", cfg)

    xml_in = base / cfg.XML_IN
    json_in = base / cfg.JSON_IN
    xml_out = base / cfg.XML_OUT

    if not xml_in.exists():
        raise FileNotFoundError(f"XML not found: {xml_in}")
    if not json_in.exists():
        raise FileNotFoundError(f"JSON not found: {json_in}")

    xml_text = read_text(xml_in)
    header = preserve_header(xml_text)

    items = parse_json_items(json_in)

    tree = ET.parse(str(xml_in))
    root = tree.getroot()

    video_el = root.find(".//sequence/media/video")
    if video_el is None:
        raise ValueError("Could not find <sequence><media><video> in XML.")

    tracks = list(video_el.findall("track"))
    if len(tracks) < cfg.TARGET_VIDEO_TRACK_INDEX_1BASED:
        raise ValueError(f"XML has {len(tracks)} video tracks; requested V{cfg.TARGET_VIDEO_TRACK_INDEX_1BASED}.")

    target_track = tracks[cfg.TARGET_VIDEO_TRACK_INDEX_1BASED - 1]
    clipitems = list(target_track.findall("clipitem"))

    ae_clips = [c for c in clipitems if is_ae_graphic_clipitem(c)]
    log(f"V{cfg.TARGET_VIDEO_TRACK_INDEX_1BASED}: clipitems={len(clipitems)} | AE graphics={len(ae_clips)}", cfg)

    if not ae_clips:
        raise ValueError("No AE graphic clipitems found on the target track.")

    # Replace only up to min(AE clips, JSON items)
    n = min(len(ae_clips), len(items))
    log(f"Replacing N={n} clips with title_001..title_{n:03}.mp4", cfg)

    # Unique file ids
    next_file_num = find_max_file_id_number(root) + 1

    for i in range(n):
        clip = ae_clips[i]
        j = items[i]

        start = int(j["seqeunce_start_frames"])
        end = int(j["seqeunce_end_frames"])
        timeline_len = max(end - start, 1)

        title_name = cfg.TITLE_NAME_FMT.format(i + 1)
        win_path = cfg.TITLES_FOLDER_WIN.rstrip("\\/") + "\\" + title_name

        file_total_frames = compute_total_frames(cfg, win_path)  # at 24 fps rule
        speed_percent = (file_total_frames / timeline_len) * 100.0

        # --- clipitem core tags (match your working example) ---
        set_text(clip, "name", title_name)
        set_text(clip, "enabled", "TRUE")

        # clip duration/out MUST be timeline_len
        set_text(clip, "duration", str(timeline_len))

        # clip rate MUST be 24 + ntsc TRUE
        rate_el = clip.find("rate")
        if rate_el is None:
            rate_el = ET.SubElement(clip, "rate")
        set_text(rate_el, "timebase", "24")
        set_text(rate_el, "ntsc", "TRUE")

        set_text(clip, "start", str(start))
        set_text(clip, "end", str(end))
        set_text(clip, "in", "0")
        set_text(clip, "out", str(timeline_len))

        # pproTicks:
        # If already present, keep (safe). If missing, set with a sane default.
        if clip.find("pproTicksIn") is None:
            set_text(clip, "pproTicksIn", "0")
        if clip.find("pproTicksOut") is None:
            # default = ticks for timeline_len at 23.976
            set_text(clip, "pproTicksOut", str(ticks_for_frames_at_23976(timeline_len)))

        set_text(clip, "alphatype", "none")
        set_text(clip, "pixelaspectratio", "square")
        set_text(clip, "anamorphic", "FALSE")

        # Remove old file + insert new unique file
        old_file = clip.find("file")
        if old_file is not None:
            clip.remove(old_file)

        file_id = f"file-{next_file_num}"
        next_file_num += 1
        clip.insert(0, build_file_block(file_id, win_path, file_total_frames))

        # Replace filters with Time Remap ONLY (exact structure like your working example)
        remove_children(clip, "filter")
        clip.append(build_timeremap_filter(speed_percent, timeline_len, file_total_frames))

    # Pretty indentation
    indent(root)

    # Write output with preserved header + body
    body = ET.tostring(root, encoding="unicode")
    xml_out.write_text(header + body, encoding="utf-8")

    log(f"✅ DONE. Output: {xml_out}", cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
