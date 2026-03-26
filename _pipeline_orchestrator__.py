#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import pty
import select
import errno
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Set, Tuple


# ==========================================================
# DRIVE REMAP
# ==========================================================
# Any Windows path coming from JOB JSON / CONFIG that starts with E:\ will be treated as Z:\
# And any WSL path starting with /mnt/e will be treated as /mnt/z
DRIVE_REMAP_FROM = ("D", "E")
DRIVE_REMAP_TO = "Z"

# ==========================================================
# REMOTE MOUNT POLLING (Z: /mnt/z)  [NEW]
# ==========================================================
# When the Z: share disappears (PC reboot, SMB drop, mount removed),
# WSL path ops often raise OSError like:
#   errno=112 "Host is down"
#   errno=107 "Transport endpoint is not connected"
# This pipeline should pause up to 20 minutes, then hard-exit.

REMOTE_MOUNT_ROOT = Path(f"/mnt/{DRIVE_REMAP_TO.lower()}")  # usually /mnt/z
REMOTE_POLL_MAX_SECONDS = 20 * 60
REMOTE_POLL_INTERVAL_SECONDS = 5

_REMOTE_ERRNOS = {
    112,  # EHOSTDOWN: Host is down
    107,  # ENOTCONN: Transport endpoint is not connected
    110,  # ETIMEDOUT: Connection timed out
    113,  # EHOSTUNREACH: No route to host
    5,    # EIO: Input/output error (sometimes SMB/DrvFs hiccups)
}


def _is_remote_mount_error(exc: BaseException) -> bool:
    """True if exception looks like a transient /mnt/z disconnect."""
    if not isinstance(exc, OSError):
        return False

    # Fast path: errno match
    if getattr(exc, "errno", None) in _REMOTE_ERRNOS:
        s = str(exc)
        # Only treat as remote-mount issue if it mentions /mnt/z
        return (f"{REMOTE_MOUNT_ROOT}/" in s) or (str(REMOTE_MOUNT_ROOT) in s)

    # Message-only match (some ops lose errno)
    msg = str(exc).lower()
    if ("host is down" in msg) or ("transport endpoint is not connected" in msg):
        return (str(REMOTE_MOUNT_ROOT) in msg)

    return False


def _poll_remote_mount(*, probe: Path, max_seconds: int = REMOTE_POLL_MAX_SECONDS, interval: int = REMOTE_POLL_INTERVAL_SECONDS) -> bool:
    """
    Poll for remote mount recovery.
    probe: a path that MUST exist when mount is healthy (e.g. Pending folder).
    Returns True if recovered within max_seconds, else False.
    """
    start = time.time()
    last_print = 0.0

    while True:
        elapsed = time.time() - start
        if elapsed > max_seconds:
            return False

        try:
            if probe.exists():
                return True
        except OSError:
            pass

        # Print progress ~once per 30s (avoid spam)
        if elapsed - last_print >= 30:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{ts}] REMOTE MOUNT DOWN. Waiting for {probe} ... ({int(elapsed)}s/{max_seconds}s)",
                file=sys.stderr
            )
            last_print = elapsed

        time.sleep(max(1, interval))


def _hard_exit(code: int = 90) -> None:
    """Hard close the entire worker after remote poll timeout."""
    try:
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(code)


SHEET_UPDATER_SCRIPT = "_SheetUpdate__.py"  # or "__update_sheet__.py" if that's your real filename
SHEETS_CONFIG_PATH = "admin/configs/sheets/config.json"
SHEET_DONE_HEADER = "Languages"
SHEET_DONE_VALUE = "Done"

# Keep fully-skipped jobs here (inside each PC processing folder) so they are not picked again.
SKIPPED_SUBFOLDER_NAME = "skipped"
SKIPPED_FINAL_SUBFOLDER_NAME = "skipped_final"
SKIPPED_FINAL_RETRY_COOLDOWN_SECONDS = 10 * 60  # 10 minutes

# Pending-claim subfolder (inside Pending/) to ensure atomic, multi-worker-safe claims.
PENDING_CLAIMED_SUBFOLDER_NAME = "_claimed"

# ----------------------------
# Language mappings (lowercase folders)
# ----------------------------

LANG_CODE_TO_NAME = {
    "KR": "Korean",
    "FR": "French",
    "ES": "Spanish",
    "PL": "Polish",
    "CZ": "Czech",
    "RU": "Russian",
    "BR": "Portuguese",
    "HR": "Croatian",
}

V1_LANGS = {"Korean", "French", "Spanish", "Polish"}
V2_LANGS = {"Czech", "Russian", "Portuguese", "Croatian"}
# ----------------------------
# Brand-aware language config (per-brand V1/V2 selection)
# ----------------------------

_LANG_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_LANG_CONFIG_PATH_USED: Optional[str] = None


def _title_case_lang_folder(lang_key: str) -> str:
    """Convert config key like 'korean' -> 'Korean' (folder convention)."""
    s = str(lang_key or "").strip()
    if not s:
        return s
    # Handle a few common special cases if you ever need them later:
    # e.g. 'pt-br' or 'zh-hans' would need custom mapping.
    return s[:1].upper() + s[1:]


