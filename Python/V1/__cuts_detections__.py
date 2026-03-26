#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import json
import sys
import os
from pathlib import Path

# =========================
# USER OVERRIDES (OPTIONAL)
# =========================
# If you want to force paths, set these to absolute paths.
# Example:
# INPUTS_BASE_DIR  = Path("/home/viralverse/experimental-dubbing-automation/inputs")
# OUTPUTS_BASE_DIR = Path("/home/viralverse/experimental-dubbing-automation/output")
INPUTS_BASE_DIR: Path | None = None
OUTPUTS_BASE_DIR: Path | None = None

# If you want to force the project root (and auto-derive inputs/output from it),
# set TOOL_ROOT here OR set env var TOOL_ROOT=/path/to/root
TOOL_ROOT_OVERRIDE: Path | None = None
# =========================

FPS = 23.976
OUTPUT_FILENAME = "A1__dubbing__.json"


_INVALID_WIN_CHARS = r'<>:"/\\|?*'


def _is_tool_root(p: Path) -> bool:
    if not p or not p.exists() or not p.is_dir():
        return False

    inputs_dir = None
    for cand in ("inputs", "Inputs", "INPUTS"):
        if (p / cand).is_dir():
            inputs_dir = p / cand
            break
    if inputs_dir is None:
        return False

    python_dir = None
    for cand in ("Python", "python", "PYTHON"):
        if (p / cand).is_dir():
            python_dir = p / cand
            break
    if python_dir is None:
        return False

    v1_ok = any((python_dir / x / "__sequence_V1__.py").exists() for x in ("V1", "v1"))
    v2_ok = any((python_dir / x / "__sequence_V2__.py").exists() for x in ("V2", "v2"))
    return v1_ok and v2_ok


def find_project_root(here: Path) -> Path:
    # 0) explicit override in code
    if TOOL_ROOT_OVERRIDE is not None:
        p = TOOL_ROOT_OVERRIDE.expanduser().resolve()
        if _is_tool_root(p):
            return p

    # 1) env var TOOL_ROOT
    env_root = (os.getenv("TOOL_ROOT") or "").strip()
    if env_root:
        p = Path(env_root).expanduser().resolve()
        if _is_tool_root(p):
            return p

    # 2) current working directory
    cwd = Path.cwd().resolve()
    if _is_tool_root(cwd):
        return cwd

    # 3) walk upward from this file
    for parent in [here] + list(here.parents):
        if _is_tool_root(parent):
            return parent

    raise RuntimeError(
        f"Could not auto-detect project root.\n"
        f"Start path: {here}\n"
        f"Expected a folder containing inputs/ and Python/V1+V2 sequence files.\n"
        f"Fix: set TOOL_ROOT env var, or set TOOL_ROOT_OVERRIDE in this script."
    )


def detect_inputs_dir(root: Path) -> Path:
    for cand in ("inputs", "Inputs", "INPUTS"):
        p = root / cand
        if p.is_dir():
            return p
    return root / "inputs"


def detect_outputs_dir(root: Path) -> Path:
    """
    Prefer output if it exists, else outputs if it exists, else default to output.
    Returns the base folder, NOT /JSON yet.
    """
    out1 = root / "output"
    out2 = root / "outputs"
    if out1.exists():
        return out1
    if out2.exists():
        return out2
    return out1


# =========================
# RESOLVE INPUT/OUTPUT BASES
# =========================
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = find_project_root(HERE)

# If user provided INPUTS_BASE_DIR/OUTPUTS_BASE_DIR, use them.
# Otherwise auto-detect from PROJECT_ROOT.
_inputs_base = INPUTS_BASE_DIR.expanduser().resolve() if INPUTS_BASE_DIR else detect_inputs_dir(PROJECT_ROOT)
_outputs_base = OUTPUTS_BASE_DIR.expanduser().resolve() if OUTPUTS_BASE_DIR else detect_outputs_dir(PROJECT_ROOT)

# Your script expects:
# inputs/XML/English.xml
# output/JSON/A1__dubbing__.json
INPUT_XML_PATH = _inputs_base / "XML" / "English.xml"
if not INPUT_XML_PATH.exists():
    alt = _inputs_base / "xml" / "English.xml"
    if alt.exists():
        INPUT_XML_PATH = alt

OUTPUT_JSON_DIR = _outputs_base / "JSON"
# =========================


def get_label(ci):
    label = ci.findtext("label")
    if label:
        return label.strip().lower()

    labels_tag = ci.find("labels")
    if labels_tag is not None:
        for child in labels_tag:
            if child.text and child.text.strip():
                return child.text.strip().lower()

    return None


def extract_a1_clips(xml_path: Path, fps: float = FPS):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("No <sequence> element found in the XML.")

    media = seq.find("media")
    if media is None:
        raise RuntimeError("No <media> inside <sequence>.")

    audio = media.find("audio")
    if audio is None:
        raise RuntimeError("No <audio> section inside <media>.")

    tracks = audio.findall("track")
    if not tracks:
        raise RuntimeError("No <track> elements inside <audio>.")

    a1_track = tracks[0]
    clips_json = []

    for idx, ci in enumerate(a1_track.findall("clipitem"), start=1):
        start_text = ci.findtext("start")
        end_text = ci.findtext("end")
        if not start_text or not end_text:
            continue

        try:
            start_frames = int(start_text)
            end_frames = int(end_text)
        except ValueError:
            continue

        if end_frames <= start_frames:
            continue

        dur_frames = end_frames - start_frames

        clips_json.append({
            "id": idx,
            "sequence_start_time_sec": round(start_frames / fps, 3),
            "sequence_end_time_sec": round(end_frames / fps, 3),
            "sequence_start_frames": start_frames,
            "sequence_end_frames": end_frames,
            "sentence_duration_sec": round(dur_frames / fps, 3),
            "sentence_duration_frames": dur_frames,
            "track": "A1",
            "label": get_label(ci),
        })

    return clips_json


def main():
    # Allow passing a custom XML path, but default is inputs/XML/English.xml
    if len(sys.argv) >= 2:
        xml_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        xml_path = INPUT_XML_PATH

    if not xml_path.exists():
        raise FileNotFoundError(
            f"Input XML not found: {xml_path}\n"
            f"PROJECT_ROOT: {PROJECT_ROOT}\n"
            f"INPUTS_BASE: {_inputs_base}\n"
            f"OUTPUTS_BASE: {_outputs_base}"
        )

    OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_JSON_DIR / OUTPUT_FILENAME

    clips = extract_a1_clips(xml_path, fps=FPS)
    out_path.write_text(json.dumps(clips, indent=4), encoding="utf-8")

    print(f"✓ Done! Extracted {len(clips)} A1 clips.")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"INPUTS_BASE:  {_inputs_base}")
    print(f"OUTPUTS_BASE: {_outputs_base}")
    print(f"XML:          {xml_path}")
    print(f"JSON:         {out_path}")


if __name__ == "__main__":
    main()
