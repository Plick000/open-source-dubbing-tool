#!/usr/bin/env python3
import json
import re
import os
import difflib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ==========================================================
# CONFIG
# ==========================================================

@dataclass
class Cfg:
    PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

    # Allow override (keeps defaults)
    CHUNKS_TXT: Path = Path(os.environ.get("EN_CHUNKS_TXT", str(PROJECT_ROOT / "inputs" / "en_chunks" / "chunks.txt")))
    CONFIG_JSON: Path = PROJECT_ROOT / "inputs" / "config" / "config.json"

    MERGED_CHUNKS_TXT: Path = PROJECT_ROOT / "output" / "merged_chunks" / "chunks_merged.txt"
    MERGE_MAP_JSON: Path = PROJECT_ROOT / "output" / "merged_chunks" / "chunks_merge_map.json"
    OUTPUT_MATCHED_JSON: Path = PROJECT_ROOT / "output" / "JSON" / "A10__matched_segments__.json"

    # Strictness
    MIN_TITLE_SCORE: float = 0.70
    MIN_LOWER_SCORE: float = 0.70

    # Embeddings
    USE_EMBEDDINGS: bool = True
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Merge strictness for multiline patterns
    PART_MATCH_MIN_TITLE: float = 0.80
    MERGED_MATCH_MIN_TITLE: float = 0.88

    PART_MATCH_MIN_LOWER: float = 0.72
    MERGED_MATCH_MIN_LOWER: float = 0.82

    # ✅ NEW: LowerThird span (split across consecutive chunks)
    LOWERTHIRD_SPAN_MAX: int = 2  # set to 3 if needed

CFG = Cfg()


# ==========================================================
# FAST CONTENT ROOT (robust for WSL + mapped drives)
# ==========================================================

def detect_fast_content_root() -> Path:
    """
    Picks the first existing root from common candidates.
    You can override by setting env var FAST_CONTENT_ROOT.
    """
    if os.name == "nt":
        return Path(r"Z:\Automated Dubbings\Projects")

    env = os.environ.get("FAST_CONTENT_ROOT", "").strip()
    candidates: List[Path] = []
    if env:
        candidates.append(Path(env))

    candidates += [
        Path("/mnt/z/Automated Dubbings/Projects"),
        Path("/mnt/z/D/Automated Dubbings/Projects"),
        Path("/mnt/z/Automated Dubbings"),
        Path("/mnt/z/D/Automated Dubbings"),
    ]

    for p in candidates:
        try:
            if p.exists():
                return p
        except OSError:
            continue

    return candidates[0] if candidates else Path("/mnt/z/Automated Dubbings/Projects")

FAST_CONTENT_ROOT = detect_fast_content_root()


# ==========================================================
# HELPERS
# ==========================================================

_NUM_PREFIX_RE = re.compile(r"^\s*(\d+)\.\s*(.*)\s*$")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_VIDEO_PREFIX_RE = re.compile(r"^\s*video\s*\d+\s*[-–—]\s*", re.IGNORECASE)
_ILLEGAL_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

# Titles are lines like: [INTRO], [CHAPTER 2], etc.
# They must NOT be searched/matched for LowerThird / UltraText / UltraExtraText.
_BRACKET_TITLE_RE = re.compile(r"^\s*\[[^\[\]]+\]\s*$")

def is_bracket_title_line(s: str) -> bool:
    return bool(_BRACKET_TITLE_RE.match((s or "").strip()))

# --- Number normalization (digits vs number-words) ---
_DIGIT_NUM_RE = re.compile(r"\b\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?\b")
_NUM_WORDS = {
    "zero","one","two","three","four","five","six","seven","eight","nine","ten",
    "eleven","twelve","thirteen","fourteen","fifteen","sixteen","seventeen","eighteen","nineteen",
    "twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety",
    "hundred","thousand","million","billion","trillion",
    "first","second","third","fourth","fifth","sixth","seventh","eighth","ninth","tenth",
}
_NUM_WORD_RE = re.compile(r"\b(" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE)

STOPWORDS = {
    "the","a","an","and","or","but","of","to","in","on","at","for","with","from",
    "by","as","is","are","was","were","be","been","being","it","this","that",
    "these","those","you","your","we","they","their","our","i","he","she","his","her"
}


_COMPACT_RE = re.compile(r"[^a-z0-9]+", re.IGNORECASE)