def _normalize_lang_config(raw: Any) -> Dict[str, Any]:
    """Your __languages.json is sometimes a [ { ... } ] wrapper."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, dict):
        return raw
    raise ValueError("Invalid languages config JSON format")


def load_languages_config(tool_root: Path, logger: Optional['JobLogger'] = None) -> Optional[Dict[str, Any]]:
    """
    Loads the per-brand language config once. If missing, returns None and the pipeline falls back
    to legacy V1_LANGS/V2_LANGS logic so nothing breaks.
    """
    global _LANG_CONFIG_CACHE, _LANG_CONFIG_PATH_USED

    if _LANG_CONFIG_CACHE is not None:
        return _LANG_CONFIG_CACHE

    # 1) Explicit env override (recommended for portability across PCs)
    env_path = (os.environ.get("LANGUAGES_CONFIG_PATH") or "").strip()

    # 2) Your stated default location (Windows Z: mapped into WSL at /mnt/z)
    default_candidates = [
        "/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json",
        "/mnt/z/Automated Dubbings/admin/configs/metadata/languages.json",
        "/mnt/z/Automated Dubbings/admin/configs/metadata/__languages.json.txt",
        "/mnt/z/Automated Dubbings/admin/configs/metadata/languages.json.txt",
    ]

    # 3) Relative candidates (in case you later move config into repo)
    rel_candidates = [
        tool_root / "admin" / "configs" / "metadata" / "__languages.json",
        tool_root / "admin" / "configs" / "metadata" / "languages.json",
    ]

    candidates: List[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    candidates += [Path(x) for x in default_candidates]
    candidates += [Path(x) for x in rel_candidates]

    for cand in candidates:
        try:
            if cand.exists() and cand.is_file():
                raw = json.loads(cand.read_text(encoding="utf-8"))
                cfg = _normalize_lang_config(raw)
                _LANG_CONFIG_CACHE = cfg
                _LANG_CONFIG_PATH_USED = str(cand)

                # Expand LANG_CODE_TO_NAME dynamically so new languages don't crash the worker.
                try:
                    _augment_lang_code_mapping_from_config(cfg)
                except Exception:
                    pass

                if logger:
                    logger.write(f"Loaded languages config: {_LANG_CONFIG_PATH_USED}")
                return _LANG_CONFIG_CACHE
        except Exception as e:
            if logger:
                logger.write(f"Failed reading languages config from {cand}: {e}")
            continue

    if logger:
        logger.write("Languages config not found; using legacy V1/V2 language sets.")
    return None


def _augment_lang_code_mapping_from_config(cfg: Dict[str, Any]) -> None:
    """Adds any unseen langcodes into LANG_CODE_TO_NAME so process_language can resolve folder names."""
    # cfg: { brand_1: { languages: { korean: {langcode:KR,...}, ... } }, ... }
    for bval in (cfg or {}).values():
        langs = (bval or {}).get("languages") if isinstance(bval, dict) else None
        if not isinstance(langs, dict):
            continue
        for lang_key, meta in langs.items():
            if not isinstance(meta, dict):
                continue
            lc = str(meta.get("langcode") or "").strip().upper()
            if not lc:
                continue
            if lc not in LANG_CODE_TO_NAME:
                LANG_CODE_TO_NAME[lc] = _title_case_lang_folder(lang_key)


def pick_brand_number(job_obj: Dict[str, Any]) -> Optional[int]:
    """Attempt to extract a brand number from the job JSON (robust across formats)."""
    # Direct keys
    direct_keys = ["brand", "brand_id", "brandId", "brand_num", "brandNum", "brand_number", "brandNumber"]
    nested = job_obj.get("job") if isinstance(job_obj.get("job"), dict) else {}
    for k in direct_keys:
        v = job_obj.get(k)
        if v is None and isinstance(nested, dict):
            v = nested.get(k)
        if v is None:
            continue
        try:
            # handles '15', 15, 'brand_15'
            m = re.search(r"(\d{1,3})", str(v))
            if m:
                return int(m.group(1))
        except Exception:
            pass

    # Heuristics from video_name / project_root path
    candidates = [
        str(pick_video_name(job_obj) or ""),
        str(pick_project_root(job_obj) or ""),
    ]
    pat = re.compile(r"\bbrand[\s_:\-]*([0-9]{1,3})\b", re.IGNORECASE)
    pat2 = re.compile(r"\bbrand_([0-9]{1,3})\b", re.IGNORECASE)

    for s in candidates:
        if not s:
            continue
        m = pat.search(s) or pat2.search(s)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    return None


def _get_brand_lang_meta(cfg: Dict[str, Any], brand_num: int, lang_code: str) -> Optional[Dict[str, Any]]:
    if not cfg or not brand_num or not lang_code:
        return None
    bkey = f"brand_{int(brand_num)}"
    bobj = cfg.get(bkey)
    if not isinstance(bobj, dict):
        return None
    langs = bobj.get("languages")
    if not isinstance(langs, dict):
        return None
    code_u = str(lang_code).strip().upper()
    for lang_key, meta in langs.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("langcode") or "").strip().upper() == code_u:
            # attach lang_key for convenience
            out = dict(meta)
            out["__lang_key__"] = lang_key
            return out
    return None


def resolve_variant_for_job(cfg: Optional[Dict[str, Any]], brand_num: Optional[int], lang_code: str) -> Optional[str]:
    """Returns 'V1'/'V2' if found in config; otherwise None."""
    if not cfg or not brand_num:
        return None
    meta = _get_brand_lang_meta(cfg, int(brand_num), lang_code)
    if not meta:
        return None
    v = str(meta.get("version") or "").strip().upper()
    if v in {"V1", "V2"}:
        return v
    return None


def resolve_lang_folder_name(cfg: Optional[Dict[str, Any]], brand_num: Optional[int], lang_code: str) -> Optional[str]:
    """Resolves the language folder name (e.g. 'Czech') by langcode, preferring config."""
    code_u = str(lang_code or "").strip().upper()
    if not code_u:
        return None
    if code_u in LANG_CODE_TO_NAME:
        return LANG_CODE_TO_NAME[code_u]
    if cfg and brand_num:
        meta = _get_brand_lang_meta(cfg, int(brand_num), code_u)
        if meta:
            return _title_case_lang_folder(meta.get("__lang_key__") or "")
    return None



# ----------------------------
# Path conversion + drive remap helpers
# ----------------------------

_WIN_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def remap_drive_in_path(s: str) -> str:
    r"""
    Remap D: or E: -> Z: for Windows paths, and /mnt/d or /mnt/e -> /mnt/z for WSL paths.
    Examples:
      D:\Dubbing Queues\X  -> Z:\Dubbing Queues\X
      E:\Dubbing Queues\X  -> Z:\Dubbing Queues\X
      /mnt/d/...           -> /mnt/z/...
      /mnt/e/...           -> /mnt/z/...
    """
    if not s:
        return s

    ss = str(s).strip()

    # 1) WSL style: /mnt/d/... or /mnt/e/... -> /mnt/z/...
    drives_wsl = "|".join(d.lower() for d in DRIVE_REMAP_FROM)
    ss = re.sub(
        rf"^/mnt/({drives_wsl})(/|$)",
        rf"/mnt/{DRIVE_REMAP_TO.lower()}\2",
        ss,
        flags=re.IGNORECASE,
    )

    # 2) Windows drive style: D:\ or E:\ or D:/ or E:/ -> Z:\ or Z:/
    drives_win = "|".join(map(re.escape, DRIVE_REMAP_FROM))
    ss = re.sub(
        rf"^({drives_win}):([\\/])",
        rf"{DRIVE_REMAP_TO}:\2",
        ss,
        flags=re.IGNORECASE,
    )

    return ss


def windows_to_wsl_path(p: Union[str, Path]) -> Path:
    """
    Converts a Windows drive path (e.g., Z:\foo\bar) into /mnt/z/foo/bar.
    Also accepts already-WSL paths.
    """
    s = remap_drive_in_path(str(p).strip())

    m = _WIN_DRIVE_RE.match(s)
    if not m:
        return Path(s)

    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return Path("/mnt") / drive / rest


# ----------------------------
# Tool root discovery
# ----------------------------

def find_tool_root(start: Path) -> Path:
    """
    Auto-detect tool root by finding:
      - inputs/ (case-insensitive)
      - Python/V1 + Python/V2 (case-insensitive)
    """
    cur = start.resolve()
    for parent in [cur] + list(cur.parents):
        # inputs folder (case-insensitive)
        inputs_dir = None
        for cand in ["inputs", "Inputs", "INPUTS"]:
            if (parent / cand).is_dir():
                inputs_dir = parent / cand
                break

        python_dir = None
        for cand in ["Python", "python", "PYTHON"]:
            if (parent / cand).is_dir():
                python_dir = parent / cand
                break

        if inputs_dir and python_dir:
            v1_ok = any((python_dir / x / "__sequence_V1__.py").exists() for x in ["V1", "v1"])
            v2_ok = any((python_dir / x / "__sequence_V2__.py").exists() for x in ["V2", "v2"])
            if v1_ok and v2_ok:
                return parent

    raise RuntimeError(
        "Could not auto-detect tool root. Ensure this folder contains:\n"
        "  - inputs/ (or Inputs)\n"
        "  - Python/V1/__sequence_V1__.py\n"
        "  - Python/V2/__sequence_V2__.py\n"
        "Or pass --tool-root /home/viralverse/dubbing-flask"
    )


# ----------------------------
# JSON parsing helpers
# ----------------------------

def try_get_job_id_from_json(job_path: Path) -> Optional[str]:
    try:
        obj = load_job_json(job_path)
        return pick_job_id(obj)
    except Exception:
        return None


def pick_oldest_json_excluding(folder: Path, exclude_abs_paths: set[str], exclude_job_ids: set[str]) -> Optional[Path]:
    """
    Picks the oldest *.json in `folder`, but skips:
      - any file whose absolute path is in exclude_abs_paths
      - any file whose job_id is in exclude_job_ids
    This prevents immediate re-pick loops in the SAME running process.
    """
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)

    for f in files:
        ap = str(f.resolve())
        if ap in exclude_abs_paths:
            continue

        jid = try_get_job_id_from_json(f)
        if jid and jid in exclude_job_ids:
            continue

        return f

    return None


def try_read_job_id_from_json_path(p: Path) -> str:
    """
    Best-effort: read job_id from a job json file.
    If it can't be read, return a unique fallback so we still can pick it.
    """
    try:
        obj = load_job_json(p)
        return str(pick_job_id(obj) or "").strip() or f"__no_job_id__::{p.name}"
    except Exception:
        return f"__unreadable__::{p.name}"
    

def pick_oldest_json_excluding_job_ids(
    folder: Path,
    *,
    exclude_abs_paths: Set[str],
    exclude_job_ids: Dict[str, float],
    cooldown_seconds: int = SKIPPED_FINAL_RETRY_COOLDOWN_SECONDS,
) -> Optional[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    now = time.time()

    for f in files:
        ap = str(f.resolve())
        if ap in exclude_abs_paths:
            continue

        jid = try_read_job_id_from_json_path(f)

        # Only suppress re-pick for a limited cooldown window
        last_ts = exclude_job_ids.get(jid)
        if last_ts is not None and (now - last_ts) < cooldown_seconds:
            continue

        return f

    return None


def load_job_json(job_json_path: Path) -> Dict[str, Any]:
    raw = json.loads(job_json_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        if not raw or not isinstance(raw[0], dict):
            raise ValueError("Job JSON is not a valid list-of-objects")
        return raw[0]
    if not isinstance(raw, dict):
        raise ValueError("Job JSON is not an object")
    return raw


def pick_project_root(job_obj: Dict[str, Any]) -> str:
    v = job_obj.get("project_root") or (job_obj.get("job") or {}).get("project_root") or ""
    return remap_drive_in_path(v)


def pick_languages(job_obj: Dict[str, Any]) -> List[str]:
    langs = (job_obj.get("job") or {}).get("languages")
    if isinstance(langs, list) and langs:
        return [str(x).strip().upper() for x in langs if str(x).strip()]
    langs2 = job_obj.get("languages")
    if isinstance(langs2, list) and langs2:
        return [str(x).strip().upper() for x in langs2 if str(x).strip()]
    return []


def pick_job_id(job_obj: Dict[str, Any]) -> str:
    return job_obj.get("job_id") or (job_obj.get("job") or {}).get("job_id") or job_obj.get("job_filename") or "unknown_job"


def pick_video_name(job_obj: Dict[str, Any]) -> str:
    return job_obj.get("video_name") or (job_obj.get("job") or {}).get("video_name") or "Unknown Video"


def pick_pc_name(job_obj: Dict[str, Any]) -> str:
    keys = [
        "pc", "PC", "worker_pc", "workerPC", "worker", "worker_name", "machine", "machine_name",
        "assigned_pc", "Assigned PC", "assignedPC",
    ]

    for k in keys:
        v = job_obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    nested = job_obj.get("job")
    if isinstance(nested, dict):
        for k in keys:
            v = nested.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    env_pc = (os.getenv("WORKER_PC") or "").strip()
    if env_pc:
        return env_pc

    return "PC_UNKNOWN"


# ----------------------------
# Windows-safe sanitization
# ----------------------------

_INVALID_WIN_CHARS = r'<>:"/\\|?*'
_invalid_re = re.compile(f"[{re.escape(_INVALID_WIN_CHARS)}]")


def sanitize_filename(name: str, replacement: str = "_") -> str:
    s = str(name or "").strip()
    s = _invalid_re.sub(replacement, s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").strip()
    return s or "Untitled"


# ----------------------------
# Logging
# ----------------------------

@dataclass
class JobLogger:
    log_path: Path

    def write(self, msg: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.log_path.open("a", encoding="utf-8").write(line)
        print(line, end="")

    def write_raw(self, s: str) -> None:
        """Write raw text to the log file without timestamps/prefixes."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(s)

