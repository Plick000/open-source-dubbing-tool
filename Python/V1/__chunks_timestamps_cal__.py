#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
__chunks_timestamps_cal__.py (STRICT + CONTIGUOUS)

YOUR REQUIREMENTS IMPLEMENTED:

1) CONTIGUOUS TIMELINE (as you requested)
   - Chunk 1 start = 0.0
   - Chunk i start = Chunk (i-1) end
   - We do NOT add whitespace / silence to ends.

2) STRICT CHUNKS == SCRIPT (punctuation-insensitive)
   - We tokenize both script.txt and chunks.txt into the SAME word-token stream
     (Unicode alnum runs; punctuation ignored; numbers kept).
   - If ANY word is extra/missing -> hard fail with an exact diff preview.

3) STRICT CHUNKS == ALIGNMENT (punctuation-insensitive)
   - Each chunk MUST match an exact contiguous token sequence in the alignment text.
   - No partial matching. No difflib fallback.
   - If a chunk contains any word not in alignment at the correct position -> hard fail.

4) STRICT COVERAGE
   - After processing all chunks, there must be NO remaining alignment words.
     If alignment has extra trailing words -> hard fail.

5) END BOUNDARIES NOT MID-WORD
   - We snap the matched end_char forward ONLY inside the SAME word and any
     directly-attached punctuation (no spaces consumed).

NOTE:
- This script assumes your alignment JSON contains:
  characters, character_start_times_seconds, character_end_times_seconds
  or the same inside {"alignment": {...}}.
