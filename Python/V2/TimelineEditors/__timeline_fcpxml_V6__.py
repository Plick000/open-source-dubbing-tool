#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
from urllib.parse import quote

# ============================================================
# CONFIG
# ============================================================

@dataclass
class Config:
    # Same folder as this script
    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    # ✅ V6 input -> V7 output (as requested)
    XML_IN: str = PROJECT_ROOT / "output" / "XML" / "V6__dubbing__.xml"
    JSON_IN: str = PROJECT_ROOT / "output" / "JSON" / "A18__computed_lowerthirds__.json"
    XML_OUT: str = PROJECT_ROOT / "output" / "XML" / "V7__dubbing__.xml"

    INPUTS_CONFIG_JSON: str = PROJECT_ROOT / "inputs" / "config" / "config.json"

    # ✅ Remove AEGraphic on V4 completely
    TARGET_VIDEO_TRACK_INDEX_1BASED: int = 4

    # ✅ LowerThird clips source folder on Windows (AUTO-BUILT at runtime)
    LOWERTHIRDS_FOLDER_WIN: str = ""  # will be set in main()

    # Lowerthird filenames
    CLIP_NAME_FMT: str = "lowerthird_{:03d}.mov"

    # Compute total frames at 24fps
    VIDEO_FRAMES_FPS: int = 24

    TOTAL_FRAMES_OVERRIDE: Optional[int] = None

    VERBOSE: bool = True


TICKS_PER_SECOND = 254016000000  # Premiere ticks constant


# ============================================================
# SMALL HELPERS
# ============================================================

def log(cfg: Config, msg: str) -> None:
    if cfg.VERBOSE:
        print(msg)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def preserve_header(xml_text: str) -> str:
    """
    Preserve everything before the <xmeml ...> root element but DO NOT add a DOCTYPE.

    Rationale:
    - Your known-good XML (V5) imports in Premiere without a <!DOCTYPE xmeml>.
    - Adding a DOCTYPE has repeatedly caused Premiere import failures in your pipeline.
    """
    lines = xml_text.splitlines(True)
    header_lines: List[str] = []
    for line in lines[:80]:
        if line.lstrip().startswith("<xmeml"):
            break
        # Drop any DOCTYPE lines if present
        if "<!DOCTYPE xmeml" in line:
            continue
        header_lines.append(line)

    header = "".join(header_lines)

    # Ensure XML declaration exists
    if not header.strip().startswith("<?xml"):
        header = '<?xml version="1.0" encoding="UTF-8"?>\n' + header

    if not header.endswith("\n"):
        header += "\n"

    return header


def strip_doctype_for_parse(xml_text: str) -> str:
    """ElementTree parsing is safer when DOCTYPE is removed."""
    return re.sub(r"<!DOCTYPE[^>]*>\s*", "", xml_text)


def indent(elem: ET.Element, level: int = 0) -> None:
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
    p = win_path.replace("\\", "/")
    
    # normalize: Z://something -> Z:/something
    p = re.sub(r"^([A-Za-z]):/+", r"\1:/", p)
    
    m = re.match(r"^([A-Za-z]):/(.+)$", p)
    if not m:
        cand = Path(p)
        return cand if cand.exists() else None
    
    drive = m.group(1).lower()
    rest = m.group(2)
    wsl = Path("/mnt") / drive / rest
    return wsl if wsl.exists() else None


def ffprobe_duration_seconds(local_path: Path) -> Optional[float]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(local_path),
            ],
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace").strip()

        # Extract the last float-like number from the output
        nums = re.findall(r"\d+(?:\.\d+)?", out)
        return float(nums[-1]) if nums else None
    except Exception:
        return None