# ----------------------------
# Status TXT (per-video resume/skip)
# ----------------------------

DONE_RE = re.compile(r"^\s*DONE\s+([A-Z]{2})\b", re.IGNORECASE)

FAIL_RE = re.compile(r"^\s*FAIL\s+([A-Z]{2})\b", re.IGNORECASE)


def status_file_path(status_dir: Path, video_name: str) -> Path:
    safe = sanitize_filename(video_name)
    return status_dir / f"{safe}.txt"


def read_status_done_langs(status_path: Path) -> Set[str]:
    done: Set[str] = set()
    if not status_path.exists():
        return done
    for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = DONE_RE.match(line)
        if m:
            done.add(m.group(1).upper())
    return done


def read_status_failed_langs(status_path: Path) -> Set[str]:
    failed: Set[str] = set()
    if not status_path.exists():
        return failed
    for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = FAIL_RE.match(line)
        if m:
            failed.add(m.group(1).upper())
    return failed


def append_status_line(status_path: Path, line: str) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.open("a", encoding="utf-8").write(line.rstrip() + "\n")


def ensure_status_header(status_path: Path, video_name: str, job_id: str, pc_name: str) -> None:
    if status_path.exists():
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = [
        f"VIDEO: {video_name}",
        f"JOB_ID: {job_id}",
        f"PC: {pc_name}",
        f"CREATED: {now}",
        "-" * 60,
    ]
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text("\n".join(header) + "\n", encoding="utf-8")