def compact_alnum(s: str) -> str:
    """
    Lowercase + remove everything except a-z0-9.
    This makes matching robust to missing spaces like 'equipmentand' or 'ringscorched'.
    """
    s = (s or "").lower()
    return _COMPACT_RE.sub("", s)


def _normalize_numbers(s: str) -> str:
    s = s or ""
    s = _DIGIT_NUM_RE.sub(" <num> ", s)
    s = _NUM_WORD_RE.sub(" <num> ", s)
    return s

def clean_video_name(name: str) -> str:
    name = (name or "").strip()
    name = _VIDEO_PREFIX_RE.sub("", name).strip()
    name = _ILLEGAL_PATH_CHARS_RE.sub("", name)
    name = name.rstrip(". ")
    name = _WS_RE.sub(" ", name).strip()
    return name

def load_video_name_from_config(config_path: Path) -> str:
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    for key in ("VideoName", "videoName", "video_name", "videoname", "name", "title"):
        v = cfg.get(key)
        if isinstance(v, str) and v.strip():
            cleaned = clean_video_name(v)
            if cleaned and not re.match(r"^\s*video\s*\d+\s*$", cleaned, flags=re.IGNORECASE):
                return cleaned

    raise KeyError("VideoName not found in config.json (or only generic 'Video 27').")

def stat_with_retries(path: Path, tries: int = 6, delay: float = 0.35):
    last_err: Optional[Exception] = None
    for _ in range(tries):
        try:
            return path.stat()
        except FileNotFoundError:
            raise
        except Exception as e:
            last_err = e
            time.sleep(delay)
    if last_err:
        raise last_err
    raise OSError("Unknown error during stat_with_retries")

def ensure_readable_file(path: Path, label: str):
    try:
        stat_with_retries(path)
    except FileNotFoundError:
        raise SystemExit(f"❌ Missing {label}: {path}")
    except PermissionError as e:
        raise SystemExit(f"❌ Permission error for {label}: {path}\n   {e}")
    except OSError as e:
        raise SystemExit(f"❌ OS/Network error accessing {label}: {path}\n   {e}")

    if not path.is_file():
        raise SystemExit(f"❌ Not a file ({label}): {path}")

def resolve_titles_json_path() -> Path:
    video_name = load_video_name_from_config(CFG.CONFIG_JSON)

    candidate = FAST_CONTENT_ROOT / video_name / "English" / "JSON" / "__titles_and_lowerthirds__.json"
    if candidate.exists():
        return candidate

    try:
        if FAST_CONTENT_ROOT.exists():
            dirs = [d for d in FAST_CONTENT_ROOT.iterdir() if d.is_dir()]
            names = [d.name for d in dirs]
            best = difflib.get_close_matches(video_name, names, n=1, cutoff=0.80)
            if best:
                alt = FAST_CONTENT_ROOT / best[0] / "English" / "JSON" / "__titles_and_lowerthirds__.json"
                if alt.exists():
                    return alt
    except Exception:
        pass

    return candidate

def strip_number_prefix(line: str) -> Tuple[Optional[int], str]:
    m = _NUM_PREFIX_RE.match(line or "")
    if not m:
        return None, (line or "").strip()
    return int(m.group(1)), (m.group(2) or "").strip()

def normalize_for_compare(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # number normalization before punctuation stripping
    s = _normalize_numbers(s)

    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s

def seq_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_for_compare(a), normalize_for_compare(b)).ratio()

def jaccard(a: str, b: str) -> float:
    A = set(normalize_for_compare(a).split())
    B = set(normalize_for_compare(b).split())
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return len(A & B) / float(len(A | B))

def content_tokens(s: str) -> List[str]:
    toks = normalize_for_compare(s).split()
    toks = [t for t in toks if t and t not in STOPWORDS]
    return toks

def join_span_text(texts: List[str]) -> str:
    return " ".join([t.strip() for t in texts if isinstance(t, str) and t.strip()])


# ==========================================================
# OPTIONAL EMBEDDINGS
# ==========================================================

def try_load_embedder(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None

    model = SentenceTransformer(model_name)

    def encode(texts: List[str]):
        return model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)

    def cosine(u, v):
        return float(np.dot(u, v))  # normalized => dot == cosine

    return encode, cosine


# ==========================================================
# LOADERS
# ==========================================================

