#!/usr/bin/env python3
# premiere_v2_magic_apply.py
# Requirements: Python 3.9+ (no extra libs)
# Usage: python premiere_v2_magic_apply.py

import base64
import copy
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Mapping

# =========================
# EASY INPUT VARIABLES
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[3]

XML_IN  = PROJECT_ROOT / "output" / "XML" / "V4__dubbing__.xml"
JSON_IN  = PROJECT_ROOT / "output" / "JSON" / "A9__absolute_interviews__.json"
XML_OUT  = PROJECT_ROOT / "output" / "XML" / "V5__dubbing__.xml"

LANGUAGE_CONFIG_PATH  = PROJECT_ROOT / "inputs" / "config" / "config.json"
FONTS_BLOB_CONFIG_PATH  = PROJECT_ROOT / "admin" / "configs" / "fonts" / "config.json"

# V2 = 2nd <track> under <sequence><media><video>
TARGET_VIDEO_TRACK_INDEX_1BASED = 2

# If JSON has more items than V2 text clips: duplicate using a working text clip
DUPLICATE_TO_MATCH_JSON = True

# For special langs, use template blob from fonts config (legacy behavior)
FORCE_TEMPLATE_FOR_SPECIAL = True  # kept for backwards compat flag naming

# NEW (Requested behavior):
# For special langs, ALWAYS use the template blob from fonts config (Z path),
# and apply it to all text clips. (NO font patching)
SPECIAL_USE_FONTS_CONFIG_TEMPLATE_BLOB = True

# For non-special langs, you can still force using first XML blob across all text clips
SPECIAL_USE_FIRST_XML_SOURCE_BLOB = True  # kept (used only for non-special or if template missing)

# IMPORTANT:
# To avoid Premiere crashes, keep blob size stable (in-place overwrite) for ALL languages.
PRESERVE_BLOB_SIZE_FOR_ALL_LANGS = True

# =========================
# LANGUAGE META (speciality_mode + font)
# =========================
Z_LANG_META_PATH = Path("/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json")
Z_FONTS_CFG_PATH = Path("/mnt/z/Automated Dubbings/admin/configs/fonts/config.json")

# Parameter IDs in your XML (inside GraphicAndType effect)
PARAM_ID_POSITION = "3"
PARAM_ID_ANCHOR   = "9"

# =========================
# Language meta lookup (speciality_mode + font)
# =========================

def resolve_existing_path(preferred: Path, fallbacks: List[Path]) -> Optional[Path]:
    if preferred and preferred.exists():
        return preferred
    for p in fallbacks:
        if p and p.exists():
            return p
    return None

def _normalize_lang(s: str) -> str:
    return (s or "").strip().lower()

def get_brand_from_config(cfg: Any) -> Optional[str]:
    if not isinstance(cfg, dict):
        return None
    for k in ("brand", "Brand", "brand_id", "brandId", "brandID", "brand_name"):
        v = cfg.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if s.isdigit():
            return f"brand_{s}"
        if re.fullmatch(r"brand_\d+", s.lower()):
            return s.lower()
        return s
    dubbing = cfg.get("dubbing") if isinstance(cfg.get("dubbing"), dict) else {}
    for k in ("brand", "brand_id", "brandId"):
        v = dubbing.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s.isdigit():
            return f"brand_{s}"
        if re.fullmatch(r"brand_\d+", s.lower()):
            return s.lower()
        return s
    return None

def load_json_file(path_str: Any) -> Any:
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(f"Missing JSON file: {p}")
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))