# ----------------------------
# File copy helpers
# ----------------------------

def safe_copy(src: Path, dst: Path, logger: JobLogger) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    logger.write(f"Copy: {src} -> {dst}")
    shutil.copy2(src, dst)


def copy_video(src: Path, dst: Path, logger: JobLogger) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Missing video source: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    size_mb = src.stat().st_size / (1024 * 1024)
    logger.write(f"Copy VIDEO ({size_mb:.1f} MB): {src} -> {dst}")
    shutil.copyfile(src, dst)


def clear_inputs(inputs_dir: Path, logger: JobLogger) -> None:
    """
    Clears everything inside tool_root/inputs (files + folders).
    Creates the folder if missing.
    """
    if not inputs_dir.exists():
        inputs_dir.mkdir(parents=True, exist_ok=True)
        return

    logger.write(f"Clearing inputs folder: {inputs_dir}")
    for child in inputs_dir.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception as e:
            logger.write(f"WARNING: could not remove {child}: {e}")


def clear_outputs(outputs_dir: Path, logger: JobLogger) -> None:
    """
    Clears everything inside tool_root/output or tool_root/outputs (files + folders).
    Creates the folder if missing.
    """
    if not outputs_dir.exists():
        outputs_dir.mkdir(parents=True, exist_ok=True)
        return

    logger.write(f"Clearing outputs folder: {outputs_dir}")
    for child in outputs_dir.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception as e:
            logger.write(f"WARNING: could not remove {child}: {e}")


def detect_outputs_dir(tool_root: Path) -> Path:
    """
    Your tool may use either:
      - tool_root/output
      - tool_root/outputs
    Prefer 'output' if it exists, else 'outputs' if it exists, else default 'output'.
    """
    out1 = tool_root / "output"
    out2 = tool_root / "outputs"
    if out1.exists():
        return out1
    if out2.exists():
        return out2
    return out1


def clear_tool_work_folders(tool_root: Path, logger: JobLogger) -> None:
    """
    Clears BOTH work folders:
      - inputs/
      - output/ (or outputs/)
    """
    inputs_dir = tool_root / "inputs"
    outputs_dir = detect_outputs_dir(tool_root)

    logger.write("Clearing tool work folders BEFORE processing...")
    clear_inputs(inputs_dir, logger)
    clear_outputs(outputs_dir, logger)


def between_languages_pause_and_cleanup(
    tool_root: Path,
    logger: JobLogger,
    pause_seconds: int = 180
) -> None:
    """
    Pause between languages and clean folders to prevent overlap.
    """
    logger.write(f"Waiting {pause_seconds}s before next language...")
    time.sleep(pause_seconds)

    # Clean BEFORE next language starts
    clear_tool_work_folders(tool_root, logger)


def after_job_pause(logger: Optional[JobLogger], pause_seconds: int = 180) -> None:
    if pause_seconds <= 0:
        return
    if logger:
        logger.write(f"JOB COMPLETE: waiting {pause_seconds}s before picking next job...")
    time.sleep(pause_seconds)



def read_config_video_path(config_path: Path) -> Optional[str]:
    """
    Reads config.json and tries to find a video path, then remaps E->Z.
    """
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    keys = [
        "video_path", "video", "videoFile", "video_file",
        "english_video_path", "rendered_video_path",
        "source_video", "source_video_path",
        "videoPath", "video_link", "videoLink",
    ]

    for k in keys:
        v = cfg.get(k)
        if isinstance(v, str) and v.strip():
            return remap_drive_in_path(v.strip())

    for k in ["config", "job"]:
        nested = cfg.get(k)
        if isinstance(nested, dict):
            for kk in keys:
                v = nested.get(kk)
                if isinstance(v, str) and v.strip():
                    return remap_drive_in_path(v.strip())

    return None


# ----------------------------
# Sequence runner
# ----------------------------

