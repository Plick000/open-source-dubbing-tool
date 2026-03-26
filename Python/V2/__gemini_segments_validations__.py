#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import subprocess
import sys
import time
import shutil
import errno
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional


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

    # Gemini CLI
    GEMINI_BIN: str = "/home/vvruntime/.npm-global/bin/gemini"
    GEMINI_MODEL: str = "gemini-2.5-pro"

    # OPTIONAL: Fast-first then fallback to Pro for failures
    USE_FAST_FIRST: bool = True
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

    # Gemini behavior hardening
    DISABLE_EXTENSIONS: bool = True  # -e none
    EMPTY_CWD_DIR: str = str(Path.home() / ".cache" / "gemini_headless_empty")

    # Candidate fallback controls (SAFE: only affects candidate generation)
    HINT_FALLBACK_MAX_HITS: int = 3          # max chunks to seed from hint hits
    HINT_FALLBACK_MIN_HINT_LEN: int = 2      # ignore trivial hints like "" or 1-char


CFG = Config()


# ============================================================
# ENV VAR OVERRIDES (optional)
# ============================================================

# =========================
# Gemini creds cache (Whisper-style copy-once)
# =========================

def _to_wsl_path(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return p
    # Windows drive path -> /mnt/<drive>/...
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:].lstrip("\\/").replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return p

GEMINI_CREDS_SHARED = _to_wsl_path(os.environ.get(
    "GEMINI_CREDS_SHARED",
    "/mnt/z/Automated Dubbings/_tools/gemini/Room-2-Sufiyan/.anotherGemini"
))
GEMINI_CREDS_LOCAL = _to_wsl_path(os.environ.get(
    "GEMINI_CREDS_LOCAL",
    r"C:\__tools\gemini\.anotherGemini"
))

# We will force Gemini CLI to read creds from HOME/.gemini by setting HOME to parent of GEMINI_CREDS_LOCAL
_GEMINI_HOME_PARENT: Optional[Path] = None

def _dir_has_files(p: Path) -> bool:
    try:
        return p.exists() and p.is_dir() and any(p.iterdir())
    except Exception:
        return False

def ensure_gemini_creds_cached() -> Path:
    """Copy creds once from shared -> local cache. Returns LOCAL .gemini path."""
    src = Path(GEMINI_CREDS_SHARED)
    dst = Path(GEMINI_CREDS_LOCAL)
    fallback_dst = Path(os.environ.get("GEMINI_CREDS_LOCAL_FALLBACK", "/app/__tools/gemini/.gemini"))

    if _dir_has_files(dst):
        return dst

    if not src.exists():
        # Fallback for Docker environments where shared drive might not be mapped
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            return dst
        except Exception:
            return fallback_dst

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If /mnt/c isn't mounted/writable inside Docker, fall back to a path under /app (bind-mounted repo)
        dst = fallback_dst
        dst.parent.mkdir(parents=True, exist_ok=True)

    lock_path = dst.parent / ".gemini_copy.lock"

    # Acquire lock (avoid overlapping copy)
    for _ in range(120):  # ~60s max
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            time.sleep(0.5)
    else:
        # If lock timeout, try to proceed anyway (race condition risk but better than crash)
        pass

    try:
        if _dir_has_files(dst):
            return dst

        tmp = dst.parent / (dst.name + ".__tmp_copy__")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

        shutil.copytree(src, tmp)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        tmp.rename(dst)

        return dst
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


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
    re_num = re.compile(r'^\s*(?:chunk\s+)?(\d{1,5})(?:\]|[:.)]|-)\s*(.*)$', re.IGNORECASE)

    for line in lines:
        s = line.strip()
        if not s:
            if cur_n is not None and cur_lines and cur_lines[-1] != "":
                cur_lines.append("")
            continue

        # Detect [source: N]
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
            return {i + 1: str(x).strip() for i, x in enumerate(j) if str(x).strip()}
    except Exception:
        pass

    raise ValueError(f"Could not parse chunks from {path}")


def _max_needed_chunk_from_base(base_items: List[Dict[str, Any]]) -> int:
    max_needed = 0
    for it in base_items:
        m = it.get("match")
        if isinstance(m, dict):
            nums = m.get("source_chunk_numbers") or []
            if isinstance(nums, list):
                for n in nums:
                    if isinstance(n, int) and n > max_needed:
                        max_needed = n
    return max_needed


def _sanity_check_chunks_or_die(chunks: Dict[int, str], base_items: List[Dict[str, Any]], chunks_path: str) -> None:
    max_needed = _max_needed_chunk_from_base(base_items)
    if max_needed <= 0:
        return

    if not chunks:
        print(f"⚠️  WARNING: Chunks parsed EMPTY. Continuing anyway.")
        return

    max_have = max(chunks.keys())
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
    text = (text or "").strip()
    dec = json.JSONDecoder()
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
    raise ValueError(f"Could not locate JSON in response. Snippet: {snippet}")