"""

import bisect
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# DEFAULT PATHS (keep your old structure)
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_JSON = PROJECT_ROOT / "inputs" / "audios" / "__dubbed__audio__alignments_output__.json"
CHUNKS_TXT = PROJECT_ROOT / "inputs" / "chunks" / "chunks.txt"
SCRIPT_TXT = PROJECT_ROOT / "inputs" / "scripts" / "script.txt"

OUTPUT_JSON = PROJECT_ROOT / "output" / "JSON" / "A3__dubbing__.json"
REPORT_JSON = OUTPUT_JSON.with_name("A3__dubbing__report.json")

FPS = 24
DEBUG = True

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]", re.UNICODE)
ONLY_NUMBER_LINE_RE = re.compile(r"^\s*\d+\s*[.)]?\s*$", re.UNICODE)
LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[.)]\s*", re.UNICODE)


class StrictValidationError(Exception):
    pass


class MissingChunksError(Exception):
    def __init__(self, missing: List[Dict[str, Any]]):
        self.missing = missing
        super().__init__(f"{len(missing)} chunk(s) missing in alignment.")


 
def _ord_in_ranges(cp: int, ranges: List[Tuple[int, int]]) -> bool:
    for a, b in ranges:
        if a <= cp <= b:
            return True
    return False

# Treat joiners as boundaries for comparison (prevents Persian/Urdu token merges)
def _zw_boundary_safe(s: str) -> str:
    if not s:
        return ""
    # ZWNJ U+200C, ZWJ U+200D, WORD JOINER U+2060
    return s.replace("\u200c", " ").replace("\u200d", " ").replace("\u2060", " ")

# --- Script detection ranges (inclusive) ---
_ARABIC_RANGES: List[Tuple[int, int]] = [
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
    (0x1EE00, 0x1EEFF),
]
_HEBREW_RANGES: List[Tuple[int, int]] = [(0x0590, 0x05FF)]
_THAI_RANGES: List[Tuple[int, int]] = [(0x0E00, 0x0E7F)]
_CJK_RANGES: List[Tuple[int, int]] = [
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana extensions
    (0xFF66, 0xFF9D),  # Halfwidth Katakana
    (0x3400, 0x4DBF),  # CJK Ext A
    (0x4E00, 0x9FFF),  # Han
    (0xF900, 0xFAFF),  # Compatibility Ideographs
]
_INDIC_RANGES: List[Tuple[int, int]] = [
    (0x0900, 0x097F),  # Devanagari (Hindi/Marathi)
    (0x0980, 0x09FF),  # Bengali (Bangla)
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0D00, 0x0D7F),  # Malayalam
]

def detect_mode(text: str) -> str:
    """
    Script-aware mode auto-detection.
    NOTE: Hangul is NOT treated as CJK here; Korean remains DEFAULT_WORD (space-delimited).
    """
    s = text or ""
    for ch in s:
        cp = ord(ch)
        if _ord_in_ranges(cp, _ARABIC_RANGES):
            return "ARABIC_WORD"
        if _ord_in_ranges(cp, _HEBREW_RANGES):
            return "HEBREW_WORD"
        if _ord_in_ranges(cp, _THAI_RANGES):
            return "THAI_CHAR"
        if _ord_in_ranges(cp, _CJK_RANGES):
            return "CJK_CHAR"
        if _ord_in_ranges(cp, _INDIC_RANGES):
            return "INDIC_WORD"
    return "DEFAULT_WORD"

def _arabic_normalize_for_compare(s: str) -> str:
    if not s:
        return ""
    # Common inconsistent Arabic marks (harakat etc.)
    arabic_diacritic_ranges: List[Tuple[int, int]] = [
        (0x064B, 0x065F),
        (0x0670, 0x0670),
        (0x06D6, 0x06ED),
        (0x08D3, 0x08FF),
    ]
    out: List[str] = []
    for ch in s:
        cp = ord(ch)
        # tatweel
        if cp == 0x0640:
            continue
        # Arabic-Indic digits
        if 0x0660 <= cp <= 0x0669:
            out.append(chr(ord("0") + (cp - 0x0660)))
            continue
        # Eastern Arabic-Indic digits
        if 0x06F0 <= cp <= 0x06F9:
            out.append(chr(ord("0") + (cp - 0x06F0)))
            continue
        # strip Arabic diacritics/marks conservatively
        if _ord_in_ranges(cp, arabic_diacritic_ranges) and unicodedata.category(ch).startswith("M"):
            continue
        # Alef forms -> ا
        if cp in (0x0623, 0x0625, 0x0622):
            out.append("\u0627")
            continue
        # Yeh forms (incl Persian ی) -> ي
        if cp in (0x0649, 0x064A, 0x0626, 0x06CC):
            out.append("\u064A")
            continue
        # Kaf forms (incl Persian ک) -> ك
        if cp in (0x0643, 0x06A9):
            out.append("\u0643")
            continue
        out.append(ch)
    return "".join(out)

def _hebrew_normalize_for_compare(s: str) -> str:
    if not s:
        return ""
    out: List[str] = []
    for ch in s:
        cp = ord(ch)
        if _ord_in_ranges(cp, _HEBREW_RANGES) and unicodedata.category(ch).startswith("M"):
            continue
        out.append(ch)
    return "".join(out)


# -----------------------------
# I/O
# -----------------------------
def load_chunks_txt(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Chunks TXT not found: {path}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    chunks: List[str] = []
    for ln in lines:
        raw = (ln or "").strip()
        if not raw:
            continue

        # Preserve your existing “punct-only line merges into previous chunk”
        is_punct = (re.sub(r"[^\w]+", "", raw, flags=re.UNICODE) == "")
        if is_punct and chunks:
            chunks[-1] += raw
        elif not is_punct:
            chunks.append(raw)

    return chunks


def load_script_txt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"script.txt not found: {path}")

    raw = path.read_text(encoding="utf-8", errors="replace")
    # Remove only obvious numbering-only lines; keep all real content
    # (so script and chunks can be strictly compared).
    cleaned_lines: List[str] = []
    for line in raw.splitlines():
        l = (line or "").strip()
        if not l:
            continue
        if ONLY_NUMBER_LINE_RE.match(l):
            continue
        # remove "1. " prefix if the script has numbering lines
        l = LEADING_NUMBER_RE.sub("", l)
        cleaned_lines.append(l)

    return "\n".join(cleaned_lines).strip()


def load_alignment(path: Path) -> Tuple[str, List[str], List[float], List[float]]:
    if not path.exists():
        raise FileNotFoundError(f"Alignment JSON not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))

    if "characters" in data:
        src = data
    elif "alignment" in data:
        src = data["alignment"]
    else:
        raise ValueError("Alignment JSON missing 'characters' or 'alignment' key.")

    chars = src.get("characters", [])
    starts = src.get("character_start_times_seconds", [])
    ends = src.get("character_end_times_seconds", [])

    min_l = min(len(chars), len(starts), len(ends))
    chars = chars[:min_l]
    starts = starts[:min_l]
    ends = ends[:min_l]

    full_text = "".join(chars)
    return full_text, chars, [float(x) for x in starts], [float(x) for x in ends]


# -----------------------------
# NORMALIZATION / TOKENIZATION (STRICT, punctuation-insensitive)
# -----------------------------
def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def normalize_token(raw: str, mode: Optional[str] = None) -> str:
    """
    For comparisons only (NOT for alignment char indexing).
    Key points:
    - NFKC
    - Treat ZWNJ/ZWJ/WORD JOINER as boundaries (space), then remove other zero-width
    - casefold (better multilingual casing than lower)
    - Conservative Arabic/Hebrew normalization before casefold
    """
    s = _nfkc(raw)
    s = _zw_boundary_safe(s)
    s = ZERO_WIDTH_RE.sub("", s)
    m = mode or detect_mode(s)
    if m == "ARABIC_WORD":
        s = _arabic_normalize_for_compare(s)
    elif m == "HEBREW_WORD":
        s = _hebrew_normalize_for_compare(s)
    return s.casefold()

def tokenize_words_strict(text: str) -> List[str]:
    """
    Punctuation-insensitive strict tokenizer:
    - Tokens are Unicode alnum runs.
    - Numbers are kept.
    - Punctuation/whitespace are boundaries and ignored.
    - Category-based across scripts (L/N always; include M so matras/marks don't split)
    - Mode auto-detected:
        DEFAULT_WORD / ARABIC_WORD / HEBREW_WORD / INDIC_WORD: contiguous token-char runs
        CJK_CHAR: each CJK char becomes a token; ASCII alnum sequences remain grouped
        THAI_CHAR: per-character-ish Thai tokens (prevents one mega-token); ASCII grouped    
    """
    mode = detect_mode(text or "")
    t = normalize_token(text, mode=mode)

    def cat0(ch: str) -> str:
        c = unicodedata.category(ch)
        return c[0] if c else ""

    def is_word_char(ch: str) -> bool:
        # letters, numbers, marks (marks kept so vowel signs/niqqud/tashkeel stay attached)
        return cat0(ch) in ("L", "N", "M")

    def is_ascii_alnum(ch: str) -> bool:
        return ord(ch) < 128 and ch.isalnum()

    def is_cjk(ch: str) -> bool:
        return _ord_in_ranges(ord(ch), _CJK_RANGES)

    def is_thai(ch: str) -> bool:
        return _ord_in_ranges(ord(ch), _THAI_RANGES)

    out: List[str] = []
    n = len(t)
    i = 0

    if mode in ("DEFAULT_WORD", "ARABIC_WORD", "HEBREW_WORD", "INDIC_WORD"):
        while i < n:
            if not is_word_char(t[i]):
                i += 1
                continue
            j = i + 1
            while j < n and is_word_char(t[j]):
                j += 1
            out.append(t[i:j])
            i = j
        return out

    if mode == "CJK_CHAR":
        while i < n:
            ch = t[i]
            if is_ascii_alnum(ch):
                j = i + 1
                while j < n and is_ascii_alnum(t[j]):
                    j += 1
                out.append(t[i:j])
                i = j
                continue
            if is_cjk(ch):
                j = i + 1
                while j < n and cat0(t[j]) == "M":
                    j += 1
                out.append(t[i:j])
                i = j
                continue
            if is_word_char(ch):
                j = i + 1
                while j < n and is_word_char(t[j]) and (not is_cjk(t[j])) and (not is_ascii_alnum(t[j])):
                    j += 1
                out.append(t[i:j])
                i = j
                continue
            i += 1
        return out

    # THAI_CHAR
    while i < n:
        ch = t[i]
        if is_ascii_alnum(ch):
            j = i + 1
            while j < n and is_ascii_alnum(t[j]):
                j += 1
            out.append(t[i:j])
            i = j
            continue
        if is_thai(ch):
            # base Thai char + its marks (prevents mega-token)
            j = i + 1
            while j < n and is_thai(t[j]) and cat0(t[j]) == "M":
                j += 1
            out.append(t[i:j])
            i = j
            continue
        if is_word_char(ch):
            j = i + 1
            while j < n and is_word_char(t[j]) and (not is_thai(t[j])) and (not is_ascii_alnum(t[j])):
                j += 1
            out.append(t[i:j])
            i = j
            continue
        i += 1
    return out

def tokenize_alignment_with_indices(full_text_raw: str) -> List[Dict[str, Any]]:
    """
    CRITICAL: must use RAW full_text (no ZERO_WIDTH removal here),
    so start_char/end_char stay aligned with starts[]/ends[] indices.

    Tokens are runs of raw .isalnum() characters.
    Norm is computed separately for matching.

    Tokens mirror tokenize_words_strict(), BUT boundaries are computed on RAW text
    (no removal/transforms) so start_char/end_char remain valid indices into `ends[]`.    
    """
    text = full_text_raw or ""
    mode = detect_mode(text)

    def cat0(ch: str) -> str:
        c = unicodedata.category(ch)
        return c[0] if c else ""

    def is_word_char(ch: str) -> bool:
        return cat0(ch) in ("L", "N", "M")

    def is_ascii_alnum(ch: str) -> bool:
        return ord(ch) < 128 and ch.isalnum()

    def is_cjk(ch: str) -> bool:
        return _ord_in_ranges(ord(ch), _CJK_RANGES)

    def is_thai(ch: str) -> bool:
        return _ord_in_ranges(ord(ch), _THAI_RANGES)

    tokens: List[Dict[str, Any]] = []
    n = len(text)
    i = 0

    def emit(start: int, end: int):
        raw = text[start:end + 1]
        norm = normalize_token(raw, mode=mode)
        if norm:
            tokens.append({"norm": norm, "start_char": start, "end_char": end, "raw": raw})

    if mode in ("DEFAULT_WORD", "ARABIC_WORD", "HEBREW_WORD", "INDIC_WORD"):
        while i < n:
            if not is_word_char(text[i]):
                i += 1
                continue
            start = i
            i += 1
            while i < n and is_word_char(text[i]):
                i += 1
            emit(start, i - 1)
        return tokens

    if mode == "CJK_CHAR":
        while i < n:
            ch = text[i]
            if is_ascii_alnum(ch):
                start = i
                i += 1
                while i < n and is_ascii_alnum(text[i]):
                    i += 1
                emit(start, i - 1)
                continue
            if is_cjk(ch):
                start = i
                i += 1
                while i < n and cat0(text[i]) == "M":
                    i += 1
                emit(start, i - 1)
                continue
            if is_word_char(ch):
                start = i
                i += 1
                while i < n and is_word_char(text[i]) and (not is_cjk(text[i])) and (not is_ascii_alnum(text[i])):
                    i += 1
                emit(start, i - 1)
                continue
            i += 1
        return tokens

    # THAI_CHAR
    while i < n:
        ch = text[i]
        if is_ascii_alnum(ch):
            start = i
            i += 1
            while i < n and is_ascii_alnum(text[i]):
                i += 1
            emit(start, i - 1)
            continue
        if is_thai(ch):
            start = i
            i += 1
            while i < n and is_thai(text[i]) and cat0(text[i]) == "M":
                i += 1
            emit(start, i - 1)
            continue
        if is_word_char(ch):
            start = i
            i += 1
            while i < n and is_word_char(text[i]) and (not is_thai(text[i])) and (not is_ascii_alnum(text[i])):
                i += 1
            emit(start, i - 1)
            continue
        i += 1
    return tokens

def clean_chunk_text_for_compare(chunk_raw: str) -> str:
    """
    Mirror numbering cleanup used in load_script_txt(), but applied per chunk line,
    so numbered chunks don't cause false mismatches.
    """
    raw = _zw_boundary_safe(chunk_raw or "")
    raw = ZERO_WIDTH_RE.sub("", raw)
    l = raw.strip()
    if not l:
        return ""
    if ONLY_NUMBER_LINE_RE.match(l):
        return ""
    l = LEADING_NUMBER_RE.sub("", l)
    return l.strip()


# -----------------------------
# STRICT VALIDATION: script vs chunks
# -----------------------------
def assert_chunks_equal_script(chunks: List[str], script_text: str) -> Dict[str, Any]:
    script_tokens = tokenize_words_strict(script_text)

    # Build chunk tokens + map global token index -> chunk id
    chunk_tokens: List[str] = []
    token_to_chunk_id: List[int] = []

    for idx, c in enumerate(chunks, 1):
        toks = tokenize_words_strict(clean_chunk_text_for_compare(c))
        chunk_tokens.extend(toks)
        token_to_chunk_id.extend([idx] * len(toks))

    if script_tokens == chunk_tokens:
        return {
            "ok": True,
            "script_tokens": len(script_tokens),
            "chunks_tokens": len(chunk_tokens),
        }

    # Find first mismatch index
    m = min(len(script_tokens), len(chunk_tokens))
    first_diff = None
    for k in range(m):
        if script_tokens[k] != chunk_tokens[k]:
            first_diff = k
            break
    if first_diff is None:
        first_diff = m  # one is a prefix of the other

    def context(seq: List[str], idx: int, win: int = 18) -> List[str]:
        a = max(0, idx - win)
        b = min(len(seq), idx + win)
        return seq[a:b]

    # Determine which chunk the mismatch lands in
    chunk_id = None
    if first_diff < len(token_to_chunk_id):
        chunk_id = token_to_chunk_id[first_diff]

    # Build extra/missing previews around mismatch
    script_next = script_tokens[first_diff] if first_diff < len(script_tokens) else None
    chunks_next = chunk_tokens[first_diff] if first_diff < len(chunk_tokens) else None

    # Collect a more explicit "first divergent run"
    # If tokens differ, show up to 30 tokens from each side starting at mismatch
    script_run = script_tokens[first_diff:first_diff + 30]
    chunks_run = chunk_tokens[first_diff:first_diff + 30]

    # If one side ended, show the remaining tail
    script_tail = script_tokens[first_diff:first_diff + 60]
    chunks_tail = chunk_tokens[first_diff:first_diff + 60]

    details = {
        "ok": False,
        "script_tokens": len(script_tokens),
        "chunks_tokens": len(chunk_tokens),
        "first_diff_index": first_diff,
        "chunk_id_at_diff": chunk_id,
        "script_next_token": script_next,
        "chunks_next_token": chunks_next,
        "script_context": context(script_tokens, first_diff),
        "chunks_context": context(chunk_tokens, first_diff),
        "script_divergent_run": script_run,
        "chunks_divergent_run": chunks_run,
        "script_tail_from_diff": script_tail,
        "chunks_tail_from_diff": chunks_tail,
    }

    # Add the actual chunk text preview to make it easy to locate
    if chunk_id is not None and 1 <= chunk_id <= len(chunks):
        preview = chunks[chunk_id - 1].replace("\n", " ").strip()
        if len(preview) > 350:
            preview = preview[:350] + "..."
        details["chunk_text_preview"] = preview

    # Print a complete human-readable error in terminal
    print("\n" + "❌ STRICT FAIL: chunks.txt words are NOT identical to script.txt words (punctuation-insensitive).")
    print(f"   - script_tokens = {len(script_tokens)}")
    print(f"   - chunks_tokens = {len(chunk_tokens)}")
    print(f"   - first_diff_index = {first_diff}")
    if chunk_id is not None:
        print(f"   - mismatch appears in chunk_id = {chunk_id}")
    print(f"   - script_next_token = {script_next}")
    print(f"   - chunks_next_token = {chunks_next}\n")

    print("   Script context around diff:")
    print("   " + " ".join(details["script_context"]) + "\n")

    print("   Chunks context around diff:")
    print("   " + " ".join(details["chunks_context"]) + "\n")

    print("   Script divergent run (from diff):")
    print("   " + " ".join(details["script_divergent_run"]) + "\n")

    print("   Chunks divergent run (from diff):")
    print("   " + " ".join(details["chunks_divergent_run"]) + "\n")

    if "chunk_text_preview" in details:
        print("   Chunk text preview at mismatch:")
        print(f"   {details['chunk_text_preview']}\n")

    # Raise with JSON details for report file
    raise StrictValidationError(json.dumps(details, ensure_ascii=False, indent=2))


# -----------------------------
# MATCHING (STRICT, exact contiguous tokens only)
# -----------------------------
def build_positions_index(audio_tokens: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    pos: Dict[str, List[int]] = {}
    for i, t in enumerate(audio_tokens):
        w = t["norm"]
        if w:
            pos.setdefault(w, []).append(i)
    return pos


def _lookahead_for_len(clen: int) -> int:
    # Prevent false matches far ahead (the main cause of “wrong” results).
    if clen <= 8:
        return 350
    if clen <= 15:
        return 800
    return 2200


def find_exact_subsequence(
    audio_norms: List[str],
    pos_index: Dict[str, List[int]],
    chunk_words: List[str],
    start_idx: int,
    max_candidates: int = 5000,
) -> Tuple[Optional[int], Optional[int]]:
    if not chunk_words:
        return None, None

    first = chunk_words[0]
    if first not in pos_index:
        return None, None

    lookahead = _lookahead_for_len(len(chunk_words))
    limit = min(len(audio_norms) - 1, start_idx + lookahead)

    cand_list = pos_index[first]
    j = bisect.bisect_left(cand_list, start_idx)

    clen = len(chunk_words)
    alen = len(audio_norms)
    tried = 0

    while j < len(cand_list) and tried < max_candidates:
        s = cand_list[j]
        j += 1
        tried += 1

        if s > limit:
            break
        if s + clen > alen:
            break

        if audio_norms[s:s + clen] == chunk_words:
            return s, s + clen - 1

    return None, None


def snap_end_to_word_boundary(text: str, end_char_idx: int) -> int:
    """
    Snap end_char_idx forward ONLY within the same word and any directly-attached
    punctuation (no whitespace consumed).
    """
    n = len(text)
    if n <= 0:
        return 0

    j = max(0, min(int(end_char_idx), n - 1))

    # Finish the word if we are in it
    def cat0(ch: str) -> str:
        c = unicodedata.category(ch)
        return c[0] if c else ""

    def is_word_char(ch: str) -> bool:
        # include marks so Arabic/Hebrew/Indic combining marks are treated as part of word
        return cat0(ch) in ("L", "N", "M")

    def is_joiner(ch: str) -> bool:
        return ch in ("\u200c", "\u200d", "\u2060")

    # Finish the word if we are in it
    while j + 1 < n and is_word_char(text[j]) and is_word_char(text[j + 1]):
        # do not cross joiners
        if is_joiner(text[j + 1]):
            break
        j += 1
        
    # Include punctuation directly attached (no spaces)
    while j + 1 < n and (not is_word_char(text[j + 1])) and (not text[j + 1].isspace()) and (not is_joiner(text[j + 1])):
        j += 1

    return j


def seconds_to_frames(sec: float, fps: float) -> int:
    return int(round(sec * fps))


# -----------------------------
# MAIN TIMESTAMP MERGE (CONTIGUOUS STARTS)
# -----------------------------
def merge_chunks_with_alignment(
    chunks: List[str],
    full_text: str,
    ends: List[float],
    fps: float
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    audio_tokens = tokenize_alignment_with_indices(full_text)
    audio_norms = [t["norm"] for t in audio_tokens]
    pos_index = build_positions_index(audio_tokens)

    segments: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []  # kept for compatibility; strict mode should keep this empty

    current_token_idx = 0
    prev_end_time = 0.0
    prev_end_frame = 0

    for i, chunk_text in enumerate(chunks, 1):
        chunk_words = tokenize_words_strict(clean_chunk_text_for_compare(chunk_text))

        # CONTIGUOUS start (your requirement)
        start_time = prev_end_time
        start_frame = prev_end_frame

        if not chunk_words:
            # empty chunk: zero duration (rare)
            final_end_time = start_time
            end_frame = start_frame
            dur_sec = 0.0
            dur_frames = 0
            segments.append({
                "id": i,
                "sentenceText": chunk_text,
                "start_time_seconds": round(start_time, 3),
                "end_time_seconds": round(final_end_time, 3),
                "duration_seconds": round(dur_sec, 3),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "fps": fps
            })
            prev_end_time = final_end_time
            prev_end_frame = end_frame
            continue

        found_start, found_end = find_exact_subsequence(
            audio_norms=audio_norms,
            pos_index=pos_index,
            chunk_words=chunk_words,
            start_idx=current_token_idx
        )

        if found_start is None or found_end is None:
            missing.append({"id": i, "sentenceText": chunk_text})
            final_end_time = start_time
            end_frame = start_frame
            dur_sec = 0.0
            dur_frames = 0

        else:
            # STRICT: verify exact match at that position (redundant safety)
            if audio_norms[found_start:found_end + 1] != chunk_words:
                missing.append({"id": i, "sentenceText": chunk_text})
                final_end_time = start_time
                end_frame = start_frame
                dur_sec = 0.0
                dur_frames = 0
            else:
                last_token = audio_tokens[found_end]
                e_char_idx = int(last_token["end_char"])
                if e_char_idx >= len(ends):
                    e_char_idx = len(ends) - 1
                if e_char_idx < 0:
                    e_char_idx = 0

                # snap end cleanly (no spaces consumed)
                e_char_idx = snap_end_to_word_boundary(full_text, e_char_idx)
                if e_char_idx >= len(ends):
                    e_char_idx = len(ends) - 1

                raw_end = float(ends[e_char_idx])

                # contiguous + monotonic
                final_end_time = max(raw_end, start_time)

                end_frame = seconds_to_frames(final_end_time, fps)
                dur_sec = final_end_time - start_time
                dur_frames = end_frame - start_frame

                current_token_idx = found_end + 1

        segments.append({
            "id": i,
            "sentenceText": chunk_text,
            "start_time_seconds": round(start_time, 3),
            "end_time_seconds": round(final_end_time, 3),
            "duration_seconds": round(dur_sec, 3),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "fps": fps
        })

        prev_end_time = final_end_time
        prev_end_frame = end_frame

    # STRICT: alignment must be fully consumed by chunks
    remaining = audio_norms[current_token_idx:]
    extra_preview = remaining[:40]
    coverage_report = {
        "alignment_tokens_total": len(audio_norms),
        "consumed_tokens": current_token_idx,
        "remaining_tokens": len(remaining),
        "remaining_preview": extra_preview,
    }

    return segments, missing, partial, coverage_report


def _print_missing(missing: List[Dict[str, Any]]) -> None:
    print("❌ ERROR: One or more chunks are missing in the alignment.")
    print(f"❌ missing_chunks_count = {len(missing)}")
    for m in missing:
        cid = m.get("id")
        txt = str(m.get("sentenceText", "") or "").replace("\n", " ").strip()
        if len(txt) > 180:
            txt = txt[:180] + "..."
        print(f"   - Chunk {cid}: {txt}")


def main():
    try:
        print("--- Processing ---")

        chunks = load_chunks_txt(CHUNKS_TXT)
        script_text = load_script_txt(SCRIPT_TXT)
        full_text, _chars, _starts, ends = load_alignment(ALIGNMENT_JSON)

        print(f"Chunks: {len(chunks)}")
        print(f"Audio chars: {len(full_text)}")

        # STRICT: chunks must match script (punctuation-insensitive)
        script_check = assert_chunks_equal_script(chunks, script_text)

        segments, missing, partial, coverage_report = merge_chunks_with_alignment(
            chunks=chunks,
            full_text=full_text,
            ends=ends,
            fps=FPS
        )

        report: Dict[str, Any] = {
            "paths": {
                "chunks": str(CHUNKS_TXT),
                "script": str(SCRIPT_TXT),
                "alignment": str(ALIGNMENT_JSON),
                "output": str(OUTPUT_JSON),
            },
            "fps": FPS,
            "script_check": script_check,
            "coverage_report": coverage_report,
            "missing_chunks_count": len(missing),
        }

        if missing:
            _print_missing(missing)
            report["missing_chunks"] = missing
            # hard fail
            raise MissingChunksError(missing)

        # STRICT: no remaining alignment tokens
        if coverage_report["remaining_tokens"] != 0:
            raise StrictValidationError(
                "STRICT FAIL: alignment contains extra words not covered by chunks.\n"
                + json.dumps(coverage_report, ensure_ascii=False, indent=2)
            )

        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")

        try:
            REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
            REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            if DEBUG:
                print(f"⚠️  Could not write report: {e}")

        print(f"✅ Success: {len(segments)} segments -> {OUTPUT_JSON}")
        print("✅ STRICT checks passed: script==chunks and chunks==alignment and full coverage.")

    except MissingChunksError:
        raise SystemExit(2)
    except StrictValidationError as e:
        print(f"❌ {e}")
        raise SystemExit(3)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