def run_sequence(tool_root: Path, variant: str, logger: JobLogger) -> None:
    """
    Run the per-variant sequence script and stream its output LIVE to:
      1) the terminal (exact spacing preserved)
      2) the log file (raw, no timestamps)

    Uses a PTY so that child + grandchild python processes behave like a real terminal
    (reduces buffering/freezing). Handles PTY EIO as normal EOF.
    """
    if variant not in {"V1", "V2"}:
        raise ValueError(f"Invalid variant: {variant}")

    seq_path = tool_root / "Python" / variant / f"__sequence_{variant}__.py"
    if not seq_path.exists():
        raise FileNotFoundError(f"Sequence file not found: {seq_path}")

    cmd = [sys.executable, "-u", str(seq_path)]
    logger.write(f"RUN: {' '.join(cmd)} (cwd={tool_root})")

    master_fd, slave_fd = pty.openpty()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(tool_root),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,     # merge stderr into stdout in correct order
            close_fds=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    finally:
        # parent must close slave side
        try:
            os.close(slave_fd)
        except Exception:
            pass

    # Stream output exactly as emitted (keeps indentation/spaces)
    try:
        while True:
            # If the child already exited, drain any remaining output then break
            if proc.poll() is not None:
                # Try to drain what's left without blocking
                r, _, _ = select.select([master_fd], [], [], 0.2)
                if not r:
                    break
        
            r, _, _ = select.select([master_fd], [], [], 0.5)
            if not r:
                continue
        
            try:
                data = os.read(master_fd, 4096)
            except OSError as e:
                if e.errno == errno.EIO:
                    break
                raise
        
            if not data:
                break
        
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = data.decode(errors="replace")
        
            sys.stdout.write(text)
            sys.stdout.flush()
            logger.write_raw(text)
        
        rc = proc.wait()
        
    finally:
        try:
            os.close(master_fd)
        except Exception:
            pass

    logger.write(f"Sequence exit code: {rc}")
    if rc != 0:
        raise RuntimeError(f"Sequence {variant} failed with exit code {rc}")


# ----------------------------
# Main per-language processing
# ----------------------------

def process_language(
    *,
    tool_root: Path,
    project_root_win: str,
    lang_code: str,
    logger: JobLogger,
    brand_num: Optional[int] = None,
    lang_cfg: Optional[Dict[str, Any]] = None,
) -> None:
    lang_code = lang_code.strip().upper()

    # Resolve language folder name (prefer brand config if available)
    lang_name = resolve_lang_folder_name(lang_cfg, brand_num, lang_code) or LANG_CODE_TO_NAME.get(lang_code)
    if not lang_name:
        raise ValueError(f"Unknown language code: {lang_code}")

    # Resolve V1/V2 variant (prefer brand-specific config). Falls back to legacy sets.
    variant = resolve_variant_for_job(lang_cfg, brand_num, lang_code)
    if not variant:
        if lang_name in V1_LANGS:
            variant = "V1"
        elif lang_name in V2_LANGS:
            variant = "V2"
        else:
            # Preserve previous behavior: if unmapped, raise (so you notice missing mapping)
            raise ValueError(f"No V1/V2 mapping for language: {lang_name} ({lang_code})")

    project_root = windows_to_wsl_path(project_root_win)  # includes remap E->Z
    source_lang_dir = project_root / lang_name

    logger.write(f"Language: {lang_code} -> {lang_name} ({variant})")
    logger.write(f"Source language folder: {source_lang_dir}")

    def pick_existing(*candidates: str) -> Path:
        for c in candidates:
            p = source_lang_dir / c
            if p.exists():
                return p
        return source_lang_dir / candidates[0]

    # required sources
    src_script = pick_existing("script.txt", "scripts.txt")
    src_chunks = pick_existing("chunks.txt")
    src_en_chunks = pick_existing("en_chunks.txt")
    src_audio = pick_existing("audio.mp3")
    src_align = pick_existing("alignments.json")
    src_config = pick_existing("config.json")

    inputs_dir = tool_root / "inputs"
    clear_inputs(inputs_dir, logger)

    # destination layout
    dst_script = inputs_dir / "scripts" / "script.txt"
    dst_chunks = inputs_dir / "chunks" / "chunks.txt"
    dst_en_chunks = inputs_dir / "en_chunks" / "chunks.txt"
    dst_audio = inputs_dir / "audios" / "__dubbed__audio__.mp3"
    dst_align = inputs_dir / "audios" / "__dubbed__audio__alignments__.json"
    dst_config = inputs_dir / "config" / "config.json"
    dst_video = inputs_dir / "video" / "EnglishRendered.mp4"

    safe_copy(src_script, dst_script, logger)
    safe_copy(src_chunks, dst_chunks, logger)
    safe_copy(src_en_chunks, dst_en_chunks, logger)
    safe_copy(src_audio, dst_audio, logger)
    safe_copy(src_align, dst_align, logger)
    safe_copy(src_config, dst_config, logger)

    # Find video path from config and remap drive E->Z
    video_path = read_config_video_path(dst_config)

    if not video_path:
        # optional fallback file inside language folder
        fallback_txt = source_lang_dir / "video_path.txt"
        if fallback_txt.exists():
            video_path = remap_drive_in_path(fallback_txt.read_text(encoding="utf-8").strip())

    if not video_path:
        raise RuntimeError(f"Could not find video path in config.json (or video_path.txt). Config={dst_config}")

    video_src = windows_to_wsl_path(video_path)  # includes remap E->Z
    copy_video(video_src, dst_video, logger)

    run_sequence(tool_root, variant, logger)
    logger.write(f"DONE language {lang_name}")


# ----------------------------
# Job file selection + moves
# ----------------------------