def compute_total_frames(cfg: Config, win_path: str) -> int:
    if cfg.TOTAL_FRAMES_OVERRIDE is not None:
        return int(cfg.TOTAL_FRAMES_OVERRIDE)

    local = windows_to_wsl_path(win_path)
    if local is None:
        # Fallback: keep pipeline running. For accurate speed, mount Z: in WSL.
        if cfg.TOTAL_FRAMES_OVERRIDE is not None:
            return int(cfg.TOTAL_FRAMES_OVERRIDE)
        return 0  # signal caller to use timeline_len

    dur = ffprobe_duration_seconds(local)
    if dur is None:
        raise RuntimeError(
            f"ffprobe failed to read duration for:\n{local}\n"
            f"Install ffprobe/ffmpeg OR set TOTAL_FRAMES_OVERRIDE."
        )

    frames = int(round(dur * cfg.VIDEO_FRAMES_FPS))
    return max(frames, 2)


def pathurl_for_premiere(win_path: str) -> str:
    p = win_path.replace("\\", "/")
    encoded = quote(p, safe="/&")
    encoded = encoded.replace("%3A", "%3a")
    return "file://localhost/" + encoded


def resolve_file_element(root: ET.Element, file_el: ET.Element) -> ET.Element:
    """
    clipitem/<file> can be either:
      - an inline <file> block with children, OR
      - a reference like <file id="file-4"/> (no children)

    Premiere/FCPXML exports often use references heavily. To reliably detect
    AEGraphic placeholders, we resolve the reference to the full <file> node.
    """
    if file_el is None:
        return file_el
    if len(list(file_el)) > 0:
        return file_el
    fid = file_el.get("id")
    if not fid or root is None:
        return file_el
    for f in root.findall(f".//file[@id='{fid}']"):
        if len(list(f)) > 0:
            return f
    return file_el


def is_ae_graphic_clipitem(clip: ET.Element, root: Optional[ET.Element] = None) -> bool:
    """
    Detect AE Graphic placeholder clipitems (GraphicAndType / .aegraphic).
    Supports both inline <file> blocks and <file id="..."/> references.
    """
    name = (clip.findtext("name") or "").strip().lower()
    if name == "graphic":
        return True

    f_ref = clip.find("file")
    if f_ref is None:
        return False

    f = resolve_file_element(root, f_ref) if root is not None else f_ref

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


