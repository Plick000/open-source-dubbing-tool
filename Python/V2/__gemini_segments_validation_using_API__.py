#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

# NEW: stdlib HTTP (no extra deps)
import urllib.request
import urllib.error


# ============================================================
# USER CONFIG
# ============================================================

@dataclass
class Config:
    # Files
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    ES_CHUNKS_TXT: str = PROJECT_ROOT / "inputs" / "chunks" / "chunks.txt"
    BASE_JSON: str = PROJECT_ROOT / "output" / "JSON" / "A11__translated_segments__.json"
    HINT_JSON: str = PROJECT_ROOT / "output" / "JSON" / "A11__translated_segments__.json"
    OUT_JSON: str = PROJECT_ROOT / "output" / "JSON" / "A12__final_segments__.json"

    CONFIG_JSON: str = PROJECT_ROOT / "inputs" / "config" / "config.json"

    LANGUAGE: str = "Polish"

    # Output field for verbatim (exact) script substring
    EXACT_TEXT_FIELD: str = "exact_script_text"

    # Gemini CLI (kept for compatibility with existing args/env; not used by API runner)
    GEMINI_BIN: str = "/home/vvruntime/.npm-global/bin/gemini"
    GEMINI_MODEL: str = "gemini-2.5-pro"

    # OPTIONAL: Fast-first then fallback to Pro for failures
    USE_FAST_FIRST: bool = False
    GEMINI_FAST_MODEL: str = "gemini-2.5-flash"

    # Batching (speed)
    BATCH_SIZE: int = 1000
    MAX_PROMPT_CHARS: int = 90000   # safety to avoid huge prompts

    TIMEOUT_SEC: int = 240

    # Behavior
    WINDOW_MINUS: int = 2
    WINDOW_PLUS: int = 2
    MERGE_NEWLINE: str = "\n"

    # Progress
    SHOW_PROGRESS: bool = True

    # Gemini behavior hardening (kept; not used by API runner)
    DISABLE_EXTENSIONS: bool = True  # -e none
    EMPTY_CWD_DIR: str = str(Path.home() / ".cache" / "gemini_headless_empty")

    # API config
    GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_API_KEY_ENV_PRIMARY: str = "GEMINI_API_KEY"
    GEMINI_API_KEY_ENV_FALLBACK: str = "GOOGLE_API_KEY"


CFG = Config()


# ============================================================
# ENV VAR OVERRIDES (optional)
# ============================================================

def normalize_lang_cap(lang: str) -> str:
    s = (lang or "").strip()
    if not s:
        return s
    parts = s.split("-")
    out = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        if i > 0 and len(p) <= 3 and p.isalpha():
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:].lower())
    return "-".join(out)

def load_language_from_config(config_path: str) -> str:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    for key in ("Language", "language", "targetLanguage", "target_lang", "lang", "TARGET_LANG"):
        v = cfg.get(key)
        if isinstance(v, str) and v.strip():
            return normalize_lang_cap(v)
    raise KeyError(f"Language not found in {config_path}")

def env_or(default: str, key: str) -> str:
    v = os.getenv(key, "").strip()
    return v if v else default

def env_int_or(default: int, key: str) -> int:
    v = os.getenv(key, "").strip()
    try:
        return int(v) if v else default
    except ValueError:
        return default

def env_bool_or(default: bool, key: str) -> bool:
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


# ============================================================
# .env loader (simple, no dependencies)
# ============================================================

def _load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and (k not in os.environ):  # don't override existing env
                os.environ[k] = v
    except Exception:
        # Never crash the pipeline due to .env parsing
        return

def load_dotenv_if_present() -> None:
    # 1) Prefer PROJECT_ROOT/.env (matches your config style)
    _load_dotenv_file(Path(CFG.PROJECT_ROOT) / ".env")
    # 2) Also allow local .env
    _load_dotenv_file(Path.cwd() / ".env")


def get_gemini_api_key() -> str:
    k1 = os.getenv(CFG.GEMINI_API_KEY_ENV_PRIMARY, "").strip()
    if k1:
        return k1
    k2 = os.getenv(CFG.GEMINI_API_KEY_ENV_FALLBACK, "").strip()
    if k2:
        return k2
    raise RuntimeError(
        "Gemini API key not found. Put it in .env as "
        f"{CFG.GEMINI_API_KEY_ENV_PRIMARY}=... (preferred) or {CFG.GEMINI_API_KEY_ENV_FALLBACK}=..."
    )


# ============================================================
# Simple progress bar
# ============================================================

def _fmt_seconds(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"

def fmt_min_sec(total_seconds: float) -> str:
    total_seconds = int(round(float(total_seconds)))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes} minutes {seconds:02d} seconds"

def print_progress(current: int, total: int, start_ts: float, label: str = "") -> None:
    if total <= 0:
        return
    pct = (current / total) * 100.0
    elapsed = time.time() - start_ts
    rate = current / elapsed if elapsed > 0 and current > 0 else 0.0
    eta = (total - current) / rate if rate > 0 else 0.0

    bar_width = 28
    filled = int(bar_width * (current / total))
    bar = "█" * filled + "░" * (bar_width - filled)

    msg = (
        f"\r[{bar}] {pct:6.2f}%  "
        f"{current}/{total}  "
        f"elapsed {_fmt_seconds(elapsed)}  "
        f"eta {_fmt_seconds(eta)}"
    )
    if label:
        msg += f"  | {label}"

    sys.stdout.write(msg)
    sys.stdout.flush()

