#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path
from typing import Any, Dict, List

# =========================
# INPUT / OUTPUT VARIABLES
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

A13_PATH       = PROJECT_ROOT / "output" / "JSON" / "A13__titles_segments__.json"
A16_PATH       = PROJECT_ROOT / "output" / "JSON" / "A16__final_titles_segments__.json"
OUT_PATH       = PROJECT_ROOT / "output" / "JSON" / "A25__final_titles_merged_segments__.json"


# A13_PATH = Path(r"/mnt/data/A13__titles_segments__.json")
# A16_PATH = Path(r"/mnt/data/A16__final_titles_segments__.json")
# OUT_PATH = Path(r"/mnt/data/A25__final_merged_Segments__.json")


def load_json_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array (list) in {path}, got: {type(data).__name__}")
    return data


def to_int(name: str, v: Any) -> int:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"{name} must be an int, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    raise ValueError(f"{name} must be an int, got {v!r}")


def to_float(name: str, v: Any) -> float:
    if v is None:
        raise ValueError(f"{name} must be a number, got None")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except Exception:
            pass
    raise ValueError(f"{name} must be a number, got {v!r}")


def merge(a13: List[Dict[str, Any]], a16: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # A13 by id
    a13_sorted = sorted(a13, key=lambda x: int(x.get("id", 0)))
    # A16 by start frames
    a16_sorted = sorted(a16, key=lambda x: int(x.get("seqeunce_start_frames", 0)))

    if len(a13_sorted) != len(a16_sorted):
        raise ValueError(
            f"Length mismatch: A13 has {len(a13_sorted)} items, A16 has {len(a16_sorted)} items. "
            "They must be the same to merge 1:1."
        )

    out: List[Dict[str, Any]] = []
    for t, ts in zip(a13_sorted, a16_sorted):
        start_frame = to_int("seqeunce_start_frames", ts.get("seqeunce_start_frames"))
        end_frame = to_int("seqeunce_end_frames", ts.get("seqeunce_end_frames"))

        out.append(
            {
                "id": to_int("id (A13)", t.get("id")),
                "text": str(t.get("text", "")),
                "number": str(t.get("number", "")),
                "body": str(t.get("body", "")),
                "start_sec": to_float("seqeunce_start_time_sec", ts.get("seqeunce_start_time_sec")),
                "end_sec": to_float("seqeunce_end_time_sec", ts.get("seqeunce_end_time_sec")),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_sec": to_float("sentence_duration_sec", ts.get("sentence_duration_sec")),
                "duration_frames": end_frame - start_frame,
            }
        )

    return out


def main() -> int:
    a13 = load_json_list(A13_PATH)
    a16 = load_json_list(A16_PATH)

    merged = merge(a13, a16)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote: {OUT_PATH} ({len(merged)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