def find_max_id_number(root: ET.Element, tag: str, prefix: str) -> int:
    mx = 0
    for el in root.findall(f".//{tag}"):
        eid = el.get("id") or ""
        m = re.match(rf"{re.escape(prefix)}-(\d+)$", eid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def ticks_for_frames_at_23976(frames: int) -> int:
    ticks_per_frame = (TICKS_PER_SECOND * 1001) / (24 * 1000)
    return int(round(frames * ticks_per_frame))


def build_timeremap_filter(speed_percent: float, timeline_len: int, file_total_frames: int) -> ET.Element:
    flt = ET.Element("filter")
    eff = ET.SubElement(flt, "effect")

    ET.SubElement(eff, "name").text = "Time Remap"
    ET.SubElement(eff, "effectid").text = "timeremap"
    ET.SubElement(eff, "effectcategory").text = "motion"
    ET.SubElement(eff, "effecttype").text = "motion"
    ET.SubElement(eff, "mediatype").text = "video"

    def add_param(pid: str, name: str, value: str, vmin: Optional[str] = None, vmax: Optional[str] = None) -> None:
        p = ET.SubElement(eff, "parameter", {"authoringApp": "PremierePro"})
        ET.SubElement(p, "parameterid").text = pid
        ET.SubElement(p, "name").text = name
        if vmin is not None:
            ET.SubElement(p, "valuemin").text = vmin
        if vmax is not None:
            ET.SubElement(p, "valuemax").text = vmax
        ET.SubElement(p, "value").text = value

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


def parse_lowerthird_items(json_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of objects.")

    out: List[Dict[str, Any]] = []
    for obj in data:
        if isinstance(obj, dict) and "Final LT Timeline Start Frame" in obj and "Final LT Timeline End Frame" in obj:
            out.append(obj)

    if not out:
        raise ValueError("No usable JSON items found (Final LT Timeline Start/End Frame).")

    out.sort(key=lambda x: int(x["Final LT Timeline Start Frame"]))
    return out


def build_file_block_video_only(file_id: str, win_path: str, file_total_frames: int) -> ET.Element:
    f = ET.Element("file", {"id": file_id})
    ET.SubElement(f, "name").text = Path(win_path.replace("\\", "/")).name
    ET.SubElement(f, "pathurl").text = pathurl_for_premiere(win_path)

    r = ET.SubElement(f, "rate")
    ET.SubElement(r, "timebase").text = "24"
    ET.SubElement(r, "ntsc").text = "FALSE"

    ET.SubElement(f, "duration").text = str(file_total_frames)

    # ✅ FIX: Premiere expects <string> and <frame> inside <timecode>
    tc = ET.SubElement(f, "timecode")
    tcr = ET.SubElement(tc, "rate")
    ET.SubElement(tcr, "timebase").text = "24"
    ET.SubElement(tcr, "ntsc").text = "FALSE"
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(f, "media")
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

    return f


def build_lowerthird_clipitem(
    clip_id: str,
    file_id: str,
    media_name: str,
    win_path: str,
    start: int,
    end: int,
    timeline_len: int,
    file_total_frames: int,
) -> ET.Element:

    speed_percent = (file_total_frames / timeline_len) * 100.0

    ci = ET.Element("clipitem", {"id": clip_id})

    set_text(ci, "name", media_name)
    set_text(ci, "enabled", "TRUE")
    set_text(ci, "duration", str(timeline_len))

    rate = ET.SubElement(ci, "rate")
    set_text(rate, "timebase", "24")
    set_text(rate, "ntsc", "TRUE")

    set_text(ci, "start", str(start))
    set_text(ci, "end", str(end))
    set_text(ci, "in", "0")
    set_text(ci, "out", str(timeline_len))

    set_text(ci, "pproTicksIn", "0")
    set_text(ci, "pproTicksOut", str(ticks_for_frames_at_23976(timeline_len)))

    set_text(ci, "alphatype", "none")
    set_text(ci, "pixelaspectratio", "square")
    set_text(ci, "anamorphic", "FALSE")

    # linked file (video-only)
    ci.append(build_file_block_video_only(file_id=file_id, win_path=win_path, file_total_frames=file_total_frames))

    # timeremap
    ci.append(build_timeremap_filter(speed_percent=speed_percent, timeline_len=timeline_len, file_total_frames=file_total_frames))

    return ci


# ============================================================
# Dynamic LOWERTHIRDS_FOLDER_WIN builder
# ============================================================

_LANG_MAP = {
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
    "portuguese": "Portuguese",
}


def language_to_folder_cap(lang_raw: str) -> str:
    key = (lang_raw or "").strip().lower()
    if not key:
        return "English"
    key = key.replace("_", "-")
    return _LANG_MAP.get(key) or key.title()


def clean_video_name(video_name: str) -> str:
    s = (video_name or "").strip()
    # remove: Video 11 -  / Video 11: / Video 11 – / Video 11 —
    s = re.sub(r"^\s*Video\s*\d+\s*[-–—:]\s*", "", s, flags=re.IGNORECASE).strip()
    return s


def build_lowerthirds_folder_win_from_config(base_dir: Path, cfg: Config) -> str:
    r"""
    Builds exactly:
    E:\Fast Content\<VideoNameClean>\<LanguageCap>\LowerThirds\VideoClips
    """
    cfg_path = base_dir / cfg.INPUTS_CONFIG_JSON
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

    return r"Z:\Automated Dubbings\Projects" + "\\" + video_clean + "\\" + lang_cap + r"\LowerThirds\VideoClips"


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    cfg = Config()
    base = Path(__file__).resolve().parent

    cfg.LOWERTHIRDS_FOLDER_WIN = build_lowerthirds_folder_win_from_config(base, cfg)
    log(cfg, f"LOWERTHIRDS_FOLDER_WIN = {cfg.LOWERTHIRDS_FOLDER_WIN}")

    xml_in = base / cfg.XML_IN
    json_in = base / cfg.JSON_IN
    xml_out = base / cfg.XML_OUT

    if not xml_in.exists():
        raise FileNotFoundError(f"XML not found: {xml_in}")
    if not json_in.exists():
        raise FileNotFoundError(f"JSON not found: {json_in}")

    xml_text = read_text(xml_in)

    # ✅ FIX: preserve header but NEVER add DOCTYPE
    header = preserve_header(xml_text)

    try:
        items = parse_lowerthird_items(json_in)
    except ValueError as e:
        # If there are NO lowerthirds, do NOTHING to the XML timeline,
        # but still write the output file so the pipeline can continue.
        log(cfg, f"⚠ {e}  -> No lowerthird items. Writing passthrough XML (no changes).")

        xml_out.parent.mkdir(parents=True, exist_ok=True)

        # Keep XML as-is, but remove DOCTYPE if present (same safety rule as normal path)
        xml_out.write_text(strip_doctype_for_parse(xml_text), encoding="utf-8")

        log(cfg, f"✅ DONE (passthrough). Output: {xml_out}")
        return 0


    # ✅ FIX: parse safely even if V6 had a DOCTYPE
    xml_for_parse = strip_doctype_for_parse(xml_text)
    root = ET.fromstring(xml_for_parse)

    video_el = root.find(".//sequence/media/video")
    if video_el is None:
        raise ValueError("Could not find <sequence><media><video> in XML.")

    tracks = list(video_el.findall("track"))
    if len(tracks) < cfg.TARGET_VIDEO_TRACK_INDEX_1BASED:
        raise ValueError(f"XML has {len(tracks)} video tracks; requested V{cfg.TARGET_VIDEO_TRACK_INDEX_1BASED}.")

    target_track = tracks[cfg.TARGET_VIDEO_TRACK_INDEX_1BASED - 1]

    # ✅ Remove AEGraphics from V4 completely (supports <file id="..."/> references)
    existing = list(target_track.findall("clipitem"))
    ae_to_remove = [c for c in existing if is_ae_graphic_clipitem(c, root)]
    for c in ae_to_remove:
        target_track.remove(c)
    log(cfg, f"V{cfg.TARGET_VIDEO_TRACK_INDEX_1BASED}: removed AE graphics = {len(ae_to_remove)}")

    # new ids
    next_file_num = find_max_id_number(root, "file", "file") + 1
    next_clip_num = find_max_id_number(root, "clipitem", "clipitem") + 1

    # ✅ Insert fresh linked lowerthirds (video-only + timeremap)
    for idx, j in enumerate(items, start=1):
        start = int(j["Final LT Timeline Start Frame"])
        end = int(j["Final LT Timeline End Frame"])
        timeline_len = max(end - start, 1)

        media_name = cfg.CLIP_NAME_FMT.format(idx)
        win_path = cfg.LOWERTHIRDS_FOLDER_WIN.rstrip("\\/") + "\\" + media_name

        file_total_frames = compute_total_frames(cfg, win_path)
        if file_total_frames <= 0:
            file_total_frames = timeline_len  # safe fallback

        file_id = f"file-{next_file_num}"
        next_file_num += 1

        clip_id = f"clipitem-{next_clip_num}"
        next_clip_num += 1

        ci = build_lowerthird_clipitem(
            clip_id=clip_id,
            file_id=file_id,
            media_name=media_name,
            win_path=win_path,
            start=start,
            end=end,
            timeline_len=timeline_len,
            file_total_frames=file_total_frames,
        )

        target_track.append(ci)

    indent(root)

    body = ET.tostring(root, encoding="unicode")

    # ✅ Output with preserved header and NO DOCTYPE
    xml_out.parent.mkdir(parents=True, exist_ok=True)
    xml_out.write_text(header + body, encoding="utf-8")

    log(cfg, f"✅ DONE. Output: {xml_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())