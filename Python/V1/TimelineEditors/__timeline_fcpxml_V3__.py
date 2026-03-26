#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import copy
import json
import re
import shutil
import platform
import urllib.parse
import errno
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import xml.etree.ElementTree as ET

try:
    import pycountry  # type: ignore
except Exception:
    pycountry = None  # fallback mapping will be used


# =========================
# CONFIG
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_INPUT_XML = PROJECT_ROOT / "output" / "XML" / "V3__dubbing__.xml"
DEFAULT_JSON = PROJECT_ROOT / "output" / "JSON" / "A4__dubbing__.json"
DEFAULT_OUTPUT_XML = PROJECT_ROOT / "output" / "XML" / "V4__dubbing__.xml"

DEFAULT_CONFIG_JSON = PROJECT_ROOT / "inputs" / "config" / "config.json"

SRC_ROOT = PROJECT_ROOT / "output" / "samples"

# DEFAULT_INPUT_XML = "output/XML/V3__dubbing__.xml"
# DEFAULT_JSON = "output/JSON/A6__interviews__.json"
# DEFAULT_OUTPUT_XML = "output/XML/V4__dubbing__.xml"

# # NEW: config.json (language + video name)
# DEFAULT_CONFIG_JSON = "inputs/config/config.json"

# Source (read):
# output/samples/{LanguageCap}/interview_{clip_id:03d}_id{clip_id}/dubbed_{shortLanguagelowercase}.mp3
# SRC_ROOT = Path("output") / "samples"
SRC_FILENAME_TMPL = "dubbed_{short}.mp3"

# Destination (copy + XML references):
# E:\Fast Content\{VideoName}\{LanguageCap}\interviews\interview_{clip_id:03d}_id{clip_id}\interview_{clip_id:03d}_id{clip_id}.mp3
WIN_FAST_CONTENT_ROOT = Path(r"Z:\Automated Dubbings\Projects")

TARGET_TYPE = "interview"
TARGET_JSON_TRACK = "V1"

PREMIERE_TICKS_PER_SECOND = 254016000000

# ✅ INSERT AFTER A4 => new interview pair becomes A5/A6
INSERT_AFTER_TRACK_1BASED = 4

# If True: still create clips even if MP3 missing (they appear OFFLINE in Premiere)
ALLOW_MISSING_AUDIO = False


# =========================
# PATH / OS HELPERS (same style as dubbing scripts)
# =========================
def is_wsl() -> bool:
    """
    True if running under WSL.
    """
    try:
        return "microsoft" in platform.uname().release.lower()
    except Exception:
        return False


def windows_to_wsl_path(win_path: str) -> str:
    """
    Convert: E:\Fast Content\X -> /mnt/e/Fast Content/X
    If not a drive path, return as-is.
    """
    p = win_path.replace("/", "\\").strip()
    m = re.match(r"^([A-Za-z]):\\(.*)$", p)
    if not m:
        return p
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def wsl_to_windows_path(wsl_path: str) -> str:
    """
    Convert: /mnt/e/Fast Content/X -> E:\Fast Content\X
    If not /mnt/<drive>/..., return as-is.
    """
    p = wsl_path.strip()
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if not m:
        return p
    drive = m.group(1).upper()
    rest = m.group(2).replace("/", "\\")
    return f"{drive}:\\{rest}"


def to_pathurl_windows(win_full_path: str) -> str:
    """
    Premiere-friendly pathurl:
      file://localhost/E%3A/Fast%20Content/...
    """
    p = win_full_path.replace("\\", "/")
    quoted = urllib.parse.quote(p, safe="/")
    quoted = quoted.replace("%3A", "%3a")
    return "file://localhost/" + quoted


# Backwards-compat alias (your old name)
def win_to_pathurl(win_full_path: str) -> str:
    return to_pathurl_windows(win_full_path)