def load_language_metadata(language: str, brand: Optional[str]) -> Tuple[str, str, Optional[Path]]:
    """
    Returns (speciality_mode_upper, font_name, meta_path_or_None)
    """
    meta_path = resolve_existing_path(
        Z_LANG_META_PATH,
        [PROJECT_ROOT / "admin" / "configs" / "metadata" / "__languages.json"]
    )
    if not meta_path:
        return ("DEFAULT", "", None)

    meta = load_json_file(meta_path)

    # __languages.json sometimes is [ { ... } ]
    if isinstance(meta, list):
        if len(meta) == 1 and isinstance(meta[0], dict):
            meta = meta[0]
        else:
            merged = {}
            for x in meta:
                if isinstance(x, dict):
                    merged.update(x)
            meta = merged

    lang = _normalize_lang(language)
    if not lang:
        return ("DEFAULT", "", meta_path)

    def _read_node(node: Mapping[str, Any]) -> Tuple[str, str]:
        sm_raw = (
            node.get("speciality mode")
            or node.get("speciality_mode")
            or node.get("specialityMode")
            or "DEFAULT"
        )
        sm = str(sm_raw).strip().upper() or "DEFAULT"
        font_raw = node.get("font") or ""
        font = str(font_raw).strip()
        return sm, font

    if brand and isinstance(meta, dict):
        b = meta.get(brand) or meta.get(str(brand).lower())
        if isinstance(b, dict):
            langs = b.get("languages")
            if isinstance(langs, dict) and lang in langs and isinstance(langs[lang], dict):
                sm, font = _read_node(langs[lang])
                return (sm, font, meta_path)

    if isinstance(meta, dict):
        for _, bv in meta.items():
            if not isinstance(bv, dict):
                continue
            langs = bv.get("languages")
            if isinstance(langs, dict) and lang in langs and isinstance(langs[lang], dict):
                sm, font = _read_node(langs[lang])
                return (sm, font, meta_path)

    return ("DEFAULT", "", meta_path)

def get_language_from_config(cfg: Any) -> str:
    if isinstance(cfg, dict):
        for k in ("language", "lang", "target_language", "targetLang", "locale"):
            if k in cfg and cfg[k]:
                return str(cfg[k]).strip().lower()
    return ""

# =========================
# Fonts cfg helpers
# =========================

def iter_font_items(fonts_cfg: Any) -> List[Dict[str, Any]]:
    if isinstance(fonts_cfg, list):
        return [x for x in fonts_cfg if isinstance(x, dict)]
    if isinstance(fonts_cfg, dict):
        for k in ("fonts", "items", "data", "list"):
            v = fonts_cfg.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    raise ValueError("Fonts blob config must be a LIST of items (or a dict containing a list).")