def safe_json_load_from_text(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return extract_first_json_object(text)


# ============================================================
# Gemini CLI calling
# ============================================================

def run_gemini(gemini_bin: str, model: str, prompt: str, timeout_sec: int, disable_ext: bool, empty_cwd: str) -> str:
    cmd = [gemini_bin, "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["-m", model]
    if disable_ext:
        cmd += ["-e", "none"]

    cwd_path = Path(empty_cwd)
    cwd_path.mkdir(parents=True, exist_ok=True)

    try:
        run_env = os.environ.copy()
        if _GEMINI_HOME_PARENT is not None:
            run_env["HOME"] = str(_GEMINI_HOME_PARENT)

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            cwd=str(cwd_path),
            env=run_env
        )
    except FileNotFoundError:
        raise RuntimeError(f"Gemini binary not found: '{gemini_bin}'")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Gemini CLI timed out.")

    if proc.returncode != 0:
        raise RuntimeError(f"Gemini CLI Error. STDERR: {proc.stderr.strip()}")

    outer = extract_first_json_object(proc.stdout)
    if not isinstance(outer, dict):
        raise RuntimeError("Gemini CLI stdout JSON is not an object.")

    resp = outer.get("response", "")
    if not isinstance(resp, str) or not resp.strip():
        raise RuntimeError("Gemini returned empty response.")
    return resp


# ============================================================
# Candidate building helpers (FIX for missing candidates)
# ============================================================

def _as_int_list(v: Any) -> List[int]:
    """Safely normalize a value into a list[int] (accepts ints and numeric strings)."""
    out: List[int] = []
    if v is None:
        return out
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return [int(s)]
        return out
    if isinstance(v, list):
        for x in v:
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, str):
                xs = x.strip()
                if xs.isdigit():
                    out.append(int(xs))
    return out

def extract_source_chunk_numbers_from_base_item(it: Dict[str, Any]) -> List[int]:
    """
    Primary: it['match']['source_chunk_numbers']
    Fallback: a few safe alternative locations/keys (keeps original logic, just more robust).
    """
    if not isinstance(it, dict):
        return []

    m = it.get("match") if isinstance(it.get("match"), dict) else {}
    nums = _as_int_list(m.get("source_chunk_numbers"))

    if nums:
        return nums

    # Conservative fallbacks (only used when primary is missing)
    for key in (
        "source_chunk_numbers",
        "source_chunk_numbers_eng",
        "source_chunk_numbers_english",
        "chunk_numbers",
        "chunks",
    ):
        nums = _as_int_list(it.get(key))
        if nums:
            return nums

    return []