def end_progress() -> None:
    sys.stdout.write("\n")
    sys.stdout.flush()

# ============================================================
# Parsing Dubbed Language chunks TXT (ROBUST & FAIL-SAFE)
# ============================================================

def parse_numbered_chunks_txt(path: Path) -> Dict[int, str]:
    # 1. Read Raw Content
    print(f"DEBUG: Reading chunks from {path}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\\n", "\n")

    lines = raw.splitlines()
    non_empty_lines = [ln for ln in lines if ln.strip()]
    non_empty_count = len(non_empty_lines)
    print(f"DEBUG: File has {len(lines)} lines ({non_empty_count} non-empty)")

    # 2. Try Parsing with Regex (Standard numbering or Source tags)
    chunks: Dict[int, str] = {}
    cur_n = None
    cur_lines: List[str] = []

    # Regex matches: "143. Text", "143 Text", "Chunk 143: Text"
    re_num = re.compile(r'^\s*(?:chunk\s+)?(\d{1,4})(?:\]|[:.)]|-)\s*(.*)$', re.IGNORECASE)

    for line in lines:
        s = line.strip()
        if not s:
            if cur_n is not None and cur_lines and cur_lines[-1] != "":
                cur_lines.append("")
            continue

        # Detect "" tags specifically
        if "[" in line and "source" in line.lower():
            m_source = re.search(r'\[\s*source\s*:\s*(\d+)\s*\]', line, re.IGNORECASE)
            if m_source:
                if cur_n is not None:
                    txt = "\n".join(cur_lines).strip()
                    if txt:
                        chunks[cur_n] = txt
                
                cur_n = int(m_source.group(1))
                text_part = line.replace(m_source.group(0), "").strip()
                cur_lines = [text_part] if text_part else []
                continue

        # Detect Standard Numbering
        m = re_num.match(line)
        if m:
            if cur_n is not None:
                txt = "\n".join(cur_lines).strip()
                if txt:
                    chunks[cur_n] = txt
            cur_n = int(m.group(1))
            cur_lines = [m.group(2).strip()]
        else:
            if cur_n is not None:
                cur_lines.append(line.rstrip())

    # Save last chunk from regex parsing
    if cur_n is not None:
        txt = "\n".join(cur_lines).strip()
        if txt:
            chunks[cur_n] = txt

    print(f"DEBUG: Parsed {len(chunks)} numbered chunks. IDs: {list(chunks.keys())[:5]}...{list(chunks.keys())[-5:]}")

    # 3. FALLBACK LOGIC (Critical Fix)
    # If Regex found almost nothing (< 10% of lines), force Line-by-Line parsing.
    # This prevents the "Parsed max chunk #3" error on raw text files.
    if len(chunks) < (non_empty_count * 0.1) and non_empty_count > 0:
        print(f"⚠️  DEBUG: Regex parsing failed (found {len(chunks)} chunks). Switching to LINE-BY-LINE fallback.")
        chunks = {}
        line_idx = 1
        for line in lines:
            clean_line = line.strip()
            if clean_line:
                chunks[line_idx] = clean_line
                line_idx += 1
        print(f"DEBUG: Fallback parsed {len(chunks)} chunks.")

    if chunks:
        return chunks

    # 4. Try JSON Array
    try:
        j = json.loads(raw.strip())
        if isinstance(j, list):
             return {i+1: str(x).strip() for i, x in enumerate(j) if str(x).strip()}
    except:
        pass

    raise ValueError(f"Could not parse chunks from {path}")


def _max_needed_chunk_from_base(base_items: List[Dict[str, Any]]) -> int:
    max_needed = 0
    for it in base_items:
        m = it.get("match")
        if not isinstance(m, dict):
            continue
        nums = m.get("source_chunk_numbers") or []
        if not isinstance(nums, list):
            continue
        for n in nums:
            if isinstance(n, int) and n > max_needed:
                max_needed = n
    return max_needed


def _sanity_check_chunks_or_die(chunks: Dict[int, str], base_items: List[Dict[str, Any]], chunks_path: str) -> None:
    max_needed = _max_needed_chunk_from_base(base_items)
    if max_needed <= 0:
        return
    
    if not chunks:
        print(f"⚠️  WARNING: Dubbed chunks parsed EMPTY from {chunks_path}. Processing will continue, but expect failures.")
        return

    max_have = max(chunks.keys())
    # If we have somewhat matching counts but slightly fewer, just warn
    if max_have < max_needed:
        print(
            f"⚠️  WARNING: Dubbed chunks mismatch. Parsed max chunk #{max_have} from {chunks_path}, "
            f"but base JSON expects chunk #{max_needed}. "
            f"Proceeding anyway (Gemini will handle missing chunks)."
        )

# ============================================================
# Robust JSON extraction helpers
# ============================================================

def extract_first_json_object(text: str) -> Any:
    """
    Finds the first valid JSON object/array in a string.
    Works even if stdout has extra logs around it.
    """
    text = (text or "").strip()
    dec = json.JSONDecoder()

    # Try direct first
    try:
        return json.loads(text)
    except Exception:
        pass

    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
            return obj
        except json.JSONDecodeError:
            continue

    snippet = text[:800].replace("\n", "\\n")
    raise ValueError(f"Could not locate JSON in text. Snippet: {snippet}")

