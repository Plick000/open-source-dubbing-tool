#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Exports the EXACT Gemini prompt(s) that __gemini_segments_validations__.py would build,
using the same default input paths / env overrides, WITHOUT requiring any CLI arguments.

Output:
- output/GEMINI_UI_DEBUG/prompt_batch_###.txt  (paste into Gemini UI)
- output/GEMINI_UI_DEBUG/prompt_batch_###.json (prompt + payload)
- output/GEMINI_UI_DEBUG/meta.json
""" 

import os
import sys
import json
from pathlib import Path
import importlib.util


def _import_validator_module() -> object:
    """
    Import __gemini_segments_validations__.py from the same directory as this file.
    Falls back to direct file import if normal import fails.
    """
    here = Path(__file__).resolve().parent
    candidate = here / "__gemini_segments_validations__.py"

    # 1) Try regular import first (works if folder is on sys.path)
    try:
        import __gemini_segments_validations__ as m  # noqa
        return m
    except Exception:
        pass

    # 2) Fallback: import by absolute path
    if not candidate.exists():
        raise FileNotFoundError(f"Cannot find validator file at: {candidate}")

    spec = importlib.util.spec_from_file_location("__gemini_segments_validations__", str(candidate))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to import module from: {candidate}")

    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    gsv = _import_validator_module()

    # Use same language source (config.json) as validator
    try:
        gsv.CFG.LANGUAGE = gsv.load_language_from_config(gsv.CFG.CONFIG_JSON)
    except Exception as e:
        raise RuntimeError(f"Failed to load language from config.json: {e}")

    # Same input defaults + env overrides (NO CLI args)
    base_json = gsv.env_or(gsv.CFG.BASE_JSON, "BASE_JSON")
    hint_json = gsv.env_or(gsv.CFG.HINT_JSON, "HINT_JSON")
    es_chunks = gsv.env_or(gsv.CFG.ES_CHUNKS_TXT, "ES_CHUNKS_TXT")

    window_minus = gsv.env_int_or(gsv.CFG.WINDOW_MINUS, "WINDOW_MINUS")
    window_plus = gsv.env_int_or(gsv.CFG.WINDOW_PLUS, "WINDOW_PLUS")
    merge_newline = gsv.env_or(gsv.CFG.MERGE_NEWLINE, "MERGE_NEWLINE")

    batch_size = gsv.env_int_or(gsv.CFG.BATCH_SIZE, "BATCH_SIZE")
    max_prompt_chars = gsv.env_int_or(gsv.CFG.MAX_PROMPT_CHARS, "MAX_PROMPT_CHARS")

    # Optional exporter-only env: limit how many prepared items you export (0 = all)
    export_limit = 0
    try:
        export_limit = int(os.getenv("EXPORT_LIMIT", "0").strip() or "0")
    except Exception:
        export_limit = 0

    base_path = Path(base_json).resolve()
    hint_path = Path(hint_json).resolve()
    es_chunks_path = Path(es_chunks).resolve()

    if not base_path.exists():
        raise FileNotFoundError(f"BASE_JSON not found: {base_path}")
    if not hint_path.exists():
        raise FileNotFoundError(f"HINT_JSON not found: {hint_path}")
    if not es_chunks_path.exists():
        raise FileNotFoundError(f"ES_CHUNKS_TXT not found: {es_chunks_path}")

    # Load raw inputs exactly like validator
    base_items = gsv.load_json_list(base_path)
    hint_items = gsv.load_json_list(hint_path)
    hint_by_id = gsv.index_by_id(hint_items)

    chunks_es = gsv.parse_numbered_chunks_txt(es_chunks_path)

    prepared = []
    for it in base_items:
        item_id = it.get("id")
        if not isinstance(item_id, int):
            continue

        match = it.get("match") if isinstance(it.get("match"), dict) else {}
        eng_nums = match.get("source_chunk_numbers") or []
        if not isinstance(eng_nums, list):
            eng_nums = []
        eng_nums = [n for n in eng_nums if isinstance(n, int)]

        hint_text = ""
        h = hint_by_id.get(item_id, {})
        if isinstance(h, dict) and isinstance(h.get("text_translated"), str):
            hint_text = h["text_translated"]

        candidates = gsv.build_candidates(eng_nums, chunks_es, window_minus, window_plus)

        prepared.append({
            "id": item_id,
            "type": it.get("type", ""),
            "text_en": it.get("text", ""),
            "text_translated_hint": hint_text,
            "source_chunk_numbers_eng": eng_nums,
            "candidate_chunks_es": [{"n": n, "text": t} for (n, t) in candidates],
        })

        if export_limit > 0 and len(prepared) >= export_limit:
            break

    bs = max(1, int(batch_size))
    max_chars = max(20000, int(max_prompt_chars))

    # Same adaptive batching behavior as validator
    batches = gsv.split_into_batches(prepared, merge_newline, max_items=bs, max_chars=max_chars)

    # Output dir (under PROJECT_ROOT/output/GEMINI_UI_DEBUG)
    project_root = Path(gsv.CFG.PROJECT_ROOT).resolve()
    out_dir = project_root / "output" / "GEMINI_UI_DEBUG"
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "language": gsv.CFG.LANGUAGE,
        "base_json": str(base_path),
        "hint_json": str(hint_path),
        "es_chunks_txt": str(es_chunks_path),
        "window_minus": window_minus,
        "window_plus": window_plus,
        "merge_newline": merge_newline,
        "batch_size": bs,
        "max_prompt_chars": max_chars,
        "prepared_items_count": len(prepared),
        "batches_count": len(batches),
        "export_limit": export_limit,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write prompt batches
    for i, batch in enumerate(batches, start=1):
        prompt = gsv.make_batch_prompt(batch, merge_newline)

        # TXT (best for Gemini UI)
        (out_dir / f"prompt_batch_{i:03d}.txt").write_text(prompt, encoding="utf-8")

        # JSON (contains prompt + payload)
        blob = {
            "batch_index": i,
            "meta": meta,
            "prompt": prompt,
            "payload": {"items": batch},
        }
        (out_dir / f"prompt_batch_{i:03d}.json").write_text(
            json.dumps(blob, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    print(f"✅ Exported {len(batches)} prompt batch(es) to: {out_dir}")
    print("   Paste prompt_batch_###.txt into Gemini UI to reproduce.")


if __name__ == "__main__":
    main()