def pick_oldest_json(folder: Path) -> Optional[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[0] if files else None


def pick_oldest_json_with_min_age(folder: Path, *, min_age_seconds: int) -> Optional[Path]:
    """
    Pick the oldest *.json in a folder, but only if it's at least min_age_seconds old.
    This prevents skipped_final from being re-picked immediately in a tight loop.
    """
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for f in files:
        age = now - f.stat().st_mtime
        if age >= min_age_seconds:
            return f
    return None


def move_into_folder(src: Path, dst_folder: Path) -> Path:
    dst_folder.mkdir(parents=True, exist_ok=True)
    dst = dst_folder / src.name
    if dst.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = dst_folder / f"{src.stem}__{stamp}.json"
    return Path(shutil.move(str(src), str(dst)))

def claim_pending_job_atomic(
    pending_job_path: Path,
    *,
    pending_dir: Path,
    pc_name: str,
) -> Optional[Path]:
    """
    Multi-worker safe claim for a Pending job.

    IMPORTANT:
    - We do NOT copy/delete.
    - We do a server-side rename (atomic) inside Pending/ so only ONE worker can win.
    - Winner gets a unique file path inside Pending/_claimed/ (not scanned by pick_oldest_json()).
    - Losers receive None and should immediately retry another job.

    This avoids the race where multiple workers pick the same pending job at the same second.
    """
    try:
        pending_dir = Path(pending_dir)
        claimed_dir = pending_dir / PENDING_CLAIMED_SUBFOLDER_NAME
        claimed_dir.mkdir(parents=True, exist_ok=True)

        # Ensure we always generate a unique target name even if timestamps match.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rnd = f"{os.getpid()}_{int(time.time() * 1000) % 100000}_{abs(hash(pc_name)) % 10000}"

        safe_pc = sanitize_filename(pc_name)
        target = claimed_dir / f"{pending_job_path.stem}__CLAIMED__{safe_pc}__{ts}__{rnd}{pending_job_path.suffix}"

        # Atomic rename/move (as long as source + target are on the same filesystem/share,
        # which they are because target is inside Pending/).
        os.replace(str(pending_job_path), str(target))
        return target
    except FileNotFoundError:
        # Another worker already claimed it.
        return None
    except PermissionError:
        # Another worker may be claiming it, or SMB transient lock.
        return None
    except OSError as e:
        if _is_remote_mount_error(e):
            raise
        return None


def choose_job_file(
    processing_workers_root: Path,
    pending_dir: Path,
    pc_name: str,
    *,
    phase: str,
    exclude_abs_paths: Set[str],
    exclude_job_ids: Dict[str, float],
) -> Tuple[Optional[Path], str]:
    """
    Series-wise order by phase:

    phase == "skipped":
      - only pick from skipped/ (each job_id only once per program run)

    phase == "processing_pending":
      - pick from processing (this PC)
      - then pending
      (and do NOT return to skipped in the same run)
    """
    pc_root = processing_workers_root / pc_name
    pc_processing_dir = pc_root
    pc_skipped_dir = pc_root / SKIPPED_SUBFOLDER_NAME

    if phase == "skipped":
        s = pick_oldest_json_excluding_job_ids(
            pc_skipped_dir,
            exclude_abs_paths=exclude_abs_paths,
            exclude_job_ids=exclude_job_ids,
        )
        if s:
            return s, "skipped"
        return None, ""

    # phase == "processing_pending"
    p = pick_oldest_json(pc_processing_dir)
    if p:
        return p, "processing"

    q = pick_oldest_json(pending_dir)
    if q:
        return q, "pending"

    # NEW: if nothing in processing/pending, also consider skipped with cooldown
    s = pick_oldest_json_excluding_job_ids(
        pc_skipped_dir,
        exclude_abs_paths=exclude_abs_paths,
        exclude_job_ids=exclude_job_ids,
    )
    if s:
        return s, "skipped"

    return None, ""




def run_sheet_update(
    done_json_path: Path,
    logger: JobLogger,
    header: str = "Process",
    value: str = "Done",
) -> None:
    """
    Runs _SheetUpdate__.py after a job is moved to done/.
    Uses the SAME python interpreter (fwenv) via sys.executable.
    Does NOT depend on tool_root.
    """
    script_path = Path(__file__).resolve().parent / "_SheetUpdate__.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Sheet update script not found: {script_path}")

    cmd = [
        sys.executable, "-u", str(script_path),
        "--done-json", str(done_json_path),
        "--header", header,
        "--value", value,
    ]

    logger.write(f"RUN SHEET UPDATE: {' '.join(cmd)}")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        logger.write("---- SHEET UPDATE STDOUT ----")
        logger.write(proc.stdout.rstrip())
        logger.write("---- /SHEET UPDATE STDOUT ----")
    if proc.stderr:
        logger.write("---- SHEET UPDATE STDERR ----")
        logger.write(proc.stderr.rstrip())
        logger.write("---- /SHEET UPDATE STDERR ----")

    if proc.returncode != 0:
        raise RuntimeError(f"_SheetUpdate__.py failed with exit code {proc.returncode}")