# =========================
# JSON / CONFIG HELPERS
# =========================
def load_json_entries(json_path: Path) -> List[Dict[str, Any]]:
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON root must be a list")

    entries = [
        it for it in data
        if it.get("type") == TARGET_TYPE and it.get("track") == TARGET_JSON_TRACK
    ]

    # dedupe by (id,start,end)
    seen = set()
    uniq = []
    for it in entries:
        key = (it.get("id"), it.get("seqeunce_start_frames"), it.get("seqeunce_end_frames"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    uniq.sort(key=lambda x: int(x["seqeunce_start_frames"]))
    print(f"Loaded {len(uniq)} {TARGET_JSON_TRACK} interview items from JSON.")
    return uniq


def _find_config_path_near(json_path: Path) -> Optional[Path]:
    """
    Best-effort search order:
      1) sibling of interview JSON: output/JSON/config.json
      2) working dir: ./config.json
      3) output/config.json
    """
    cand1 = json_path.parent / "config.json"
    if cand1.exists():
        return cand1
    cand2 = Path(DEFAULT_CONFIG_JSON)
    if cand2.exists():
        return cand2
    cand3 = Path("output") / "config.json"
    if cand3.exists():
        return cand3
    return None


def load_config(json_path: Path, explicit_config: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = explicit_config if explicit_config is not None else _find_config_path_near(json_path)
    if cfg_path is None or not cfg_path.exists():
        raise FileNotFoundError(
            "config.json not found. Looked near the interview JSON and common locations."
        )
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json root must be an object/dict")
    return data


def get_language_name_from_config(cfg: Dict[str, Any]) -> str:
    for k in ("language", "Language", "lang", "Lang"):
        v = cfg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise KeyError("config.json missing language (expected key like 'language')")


def get_videoname_from_config(cfg: Dict[str, Any]) -> str:
    for k in ("VideoName", "videoName", "video_name", "videoname", "name", "title"):
        v = cfg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise KeyError("config.json missing VideoName (expected key like 'VideoName' or 'videoName')")


def language_capitalize(name: str) -> str:
    # "polish" -> "Polish", "portuguese-br" -> "Portuguese-Br"
    s = (name or "").strip()
    if not s:
        return s
    return s[:1].upper() + s[1:]


_FALLBACK_LANG_TO_ISO2 = {
    "polish": "pl",
    "french": "fr",
    "spanish": "es",
    "russian": "ru",
    "czech": "cs",
    "croatian": "hr",
    "turkish": "tr",
    "korean": "ko",
    "portuguese": "pt",
    "portuguese-br": "pt",
    "portuguese (brazil)": "pt",
    "brazilian portuguese": "pt",
    "arabic": "ar",
    "german": "de",
    "italian": "it",
    "hindi": "hi",
    "urdu": "ur",
    "japanese": "ja",
    "dutch": "nl",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "romanian": "ro",
    "hungarian": "hu",
    "ukrainian": "uk",
    "greek": "el",
    "hebrew": "he",
    "persian": "fa",
}


def language_to_iso2_short(name: str) -> str:
    """
    Return lowercase ISO-639-1 alpha_2 code (e.g., Polish -> pl).
    Uses pycountry if available, else fallback map.
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Empty language name in config.json")

    key = raw.lower().strip()
    if key in _FALLBACK_LANG_TO_ISO2:
        return _FALLBACK_LANG_TO_ISO2[key].lower()

    if pycountry is not None:
        # Try direct lookup by name/common_name
        try:
            hit = pycountry.languages.lookup(raw)  # may match common names
            code = getattr(hit, "alpha_2", None)
            if isinstance(code, str) and code.strip():
                return code.strip().lower()
        except Exception:
            pass

        # Try a contains-search in pycountry list (best-effort)
        try:
            raw_l = raw.lower()
            for lang in pycountry.languages:
                nm = getattr(lang, "name", "") or ""
                cn = getattr(lang, "common_name", "") or ""
                if raw_l == nm.lower() or raw_l == cn.lower():
                    code = getattr(lang, "alpha_2", None)
                    if isinstance(code, str) and code.strip():
                        return code.strip().lower()
        except Exception:
            pass

    raise ValueError(f"Could not resolve ISO-639-1 alpha_2 code for language: {raw!r}")


# =========================
# PATH BUILDERS (ONLY LOGIC CHANGE AREA)
# =========================
def build_interview_paths(
    clip_id: int,
    language_cap: str,
    short_iso2_lower: str,
    video_name: str,
) -> Tuple[Path, str, str, Path]:
    """
    Returns:
      - source_fs_path (Path): file system path to read from
      - dest_win_full (str): Windows full path for XML <name>
      - dest_pathurl (str): file://localhost/... for XML <pathurl>
      - dest_fs_path (Path): file system path to copy to (WSL-safe)
    """
    folder = f"interview_{clip_id:03d}_id{clip_id}"

    # Source (read)
    src_fs = SRC_ROOT / language_cap / folder / SRC_FILENAME_TMPL.format(short=short_iso2_lower)

    # Destination (copy + XML reference)
    dest_win = WIN_FAST_CONTENT_ROOT / video_name / language_cap / "interviews" / folder / f"{folder}.mp3"
    dest_win_full = str(dest_win)

    dest_fs = Path(windows_to_wsl_path(dest_win_full)) if is_wsl() else dest_win

    return src_fs, dest_win_full, to_pathurl_windows(dest_win_full), dest_fs


# =========================
# XML HELPERS (UNCHANGED LOGIC)
# =========================
def safe_copy(src: Path, dst: Path) -> None:
    """
    Copy a file to dst. Prefer copy2, but if the target filesystem (often /mnt/*, SMB, mapped drives)
    rejects metadata operations (EPERM/EACCES), fallback to copyfile (bytes-only).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(src, dst)  # tries to preserve metadata
    except OSError as e:
        # Common on /mnt/z or network shares where chmod/utime isn't permitted
        if e.errno in (errno.EPERM, errno.EACCES):
            shutil.copyfile(src, dst)  # bytes only, no metadata
        else:
            raise


def frames_to_ticks(frames: int, fps: int) -> int:
    return int(frames * PREMIERE_TICKS_PER_SECOND / fps)


def get_seq_fps(seq: ET.Element) -> int:
    tb_txt = (seq.findtext("./rate/timebase") or "24").strip()
    try:
        return int(tb_txt)
    except ValueError:
        return 24


def get_next_numeric_id(root: ET.Element, prefix: str) -> int:
    mx = 0
    if prefix == "clipitem":
        for el in root.findall(".//clipitem"):
            _id = el.get("id") or ""
            m = re.match(r"^clipitem-(\d+)$", _id)
            if m:
                mx = max(mx, int(m.group(1)))
    elif prefix == "file":
        for el in root.findall(".//file"):
            _id = el.get("id") or ""
            m = re.match(r"^file-(\d+)$", _id)
            if m:
                mx = max(mx, int(m.group(1)))
    elif prefix == "masterclip":
        for el in root.findall(".//masterclipid"):
            txt = (el.text or "").strip()
            m = re.match(r"^masterclip-(\d+)$", txt)
            if m:
                mx = max(mx, int(m.group(1)))
    return mx + 1


def max_groupindex(root: ET.Element) -> int:
    mg = 0
    for gi in root.findall(".//link/groupindex"):
        try:
            mg = max(mg, int((gi.text or "0").strip()))
        except ValueError:
            pass
    return mg


def remove_all_clipitems(track: ET.Element) -> None:
    for ci in list(track.findall("clipitem")):
        track.remove(ci)


def shift_audio_link_trackindices(root: ET.Element, start_trackindex: int, delta: int) -> None:
    # Shift <link><trackindex> for audio links only
    for link in root.findall(".//link"):
        med = link.find("mediatype")
        ti = link.find("trackindex")
        if med is None or ti is None:
            continue
        if (med.text or "").strip() != "audio":
            continue
        try:
            n = int((ti.text or "").strip())
        except ValueError:
            continue
        if n >= start_trackindex:
            ti.text = str(n + delta)


def append_sorted_before_footer(track: ET.Element, clip: ET.Element) -> None:
    new_start = int(clip.findtext("start", "0") or "0")

    kids = list(track)
    footer_index = len(kids)
    for i, k in enumerate(kids):
        if k.tag != "clipitem":
            footer_index = i
            break

    insert_at = footer_index
    for i in range(0, footer_index):
        k = kids[i]
        if k.tag != "clipitem":
            continue
        try:
            s = int(k.findtext("start", "0") or "0")
        except ValueError:
            continue
        if s > new_start:
            insert_at = i
            break

    track.insert(insert_at, clip)


def insert_new_pair_after_track(audio: ET.Element, after_track_1based: int) -> Tuple[int, int, ET.Element, ET.Element]:
    """
    TRACK-based insertion (Premiere-safe).
    After A4 => insert BEFORE old A5 track node.
    """
    tracks = audio.findall("track")
    if len(tracks) < 4:
        raise RuntimeError(f"Need at least 4 audio tracks. Found {len(tracks)}")

    # Clone existing exploded stereo pair templates (A3/A4)
    tmpl_left = tracks[2]   # A3 template
    tmpl_right = tracks[3]  # A4 template

    new_left = copy.deepcopy(tmpl_left)
    new_right = copy.deepcopy(tmpl_right)
    remove_all_clipitems(new_left)
    remove_all_clipitems(new_right)

    insert_before_track_1based = after_track_1based + 1  # after A4 => before old A5

    if insert_before_track_1based <= len(tracks):
        target_track = tracks[insert_before_track_1based - 1]  # the old track we insert before
        audio_children = list(audio)
        pos = audio_children.index(target_track)
        audio.insert(pos, new_left)
        audio.insert(pos + 1, new_right)
    else:
        # after last track, append
        audio.append(new_left)
        audio.append(new_right)

    new_left_index = after_track_1based + 1
    new_right_index = after_track_1based + 2
    return new_left_index, new_right_index, new_left, new_right


def ensure_file_audio_is_stereo(file_el: ET.Element) -> None:
    """
    Force the <file> audio metadata to describe a 2-channel (stereo) asset.

    Why this matters:
    - Premiere may interpret an asset as MONO if the XML declares channelcount=1,
      even if the underlying MP3 is stereo.
    - When using an exploded-stereo pair (two clipitems with sourcetrack trackindex 1/2),
      the referenced <file> should advertise 2 channels.

    This function is best-effort and preserves any existing sample characteristics.
    """
    media = file_el.find("media")
    if media is None:
        media = ET.SubElement(file_el, "media")

    audio = media.find("audio")
    if audio is None:
        audio = ET.SubElement(media, "audio")

    # Top-level channelcount (commonly used by Premiere)
    cc = audio.find("channelcount")
    if cc is None:
        cc = ET.SubElement(audio, "channelcount")
    cc.text = "2"

    # Also set within samplecharacteristics if present/needed
    sc = audio.find("samplecharacteristics")
    if sc is None:
        sc = ET.SubElement(audio, "samplecharacteristics")
    cc2 = sc.find("channelcount")
    if cc2 is None:
        cc2 = ET.SubElement(sc, "channelcount")
    cc2.text = "2"

    # Ensure audiochannel mapping exists (1..2). If it already exists, keep it if valid.
    audiochannels = list(audio.findall("audiochannel"))
    if len(audiochannels) != 2:
        for ac in audiochannels:
            audio.remove(ac)
        for ch in (1, 2):
            ac = ET.SubElement(audio, "audiochannel")
            ET.SubElement(ac, "sourcechannel").text = str(ch)
    else:
        # Normalize existing to 1 and 2 (best-effort)
        for idx, ch in enumerate((1, 2)):
            ac = audiochannels[idx]
            sc_el = ac.find("sourcechannel")
            if sc_el is None:
                sc_el = ET.SubElement(ac, "sourcechannel")
            sc_el.text = str(ch)


def ensure_clipitem_sourcechannel(clipitem: ET.Element, audio_channel_1based: int) -> None:
    """
    Some Premiere XMLs include <sourcechannel><audiochannel>..</audiochannel></sourcechannel>.
    When present (or when Premiere expects it), setting this helps avoid mono interpretation.
    """
    # Keep existing structure if present, otherwise create minimal.
    sc = clipitem.find("sourcechannel")
    if sc is None:
        sc = ET.SubElement(clipitem, "sourcechannel")

    ac = sc.find("audiochannel")
    if ac is None:
        ac = ET.SubElement(sc, "audiochannel")
    ac.text = str(audio_channel_1based)


def ensure_violet_label(clipitem: ET.Element) -> None:
    """
    Premiere label color:
      <labels><label2>Violet</label2></labels>
    """
    labels = clipitem.find("labels")
    if labels is None:
        labels = ET.SubElement(clipitem, "labels")

    label2 = labels.find("label2")
    if label2 is None:
        label2 = ET.SubElement(labels, "label2")

    label2.text = "Violet"


def build_pair_clipitems_from_templates(
    tmpl_left_ci: ET.Element,
    tmpl_right_ci: ET.Element,
    clip_left_id: str,
    clip_right_id: str,
    masterclip_id: str,
    file_id: str,
    win_full: str,
    pathurl: str,
    start: int,
    end: int,
    fps: int,
    new_left_trackindex: int,
    new_right_trackindex: int,
    groupindex: int,
) -> Tuple[ET.Element, ET.Element]:
    left = copy.deepcopy(tmpl_left_ci)
    right = copy.deepcopy(tmpl_right_ci)

    out_len = end - start
    if out_len <= 0:
        out_len = 1

    # Update both clipitems basics
    for c, cid in ((left, clip_left_id), (right, clip_right_id)):
        c.set("id", cid)

        mc = c.find("masterclipid")
        if mc is None:
            mc = ET.SubElement(c, "masterclipid")
        mc.text = masterclip_id

        nm = c.find("name")
        if nm is None:
            nm = ET.SubElement(c, "name")
        nm.text = win_full

        dur = c.find("duration")
        if dur is None:
            dur = ET.SubElement(c, "duration")
        dur.text = str(out_len)

        st = c.find("start")
        if st is None:
            st = ET.SubElement(c, "start")
        st.text = str(start)

        ed = c.find("end")
        if ed is None:
            ed = ET.SubElement(c, "end")
        ed.text = str(end)

        inn = c.find("in")
        if inn is None:
            inn = ET.SubElement(c, "in")
        inn.text = "0"

        out = c.find("out")
        if out is None:
            out = ET.SubElement(c, "out")
        out.text = str(out_len)

        pti = c.find("pproTicksIn")
        if pti is None:
            pti = ET.SubElement(c, "pproTicksIn")
        pti.text = "0"

        pto = c.find("pproTicksOut")
        if pto is None:
            pto = ET.SubElement(c, "pproTicksOut")
        pto.text = str(frames_to_ticks(out_len, fps))

        # File block (keep template structure, just retarget name/pathurl/duration + id)
        f = c.find("file")
        if f is None:
            f = ET.SubElement(c, "file", {"id": file_id})
        f.set("id", file_id)

        fn = f.find("name")
        if fn is None:
            fn = ET.SubElement(f, "name")
        fn.text = win_full

        pu = f.find("pathurl")
        if pu is None:
            pu = ET.SubElement(f, "pathurl")
        pu.text = pathurl

        fd = f.find("duration")
        if fd is None:
            fd = ET.SubElement(f, "duration")
        fd.text = str(out_len)

        # Force stereo (avoid Premiere interpreting a stereo MP3 as mono via XML metadata)
        ensure_file_audio_is_stereo(f)

    # Exploded stereo channels (source trackindex 1/2)
    stL = left.find("sourcetrack")
    if stL is None:
        stL = ET.SubElement(left, "sourcetrack")
    mt = stL.find("mediatype")
    if mt is None:
        mt = ET.SubElement(stL, "mediatype")
    mt.text = "audio"
    ti = stL.find("trackindex")
    if ti is None:
        ti = ET.SubElement(stL, "trackindex")
    ti.text = "1"

    stR = right.find("sourcetrack")
    if stR is None:
        stR = ET.SubElement(right, "sourcetrack")
    mt = stR.find("mediatype")
    if mt is None:
        mt = ET.SubElement(stR, "mediatype")
    mt.text = "audio"
    ti = stR.find("trackindex")
    if ti is None:
        ti = ET.SubElement(stR, "trackindex")
    ti.text = "2"

    # Ensure channel mapping (helps Premiere keep this as an exploded-stereo pair)
    ensure_clipitem_sourcechannel(left, 1)
    ensure_clipitem_sourcechannel(right, 2)

    # Links: clear and rebuild (self + partner)
    for c in (left, right):
        for l in list(c.findall("link")):
            c.remove(l)

    def add_link(parent: ET.Element, clipref: str, trackindex: int) -> None:
        l = ET.SubElement(parent, "link")
        ET.SubElement(l, "linkclipref").text = clipref
        ET.SubElement(l, "mediatype").text = "audio"
        ET.SubElement(l, "trackindex").text = str(trackindex)
        ET.SubElement(l, "clipindex").text = "1"
        ET.SubElement(l, "groupindex").text = str(groupindex)

    add_link(left, clip_left_id, new_left_trackindex)
    add_link(left, clip_right_id, new_right_trackindex)
    add_link(right, clip_right_id, new_right_trackindex)
    add_link(right, clip_left_id, new_left_trackindex)

    ensure_violet_label(left)
    ensure_violet_label(right)

    return left, right


# =========================
# MAIN APPLY
# =========================
def main_apply(input_xml: Path, json_path: Path, output_xml: Path, config_path: Optional[Path] = None) -> None:
    # Load config.json (Language + VideoName)
    cfg = load_config(json_path, explicit_config=config_path)
    language_name = get_language_name_from_config(cfg)
    video_name = get_videoname_from_config(cfg)

    language_cap = language_capitalize(language_name)
    short_iso2 = language_to_iso2_short(language_name)  # lowercase

    print(f"Config: language={language_name!r} -> LanguageCap={language_cap!r}, short={short_iso2!r}")
    print(f"Config: VideoName={video_name!r}")

    tree = ET.parse(input_xml)
    root = tree.getroot()

    seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("No <sequence> found")

    fps = get_seq_fps(seq)

    audio = seq.find("./media/audio")
    if audio is None:
        raise RuntimeError("No <media><audio> found")

    tracks_before = audio.findall("track")
    if len(tracks_before) < 4:
        raise RuntimeError(f"Expected >=4 audio tracks, found {len(tracks_before)}")

    # Templates from original A3/A4 clipitems (safe exploded stereo templates)
    tmpl_left_ci = tracks_before[2].find("clipitem")
    tmpl_right_ci = tracks_before[3].find("clipitem")
    if tmpl_left_ci is None or tmpl_right_ci is None:
        raise RuntimeError("Could not find clipitem templates on original A3/A4")

    tmpl_left_ci = copy.deepcopy(tmpl_left_ci)
    tmpl_right_ci = copy.deepcopy(tmpl_right_ci)

    # Insert NEW pair after A4 => NEW becomes A5/A6
    new_left_idx, new_right_idx, new_left_track, new_right_track = insert_new_pair_after_track(
        audio, after_track_1based=INSERT_AFTER_TRACK_1BASED
    )

    # Shift existing audio links from old A5+ (which is after A4) by +2
    shift_start = INSERT_AFTER_TRACK_1BASED + 1  # after A4 => start shifting at 5
    shift_audio_link_trackindices(root, start_trackindex=shift_start, delta=2)

    entries = load_json_entries(json_path)

    next_clip_n = get_next_numeric_id(root, "clipitem")
    next_file_n = get_next_numeric_id(root, "file")
    next_mc_n = get_next_numeric_id(root, "masterclip")
    group_n = max_groupindex(root) + 1

    added = 0
    missing = 0
    copied = 0
    copy_failed = 0

    for it in entries:
        clip_id = int(it["id"])
        start = int(it["seqeunce_start_frames"])
        end = int(it["seqeunce_end_frames"])

        # NEW: paths + copy source -> destination
        src_fs, dest_win_full, dest_pathurl, dest_fs = build_interview_paths(
            clip_id=clip_id,
            language_cap=language_cap,
            short_iso2_lower=short_iso2,
            video_name=video_name,
        )

        if not src_fs.exists():
            if ALLOW_MISSING_AUDIO:
                print(f"  ⚠ missing src mp3 id={clip_id} (adding OFFLINE clip anyway): {src_fs}")
            else:
                print(f"  ⚠ missing src mp3 id={clip_id} (skipped): {src_fs}")
                missing += 1
                continue
        else:
            try:
                dest_fs.parent.mkdir(parents=True, exist_ok=True)
                safe_copy(src_fs, dest_fs)
                copied += 1
            except Exception as e:
                # If copy fails, we keep behavior strict like missing (unless ALLOW_MISSING_AUDIO)
                if ALLOW_MISSING_AUDIO:
                    print(f"  ⚠ copy failed id={clip_id} (adding OFFLINE anyway): {e}")
                else:
                    print(f"  ⚠ copy failed id={clip_id} (skipped): {e}")
                    copy_failed += 1
                    continue

        clip_left_id = f"clipitem-{next_clip_n}"
        clip_right_id = f"clipitem-{next_clip_n + 1}"
        masterclip_id = f"masterclip-{next_mc_n}"
        file_id = f"file-{next_file_n}"

        left_ci, right_ci = build_pair_clipitems_from_templates(
            tmpl_left_ci=tmpl_left_ci,
            tmpl_right_ci=tmpl_right_ci,
            clip_left_id=clip_left_id,
            clip_right_id=clip_right_id,
            masterclip_id=masterclip_id,
            file_id=file_id,
            win_full=dest_win_full,
            pathurl=dest_pathurl,
            start=start,
            end=end,
            fps=fps,
            new_left_trackindex=new_left_idx,
            new_right_trackindex=new_right_idx,
            groupindex=group_n,
        )

        append_sorted_before_footer(new_left_track, left_ci)
        append_sorted_before_footer(new_right_track, right_ci)

        print(f"  ✓ added interview id={clip_id} on NEW A{new_left_idx}/A{new_right_idx} ({start}-{end})")

        added += 1
        next_clip_n += 2
        next_file_n += 1
        next_mc_n += 1
        group_n += 1

    tree.write(output_xml, encoding="utf-8", xml_declaration=True)

    print("============================================")
    print("Done.")
    print(f"Inserted NEW stereo pair after A{INSERT_AFTER_TRACK_1BASED} -> NEW A{new_left_idx}/A{new_right_idx}")
    print(f"Shifted old A{INSERT_AFTER_TRACK_1BASED+1}+ links by +2")
    print(f"Interview pairs added: {added}")
    print(f"Copied audio files: {copied}")
    if copy_failed:
        print(f"Copy failed (skipped): {copy_failed}")
    if not ALLOW_MISSING_AUDIO:
        print(f"Skipped missing audio: {missing}")
    print(f"Saved XML: {output_xml}")
    print("============================================")


def main() -> None:
    import sys
    input_xml = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_INPUT_XML)
    json_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(DEFAULT_JSON)
    output_xml = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(DEFAULT_OUTPUT_XML)

    # Optional 4th arg: explicit config.json path
    config_path = Path(sys.argv[4]) if len(sys.argv) > 4 else None

    main_apply(input_xml, json_path, output_xml, config_path=config_path)


if __name__ == "__main__":
    main()
