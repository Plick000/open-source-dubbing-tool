#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# PATH CONFIG (YOUR REQUIRED METHOD)
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Put your source alignment JSON here (the "wrapped" one)
INPUT_ALIGNMENT_JSON = PROJECT_ROOT / "inputs" / "audios" / "__dubbed__audio__alignments__.json"

# Output will be written in the SAME location (inputs/audios/)
OUTPUT_ALIGNMENT_JSON = PROJECT_ROOT / "inputs" / "audios" / "__dubbed__audio__alignments_output__.json"


# ============================================================
# CONVERTER
# ============================================================

def pick_alignment_payload(data: Any) -> Dict[str, Any]:
    """
    Accepts:
      - { "alignment": {...} }
      - [ { "alignment": {...} } ]
      - already-flat: { "characters": [...], ... }
    Returns the dict that contains the characters + timing arrays.
    """
    if isinstance(data, dict):
        if "alignment" in data and isinstance(data["alignment"], dict):
            return data["alignment"]
        return data

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "alignment" in first and isinstance(first["alignment"], dict):
            return first["alignment"]
        if isinstance(first, dict):
            return first

    raise ValueError("Unsupported JSON structure: expected dict or non-empty list")


def find_key(d: Dict[str, Any], candidates: List[str]) -> Optional[str]:
    # Exact match first
    for k in candidates:
        if k in d:
            return k

    # Fuzzy fallback for variations
    for k in d.keys():
        lk = k.lower()
        for cand in candidates:
            if cand.lower() in lk:
                return k
    return None


def maybe_ms_to_seconds(values: Any) -> Any:
    """
    If values look like milliseconds (max > 10,000), convert to seconds.
    Otherwise keep as seconds.
    """
    if values is None:
        return None
    if not isinstance(values, list) or not values:
        return values

    try:
        mx = max(float(v) for v in values)
    except Exception:
        return values

    if mx > 10_000:
        return [round(float(v) / 1000.0, 6) for v in values]
    return [round(float(v), 6) for v in values]


def convert_alignment_json(input_path: Path, output_path: Path) -> Dict[str, Any]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    payload = pick_alignment_payload(data)

    chars_key = find_key(payload, ["characters", "chars", "character"])
    start_key = find_key(payload, [
        "character_start_times_seconds",
        "character_start_times",
        "char_start_times_seconds",
        "char_start_times",
        "start_times_seconds",
        "start_times",
    ])
    end_key = find_key(payload, [
        "character_end_times_seconds",
        "character_end_times",
        "char_end_times_seconds",
        "char_end_times",
        "end_times_seconds",
        "end_times",
    ])

    if not chars_key:
        raise ValueError("Could not find 'characters' array in input JSON (expected key like 'characters').")

    out: Dict[str, Any] = {"characters": payload[chars_key]}

    if start_key:
        out["character_start_times_seconds"] = maybe_ms_to_seconds(payload[start_key])
    if end_key:
        out["character_end_times_seconds"] = maybe_ms_to_seconds(payload[end_key])

    # Sanity check if both arrays exist
    if "character_start_times_seconds" in out and "character_end_times_seconds" in out:
        if (
            len(out["characters"]) != len(out["character_start_times_seconds"])
            or len(out["characters"]) != len(out["character_end_times_seconds"])
        ):
            raise ValueError(
                f"Length mismatch: characters={len(out['characters'])}, "
                f"start_times={len(out['character_start_times_seconds'])}, "
                f"end_times={len(out['character_end_times_seconds'])}"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    if not INPUT_ALIGNMENT_JSON.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_ALIGNMENT_JSON}")

    convert_alignment_json(INPUT_ALIGNMENT_JSON, OUTPUT_ALIGNMENT_JSON)
    print(f"OK -> {OUTPUT_ALIGNMENT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