def _normalize_for_search(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # collapse whitespace only (do NOT change punctuation; this is just for searching)
    s = re.sub(r"\s+", " ", s)
    return s

def seed_eng_nums_from_hint(hint_text: str, chunks_es: Dict[int, str], max_hits: int) -> List[int]:
    """
    SAFE fallback: use hint only to locate which chunk IDs might contain it.
    This only affects which candidates we provide; Gemini is still forced to copy verbatim from candidates.
    """
    hint = (hint_text or "").strip()
    if len(hint) < int(CFG.HINT_FALLBACK_MIN_HINT_LEN):
        return []

    hint_norm = _normalize_for_search(hint)
    if not hint_norm:
        return []

    hits: List[int] = []
    for n, txt in chunks_es.items():
        if not isinstance(txt, str) or not txt:
            continue
        if hint in txt:
            hits.append(n)
        else:
            # light whitespace-normalized contains (search-only)
            if hint_norm in _normalize_for_search(txt):
                hits.append(n)

        if len(hits) >= max_hits:
            break

    return hits

def build_candidates(eng_nums: List[int], chunks_es: Dict[int, str], minus: int, plus: int) -> List[Tuple[int, str]]:
    cand_set = set()
    for n in eng_nums:
        for k in range(n - minus, n + plus + 1):
            if k in chunks_es:
                cand_set.add(k)
    return [(k, chunks_es[k]) for k in sorted(cand_set)]


# ============================================================
# Prompt building
# ============================================================

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
    Retries on ANY failure up to 5 times (Gemini CLI failures OR JSON parse failures).
    """
    MAX_RETRIES = 5

    def _retry_after_seconds_from_error(err: Exception) -> int:
        s = str(err)
        m = re.search(r"Please retry in\s+([\d.]+)s", s)
        if m:
            return int(float(m.group(1))) + 2
        m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', s)
        if m:
            return int(m.group(1)) + 2
        return 0

    def _sleep_backoff(attempt: int, err: Exception = None) -> None:
        sec = min(10.0, float(2 ** (attempt - 1)))
        if err is not None:
            ra = _retry_after_seconds_from_error(err)
            if ra > sec:
                sec = float(ra)
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
                _sleep_backoff(attempt, e)
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

        # normalize wrappers
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


def run_api_fallback_and_exit(raw_dir: Path, err: Exception) -> None:
    """
    Fallback: after this script exhausts its 5 internal retries and still fails,
    run the API-based validator script once using the SAME CLI args as this script.
    """
    api_script = Path(__file__).with_name("__gemini_segments_validation_using_API__.py")
    if not api_script.exists():
        raise RuntimeError(
            f"API fallback script not found: {api_script}. "
            "Expected it in the same folder as this script."
        ) from err

    cmd = [sys.executable, str(api_script)] + sys.argv[1:]
    log_path = raw_dir / f"api_fallback_batch_failure_{int(time.time())}.log"

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    log_path.write_text(
        "CMD:\n" + " ".join(cmd) + "\n\nSTDOUT:\n" + (proc.stdout or "") + "\n\nSTDERR:\n" + (proc.stderr or ""),
        encoding="utf-8",
        errors="replace",
    )

    if proc.returncode == 0:
        print(f"✅ API fallback succeeded. Log saved: {log_path}")
        sys.exit(0)

    raise RuntimeError(
        f"❌ API fallback failed (exit code {proc.returncode}). Log saved: {log_path}"
    ) from err


# ============================================================
# MAIN
# ============================================================

def main():
    total_start_ts = time.time()

    ap = argparse.ArgumentParser()

    # Files
    ap.add_argument("--base-json", default=env_or(CFG.BASE_JSON, "BASE_JSON"))
    ap.add_argument("--hint-json", default=env_or(CFG.HINT_JSON, "HINT_JSON"))
    ap.add_argument("--es-chunks", default=env_or(CFG.ES_CHUNKS_TXT, "ES_CHUNKS_TXT"))
    ap.add_argument("--out", default=env_or(CFG.OUT_JSON, "OUT_JSON"))

    # Gemini
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

    # ✅ Cache Gemini creds locally once, then force Gemini CLI to use it via HOME/.gemini
    global _GEMINI_HOME_PARENT
    local_gemini_dir = ensure_gemini_creds_cached()
    _GEMINI_HOME_PARENT = local_gemini_dir.parent

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
    required_ids = []
    for it in base_items:
        m = it.get("match") or {}
        nums = m.get("source_chunk_numbers") or []
        if isinstance(nums, list):
            required_ids.extend([n for n in nums if isinstance(n, int)])

    if required_ids and chunks_es:
        min_required = min(required_ids)
        min_available = min(chunks_es.keys())

        # If chunks start at 1 but JSON needs 140+
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

        # --- FIX: robust extraction of source chunk numbers ---
        eng_nums = extract_source_chunk_numbers_from_base_item(it)

        hint_text_es = ""
        h = hint_by_id.get(item_id, {})
        if isinstance(h, dict) and isinstance(h.get("text_translated"), str):
            hint_text_es = h["text_translated"]

        # --- FIX: if eng_nums empty, seed from hint hits (candidate-building only) ---
        eng_nums_for_candidates = list(eng_nums)
        if not eng_nums_for_candidates:
            seeded = seed_eng_nums_from_hint(
                hint_text=hint_text_es,
                chunks_es=chunks_es,
                max_hits=int(CFG.HINT_FALLBACK_MAX_HITS),
            )
            if seeded:
                eng_nums_for_candidates = seeded

        candidates = build_candidates(eng_nums_for_candidates, chunks_es, args.window_minus, args.window_plus)

        prepared.append({
            "id": item_id,
            "type": it.get("type", ""),
            "text_en": it.get("text", ""),
            "text_translated_hint": hint_text_es,
            # Keep original eng nums, but reflect what we used for candidate building if original was empty
            "source_chunk_numbers_eng": eng_nums_for_candidates,
            "candidate_chunks_es": [{"n": n, "text": t} for (n, t) in candidates],
        })

    if args.dry_run:
        sample = prepared[: min(len(prepared), 5)]
        print(make_batch_prompt(sample, args.merge_newline))
        return

    bs = max(1, int(args.batch_size))
    max_chars = max(20000, int(args.max_prompt_chars))

    # Adaptive batches
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

    # Pass 1: either Pro (default) or Fast model (if enabled)
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
            # Only reached AFTER call_and_parse_batch exhausted its 5 retries
            run_api_fallback_and_exit(raw_dir, e)
            raise

        apply_rows(rows)

        if show_progress:
            print_progress(bi, total_batches, batch_start_ts, label=f"batch {bi}/{total_batches}")

    # Pass 2 (optional): if fast-first, re-run ONLY failures with Pro
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
                        batch_index=1000 + bi2,  # separate numbering
                    )
                except Exception as e:
                    # Only reached AFTER call_and_parse_batch exhausted its 5 retries
                    run_api_fallback_and_exit(raw_dir, e)
                    raise
                apply_rows(rows)

    if show_progress:
        end_progress()

    # Merge back into base JSON
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