def _get_any_key(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    lower_map = {str(k).lower(): k for k in d.keys()}
    for want in keys:
        k = lower_map.get(want.lower())
        if k is not None:
            return d.get(k)
    return None

def pick_font_template(font_items: List[Dict[str, Any]], font_family: str) -> Dict[str, Any]:
    """
    Returns dict with:
      - encoded_b64
      - TextPosition (optional)
      - TextAnchorPoint (optional)

    FIX:
    - Avoid false matches like "NotoSansKR-ExtraBold" matching "NotoSans" via startswith().
    - Allow:
        * exact font_family match
        * exact font_name match
        * font_name prefix ONLY if it is followed by a separator ("-" or space)
    """
    fam = (font_family or "").strip().lower()
    if not fam:
        raise ValueError("font_family is empty.")

    for it in font_items:
        ff = str(it.get("font_family", "")).strip().lower()
        fn = str(it.get("font_name", "")).strip().lower()

        family_match = (ff == fam)
        name_match = (fn == fam) or fn.startswith(fam + "-") or fn.startswith(fam + " ")

        if family_match or name_match:
            encoded = _get_any_key(it, ["encoded_b64", "EncodedB64", "blob_b64"])
            if encoded and str(encoded).strip():
                pos = _get_any_key(it, ["TextPosition", "text_position", "position", "Position"])
                anc = _get_any_key(it, ["TextAnchorPoint", "text_anchor_point", "anchor", "AnchorPoint", "Anchor Point"])
                return {
                    "encoded_b64": str(encoded).strip(),
                    "TextPosition": pos,
                    "TextAnchorPoint": anc,
                    "raw_item": it,
                }

    raise ValueError(f"No encoded_b64 template found for font_family={font_family!r}")
# =========================
# Text sanitize (multi-language safe)
# =========================

def sanitize_text(s: str) -> str:
    s = str(s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\x00", "")
    s = "".join(ch for ch in s if ch in ("\n", "\t") or ord(ch) >= 32)
    return unicodedata.normalize("NFC", s)

def _normalize_transform_value(existing: str, desired: Any) -> Optional[str]:
    """
    Accepts:
      - full Premiere string (contains ',') -> use directly
      - "x:y" -> merge into existing string (replace only x:y)
      - [x, y] or {"x":..,"y":..} -> convert to "x:y" and merge
      - {"value": "..."} -> unwrap and process again
    """
    if desired is None:
        return None

    # unwrap common shapes
    if isinstance(desired, dict) and "value" in desired and isinstance(desired["value"], str):
        desired = desired["value"]

    xy = None
    if isinstance(desired, (list, tuple)) and len(desired) >= 2:
        xy = f"{desired[0]}:{desired[1]}"
    elif isinstance(desired, dict):
        if "x" in desired and "y" in desired:
            xy = f"{desired['x']}:{desired['y']}"
        elif "X" in desired and "Y" in desired:
            xy = f"{desired['X']}:{desired['Y']}"

    if isinstance(desired, str):
        d = desired.strip()
        if not d:
            return None
        # full Premiere value
        if "," in d:
            return d
        # x:y only
        if ":" in d and "," not in d:
            xy = d

    if xy is None:
        return None

    ex = (existing or "").strip()
    if not ex or "," not in ex:
        # no existing structure; best effort
        return xy

    # expected: "<t>,<x:y>,rest..."
    parts = ex.split(",", 2)
    if len(parts) < 2:
        return xy
    t = parts[0]
    if len(parts) == 2:
        return f"{t},{xy}"
    return f"{t},{xy},{parts[2]}"

# =========================
# Premiere blob helpers (text replacement)
# =========================

def b64decode_loose(s: str) -> bytes:
    s = "".join(str(s or "").split())
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s)

def b64encode_std(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")

def is_mostly_printable(txt: str) -> bool:
    if not txt:
        return False
    good = 0
    for ch in txt:
        if ch in ("\n", "\t"):
            good += 1
        elif ch.isprintable():
            good += 1
    return (good / max(1, len(txt))) >= 0.90

def looks_like_non_content(txt: str) -> bool:
    t = (txt or "").strip()
    if not t:
        return True
    if len(t) <= 2:
        return True
    if "\\" in t or "/" in t:
        return True
    if "graphicandtype" in t.lower():
        return True
    return False

def extract_all_utf8_len_slots(blob: bytes, max_L: int = 10000) -> List[Tuple[int, int, int, str]]:
    out = []
    n = len(blob)
    for pos in range(0, n - 8):
        L = int.from_bytes(blob[pos:pos + 4], "little", signed=False)
        if L <= 0 or L > max_L:
            continue
        textpos = pos + 4
        endpos = textpos + L
        if endpos > n:
            continue
        seg = blob[textpos:endpos]
        try:
            txt = seg.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "\x00" in txt:
            continue
        if not any(ch.isalnum() for ch in txt):
            continue
        if not is_mostly_printable(txt):
            continue
        out.append((pos, textpos, L, txt))
    return out

def score_slot(txt: str, hints: List[str]) -> int:
    t = (txt or "").strip()
    if not t:
        return -10
    s = 0
    t_low = t.lower()
    for h in hints:
        hh = (h or "").strip()
        if not hh:
            continue
        if t == hh:
            s += 200
        if t_low == hh.lower():
            s += 160
        if hh.lower() in t_low:
            s += 40
    if "\n" in t:
        s += 25
    if " " in t:
        s += 15
    s += min(80, len(t))
    if looks_like_non_content(t):
        s -= 120
    return s

def choose_best_text_slot(blob: bytes, hints: List[str]) -> Optional[Tuple[int, int, int, str]]:
    candidates = extract_all_utf8_len_slots(blob, max_L=10000)
    if not candidates:
        return None
    best = None
    best_score = -10**9
    for lenpos, textpos, cap, txt in candidates:
        sc = score_slot(txt, hints)
        if sc > best_score:
            best_score = sc
            best = (lenpos, textpos, cap, txt)
    return best

def in_place_fixed_cap_replace(raw: bytes, lenpos: int, textpos: int, cap: int, new_text: str) -> Tuple[bytes, str]:
    new_bytes = new_text.encode("utf-8")
    if len(new_bytes) > cap:
        b2 = new_bytes[:cap]
        while b2:
            try:
                b2.decode("utf-8")
                break
            except UnicodeDecodeError:
                b2 = b2[:-1]
        new_bytes = b2

    applied_text = new_bytes.decode("utf-8", errors="ignore")
    padded = new_bytes + b"\x00" * (cap - len(new_bytes))

    raw2 = bytearray(raw)
    raw2[textpos:textpos + cap] = padded
    raw2[lenpos:lenpos + 4] = len(new_bytes).to_bytes(4, "little")
    return bytes(raw2), applied_text


def looks_like_font_name_candidate(txt: str) -> bool:
    """
    Detect the embedded font-family string slot, e.g. 'NimbusSansBeckerPBla'.
    We only use this for the earliest UTF-8 slot, not for normal content.
    """
    t = (txt or "").strip()
    if len(t) < 8:
        return False
    if " " in t or "\n" in t or "\t" in t:
        return False

    alpha = sum(1 for ch in t if ch.isalpha())
    upper = sum(1 for ch in t if ch.isupper())
    lower = sum(1 for ch in t if ch.islower())

    return (
        (alpha / max(1, len(t))) >= 0.75 and
        upper >= 2 and
        lower >= 2
    )


def extract_content_text_slots(blob: bytes) -> List[Tuple[int, int, int, str]]:
    """
    Returns ONLY the real displayed text runs from the Premiere blob.

    Important:
    - Premiere stores the visual text runs in reverse byte order.
    - So display order is DESC by lenpos.
    - The earliest asc slot is often the font-family name and must be ignored.
    """
    candidates = extract_all_utf8_len_slots(blob, max_L=10000)
    if not candidates:
        return []

    # Ascending only for detecting the earliest 'font name' slot
    asc = sorted(candidates, key=lambda x: x[0])

    font_slot_lenpos = None
    first_lenpos, _first_textpos, _first_cap, first_txt = asc[0]
    if looks_like_font_name_candidate(first_txt):
        font_slot_lenpos = first_lenpos

    content = []
    for lenpos, textpos, cap, txt in asc:
        if font_slot_lenpos is not None and lenpos == font_slot_lenpos:
            continue
        if looks_like_non_content(txt):
            continue
        content.append((lenpos, textpos, cap, txt))

    # Premiere reading order for visible text runs
    content.sort(key=lambda x: x[0], reverse=True)
    return content


def is_colored_multi_run_blob(blob_b64: str) -> bool:
    """
    Validator:
    True when the source text is split into multiple visible text runs
    (usually because some part has a different style/color).
    """
    raw = b64decode_loose(blob_b64)
    slots = extract_content_text_slots(raw)
    return len(slots) >= 2


def _is_good_cut(text: str, k: int) -> bool:
    if k <= 0 or k >= len(text):
        return True
    return text[k - 1].isspace() or text[k].isspace()


def _snap_cut_to_word_boundary(text: str, target: int, left_min: int, right_max: int) -> int:
    target = max(left_min, min(right_max, target))

    if _is_good_cut(text, target):
        return target

    max_delta = max(target - left_min, right_max - target)
    for d in range(1, max_delta + 1):
        a = target - d
        if a >= left_min and _is_good_cut(text, a):
            return a

        b = target + d
        if b <= right_max and _is_good_cut(text, b):
            return b

    return target


def split_text_for_existing_runs(new_text: str, ordered_slots: List[Tuple[int, int, int, str]]) -> List[str]:
    """
    Split new_text across the existing styled runs while keeping cuts on word boundaries,
    so the colored/styled run gets whole words, not random half-words.
    """
    if len(ordered_slots) <= 1:
        return [new_text]

    old_total = sum(len(txt) for _, _, _, txt in ordered_slots)
    if old_total <= 0:
        return [new_text] + [""] * (len(ordered_slots) - 1)

    cuts = [0]
    acc = 0

    for i in range(len(ordered_slots) - 1):
        acc += len(ordered_slots[i][3])
        raw_target = round((acc / old_total) * len(new_text))
        cut = _snap_cut_to_word_boundary(new_text, raw_target, cuts[-1], len(new_text))
        cuts.append(cut)

    cuts.append(len(new_text))

    parts = []
    for i in range(len(cuts) - 1):
        parts.append(new_text[cuts[i]:cuts[i + 1]])

    return parts


def replace_colored_multi_run_blob_preserve_size(blob_b64: str, new_text: str) -> Tuple[str, str, int]:
    """
    Replace ALL visible text runs in a colored/multi-run blob while preserving:
    - existing style/color run structure
    - blob size safety
    """
    raw = b64decode_loose(blob_b64)
    slots = extract_content_text_slots(raw)
    if len(slots) < 2:
        raise ValueError("Blob is not a colored/multi-run Source Text blob.")

    parts = split_text_for_existing_runs(new_text, slots)

    raw2 = raw
    applied_parts: List[str] = []

    for (lenpos, textpos, cap, _old_txt), part in zip(slots, parts):
        raw2, applied_part = in_place_fixed_cap_replace(raw2, lenpos, textpos, cap, part)
        applied_parts.append(applied_part)

    return b64encode_std(raw2), "".join(applied_parts), len(slots)


def replace_blob_text_preserve_size(blob_b64: str, new_text: str, hints: List[str]) -> Tuple[str, str, int, str]:
    raw = b64decode_loose(blob_b64)
    slot = choose_best_text_slot(raw, hints=hints)
    if not slot:
        raise ValueError("Could not locate a safe text slot in this blob.")
    lenpos, textpos, cap, old_text = slot
    raw2, applied_text = in_place_fixed_cap_replace(raw, lenpos, textpos, cap, new_text)
    return b64encode_std(raw2), applied_text, cap, old_text

# =========================
# XML helpers
# =========================
def apply_template_transforms(effect_el: ET.Element, pos_val_cfg: Any, anc_val_cfg: Any) -> int:
    changed = 0

    # Position (parameterid=3)
    pos_el = find_param_value_el_by_id(effect_el, PARAM_ID_POSITION)
    if pos_el is not None:
        new_pos = _normalize_transform_value(pos_el.text or "", pos_val_cfg)
        if new_pos:
            pos_el.text = new_pos
            changed += 1

    # Anchor (parameterid=9)
    anc_el = find_param_value_el_by_id(effect_el, PARAM_ID_ANCHOR)
    if anc_el is not None:
        new_anc = _normalize_transform_value(anc_el.text or "", anc_val_cfg)
        if new_anc:
            anc_el.text = new_anc
            changed += 1

    return changed


def get_video_tracks(root: ET.Element) -> List[ET.Element]:
    seq = root.find("sequence")
    if seq is None:
        raise ValueError("No <sequence> found.")
    video = seq.find("./media/video")
    if video is None:
        raise ValueError("No <media><video> found.")
    return video.findall("track")

def find_graphicandtype_effect(clipitem: ET.Element) -> Optional[ET.Element]:
    for eff in clipitem.findall("./filter/effect"):
        if (eff.findtext("effectid") or "").strip() == "GraphicAndType":
            return eff
    return None

def find_source_text_value_el(effect_el: ET.Element) -> Optional[ET.Element]:
    for param in effect_el.findall("parameter"):
        if (param.findtext("name") or "").strip() == "Source Text":
            val_el = param.find("value")
            if val_el is not None and (val_el.text or "").strip():
                return val_el
    return None

def find_param_value_el_by_id(effect_el: ET.Element, param_id: str) -> Optional[ET.Element]:
    for param in effect_el.findall("parameter"):
        pid = (param.findtext("parameterid") or param.findtext("paramid") or "").strip()
        if pid == str(param_id):
            val = param.find("value")
            if val is None:
                val = ET.SubElement(param, "value")
            return val
    return None


def max_clipitem_number(root: ET.Element) -> int:
    mx = 0
    for ci in root.findall(".//clipitem"):
        cid = ci.get("id") or ""
        m = re.match(r"clipitem-(\d+)$", cid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx

def reorder_track_children(track_el: ET.Element) -> None:
    children = list(track_el)
    clipitems = [c for c in children if c.tag == "clipitem"]
    others = [c for c in children if c.tag != "clipitem"]

    def start_of(ci: ET.Element) -> int:
        try:
            return int(ci.findtext("start") or 0)
        except Exception:
            return 0

    clipitems.sort(key=start_of)
    track_el.clear()
    for c in clipitems:
        track_el.append(c)
    for c in others:
        track_el.append(c)

def flatten_json_items(j: Any) -> List[Dict[str, Any]]:
    items: List[Any] = []
    if isinstance(j, list):
        items = j
    elif isinstance(j, dict):
        for k in sorted(j.keys()):
            v = j[k]
            if isinstance(v, list):
                items.extend(v)
    else:
        raise ValueError("JSON must be a dict or a list.")

    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if "text" in it and "timeline_start_frame" in it and "timeline_end_frame" in it:
            out.append(it)

    out.sort(key=lambda x: int(x.get("timeline_start_frame", 0)))
    return out

# =========================
# MAIN
# =========================

def main():
    # Preserve DOCTYPE if present, but force UTF-8 XML declaration.
    original_text = Path(XML_IN).read_text(encoding="utf-8", errors="replace")
    doctype_lines = []
    for line in original_text.splitlines()[:10]:
        if line.strip().startswith("<!DOCTYPE"):
            doctype_lines.append(line.rstrip())
    doctype_header = "\n".join(doctype_lines).strip()

    # Load language config
    lang_cfg = load_json_file(LANGUAGE_CONFIG_PATH)
    language = get_language_from_config(lang_cfg)
    brand = get_brand_from_config(lang_cfg)
    speciality_mode, meta_font, meta_path = load_language_metadata(language, brand)
    use_special = (speciality_mode or "DEFAULT").upper() != "DEFAULT"
    print(f"[LANG_META] language={language} brand={brand} speciality={speciality_mode} font={meta_font}")

    # Load fonts cfg (SPECIAL ONLY: to get template blob)
    font_items: List[Dict[str, Any]] = []
    fonts_path = resolve_existing_path(Z_FONTS_CFG_PATH, [FONTS_BLOB_CONFIG_PATH])
    if fonts_path:
        try:
            fonts_cfg = load_json_file(fonts_path)
            font_items = iter_font_items(fonts_cfg)
        except Exception as e:
            print("[FONTS_CFG] Failed to load:", str(e))
            font_items = []

    # ---------------------------------------------------------
    # SPECIAL LANGS: Use the REAL blob from fonts config (NO PATCHING)
    # ---------------------------------------------------------
    template_b64: Optional[str] = None
    template_pos = None
    template_anchor = None

    if use_special and meta_font and font_items and SPECIAL_USE_FONTS_CONFIG_TEMPLATE_BLOB:
        try:
            tpl = pick_font_template(font_items, meta_font)
            template_b64 = (tpl.get("encoded_b64") or "").strip()
            template_pos = tpl.get("TextPosition")
            template_anchor = tpl.get("TextAnchorPoint")
            print("[FONT_TEMPLATE] font_family=", meta_font, " template_b64=", "OK" if template_b64 else "EMPTY")
        except Exception as e:
            template_b64 = None
            print("[FONT_TEMPLATE] FAIL:", str(e))

    # load interviews JSON
    j = json.loads(Path(JSON_IN).read_text(encoding="utf-8", errors="replace"))
    entries = flatten_json_items(j)

    # Parse XML from bytes to avoid encoding surprises
    root = ET.fromstring(Path(XML_IN).read_bytes())

    tracks = get_video_tracks(root)
    if TARGET_VIDEO_TRACK_INDEX_1BASED < 1 or TARGET_VIDEO_TRACK_INDEX_1BASED > len(tracks):
        raise ValueError(f"Track index out of range (1..{len(tracks)}).")

    v2 = tracks[TARGET_VIDEO_TRACK_INDEX_1BASED - 1]

    # gather V2 GraphicAndType clips
    text_clips: List[Tuple[ET.Element, ET.Element, ET.Element]] = []
    for ci in v2.findall("clipitem"):
        eff = find_graphicandtype_effect(ci)
        if eff is None:
            continue
        val_el = find_source_text_value_el(eff)
        if val_el is not None:
            text_clips.append((ci, eff, val_el))

    if not text_clips:
        raise ValueError("No GraphicAndType -> Source Text clips found in the target track.")

    base_source_blob: Optional[str] = None
    pruned_clipitems = 0

    # Keep your pruning behavior ONLY for special languages
    if SPECIAL_USE_FIRST_XML_SOURCE_BLOB:
        base_ci, base_eff, base_val_el = text_clips[0]
        base_source_blob = (base_val_el.text or "").strip()
        if not base_source_blob:
            raise ValueError("First Source Text blob is empty; cannot use it as the template.")

        if use_special:
            for ci in list(v2.findall("clipitem")):
                if ci is base_ci:
                    continue
                eff = find_graphicandtype_effect(ci)
                if eff is None:
                    continue
                val_el = find_source_text_value_el(eff)
                if val_el is None:
                    continue
                v2.remove(ci)
                pruned_clipitems += 1

        # rebuild list (now should contain only the base text clip)
        text_clips = []
        for ci in v2.findall("clipitem"):
            eff = find_graphicandtype_effect(ci)
            if eff is None:
                continue
            val_el = find_source_text_value_el(eff)
            if val_el is not None:
                text_clips.append((ci, eff, val_el))

        if not text_clips:
            raise ValueError("After pruning, no GraphicAndType -> Source Text clips remain in the target track.")

    # duplicate if needed
    if DUPLICATE_TO_MATCH_JSON and len(text_clips) < len(entries):
        base_ci = text_clips[0][0]
        next_id = max_clipitem_number(root) + 1
        needed = len(entries) - len(text_clips)
        for _ in range(needed):
            new_ci = copy.deepcopy(base_ci)
            new_ci.set("id", f"clipitem-{next_id}")
            next_id += 1
            v2.append(new_ci)

        # rebuild list
        text_clips = []
        for ci in v2.findall("clipitem"):
            eff = find_graphicandtype_effect(ci)
            if eff is None:
                continue
            val_el = find_source_text_value_el(eff)
            if val_el is not None:
                text_clips.append((ci, eff, val_el))

    # sort by current start
    text_clips.sort(key=lambda t: int(t[0].findtext("start") or 0))

    updated = 0
    failed: List[Tuple[str, str]] = []
    used_template_blob = 0
    used_first_xml_blob = 0

    for i, entry in enumerate(entries):
        if i >= len(text_clips):
            break

        ci, eff, val_el = text_clips[i]

        start_f = int(entry["timeline_start_frame"])
        end_f   = int(entry["timeline_end_frame"])
        new_txt = sanitize_text(entry["text"])

        # update timing
        if ci.find("start") is None:
            ET.SubElement(ci, "start")
        if ci.find("end") is None:
            ET.SubElement(ci, "end")
        ci.find("start").text = str(start_f)
        ci.find("end").text   = str(end_f)

        # effect visible label
        name_el = eff.find("name")
        if name_el is None:
            name_el = ET.SubElement(eff, "name")

        clip_name_hint = (ci.findtext("name") or "").strip()
        eff_name_hint  = (name_el.text or "").strip()
        hints = [h for h in [eff_name_hint, clip_name_hint] if h]

        # ---------------------------------------------------------
        # CHOOSE SOURCE BLOB
        # - SPECIAL: ALWAYS use template blob from fonts config (true font)
        # - Else: use first XML blob (if enabled)
        # - Else: use existing val_el.text
        # ---------------------------------------------------------
        if use_special and template_b64 and SPECIAL_USE_FONTS_CONFIG_TEMPLATE_BLOB:
            source_blob = template_b64
        elif SPECIAL_USE_FIRST_XML_SOURCE_BLOB and base_source_blob:
            source_blob = base_source_blob
        else:
            source_blob = val_el.text

        try:
            # New validator:
            # If the source blob already contains multiple styled text runs
            # (colored text in the XML), preserve that run structure.
            if is_colored_multi_run_blob(source_blob):
                new_blob, applied_text, _run_count = replace_colored_multi_run_blob_preserve_size(
                    source_blob, new_txt
                )
            else:
                new_blob, applied_text, _cap, _old_text = replace_blob_text_preserve_size(
                    source_blob, new_txt, hints=hints
                )

            val_el.text = new_blob

            # Apply Position + AnchorPoint ONLY when using template blob (special languages)
            if use_special and template_b64 and SPECIAL_USE_FONTS_CONFIG_TEMPLATE_BLOB and source_blob == template_b64:
                apply_template_transforms(eff, template_pos, template_anchor)            

            # DO NOT PATCH FONT. (Template blob already encodes the font.)
            name_el.text = applied_text
            updated += 1

            if use_special and template_b64 and source_blob == template_b64:
                used_template_blob += 1
            elif base_source_blob and source_blob == base_source_blob:
                used_first_xml_blob += 1

            # IMPORTANT:
            # Do NOT apply transforms here (Position/Anchor) because duplicating the base clip
            # already keeps placement correct, and applying transforms may break placement.

        except Exception as ex:
            failed.append((ci.get("id") or "clipitem-?", str(ex)))
            name_el.text = new_txt

    reorder_track_children(v2)

    # write output
    xml_body = ET.tostring(root, encoding="unicode").strip()
    xml_body = re.sub(r'^\s*<\?xml[^>]*\?>\s*\n?', '', xml_body)

    prefix = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if doctype_header:
        prefix += doctype_header + "\n"

    out_text = prefix + xml_body
    Path(XML_OUT).write_bytes(out_text.encode("utf-8"))

    print(f"[LANG] language={language!r} | brand={brand!r} | speciality_mode={speciality_mode!r} | font={meta_font!r} | special={use_special}")
    if meta_path:
        print(f"[PATH] languages_meta={meta_path}")
    print(f"[DONE] JSON items: {len(entries)} | V2 clips: {len(text_clips)}")
    print(f"[INFO] Used template blob (special): {used_template_blob}")
    print(f"[INFO] Used first XML blob: {used_first_xml_blob}")
    print(f"[INFO] Pruned Source Text clipitems: {pruned_clipitems}")
    print(f"[OK] Updated: {updated} | Failed: {len(failed)}")
    if failed:
        print("[WARN] Failed clips:")
        for cid, err in failed:
            print(" -", cid, err)
    print(f"[OUT] {XML_OUT}")

if __name__ == "__main__":
    main()