def load_chunks_txt(path: Path) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()

    tmp = []
    numbered_count = 0

    for i, line in enumerate(lines):
        raw = (line or "").rstrip("\n")
        num, text = strip_number_prefix(raw)
        text = text.strip()

        if not text:
            continue

        if num is not None:
            numbered_count += 1

        tmp.append({
            "pos": i,
            "num": num,
            "text": text,
            "raw": raw
        })

    # Only trust explicit numbering if MOST lines are actually numbered.
    # This prevents normal content like "1945. As Soviet forces..." from
    # being mistaken as chunk numbering.
    total_chunks = len(tmp)
    is_reliably_numbered = (
        total_chunks > 0 and
        (numbered_count / total_chunks) >= 0.9
    )

    if not is_reliably_numbered:
        for k, c in enumerate(tmp, start=1):
            c["num"] = k

    return tmp

def load_titles_items(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("The Titles JSON must be a JSON array (list).")
    items = []
    for it in data:
        if isinstance(it, dict) and isinstance(it.get("text"), str) and it["text"].strip():
            items.append(it)
    return items


# ==========================================================
# MERGE LOGIC (multiline Titles + multiline LowerThirds)
# ==========================================================

def _split_multiline_text(t: str) -> List[str]:
    if not isinstance(t, str):
        return []
    parts = [p.strip() for p in t.replace("\r\n", "\n").replace("\r", "\n").split("\n") if p.strip()]
    return parts

def build_multiline_patterns(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    patterns: List[Dict[str, Any]] = []
    for it in items:
        t = it.get("text", "")
        it_type = str(it.get("type", "") or "")
        parts = _split_multiline_text(t)
        if len(parts) < 2:
            continue

        if it_type == "Title":
            patterns.append({
                "type": "Title",
                "parts": parts,
                "part_min": CFG.PART_MATCH_MIN_TITLE,
                "merged_min": CFG.MERGED_MATCH_MIN_TITLE,
            })
        else:
            patterns.append({
                "type": it_type or "LowerThird",
                "parts": parts,
                "part_min": CFG.PART_MATCH_MIN_LOWER,
                "merged_min": CFG.MERGED_MATCH_MIN_LOWER,
            })
    return patterns

def find_merge_spans(
    chunks: List[Dict[str, Any]],
    patterns: List[Dict[str, Any]]
) -> List[Tuple[int, int, str, List[str], str]]:
    merges: List[Tuple[int, int, str, List[str], str]] = []
    n = len(chunks)

    patterns_sorted = sorted(patterns, key=lambda p: len(p["parts"]), reverse=True)

    for pat in patterns_sorted:
        parts = pat["parts"]
        k = len(parts)
        part_min = float(pat["part_min"])
        merged_min = float(pat["merged_min"])
        ptype = str(pat.get("type", ""))

        for i in range(0, n - k + 1):
            ok_parts = True
            for j in range(k):
                s = seq_ratio(parts[j], chunks[i + j]["text"])
                if s < part_min:
                    ok_parts = False
                    break
            if not ok_parts:
                continue

            merged_candidate = "\n".join([chunks[i + j]["text"].strip() for j in range(k)])
            full_text = "\n".join(parts)
            full_score = seq_ratio(full_text, merged_candidate)

            if full_score >= merged_min:
                merges.append((i, i + k, merged_candidate, [chunks[i + j]["text"] for j in range(k)], ptype))

    merges.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    chosen: List[Tuple[int, int, str, List[str], str]] = []
    used = set()
    for s, e, mtxt, src, ptype in merges:
        if any(idx in used for idx in range(s, e)):
            continue
        for idx in range(s, e):
            used.add(idx)
        chosen.append((s, e, mtxt, src, ptype))
    chosen.sort(key=lambda x: x[0])
    return chosen

def apply_merges(
    chunks: List[Dict[str, Any]],
    merge_spans: List[Tuple[int, int, str, List[str], str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    merged: List[Dict[str, Any]] = []
    merge_map: List[Dict[str, Any]] = []

    i = 0
    span_by_start = {s: (s, e, mtxt, src, ptype) for (s, e, mtxt, src, ptype) in merge_spans}
    new_index = 0

    while i < len(chunks):
        if i in span_by_start:
            s, e, mtxt, _src, ptype = span_by_start[i]
            source = chunks[s:e]
            nums = [c["num"] for c in source if c["num"] is not None]

            merged_obj = {
                "merged_index": new_index,
                "text": mtxt,
                "source_chunk_numbers": nums,
                "source_chunk_pos": [c["pos"] for c in source],
            }
            merged.append(merged_obj)

            merge_map.append({
                "merged_index": new_index,
                "merged_text": mtxt,
                "pattern_type": ptype,
                "from_chunks": [
                    {"pos": c["pos"], "num": c["num"], "text": c["text"], "raw": c["raw"]}
                    for c in source
                ],
            })

            new_index += 1
            i = e
        else:
            c = chunks[i]
            merged_obj = {
                "merged_index": new_index,
                "text": c["text"],
                "source_chunk_numbers": [c["num"]] if c["num"] is not None else [],
                "source_chunk_pos": [c["pos"]],
            }
            merged.append(merged_obj)

            merge_map.append({
                "merged_index": new_index,
                "merged_text": c["text"],
                "pattern_type": "single",
                "from_chunks": [{"pos": c["pos"], "num": c["num"], "text": c["text"], "raw": c["raw"]}],
            })

            new_index += 1
            i += 1

    return merged, merge_map

def save_merged_chunks_txt(merged_chunks: List[Dict[str, Any]], out_path: Path):
    lines = []
    for i, c in enumerate(merged_chunks, start=1):
        txt = c["text"].replace("\n", "\\n")
        lines.append(f"{i}. {txt}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ==========================================================
# MATCHING
# ==========================================================

def semantic_lexical_score(q: str, ch: str, q_emb=None, ch_emb=None, cosine_fn=None) -> Tuple[float, Dict[str, float]]:
    s_seq = seq_ratio(q, ch)
    s_jac = jaccard(q, ch)

    if q_emb is not None and ch_emb is not None and cosine_fn is not None:
        s_sem = cosine_fn(q_emb, ch_emb)
        s_sem = max(0.0, min(1.0, float(s_sem)))
        score = 0.70 * s_sem + 0.20 * s_seq + 0.10 * s_jac
        return score, {"semantic": s_sem, "seq": s_seq, "jaccard": s_jac}

    score = 0.65 * s_seq + 0.35 * s_jac
    return score, {"seq": s_seq, "jaccard": s_jac}

def lowerthird_contains_score(q: str, ch: str) -> Tuple[float, Dict[str, float]]:
    # Token-based contains (current behavior)
    q_tokens = content_tokens(q)
    if not q_tokens:
        return 0.0, {"contains": 0.0, "contains_compact": 0.0}

    ch_tokens = set(normalize_for_compare(ch).split())
    token_ok = all(t in ch_tokens for t in q_tokens)
    if token_ok:
        return 1.0, {"contains": 1.0, "contains_compact": 0.0}

    # ✅ NEW: compact contains (space-insensitive)
    # Example: "equipmentand" will still match "equipmentand"
    q_comp = compact_alnum(q)
    ch_comp = compact_alnum(ch)

    if q_comp and ch_comp and (q_comp in ch_comp):
        return 1.0, {"contains": 0.0, "contains_compact": 1.0}

    return 0.0, {"contains": 0.0, "contains_compact": 0.0}

def match_item(
    item: Dict[str, Any],
    merged_chunks: List[Dict[str, Any]],
    merged_texts: List[str],
    chunk_embeds=None,
    encode_fn=None,
    cosine_fn=None,
    start_from: int = 0,
    fallback_to_full_search: bool = False,
) -> Dict[str, Any]:
    q = item["text"]
    item_type = str(item.get("type", "") or "")

    start_from = max(0, min(int(start_from or 0), len(merged_texts)))

    # If cursor starts on a [TITLE] chunk, skip it for LowerThird/UltraText types
    if item_type in {"LowerThird", "UltraText", "UltraExtraText"}:
        while start_from < len(merged_texts) and is_bracket_title_line(merged_texts[start_from]):
            start_from += 1
            
    q_emb = None
    if chunk_embeds is not None and encode_fn is not None:
        q_emb = encode_fn([normalize_for_compare(q)])[0]

    def _search_range(r_start: int) -> Tuple[int, float, Dict[str, float], str]:
        best_idx_local = -1
        best_score_local = -1.0
        best_comps_local: Dict[str, float] = {}
        best_method_local = "semantic"
        
        for i in range(r_start, len(merged_texts)):
            ch_text = merged_texts[i]
        
            # Do NOT search/match [TITLE] chunks for LowerThird/UltraText types
            if item_type in {"LowerThird", "UltraText", "UltraExtraText"} and is_bracket_title_line(ch_text):
                continue
            
            if item_type in {"LowerThird", "UltraText", "UltraExtraText"}:
                c_score, c_comps = lowerthird_contains_score(q, ch_text)
                if c_score > best_score_local:
                    best_score_local = c_score
                    best_idx_local = i
                    best_comps_local = dict(c_comps)
                    best_method_local = "contains"
        
            ch_emb = chunk_embeds[i] if chunk_embeds is not None else None
            s_score, s_comps = semantic_lexical_score(q, ch_text, q_emb=q_emb, ch_emb=ch_emb, cosine_fn=cosine_fn)
            
            if s_score > best_score_local or (
                abs(s_score - best_score_local) < 1e-9
                and (best_idx_local < 0 or len(ch_text) < len(merged_texts[best_idx_local]))
            ):
                best_score_local = s_score
                best_idx_local = i
                best_comps_local = dict(s_comps)
                best_method_local = "semantic"

        return best_idx_local, best_score_local, best_comps_local, best_method_local

    def _is_ok(bi: int, bs: float, comps: Dict[str, float], method: str) -> bool:
        if item_type == "Title":
            return (bi >= 0) and (bs >= CFG.MIN_TITLE_SCORE)
    
        # Hard rule: never accept a [TITLE] chunk for LowerThird/UltraText types
        if item_type in {"LowerThird", "UltraText", "UltraExtraText"} and bi >= 0:
            if is_bracket_title_line(merged_texts[bi]):
                return False
    
        if bi >= 0:
            contains_score, _ = lowerthird_contains_score(q, merged_texts[bi])
            if contains_score == 1.0:
                return True
    
        return (bi >= 0) and (bs >= CFG.MIN_LOWER_SCORE)

    best_idx, best_score, best_comps, best_method = _search_range(start_from)
    ok = _is_ok(best_idx, best_score, best_comps, best_method)

    # Optional fallback full search
    if (not ok) and fallback_to_full_search and start_from > 0:
        bi2, bs2, bc2, bm2 = _search_range(0)
        if _is_ok(bi2, bs2, bc2, bm2):
            best_idx, best_score, best_comps, best_method = bi2, bs2, bc2, bm2
            ok = True

    # ✅ NEW: LowerThird can be split across multiple consecutive chunks (span match)
    span_len_used = 1
    if item_type in {"LowerThird", "UltraText", "UltraExtraText"} and not ok:
        best_span: Optional[Tuple[int, int, float, Dict[str, float], str]] = None  # (start, span_len, score, comps, method)

        for span_len in range(2, int(CFG.LOWERTHIRD_SPAN_MAX) + 1):
            for i in range(start_from, len(merged_texts) - span_len + 1):
        
                # If any chunk inside this span is a [TITLE], skip this span
                if any(is_bracket_title_line(t) for t in merged_texts[i:i + span_len]):
                    continue
        
                span_text = join_span_text(merged_texts[i:i + span_len])
        
                c_score, c_comps = lowerthird_contains_score(q, span_text)

                if c_score == 1.0:
                    best_span = (i, span_len, 1.0, {"contains": 1.0, **c_comps}, "contains_span")
                    break

                # Span semantic: no span embedding => lexical blend (still uses number normalization)
                s_score, s_comps = semantic_lexical_score(q, span_text, q_emb=q_emb, ch_emb=None, cosine_fn=None)
                if best_span is None or s_score > best_span[2]:
                    best_span = (i, span_len, s_score, dict(s_comps), "semantic_span")

            if best_span and best_span[2] == 1.0:
                break

        if best_span:
            s_i, s_len, s_score, s_comps, s_method = best_span
            if s_score == 1.0 or s_score >= CFG.MIN_LOWER_SCORE:
                best_idx, best_score, best_comps, best_method = s_i, s_score, s_comps, s_method
                ok = True
                span_len_used = s_len

    out_item = dict(item)

    # Build match output
    match_obj: Dict[str, Any] = {
        "matched": bool(ok),
        "method": best_method,
        "best_merged_chunk_index": int(best_idx) if isinstance(best_idx, int) else -1,
        "best_chunk_text": None,
        "score": float(best_score) if isinstance(best_score, (int, float)) else 0.0,
        "score_components": best_comps if isinstance(best_comps, dict) else {},
        "source_chunk_numbers": None,
        "source_chunk_pos": None,
    }

    if ok and isinstance(best_idx, int) and best_idx >= 0:
        if span_len_used <= 1:
            match_obj["best_chunk_text"] = merged_texts[best_idx]
            match_obj["source_chunk_numbers"] = merged_chunks[best_idx]["source_chunk_numbers"]
            match_obj["source_chunk_pos"] = merged_chunks[best_idx]["source_chunk_pos"]
        else:
            span_text = join_span_text(merged_texts[best_idx:best_idx + span_len_used])
            span_nums: List[int] = []
            span_pos: List[int] = []

            for k in range(best_idx, best_idx + span_len_used):
                span_nums += merged_chunks[k]["source_chunk_numbers"]
                span_pos += merged_chunks[k]["source_chunk_pos"]

            # de-dup while preserving order
            seen = set()
            span_nums = [x for x in span_nums if isinstance(x, int) and (x not in seen and not seen.add(x))]
            seen = set()
            span_pos = [x for x in span_pos if isinstance(x, int) and (x not in seen and not seen.add(x))]

            match_obj["best_chunk_text"] = span_text
            match_obj["source_chunk_numbers"] = span_nums
            match_obj["source_chunk_pos"] = span_pos

    out_item["match"] = match_obj
    return out_item


# ==========================================================
# MAIN
# ==========================================================

def main():
    CFG.MERGED_CHUNKS_TXT.parent.mkdir(parents=True, exist_ok=True)
    CFG.MERGE_MAP_JSON.parent.mkdir(parents=True, exist_ok=True)
    CFG.OUTPUT_MATCHED_JSON.parent.mkdir(parents=True, exist_ok=True)

    chunks_path = Path(CFG.CHUNKS_TXT)
    titles_path = resolve_titles_json_path()

    ensure_readable_file(chunks_path, "English chunks.txt")
    ensure_readable_file(titles_path, "Titles JSON (__titles_and_lowerthirds__.json)")

    chunks = load_chunks_txt(chunks_path)
    items = load_titles_items(titles_path)

    patterns = build_multiline_patterns(items)
    merge_spans = find_merge_spans(chunks, patterns)
    merged_chunks, merge_map = apply_merges(chunks, merge_spans)

    save_merged_chunks_txt(merged_chunks, CFG.MERGED_CHUNKS_TXT)
    CFG.MERGE_MAP_JSON.write_text(json.dumps(merge_map, ensure_ascii=False, indent=2), encoding="utf-8")

    merged_texts = [c["text"] for c in merged_chunks]

    encode_fn = None
    cosine_fn = None
    chunk_embeds = None

    if CFG.USE_EMBEDDINGS:
        loaded = try_load_embedder(CFG.EMBEDDING_MODEL)
        if loaded is None:
            print("⚠ sentence-transformers not installed => lexical similarity only.")
        else:
            encode_fn, cosine_fn = loaded
            chunk_embeds = encode_fn([normalize_for_compare(t) for t in merged_texts])

    matched_out: List[Dict[str, Any]] = []
    current_type: Optional[str] = None
    cursor = 0

    for it in items:
        it_type = str(it.get("type", "") or "")
        if it_type != current_type:
            current_type = it_type
            cursor = 0

        out = match_item(
            it,
            merged_chunks=merged_chunks,
            merged_texts=merged_texts,
            chunk_embeds=chunk_embeds,
            encode_fn=encode_fn,
            cosine_fn=cosine_fn,
            start_from=cursor,
        )
        matched_out.append(out)

        m = out.get("match") or {}
        if m.get("matched") and isinstance(m.get("best_merged_chunk_index"), int) and m["best_merged_chunk_index"] >= 0:
            cursor = int(m["best_merged_chunk_index"])

    CFG.OUTPUT_MATCHED_JSON.write_text(json.dumps(matched_out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Done")
    print(f"   FAST_CONTENT_ROOT: {FAST_CONTENT_ROOT}")
    print(f"   raw chunks: {len(chunks)} ({chunks_path})")
    print(f"   merged chunks: {len(merged_chunks)} ({CFG.MERGED_CHUNKS_TXT})")
    print(f"   merge map: {CFG.MERGE_MAP_JSON}")
    print(f"   matched output: {CFG.OUTPUT_MATCHED_JSON}")
    print(f"   thresholds: title={CFG.MIN_TITLE_SCORE}, lower={CFG.MIN_LOWER_SCORE}")
    print(f"   lowerthird span max: {CFG.LOWERTHIRD_SPAN_MAX}")
    print("   number normalization: digits/words => <num>")


if __name__ == "__main__":
    main()