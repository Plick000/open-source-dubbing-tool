#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import subprocess
import time
import os
import shutil
import errno
from pathlib import Path
from typing import Dict, Any, List, Optional

# =========================
# CONFIG
# =========================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_JSON   = PROJECT_ROOT / "output" / "JSON" / "A10__matched_segments__.json"
OUTPUT_JSON  = PROJECT_ROOT / "output" / "JSON" / "A11__translated_segments__.json"
CONFIG_JSON  = PROJECT_ROOT / "inputs" / "config" / "config.json"

TARGET_LANG = "English"

# Allow override from env without breaking your hardcoded default
GEMINI_BIN = os.environ.get("GEMINI_BIN", "/home/vvruntime/.npm-global/bin/gemini")

BATCH_SIZE = 1000
TIMEOUT_SEC = 240

# Run Gemini from an empty dir
HEADLESS_CWD = Path.home() / ".cache" / "gemini_headless_empty"

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

MAX_RETRIES = 5
CLI_MAX_RETRIES = 5
CLI_RETRY_BASE_SLEEP_SEC = 2.0

# =========================
# Gemini creds cache (Whisper-style copy-once)
# Local cache:  C:\__tools\gemini\.gemini  -> /mnt/c/__tools/gemini/.gemini
# Shared source: D:\Automated Dubbings\_tools\gemini\.gemini -> /mnt/z/Automated Dubbings/_tools/gemini/.gemini
# =========================