# ----------------------------
# CLI / Runner
# ----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", default="/mnt/z/Automated Dubbings/JOBS/Pending")
    ap.add_argument("--processing", default="/mnt/z/Automated Dubbings/JOBS/Processing")
    ap.add_argument("--loop", action="store_true", help="Run continuously (recommended)")
    ap.add_argument("--poll-seconds", type=int, default=3)
    ap.add_argument("--tool-root", default="", help="Optional explicit tool root")

    args = ap.parse_args()

    pending_dir = Path(args.pending).resolve()
    processing_dir = Path(args.processing).resolve()

    # If pending is missing because /mnt/z is down, poll up to 20 minutes.
    try:
        pending_ok = pending_dir.exists()
    except OSError as e:
        if _is_remote_mount_error(e):
            print(f"REMOTE MOUNT ERROR while checking Pending: {e}", file=sys.stderr)
            if not _poll_remote_mount(probe=pending_dir):
                print("REMOTE MOUNT did not recover within 20 minutes. Hard exiting.", file=sys.stderr)
                _hard_exit(90)
            pending_ok = pending_dir.exists()
        else:
            raise
    
    if not pending_ok:
        # Covers the “silent False” case when /mnt/z is not mounted (no exception)
        if str(pending_dir).startswith(str(REMOTE_MOUNT_ROOT)):
            print(f"Pending missing (possibly mount down). Polling for: {pending_dir}", file=sys.stderr)
            if not _poll_remote_mount(probe=pending_dir):
                print("REMOTE MOUNT did not recover within 20 minutes. Hard exiting.", file=sys.stderr)
                _hard_exit(90)
            pending_ok = pending_dir.exists()
    
    if not pending_ok:
        print(f"Pending folder not found: {pending_dir}", file=sys.stderr)
        return 2
    
    
    processing_dir.mkdir(parents=True, exist_ok=True)

    jobs_root = processing_dir.parent.resolve()

    done_root = jobs_root / "done"
    done_root.mkdir(parents=True, exist_ok=True)

    processing_workers_root = processing_dir / "0workers"
    processing_workers_root.mkdir(parents=True, exist_ok=True)

    workers_root = jobs_root / "workers"
    workers_root.mkdir(parents=True, exist_ok=True)

    tool_root = (
        Path(args.tool_root).resolve()
        if args.tool_root.strip()
        else find_tool_root(Path(__file__).resolve().parent)
    )

    # Runtime-only suppression so a job moved to skipped is not picked again in the SAME run.
    # On restart, these sets are empty, so skipped jobs WILL be picked again.
    # Runtime-only suppression by absolute path (fine for “same file”), but NOT sufficient alone.
    skip_once_this_run: Set[str] = set()

    # Strong suppression by job_id (prevents rename-loop when moving in/out of folders).
    attempted_job_ids_this_run: Dict[str, float] = {}

    # Phase control:
    #  - Start in "skipped" phase (attempt each skipped job once per program start)
    #  - After skipped is exhausted, switch to "processing_pending" and never come back to skipped in same run.
    phase: str = "skipped"

    def process_one_job() -> bool:
        nonlocal phase

        # Resolve worker PC name: WORKER_PC -> hostname -> PC_UNKNOWN
        env_pc = (os.getenv("WORKER_PC") or "").strip()
        if env_pc:
            pc_name = sanitize_filename(env_pc)
        else:
            host = (socket.gethostname() or "").strip()
            pc_name = sanitize_filename(host) if host else "PC_UNKNOWN"

        job_candidate, source_label = choose_job_file(
            processing_workers_root,
            pending_dir,
            pc_name,
            phase=phase,
            exclude_abs_paths=skip_once_this_run,
            exclude_job_ids=attempted_job_ids_this_run,
        )

        # If skipped phase has nothing left, advance phase immediately (no sleep needed).
        if not job_candidate:
            if phase == "skipped":
                phase = "processing_pending"
            return False

        pc_root = processing_workers_root / pc_name
        pc_processing_dir = pc_root
        pc_skipped_dir = pc_root / SKIPPED_SUBFOLDER_NAME
        pc_processing_dir.mkdir(parents=True, exist_ok=True)
        pc_skipped_dir.mkdir(parents=True, exist_ok=True)

        moved_job: Optional[Path] = None
        logger: Optional[JobLogger] = None
        job_id: str = "unknown_job"

        try:
            # Always move pending OR skipped into processing before attempting (lock it)
            # IMPORTANT (multi-worker safety):
            # - For "pending" jobs, first do an ATOMIC claim inside Pending/_claimed/ so only one PC can win.
            # - Then move the claimed file into this PC's processing folder.
            # This prevents multiple PCs from "cut/copy" racing on the same job at the same second.
            if source_label == "pending":
                claimed = claim_pending_job_atomic(
                    job_candidate,
                    pending_dir=pending_dir,
                    pc_name=pc_name,
                )
                if not claimed:
                    # Someone else claimed it in the same moment; immediately retry without sleeping.
                    return True
                moved_job = move_into_folder(claimed, pc_processing_dir)
            elif source_label == "skipped":
                moved_job = move_into_folder(job_candidate, pc_processing_dir)
            else:
                moved_job = job_candidate  # already in processing

            job_obj = load_job_json(moved_job)

            job_id = pick_job_id(job_obj)
            video_name = pick_video_name(job_obj)
            project_root = pick_project_root(job_obj)  # remapped E->Z
            brand_num = pick_brand_number(job_obj)
            languages = pick_languages(job_obj)

            # Mark this job_id as attempted for this program run (prevents rename loop).
            attempted_job_ids_this_run[str(job_id)] = time.time()

            job_pc = sanitize_filename(pick_pc_name(job_obj))

            worker_dir = workers_root / pc_name
            logs_dir = worker_dir / "logs"
            status_dir = worker_dir / "status"
            logs_dir.mkdir(parents=True, exist_ok=True)
            status_dir.mkdir(parents=True, exist_ok=True)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = logs_dir / f"{sanitize_filename(video_name)}__{stamp}.log"
            logger = JobLogger(log_path=log_path)

            lang_cfg = load_languages_config(tool_root, logger)
            logger.write(f"Brand detected: {brand_num if brand_num is not None else 'unknown'}")

            logger.write(f"JOB SOURCE: {source_label}")
            logger.write(f"JOB FILE (processing): {moved_job}")
            logger.write(f"Tool root: {tool_root}")
            logger.write(f"WORKER PC (env/derived): {pc_name}")
            logger.write(f"JOB PC (from json): {job_pc}")
            logger.write(f"VIDEO: {video_name}")
            logger.write(f"JOB_ID: {job_id}")
            logger.write(f"project_root (WIN, remapped): {project_root}")
            logger.write(f"languages in job: {languages}")

            clear_tool_work_folders(tool_root, logger)

            # If job json is broken, send it to skipped and move on.
            if not project_root or not languages:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.write("ERROR: invalid job json (missing project_root or languages) -> moving to skipped")
                try:
                    status_path = status_file_path(status_dir, video_name)
                    ensure_status_header(status_path, video_name, job_id, pc_name)
                    append_status_line(status_path, f"SKIPPED INVALID_JOB_JSON {ts}")
                except Exception:
                    pass

                final_path = move_into_folder(moved_job, pc_skipped_dir)
                skip_once_this_run.add(str(final_path.resolve()))
                logger.write(f"MOVED JOB JSON TO SKIPPED: {final_path}")
                logger.write("JOB FINISHED")
                after_job_pause(logger, 180)
                return True

            # ---- status file (resume/skip) ----
            status_path = status_file_path(status_dir, video_name)
            ensure_status_header(status_path, video_name, job_id, pc_name)

            done_langs = read_status_done_langs(status_path)
            failed_langs = read_status_failed_langs(status_path)

            # Retry rule:
            # - If source is "skipped", we IGNORE previous FAIL markers and retry any language not DONE.
            # - If source is "processing"/"pending", we keep the “no retry if FAIL” behavior.
            retry_failed = (source_label == "skipped")

            if retry_failed:
                remaining_actionable = [c for c in languages if c.upper() not in done_langs]
            else:
                remaining_actionable = [
                    c for c in languages
                    if c.upper() not in done_langs and c.upper() not in failed_langs
                ]

            logger.write(f"status file: {status_path}")
            logger.write(f"already DONE: {sorted(done_langs) if done_langs else 'none'}")
            logger.write(f"already FAIL: {sorted(failed_langs) if failed_langs else 'none'}")
            logger.write(f"remaining actionable: {remaining_actionable if remaining_actionable else 'none'}")

            # If nothing actionable:
            # - If all languages are done -> complete to done
            # - Else -> move to skipped and move on
            all_done_now = all(c.upper() in done_langs for c in languages)
            if not remaining_actionable and not all_done_now:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                pending_codes = [c for c in languages if c.upper() not in done_langs]
                append_status_line(status_path, f"SKIPPED NO_ACTIONABLE PENDING={pending_codes} {ts}")
                logger.write(f"VIDEO SKIPPED: no remaining actionable languages. Pending={pending_codes}")

                final_path = move_into_folder(moved_job, pc_skipped_dir)
                skip_once_this_run.add(str(final_path.resolve()))
                logger.write(f"MOVED JOB JSON TO SKIPPED: {final_path}")
                logger.write("JOB FINISHED")
                after_job_pause(logger, 180)
                return True

            # ---- process remaining actionable languages ----
            for idx, code in enumerate(remaining_actionable):
                code = code.strip().upper()
                lang_folder = resolve_lang_folder_name(lang_cfg, brand_num, code) or LANG_CODE_TO_NAME.get(code, "unknown")

                done_langs = read_status_done_langs(status_path)
                failed_langs = read_status_failed_langs(status_path)

                if code in done_langs:
                    logger.write(f"Skip {code} ({lang_folder}) - already DONE.")
                    continue

                if (not retry_failed) and (code in failed_langs):
                    logger.write(f"Skip {code} ({lang_folder}) - already FAIL (no retry).")
                    continue

                start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                append_status_line(status_path, f"START {code} {lang_folder} {start_ts}")

                try:
                    process_language(
                        tool_root=tool_root,
                        project_root_win=project_root,
                        lang_code=code,
                        logger=logger,
                        brand_num=brand_num,
                        lang_cfg=lang_cfg,
                    )
                    end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    append_status_line(status_path, f"DONE {code} {lang_folder} {end_ts}")

                except Exception as e:
                    # If /mnt/z dropped, DO NOT mark FAIL; bubble up so main loop can poll+resume.
                    if _is_remote_mount_error(e):
                        raise
                
                    fail_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    append_status_line(status_path, f"FAIL {code} {lang_folder} {fail_ts} :: {e}")
                    logger.write(f"FAILED language {code} ({lang_folder}): {e}")

                    return True

                if idx < len(remaining_actionable) - 1:
                    between_languages_pause_and_cleanup(tool_root, logger, pause_seconds=180)

            # ---- completion check ----
            done_langs_final = read_status_done_langs(status_path)
            failed_langs_final = read_status_failed_langs(status_path)
            all_done = all(c.upper() in done_langs_final for c in languages)

            if all_done:
                append_status_line(status_path, f"COMPLETE ALL_LANGS {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.write("VIDEO COMPLETE: all languages done.")

                final_path = move_into_folder(moved_job, done_root)
                logger.write(f"MOVED JOB JSON TO DONE: {final_path}")

                try:
                    run_sheet_update(final_path, logger, header="Process", value="Done")
                except Exception as e:
                    logger.write(f"WARNING: Sheet update failed: {e}")

            else:
                # IMPORTANT CHANGE:
                # You want “attempt once then move on”.
                # So we ALWAYS move incomplete jobs to skipped (even if some actionable remain),
                # and we DO NOT keep them in processing to be re-picked in the same run.
                pending_codes = [c for c in languages if c.upper() not in done_langs_final]
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                append_status_line(
                    status_path,
                    f"INCOMPLETE PENDING={pending_codes} FAILED={sorted(failed_langs_final)} {ts}"
                )
                logger.write(f"VIDEO INCOMPLETE: moving to skipped for next program start. Pending={pending_codes}")

                final_path = move_into_folder(moved_job, pc_skipped_dir)
                skip_once_this_run.add(str(final_path.resolve()))
                logger.write(f"MOVED JOB JSON TO SKIPPED: {final_path}")

            logger.write("JOB FINISHED")
            after_job_pause(logger, 180)
            return True

        except Exception as e:
            # ✅ If /mnt/z dropped (Host down / mount removed), bubble up to the outer loop
            # so it can poll up to 20 minutes, then hard-exit if not recovered.
            if _is_remote_mount_error(e):
                raise
        
            # Hard safety: never crash the whole loop; move the job to skipped if possible.
            if logger:
                logger.write(f"FATAL ERROR while processing job: {e}")

            if moved_job and moved_job.exists():
                try:
                    pc_root = processing_workers_root / sanitize_filename(os.getenv("WORKER_PC") or socket.gethostname() or "PC_UNKNOWN")
                    pc_skipped_dir = pc_root / SKIPPED_SUBFOLDER_NAME
                    pc_skipped_dir.mkdir(parents=True, exist_ok=True)

                    final_path = move_into_folder(moved_job, pc_skipped_dir)
                    skip_once_this_run.add(str(final_path.resolve()))
                    if logger:
                        logger.write(f"MOVED JOB JSON TO SKIPPED (after fatal): {final_path}")
                except Exception:
                    pass

            return True

    if args.loop:
        while True:
            try:
                did = process_one_job()
            except OSError as e:
                if _is_remote_mount_error(e):
                    print(f"REMOTE MOUNT ERROR during job processing: {e}", file=sys.stderr)
                    if not _poll_remote_mount(probe=pending_dir):
                        print("REMOTE MOUNT did not recover within 20 minutes. Hard exiting.", file=sys.stderr)
                        _hard_exit(90)
                    # recovered -> retry loop iteration
                    continue
                raise
            if not did:
                # If we just finished skipped phase (i.e. no skipped left), immediately continue
                # so we start processing/then pending without waiting.
                if phase == "processing_pending":
                    time.sleep(max(1, args.poll_seconds))
                else:
                    # phase just switched inside process_one_job
                    continue
    else:
        try:
            did = process_one_job()
        except OSError as e:
            if _is_remote_mount_error(e):
                print(f"REMOTE MOUNT ERROR during job processing: {e}", file=sys.stderr)
                if not _poll_remote_mount(probe=pending_dir):
                    print("REMOTE MOUNT did not recover within 20 minutes. Hard exiting.", file=sys.stderr)
                    _hard_exit(90)
                did = process_one_job()
            else:
                raise        
        if not did:
            print("No jobs found for this run (skipped phase exhausted, no processing/pending).")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())