def safe_json_load_from_text(text: str) -> Any:
    """
    Extract FIRST valid JSON value from arbitrary text.
    Removes markdown fences.
    """
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return extract_first_json_object(text)


# ============================================================
# Gemini API calling (FAST + stable)
# ============================================================

def _normalize_api_model(model: str) -> str:
    m = (model or "").strip()
    if not m:
        raise RuntimeError("Model is empty.")
    # API expects "models/<name>"
    if m.startswith("models/"):
        return m
    return f"models/{m}"

def run_gemini(gemini_bin: str, model: str, prompt: str, timeout_sec: int, disable_ext: bool, empty_cwd: str) -> str:
    """
    API-based replacement for the CLI runner.
    Signature kept EXACTLY the same so all existing logic remains unchanged.
    """
    api_key = get_gemini_api_key()
    base_url = env_or(CFG.GEMINI_API_BASE_URL, "GEMINI_API_BASE_URL").rstrip("/")
    model_path = _normalize_api_model(model)

    url = f"{base_url}/{model_path}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        # IMPORTANT: do not force generationConfig defaults here to avoid behavior drift
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(
            "Gemini API HTTPError.\n"
            f"URL: {url}\n"
            f"Status: {getattr(e, 'code', 'unknown')}\n"
            f"Body:\n{body.strip()}\n"
        )
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gemini API URLError: {e}")
    except Exception as e:
        raise RuntimeError(f"Gemini API unknown error: {e}")

    try:
        j = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Gemini API returned non-JSON. Snippet: {raw[:800]}") from e

    # Expected: candidates[0].content.parts[0].text
    cands = j.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise RuntimeError(f"Gemini API returned no candidates. Full JSON: {json.dumps(j, ensure_ascii=False)[:1500]}")

    c0 = cands[0] if isinstance(cands[0], dict) else {}
    content = c0.get("content") if isinstance(c0, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None

    if not isinstance(parts, list) or not parts:
        raise RuntimeError(f"Gemini API candidate missing parts. Full JSON: {json.dumps(j, ensure_ascii=False)[:1500]}")

    p0 = parts[0] if isinstance(parts[0], dict) else {}
    text = p0.get("text", "") if isinstance(p0, dict) else ""
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Gemini API returned empty text in candidate.")
    return text


# ============================================================
# Prompt building
# ============================================================

def build_candidates(eng_nums: List[int], chunks_es: Dict[int, str], minus: int, plus: int) -> List[Tuple[int, str]]:
    cand_set = set()
    for n in eng_nums:
        for k in range(n - minus, n + plus + 1):
            if k in chunks_es:
                cand_set.add(k)
    return [(k, chunks_es[k]) for k in sorted(cand_set)]

def make_batch_prompt(batch: List[Dict[str, Any]], merge_newline: str) -> str:
    instructions = (
        f"You are aligning on-screen text to {CFG.LANGUAGE} script chunks.\n"
        f"You MUST ONLY use the provided candidate {CFG.LANGUAGE} chunks for each item.\n"
        "Do NOT invent, guess, paraphrase, or use any text that does not already exist inside the provided candidates.\n"
        "Do NOT use any other chunk numbers beyond the candidates for that item.\n"
        "Do NOT use text_translated_hint as a source of truth. Candidates are the ONLY source.\n\n"

        "PRIMARY OBJECTIVE:\n"
        "- Your job is to recover the BEST AVAILABLE verbatim target-language text from candidates for each item.\n"
        "- If the correct or closest documentary-style translation already exists anywhere in candidates, you MUST find it there.\n"
        "- Missing a valid available candidate match is a failure.\n\n"

        "NON-NEGOTIABLE WORKFLOW (MUST follow in this exact order for each item):\n"
        "1) Inspect ALL candidate chunks for that item before deciding.\n"
        "2) Choose source_chunk_numbers_dubbed ONLY from candidate chunk numbers for that item.\n"
        "3) Choose exact_script_text by COPY-PASTING a verbatim substring ONLY from the chosen candidate chunk text.\n"
        "4) Derive text_translated ONLY by converting TARGET-LANGUAGE number-words to digits inside exact_script_text.\n"
        "If you cannot guarantee this order, you must fail-closed using the rules below.\n\n"

        "CANDIDATE COVERAGE RULE (HARD):\n"
        "- You MUST scan EVERY candidate chunk for a stronger match before using fallback logic.\n"
        "- You MUST prefer an actually present verbatim candidate substring over a merely conceptual or approximate match.\n"
        "- You MUST NOT stop at the first plausible candidate if a later candidate contains a more exact or more title-like/lower-third-like match.\n"
        "- If multiple candidates contain valid matches, choose the one with the strongest direct evidence under the priority rules below.\n\n"

        "SOURCE-OF-TRUTH ENFORCEMENT (HARD):\n"
        "- exact_script_text MUST be character-for-character present inside the chosen candidate chunk text.\n"
        "- NEVER reconstruct, normalize, autocorrect, translate, or \"improve\" exact_script_text.\n"
        "- Preserve typos, spacing, punctuation, diacritics, symbols, and casing exactly.\n"
        "- HINT IS NOT A SOURCE: you may use text_translated_hint ONLY to search; you may NEVER copy from hint unless that exact substring exists in candidates.\n\n"

        "DIRECT-EVIDENCE PREFERENCE (VERY IMPORTANT):\n"
        "- Prefer candidate substrings that look like true on-screen text units: short labels, titles, lower-thirds, names, dates, counts, rankings, locations, identifiers, or concise documentary phrases.\n"
        "- Prefer the candidate substring with the highest lexical overlap with text_en and/or text_translated_hint WHEN that substring actually exists verbatim in candidates.\n"
        "- Prefer a candidate substring that preserves distinctive anchor terms from text_en/text_translated_hint such as names, numbers, places, dates, superlatives, ordinal/cardinal quantities, and rare nouns.\n"
        "- If one candidate contains these anchor terms and another only matches loosely by topic, choose the anchor-term candidate.\n"
        "- Conceptual similarity is allowed ONLY after exhausting stronger direct-evidence matches.\n\n"

        "WRAPPER STRIP RULE (brackets/braces) — DO THIS BEFORE OUTPUT:\n"
        "- Candidate text may contain full-line wrappers like [ ... ] or { ... }.\n"
        "- If the best match region in candidates is wrapped, you MUST output the INNER text WITHOUT the outer wrappers.\n"
        "  Allowed wrappers: leading/trailing [ ] and leading/trailing { } ONLY.\n"
        "- You may remove ONLY the OUTERMOST wrapper characters.\n"
        "- IMPORTANT: exact_script_text must still be a verbatim substring of the candidate text.\n"
        "  (The inner text is a substring of the wrapped candidate line.)\n"
        "- Apply the same wrapper stripping to text_translated after number conversion.\n"
        "- If you used wrapper stripping, set notes=\"wrapper_stripped\" (or append it if notes already set).\n\n"

        "TEXT TRANSLATED RULE (HARD):\n"
        "- text_translated MUST equal exact_script_text with ONE allowed change only:\n"
        "  convert spelled-out TARGET-LANGUAGE number-words into digits (including thousand/million equivalents).\n"
        "- No other edits: no word changes, no punctuation changes, no casing/diacritics/spacing changes.\n"
        "- If exact_script_text contains ANY spelled-out number expression and you fail to convert it, then fail-closed:\n"
        "  matched=false, exact_script_text=\"\", text_translated=\"\", notes=\"number conversion failed\".\n\n"

        "MATCHING PHILOSOPHY (IMPORTANT):\n"
        "- You are allowed to locate a candidate substring using documentary-style/contextual reasoning.\n"
        "- You are allowed to use fuzzy searching ONLY to FIND the location, but OUTPUT must remain verbatim.\n"
        "- Fuzzy search is SEARCH-ONLY (NOT output): case-insensitive, punctuation-insensitive, whitespace-collapsed,\n"
        "  quote variants, diacritics-insensitive, hyphenation differences, and abbreviations/acronyms vs expanded forms.\n"
        "- Fuzzy searching does NOT permit reconstruction. It only helps you find an already-existing substring inside candidates.\n\n"

        "OUTPUT POLICY (fixes missing lower-thirds):\n"
        "- If an item has NON-EMPTY candidate chunks, you MUST NOT return empty exact_script_text just because the lower-third/title is missing in translation.\n"
        "- You MUST select the best available documentary-style phrase from candidates (verbatim substring) and output it.\n"
        "- Empty exact_script_text is allowed ONLY if:\n"
        "  (a) the item has ZERO candidate chunks (notes=\"no_candidates\"), OR\n"
        "  (b) conflict is unresolvable per the GLOBAL conflict rule below.\n\n"

        "SELECTION RULES (strict priority order):\n"
        "A) EXACT-HINT HIT (highest priority)\n"
        "- If text_translated_hint appears verbatim inside a candidate chunk, output EXACTLY that substring as exact_script_text.\n"
        "- When multiple candidate substrings match, ALWAYS choose the SHORTEST matching substring.\n"
        "- If multiple shortest matches exist, prefer the one with the strongest anchor-term correspondence to text_en.\n\n"

        "B) STRONG DIRECT CANDIDATE MATCH (if no exact-hint hit)\n"
        "- Choose a verbatim substring from candidates that preserves the most specific anchor terms from text_en and/or text_translated_hint.\n"
        "- This includes names, numbers, dates, places, rankings, quantities, distinctive nouns, and short label-style documentary phrasing.\n"
        "- Prefer the most specific available candidate substring, not the most generic one.\n"
        "- When multiple candidate substrings are valid, ALWAYS choose the SHORTEST matching substring that still preserves the key anchor terms.\n\n"

        "C) VERBATIM BEST-MATCH (if no strong direct candidate match)\n"
        "- Choose a verbatim substring from candidates that best matches the meaning of text_en.\n"
        "- The match can be conceptual/documentary-style even if literal word overlap is low, BUT you may ONLY SELECT from candidates.\n"
        "- When multiple candidate substrings match, ALWAYS choose the SHORTEST matching substring.\n\n"

        "D) TITLE MERGE (Title only)\n"
        f"- If item.type == 'Title', you may MERGE multiple verbatim substrings from multiple candidate chunks using {json.dumps(merge_newline)}.\n"
        "- If you merge, exact_script_text is the merged verbatim substrings joined with the delimiter.\n"
        "- Merge ONLY when a single candidate substring does not fully capture the title but multiple candidate substrings clearly do.\n\n"

        "ANTI-GENERIC SELECTION RULE:\n"
        "- Do NOT choose a vague/generic phrase if a more specific candidate substring exists anywhere in candidates.\n"
        "- Do NOT choose a broad documentary sentence fragment when a shorter true label/title/lower-third phrase is available.\n"
        "- Prefer specificity over generality, and direct evidence over thematic similarity.\n\n"

        "SHORT EXTRACT RULE (HARD):\n"
        "- For non-Title items, exact_script_text MUST be short.\n"
        "- Target length: <= 6 words (max 9 words). Prefer the shortest meaningful phrase.\n"
        "- exact_script_text MUST NOT span multiple clauses/sentences.\n"
        "- If your selected candidate region is long, you MUST shrink it to the smallest self-contained phrase that still matches the concept.\n"
        "- While shrinking, preserve the strongest anchor terms whenever possible.\n"
        "- If you cannot shrink without breaking meaning, set matched=false and set exact_script_text=\"empty\", text_translated=\"empty\", notes=\"too_long_cannot_shorten\".\n\n"

        "CONTEXTUAL FALLBACK FLAGGING:\n"
        "- If you used rule C (no exact-hint hit and no strong direct candidate match), set notes=\"contextual_fallback_missing_lowerthird\" (or append it if notes already set).\n"
        "- If you used rule A or B, notes may be empty unless another rule requires notes.\n\n"

        "GLOBAL LOWER-THIRD COLLISION / CONFLICT RULE (APPLIES TO ALL MATCHES):\n"
        "- No item is allowed to output an exact_script_text that captures another lower-third/title.\n"
        "- CONFLICT is defined as ANY of the following:\n"
        "  (1) exact_script_text contains another item's exact_script_text, OR is contained by it (substring overlap)\n"
        "  (2) exact_script_text contains another item's text_translated_hint as a verbatim substring (when that hint exists verbatim in candidates)\n"
        "  (3) CHUNK-LEVEL CONFLICT (IMPORTANT): if ANY chosen candidate chunk TEXT contains another item's text_translated_hint verbatim,\n"
        "      then you MUST NOT take a contextual fallback from that chunk because it already includes another lower-third.\n"
        "- If conflict is detected, you MUST try to RESOLVE it by SHRINKING:\n"
        "  * Shrink exact_script_text to the smallest phrase (still verbatim) that avoids conflict and stays <= 9 words.\n"
        "- If you cannot resolve by shrinking, then DO NOT output the text. Output:\n"
        "  matched=false,\n"
        "  KEEP source_chunk_numbers_dubbed as-is,\n"
        "  exact_script_text=\"empty\",\n"
        "  text_translated=\"empty\",\n"
        "  notes=\"overlap_conflict_unresolvable\".\n\n"

        "MATCHED FIELD RULES (IMPORTANT):\n"
        "- If candidates are NON-EMPTY and you output a non-empty exact_script_text (rule A or B or C or merged Title), set matched=true.\n"
        "- If candidates are EMPTY: matched=false, source_chunk_numbers_dubbed=[], exact_script_text=\"empty\", text_translated=\"empty\", notes=\"no_candidates\".\n"
        "- If you triggered any \"empty\" output rule (too_long_cannot_shorten / overlap_conflict_unresolvable): matched=false with exact_script_text=\"empty\" and text_translated=\"empty\".\n\n"

        "BLANK-STRING FORBIDDEN RULE (HARD):\n"
        "- You MUST NEVER output exact_script_text=\"\" or text_translated=\"\".\n"
        "- Blank strings are forbidden in ALL cases.\n"
        "- If candidate_chunks_es is NON-EMPTY but you still cannot find a valid verbatim substring after scanning ALL candidates,\n"
        "  then output:\n"
        "  matched=false,\n"
        "  KEEP source_chunk_numbers_dubbed as-is,\n"
        "  exact_script_text=\"empty\",\n"
        "  text_translated=\"empty\",\n"
        "  notes=\"not_found_in_candidates\".\n"
        "- If candidates are EMPTY, use notes=\"no_candidates\" with exact_script_text=\"empty\" and text_translated=\"empty\".\n"
        "- If any integrity gate fails, use notes=\"integrity_gate_failed\" with exact_script_text=\"empty\" and text_translated=\"empty\".\n\n"

        "FINAL INTEGRITY GATES (FAIL-CLOSED):\n"
        "- If matched=true:\n"
        "  * source_chunk_numbers_dubbed MUST be non-empty\n"
        "  * You MUST have checked all candidate chunks before finalizing\n"
        "  * exact_script_text MUST be found verbatim inside at least one chosen candidate chunk text (or across chosen chunks if Title merge)\n"
        "  * text_translated MUST equal exact_script_text with ONLY number-word→digit edits\n"
        "  * If a more direct/specific candidate substring exists in candidates, you MUST prefer it over a weaker generic fallback\n"
        "- If ANY gate fails: matched=false, source_chunk_numbers_dubbed=[], exact_script_text=\"empty\", text_translated=\"empty\", notes=\"integrity_gate_failed\".\n\n"

        "OUTPUT FORMAT RULES:\n"
        "- Output ONLY valid JSON (no markdown, no commentary).\n"
        "- Output a JSON array with objects:\n"
        "  id (number), matched (boolean), source_chunk_numbers_dubbed (array[number]), exact_script_text (string), text_translated (string), notes (string)\n"
    )

    payload = {"items": batch}
    return instructions + "\n\nINPUT JSON:\n" + json.dumps(payload, ensure_ascii=False)

# ============================================================
# Helpers
# ============================================================

def load_json_list(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}")
    return data

def index_by_id(items: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("id"), int):
            out[it["id"]] = it
    return out

def split_into_batches(prepared: List[Dict[str, Any]], merge_newline: str, max_items: int, max_chars: int) -> List[List[Dict[str, Any]]]:
    """
    Adaptive batching: minimize number of Gemini calls while keeping prompt size safe.
    """
    batches: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []

    for item in prepared:
        if not cur:
            cur.append(item)
            continue

        test = cur + [item]
        prompt = make_batch_prompt(test, merge_newline)
        if (len(test) > max_items) or (len(prompt) > max_chars):
            batches.append(cur)
            cur = [item]
        else:
            cur.append(item)

    if cur:
        batches.append(cur)
    return batches


def call_and_parse_batch(
    batch: List[Dict[str, Any]],
    gemini_bin: str,
    model: str,
    merge_newline: str,
    timeout_sec: int,
    disable_ext: bool,
    empty_cwd: str,
    raw_dir: Path,
    batch_index: int,
) -> List[Dict[str, Any]]:
    """
    Calls Gemini for one batch, saves raw output, parses JSON array robustly.
    Retries on ANY failure up to 5 times (Gemini API failures OR JSON parse failures).
    Keeps existing STRICT suffix behavior.
    """
    MAX_RETRIES = 5

    def _sleep_backoff(attempt: int) -> None:
        sec = min(10.0, float(2 ** (attempt - 1)))
        time.sleep(sec)

    prompt = make_batch_prompt(batch, merge_newline)

    last_raw_path: Optional[Path] = None
    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp_text = run_gemini(gemini_bin, model, prompt, timeout_sec, disable_ext, empty_cwd)
        except Exception as e:
            last_err = e
            last_raw_path = raw_dir / f"batch_{batch_index:03d}_attempt_{attempt}_{model.replace('/','_')}_ERROR.txt"
            last_raw_path.write_text(str(e), encoding="utf-8", errors="replace")

            if attempt < MAX_RETRIES:
                _sleep_backoff(attempt)
                continue
            raise RuntimeError(f"❌ Gemini call failed after {MAX_RETRIES} retries. Last raw/error saved: {last_raw_path}") from e

        last_raw_path = raw_dir / f"batch_{batch_index:03d}_attempt_{attempt}_{model.replace('/','_')}.txt"
        last_raw_path.write_text(resp_text, encoding="utf-8", errors="replace")

        try:
            parsed = safe_json_load_from_text(resp_text)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                prompt = (
                    prompt
                    + "\n\nSTRICT MODE:\n"
                      "- Output MUST be ONLY a JSON ARRAY.\n"
                      "- Start with '[' and end with ']'.\n"
                      "- No extra text.\n"
                      "- If you cannot, return []\n"
                )
                _sleep_backoff(attempt)
                continue
            raise RuntimeError(f"❌ JSON parse failed after {MAX_RETRIES} retries. Raw saved: {last_raw_path}") from e

        if isinstance(parsed, dict):
            for key in ("items", "data", "results", "output", "response"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break

        if isinstance(parsed, dict) and isinstance(parsed.get("id"), (int, str)):
            parsed = [parsed]

        if not isinstance(parsed, list):
            last_err = RuntimeError("Parsed output is not a list.")
            if attempt < MAX_RETRIES:
                prompt = (
                    prompt
                    + "\n\nSTRICT MODE:\n"
                      "- Output MUST be ONLY a JSON ARRAY.\n"
                      "- Start with '[' and end with ']'.\n"
                      "- No extra text.\n"
                      "- If you cannot, return []\n"
                )
                _sleep_backoff(attempt)
                continue
            raise RuntimeError(f"❌ Parsed output is not a list after {MAX_RETRIES} retries. Raw saved: {last_raw_path}")

        return [x for x in parsed if isinstance(x, dict)]

    raise RuntimeError(f"Failed batch {batch_index} after retries. Raw: {last_raw_path}\nLast error: {last_err}")


# ============================================================
# MAIN
# ============================================================

def main():
    total_start_ts = time.time()

    # NEW: load .env before argparse defaults read env vars
    load_dotenv_if_present()

    ap = argparse.ArgumentParser()

    # Files
    ap.add_argument("--base-json", default=env_or(CFG.BASE_JSON, "BASE_JSON"))
    ap.add_argument("--hint-json", default=env_or(CFG.HINT_JSON, "HINT_JSON"))
    ap.add_argument("--es-chunks", default=env_or(CFG.ES_CHUNKS_TXT, "ES_CHUNKS_TXT"))
    ap.add_argument("--out", default=env_or(CFG.OUT_JSON, "OUT_JSON"))

    # Gemini (args kept identical for compatibility)
    ap.add_argument("--gemini-bin", default=env_or(CFG.GEMINI_BIN, "GEMINI_BIN"))
    ap.add_argument("--model", default=env_or(CFG.GEMINI_MODEL, "GEMINI_MODEL"))
    ap.add_argument("--batch-size", type=int, default=env_int_or(CFG.BATCH_SIZE, "BATCH_SIZE"))
    ap.add_argument("--max-prompt-chars", type=int, default=env_int_or(CFG.MAX_PROMPT_CHARS, "MAX_PROMPT_CHARS"))
    ap.add_argument("--timeout-sec", type=int, default=env_int_or(CFG.TIMEOUT_SEC, "TIMEOUT_SEC"))

    # Fast-first
    ap.add_argument("--fast-first", action="store_true",
                    help="Use flash first, then fallback to pro for failures.")
    ap.add_argument("--fast-model", default=env_or(CFG.GEMINI_FAST_MODEL, "GEMINI_FAST_MODEL"))

    # Behavior
    ap.add_argument("--window-minus", type=int, default=env_int_or(CFG.WINDOW_MINUS, "WINDOW_MINUS"))
    ap.add_argument("--window-plus", type=int, default=env_int_or(CFG.WINDOW_PLUS, "WINDOW_PLUS"))
    ap.add_argument("--merge-newline", default=env_or(CFG.MERGE_NEWLINE, "MERGE_NEWLINE"))

    # Progress
    ap.add_argument("--no-progress", action="store_true", help="Disable progress bar output.")
    ap.add_argument("--dry-run", action="store_true", help="Print first prompt and exit (no Gemini calls).")

    args = ap.parse_args()

    # Load language from config.json (capitalized)
    CFG.LANGUAGE = load_language_from_config(CFG.CONFIG_JSON)

    show_progress = CFG.SHOW_PROGRESS and (not args.no_progress) and env_bool_or(True, "SHOW_PROGRESS")
    disable_ext = env_bool_or(CFG.DISABLE_EXTENSIONS, "DISABLE_EXTENSIONS")
    empty_cwd = env_or(CFG.EMPTY_CWD_DIR, "EMPTY_CWD_DIR")

    base_path = Path(args.base_json).resolve()
    hint_path = Path(args.hint_json).resolve()
    es_chunks_path = Path(args.es_chunks).resolve()
    out_path = Path(args.out).resolve()

    base_items = load_json_list(base_path)
    hint_items = load_json_list(hint_path)
    hint_by_id = index_by_id(hint_items)

    chunks_es = parse_numbered_chunks_txt(es_chunks_path)

    # --- CRITICAL AUTO-OFFSET LOGIC ---
    # Automatically fixes if chunks start at 1 but Base JSON needs 140+
    required_ids = []
    for it in base_items:
        m = it.get("match") or {}
        nums = m.get("source_chunk_numbers") or []
        if isinstance(nums, list):
            required_ids.extend([n for n in nums if isinstance(n, int)])
    
    if required_ids and chunks_es:
        min_required = min(required_ids)
        min_available = min(chunks_es.keys())
        
        # If chunks start at 1 (from line counting) but JSON needs 140+
        # Trigger offset only if difference is significant (>10)
        if min_available < 10 and min_required > 50:
            offset = min_required - min_available
            print(f"⚠️  DEBUG: Auto-Offsetting Chunk IDs by +{offset} (Converting #{min_available} -> #{min_required})")
            chunks_es = {k + offset: v for k, v in chunks_es.items()}
    # ----------------------------------

    _sanity_check_chunks_or_die(chunks_es, base_items, args.es_chunks)

    
    prepared: List[Dict[str, Any]] = []
    for it in base_items:
        item_id = it.get("id")
        if not isinstance(item_id, int):
            continue

        match = it.get("match") if isinstance(it.get("match"), dict) else {}
        eng_nums = match.get("source_chunk_numbers") or []
        if not isinstance(eng_nums, list):
            eng_nums = []
        eng_nums = [n for n in eng_nums if isinstance(n, int)]

        hint_text_es = ""
        h = hint_by_id.get(item_id, {})
        if isinstance(h, dict) and isinstance(h.get("text_translated"), str):
            hint_text_es = h["text_translated"]

        candidates = build_candidates(eng_nums, chunks_es, args.window_minus, args.window_plus)

        prepared.append({
            "id": item_id,
            "type": it.get("type", ""),
            "text_en": it.get("text", ""),
            "text_translated_hint": hint_text_es,
            "source_chunk_numbers_eng": eng_nums,
            "candidate_chunks_es": [{"n": n, "text": t} for (n, t) in candidates],
        })

    if args.dry_run:
        sample = prepared[: min(len(prepared), 5)]
        print(make_batch_prompt(sample, args.merge_newline))
        return

    bs = max(1, int(args.batch_size))
    max_chars = max(20000, int(args.max_prompt_chars))

    batches = split_into_batches(prepared, args.merge_newline, max_items=bs, max_chars=max_chars)

    updates: Dict[int, Dict[str, Any]] = {}

    raw_dir = Path("gemini_raw_responses")
    raw_dir.mkdir(parents=True, exist_ok=True)

    batch_start_ts = time.time()
    total_batches = len(batches)

    if show_progress:
        print_progress(0, total_batches, batch_start_ts, label="starting")

    def _clean_str(v: Any) -> str:
        return v.strip() if isinstance(v, str) else ""

    def _append_note(row: Dict[str, Any], note: str) -> None:
        old = _clean_str(row.get("notes"))
        if not old:
            row["notes"] = note
            return
        parts = [p.strip() for p in old.split("|") if p.strip()]
        if note not in parts:
            parts.append(note)
        row["notes"] = " | ".join(parts)

    def apply_rows(rows: List[Dict[str, Any]]):
        for row in rows:
            rid = row.get("id")
            if isinstance(rid, str) and rid.isdigit():
                rid = int(rid)

            if not isinstance(rid, int):
                continue

            exact_val = _clean_str(row.get(CFG.EXACT_TEXT_FIELD))
            translated_val = _clean_str(row.get("text_translated"))
            notes_val = _clean_str(row.get("notes"))

            # HARD SAFETY:
            # exact_script_text must NEVER be blank/null in final updates.
            # Do NOT replace it with text_translated because downstream alignment
            # depends on exact spoken span copied from candidates.
            if not exact_val:
                row[CFG.EXACT_TEXT_FIELD] = "empty"
                row["matched"] = False

                if not translated_val:
                    row["text_translated"] = "empty"

                if not notes_val:
                    row["notes"] = "blank_exact_script_text_normalized"
                else:
                    _append_note(row, "blank_exact_script_text_normalized")

            # If exact_script_text is explicitly empty placeholder, force matched=false
            if _clean_str(row.get(CFG.EXACT_TEXT_FIELD)).lower() == "empty":
                row["matched"] = False
                if not _clean_str(row.get("text_translated")):
                    row["text_translated"] = "empty"

            if rid not in updates:
                updates[rid] = row
            else:
                old = updates[rid]

                old_exact = _clean_str(old.get(CFG.EXACT_TEXT_FIELD))
                new_exact = _clean_str(row.get(CFG.EXACT_TEXT_FIELD))

                old_good = bool(old.get("matched")) and old_exact and old_exact.lower() != "empty"
                new_good = bool(row.get("matched")) and new_exact and new_exact.lower() != "empty"

                # prefer a truly valid matched row over an invalid/unmatched one
                if new_good and not old_good:
                    updates[rid] = row

    use_fast = args.fast_first or CFG.USE_FAST_FIRST
    model_pass1 = args.fast_model if use_fast else args.model

    for bi, batch in enumerate(batches, start=1):
        try:
            rows = call_and_parse_batch(
                batch=batch,
                gemini_bin=args.gemini_bin,
                model=model_pass1,
                merge_newline=args.merge_newline,
                timeout_sec=int(args.timeout_sec),
                disable_ext=disable_ext,
                empty_cwd=empty_cwd,
                raw_dir=raw_dir,
                batch_index=bi,
            )
        except Exception as e:
            # Re-raise to ensure proper exit on API failure
            raise RuntimeError(f"Batch {bi} failed completely: {e}")
        apply_rows(rows)

        if show_progress:
            print_progress(bi, total_batches, batch_start_ts, label=f"batch {bi}/{total_batches}")

    if use_fast and args.model != model_pass1:
        failed_ids = []
        for it in prepared:
            rid = it["id"]
            got = updates.get(rid)
            if (
                not got
                or (not bool(got.get("matched")))
                or (not isinstance(got.get(CFG.EXACT_TEXT_FIELD), str))
                or (not got.get(CFG.EXACT_TEXT_FIELD).strip())
            ):
                failed_ids.append(rid)

        if failed_ids:
            failed_set = set(failed_ids)
            failed_items = [x for x in prepared if x["id"] in failed_set]
            failed_batches = split_into_batches(failed_items, args.merge_newline, max_items=bs, max_chars=max_chars)

            for bi2, batch in enumerate(failed_batches, start=1):
                try:
                    rows = call_and_parse_batch(
                        batch=batch,
                        gemini_bin=args.gemini_bin,
                        model=args.model,  # Pro fallback
                        merge_newline=args.merge_newline,
                        timeout_sec=int(args.timeout_sec),
                        disable_ext=disable_ext,
                        empty_cwd=empty_cwd,
                        raw_dir=raw_dir,
                        batch_index=1000 + bi2,
                    )
                except Exception as e:
                    raise RuntimeError(f"Retry Batch {bi2} failed completely: {e}")
                apply_rows(rows)

    if show_progress:
        end_progress()

    final_items = []
    for it in base_items:
        item_id = it.get("id")
        if not isinstance(item_id, int) or item_id not in updates:
            final_items.append(it)
            continue

        upd = updates[item_id]
        matched = bool(upd.get("matched", False))

        src_dub = upd.get("source_chunk_numbers_dubbed", [])
        if not isinstance(src_dub, list):
            src_dub = []
        src_dub = [n for n in src_dub if isinstance(n, int)]

        exact_txt = upd.get(CFG.EXACT_TEXT_FIELD, "empty")
        if not isinstance(exact_txt, str) or not exact_txt.strip():
            exact_txt = "empty"

        new_text = upd.get("text_translated", "empty")
        if not isinstance(new_text, str) or not new_text.strip():
            new_text = "empty"

        it2 = dict(it)
        if "match" not in it2 or not isinstance(it2["match"], dict):
            it2["match"] = {}

        it2["match"]["source_chunk_numbers_dubbed"] = src_dub
        it2["match"]["matched_dubbed"] = matched
        it2["text_translated"] = new_text
        it2[CFG.EXACT_TEXT_FIELD] = exact_txt

        final_items.append(it2)

    out_path.write_text(json.dumps(final_items, ensure_ascii=False, indent=2), encoding="utf-8")

    total_elapsed = time.time() - total_start_ts
    print(f"✅ Saved -> {out_path}")
    print(f"⏱️ Total time taken: {fmt_min_sec(total_elapsed)}")


if __name__ == "__main__":
    main()