def _to_wsl_path(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return p
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

# Gemini CLI reads creds from ~/.gemini, so we set HOME = parent_of_.gemini
_GEMINI_HOME_PARENT: Optional[Path] = None

def _dir_has_files(p: Path) -> bool:
    try:
        return p.exists() and p.is_dir() and any(p.iterdir())
    except Exception:
        return False

def ensure_gemini_creds_cached() -> Path:
    """
    Copy creds once from shared -> local cache.
    Returns LOCAL .gemini path.
    """
    src = Path(GEMINI_CREDS_SHARED)
    dst = Path(GEMINI_CREDS_LOCAL)

    # If /mnt/c isn't mounted/writable inside Docker, fall back under /app (bind-mounted repo)
    fallback_dst = Path(os.environ.get("GEMINI_CREDS_LOCAL_FALLBACK", "/app/__tools/gemini/.gemini"))

    if _dir_has_files(dst):
        return dst

    if not src.exists():
        raise RuntimeError(f"Gemini creds source not found: {src}")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        dst = fallback_dst
        dst.parent.mkdir(parents=True, exist_ok=True)

    lock_path = dst.parent / ".gemini_copy.lock"

    # Acquire lock (avoid parallel copy)
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
        raise RuntimeError(f"Timeout acquiring lock: {lock_path}")

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

# =========================

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

def load_target_lang_from_config(config_path: str) -> str:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    for key in ("Language", "language", "targetLanguage", "target_lang", "lang", "TARGET_LANG"):
        v = cfg.get(key)
        if isinstance(v, str) and v.strip():
            return normalize_lang_cap(v)
    raise KeyError("Language not found in inputs/config/config.json")

def load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def save_json(path: str, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()

def extract_first_json_object(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    i = text.find("{")
    j = text.rfind("}")
    if i == -1 or j == -1 or j <= i:
        raise ValueError(f"Could not locate JSON object in model output:\n{text[:2000]}")
    return json.loads(text[i:j+1])

def _detect_auth_missing(stderr_or_text: str) -> bool:
    s = (stderr_or_text or "")
    return (
        "Please set an Auth method" in s
        or "GEMINI_API_KEY" in s
        or '"code": 41' in s
    )

def _raise_auth_help():
    raise RuntimeError(
        "Gemini CLI has NO auth configured (no creds found).\n"
        "This script expects creds to exist in the cached ~/.gemini folder.\n"
        "Check that the shared source folder contains oauth_creds.json/state.json/etc.\n"
    )

def call_gemini_headless(prompt: str) -> dict:
    HEADLESS_CWD.mkdir(parents=True, exist_ok=True)

    cmd = [GEMINI_BIN, "-m", MODEL, "-p", prompt, "--output-format", "json"]

    last_err: Optional[Exception] = None

    for attempt in range(1, CLI_MAX_RETRIES + 1):
        try:
            run_env = os.environ.copy()
            if _GEMINI_HOME_PARENT is not None:
                run_env["HOME"] = str(_GEMINI_HOME_PARENT)  # must contain ".gemini" folder inside it

            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SEC,
                cwd=str(HEADLESS_CWD),
                env=run_env,
            )
        except subprocess.TimeoutExpired as e:
            last_err = e
            time.sleep(min(CLI_RETRY_BASE_SLEEP_SEC * (2 ** (attempt - 1)), 20))
            continue

        if _detect_auth_missing(p.stderr) or _detect_auth_missing(p.stdout):
            _raise_auth_help()

        if p.returncode != 0:
            last_err = RuntimeError(
                "Gemini CLI failed.\n"
                f"CMD: {' '.join(cmd)}\n"
                f"STDERR:\n{p.stderr}\n"
                f"STDOUT:\n{p.stdout}"
            )
            time.sleep(min(CLI_RETRY_BASE_SLEEP_SEC * (2 ** (attempt - 1)), 20))
            continue

        outer = extract_first_json_object(p.stdout)

        if isinstance(outer, dict) and outer.get("error"):
            err_str = json.dumps(outer["error"], ensure_ascii=False)
            if _detect_auth_missing(err_str):
                _raise_auth_help()
            last_err = RuntimeError(f"Gemini CLI error: {outer['error']}")
            time.sleep(min(CLI_RETRY_BASE_SLEEP_SEC * (2 ** (attempt - 1)), 20))
            continue

        return outer

    raise RuntimeError(f"Gemini CLI failed after {CLI_MAX_RETRIES} retries.\nLast error: {last_err}")

def translate_batch(batch: List[Dict[str, Any]]) -> Dict[int, str]:
    payload = {"items": [{"id": int(x["id"]), "text": x["text"]} for x in batch]}

    base_prompt = (
        f"You are a translation engine.\n"
        f"Task: Translate each items[i].text into {TARGET_LANG}.\n"
        f"Hard rules:\n"
        f"- Keep line breaks EXACTLY as input.\n"
        f"- Translate the COMPLETE text without omissions.\n"
        f"- Return ONLY valid JSON (no markdown, no commentary).\n"
        f'- Output schema EXACTLY: {{"items":[{{"id":1,"text_translated":"..."}}, ...]}}\n\n'
        f"INPUT JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    last_err = None

    for _ in range(1, MAX_RETRIES + 1):
        outer = call_gemini_headless(base_prompt)

        response = strip_code_fences(outer.get("response", ""))

        if not response:
            last_err = RuntimeError("Empty 'response' from Gemini CLI.")
            continue

        try:
            inner = extract_first_json_object(response)
        except Exception as e:
            last_err = e
            base_prompt = (
                f"IMPORTANT: Output ONLY valid JSON for schema:\n"
                f'{{"items":[{{"id":1,"text_translated":"..."}}, ...]}}\n'
                f"NO extra text.\n\n"
                f"INPUT JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            )
            continue

        mapping: Dict[int, str] = {}
        for it in inner.get("items", []):
            if "id" in it:
                mapping[int(it["id"])] = str(it.get("text_translated", "") or "")
        return mapping

    raise RuntimeError(f"Failed to get valid JSON from Gemini after {MAX_RETRIES} tries.\nLast error: {last_err}")

def main():
    t0 = time.time()

    global TARGET_LANG
    TARGET_LANG = load_target_lang_from_config(str(CONFIG_JSON))

    # ✅ Cache Gemini creds locally once, then force Gemini CLI to use it via HOME/.gemini
    global _GEMINI_HOME_PARENT
    local_gemini_dir = ensure_gemini_creds_cached()
    _GEMINI_HOME_PARENT = local_gemini_dir.parent  # parent must contain ".gemini"

    data = load_json(str(INPUT_JSON))
    if not isinstance(data, list):
        raise ValueError("Expected INPUT_JSON to be a JSON list.")

    todo = []
    for item in data:
        txt = item.get("text")
        if isinstance(txt, str) and txt.strip():
            todo.append({"id": int(item["id"]), "text": txt})

    total = len(todo)
    print(f"Translating {total} items in batches of {BATCH_SIZE} using {MODEL}...")

    id_to_trans: Dict[int, str] = {}

    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        batch = todo[start:end]
        print(f"✅ Batch {start//BATCH_SIZE + 1}: {start+1}-{end}/{total} ...", flush=True)

        batch_map = translate_batch(batch)
        id_to_trans.update(batch_map)

    out = []
    for item in data:
        _id = int(item["id"])
        item2 = dict(item)
        item2["text_translated"] = id_to_trans.get(_id, "")
        item2.pop("translation_error", None)
        out.append(item2)

    save_json(str(OUTPUT_JSON), out)
    print(f"✅ Saved translated JSON -> {OUTPUT_JSON}")
    print(f"Total Time taken: {round(time.time() - t0, 2)} seconds.")

if __name__ == "__main__":
    main()
