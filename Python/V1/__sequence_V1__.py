#!/usr/bin/env python3
import sys
import subprocess
import time
import json
import socket
import urllib.request
import urllib.error
import os
import traceback
from dotenv import load_dotenv
from collections import deque
from pathlib import Path
from threading import Thread

# ==========================================================
# CONFIG: env-driven webhook (do NOT hardcode)
# ==========================================================

load_dotenv(override=True)

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "").replace("\r", "").replace("\n", "").strip()
if not N8N_WEBHOOK_URL:
    raise RuntimeError("Missing required env var: N8N_WEBHOOK_URL")

# Optional (only if you set Header Auth in n8n)
N8N_WEBHOOK_TOKEN_DEFAULT = ""

# Optional: webhook timeout
N8N_WEBHOOK_TIMEOUT_SECONDS_DEFAULT = 10
# ==========================================================

BASE = Path(__file__).resolve().parent

# Read config.json for video_name + language
CONFIG_PATH = BASE.parent.parent / "inputs" / "config" / "config.json"

# Folder conventions (inside same folder as this runner)
TIMELINE_DIRS = [
    BASE / "TimelineEditors",
]
STATES_DIRS = [
    BASE / "states",
]

# Global search dirs (root + the known subfolders)
SEARCH_DIRS = [BASE] + TIMELINE_DIRS + STATES_DIRS


def _ts() -> str:
    # Local time in WSL
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _print_ts(msg: str, is_err: bool = False):
    # One line in, one line out (always timestamped)
    line = f"[{_ts()}] {msg}"
    if is_err:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    else:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _cfg_str(cfg: dict, key: str) -> str:
    return str(cfg.get(key, "") or "").strip()


def _limit_text(s: str, max_chars: int = 200000) -> tuple[str, bool]:
    s = s or ""
    if len(s) <= max_chars:
        return s, False
    # keep the end (most useful for debugging)
    return s[-max_chars:], True


def _list_py_files():
    out = []
    for d in SEARCH_DIRS:
        if d.exists() and d.is_dir():
            py_files = sorted([p.name for p in d.glob("*.py")])
            if py_files:
                out.append(f"\n[{d.relative_to(BASE) if d != BASE else '.'}]")
                out.extend([f"  - {x}" for x in py_files])
    return "\n".join(out) if out else "(No .py files found in root/TimelineEditors/States)"


def pick_path(*names: str) -> Path:
    """
    Find a script by name. Rules:
      - Always checks BASE first (backward compatible).
      - If it's a timeline fcpxml script, prefers TimelineEditors/.
      - If it's a states script, prefers states/.
      - Otherwise searches all known dirs.
    """
    for n in names:
        n = str(n).strip()

        # Prefer subfolders based on naming convention
        if n.startswith("__timeline_fcpxml_"):
            preferred_dirs = TIMELINE_DIRS + [BASE]  # prefer TimelineEditors, fallback root
        elif n.startswith("__states_"):
            preferred_dirs = STATES_DIRS + [BASE]    # prefer states, fallback root
        else:
            preferred_dirs = [BASE] + TIMELINE_DIRS + STATES_DIRS

        for d in preferred_dirs:
            p = d / n
            if p.exists():
                return p

    raise FileNotFoundError(
        "Could not find any of these:\n  - " + "\n  - ".join(names) +
        "\n\nPython files found:\n" + _list_py_files()
    )


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def notify_n8n_webhook(payload: dict) -> bool:
    """
    Posts JSON payload to n8n webhook.

    Env vars (optional):
      - N8N_WEBHOOK_URL
      - N8N_WEBHOOK_TOKEN
      - N8N_WEBHOOK_TIMEOUT_SECONDS
    """
    url = (_env("N8N_WEBHOOK_URL", "") or N8N_WEBHOOK_URL or "").replace("\r", "").replace("\n", "").strip()
    if not url:
        return False

    token = (_env("N8N_WEBHOOK_TOKEN", "") or (N8N_WEBHOOK_TOKEN_DEFAULT or "")).replace("\r", "").replace("\n", "").strip()

    timeout_raw = _env("N8N_WEBHOOK_TIMEOUT_SECONDS", "")
    try:
        timeout_s = int(timeout_raw) if timeout_raw else int(N8N_WEBHOOK_TIMEOUT_SECONDS_DEFAULT)
    except Exception:
        timeout_s = int(N8N_WEBHOOK_TIMEOUT_SECONDS_DEFAULT)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "sequence-runner/1.0",
    }
    if token:
        headers["X-Webhook-Token"] = token

    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            _ = resp.read()  # consume
        return True
    except urllib.error.HTTPError as e:
        _print_ts(f"[Webhook] HTTPError {e.code}: {e.reason}", is_err=True)
        try:
            body = e.read().decode("utf-8", errors="replace")
            _print_ts(f"[Webhook] Response body: {body}", is_err=True)
        except Exception:
            pass
        return False
    except Exception as e:
        _print_ts(f"[Webhook] Failed to send webhook: {e}", is_err=True)
        return False


def build_pipeline():
    # ==========================================================
    # PIPELINE ORDER (unchanged)
    # ==========================================================
    STEP1  = pick_path("__copy_project_file__.py")
    STEP1_5 = pick_path("__audio_conversion__.py")
    STEP1_7 = pick_path("__premiere_hard_close__.py")
    STEP2  = pick_path("__run_premiere_before_xml__.py")
    STEP2_5 = pick_path("__premiere_execution_validation_before__.py")
    STEP3  = pick_path("__xml_validation__.py")
    STEP4  = pick_path("__cuts_detections__.py")
    STEP5  = pick_path("__v1_cuts_implementor__.py")
    STEP6  = pick_path("__timeline_detections__.py")
    STEP6_5 = pick_path("__chunks_formation__.py")
    STEP7  = pick_path("__chunks_timestamps_cal__.py")
    STEP8  = pick_path("__timeline_manager__.py")
    STEP9  = pick_path("__timestamps_V1_A1_A2__.py")
    STEP10 = pick_path("__extract_interview_timestamps__.py")
    STEP11 = pick_path("__ffmpeg_clips_extraction__.py")
    STEP11_5 = pick_path("__interviews_validation__.py")
    STEP12 = pick_path("__interviews_dubbing__.py")
    STEP13 = pick_path("__words_segments__.py")

    STEP14 = pick_path("__timeline_fcpxml_V1__.py")
    STEP15 = pick_path("__states_disclaimers__.py")
    STEP16 = pick_path("__timeline_fcpxml_V2__.py")
    STEP17 = pick_path("__timeline_fcpxml_V3__.py")
    STEP18 = pick_path("__timestamps_interviews__.py")
    STEP19 = pick_path("__timeline_fcpxml_V4__.py")

    STEP20 = pick_path("__segments_similarity__.py")
    STEP21 = pick_path("__gemini_translation_cli__.py")
    STEP22 = pick_path("__gemini_segments_validations__.py")
    STEP22_5 = pick_path("__gemini_results_validation__.py")
    STEP23 = pick_path("__titles_objects_merged__.py")
    STEP24 = pick_path("__extract_V3_V4_timestamps__.py")
    STEP25 = pick_path("__timestamps_titles_cal__.py")
    STEP26 = pick_path("__merge_titles_segments__.py")

    STEP27 = pick_path("__extract_lowerthirds__.py")
    STEP28 = pick_path("__timestamps_lowerthirds_cal__.py")

    STEP29 = pick_path("__extract_A6_A7_timestamps__.py")
    STEP30 = pick_path("__timestamps_SFX_cal__.py")

    STEP31 = pick_path("__timeline_fcpxml_V7__.py")
    STEP32 = pick_path("__timeline_fcpxml_V8__.py")

    STEP33 = pick_path("__copy_xml_to_windows__.py")

    STEP34 = pick_path("__timeline_transitions_detection__.py")
    STEP35 = pick_path("__transitions_timestamps_cal__.py")
    STEP36 = pick_path("__copy_transitions_segments_to_windows__.py")

    STEP36_5 = pick_path("__premiere_hard_close__.py")
    STEP37 = pick_path("__run_premiere_after_xml__.py")
    STEP38 = pick_path("__premiere_execution_validation__.py")

    return [
        STEP1, STEP1_5, STEP1_7, STEP2, STEP2_5, STEP3,
        STEP4, STEP5, STEP6, STEP6_5,
        STEP7,
        STEP8,
        STEP9, STEP10,
        STEP11, 
        STEP11_5, STEP12, 
        STEP13,
        STEP14, STEP15, STEP16, 
        STEP17,
        STEP18, 
        STEP19,
        STEP20, STEP21, STEP22, STEP22_5, STEP23,
        STEP24, STEP25, STEP26,
        STEP27, STEP28,
        STEP29, STEP30,
        STEP31, STEP32,
        STEP33,
        STEP34, STEP35, STEP36, STEP36_5,
        STEP37, STEP38
    ]


def run_with_live_capture(script_path: Path, tail_lines: int = 80) -> dict:
    """
    Runs a python script with live stdout/stderr printing,
    while keeping a tail buffer AND full buffers for webhook payloads.
    Returns dict with returncode and captured tails + full combined output.
    """ 
    rel = script_path.relative_to(BASE) if script_path.is_absolute() else script_path
    _print_ts("=" * 60)
    _print_ts(f"▶ RUNNING: {rel}")
    _print_ts("=" * 60)
    _print_ts("")  # blank line, still timestamped

    cmd = [sys.executable, str(script_path)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    out_tail = deque(maxlen=tail_lines)
    err_tail = deque(maxlen=tail_lines)

    out_all = []
    err_all = []

    def _pump(stream, is_err: bool):
        for line in iter(stream.readline, ""):
            raw = line.rstrip("\n")
            stamped = f"[{_ts()}] {raw}"
        
            if is_err:
                sys.stderr.write(stamped + "\n")
                sys.stderr.flush()
                err_tail.append(stamped)
                err_all.append(stamped)
            else:
                sys.stdout.write(stamped + "\n")
                sys.stdout.flush()
                out_tail.append(stamped)
                out_all.append(stamped)
        
        stream.close()


    t_out = Thread(target=_pump, args=(proc.stdout, False), daemon=True)
    t_err = Thread(target=_pump, args=(proc.stderr, True), daemon=True)
    t_out.start()
    t_err.start()

    returncode = proc.wait()
    t_out.join(timeout=1)
    t_err.join(timeout=1)

    # "Complete terminal output" (best-effort): stdout then stderr
    combined_full = ""
    stdout_full = "\n".join(out_all).strip()
    stderr_full = "\n".join(err_all).strip()
    if stdout_full and stderr_full:
        combined_full = stdout_full + "\n" + stderr_full
    else:
        combined_full = stdout_full or stderr_full

    combined_limited, combined_truncated = _limit_text(combined_full)

    return {
        "returncode": returncode,
        "stdout_tail": "\n".join(out_tail).strip(),
        "stderr_tail": "\n".join(err_tail).strip(),
        "combined_output": combined_limited,
        "combined_truncated": bool(combined_truncated),
    }


def main() -> int:
    t0 = time.time()
    worker = _env("WORKER_NAME", "") or socket.gethostname()

    cfg = _load_config()
    video_name = _cfg_str(cfg, "video_name")
    language = _cfg_str(cfg, "language")

    # Build pipeline inside main so we can webhook on FileNotFoundError too
    try:
        pipeline = build_pipeline()
    except Exception as e:
        tb = traceback.format_exc()
        tb_limited, tb_truncated = _limit_text(tb)

        payload = {
            "event": "sequence_bootstrap_failed",
            "status": "failed",
            "video_name": video_name,
            "language": language,
            "worker": worker,
            "base_dir": str(BASE),
            "error_type": type(e).__name__,
            "error_message": str(e),
            "body": {
                "stderr_tail": tb_limited,
                "stderr_tail_truncated": bool(tb_truncated),
            },
            "ts_epoch": int(time.time()),
        }
        sent = notify_n8n_webhook(payload)
        _print_ts("❌ PIPELINE BUILD FAILED", is_err=True)
        if sent:
            _print_ts("↳ Webhook notified.", is_err=True)
        raise

    total = len(pipeline)

    for i, p in enumerate(pipeline, start=1):
        start_step = time.time()
        result = run_with_live_capture(p)

        if result["returncode"] != 0:
            elapsed_total = time.time() - t0
            elapsed_step = time.time() - start_step
            rel = str(p.relative_to(BASE)) if p.is_absolute() else str(p)

            payload = {
                "event": "sequence_step_failed",
                "status": "failed",
                "video_name": video_name,
                "language": language,
                "worker": worker,
                "base_dir": str(BASE),
                "step_index": i,
                "step_total": total,
                "failed_step": rel,
                "script": rel,
                "script_abs": str(p),
                "returncode": result["returncode"],
                "elapsed_step_seconds": round(elapsed_step, 3),
                "elapsed_total_seconds": round(elapsed_total, 3),

                # keep tails (useful quick preview)
                "stdout_tail": result["stdout_tail"],
                "stderr_tail": result["stderr_tail"],

                # REQUIRED: full terminal failed file output here
                "body": {
                    "stderr_tail": result["combined_output"],
                    "stderr_tail_truncated": result["combined_truncated"],
                },

                "ts_epoch": int(time.time()),
            }

            sent = notify_n8n_webhook(payload)

            _print_ts("=" * 60, is_err=True)
            _print_ts(f"❌ FAILED at step {i}/{total}: {rel}", is_err=True)
            if sent:
                _print_ts("↳ n8n webhook notified.", is_err=True)
            else:
                _print_ts("↳ n8n webhook NOT notified (check URL/token/connectivity).", is_err=True)
            _print_ts("=" * 60, is_err=True)
            return result["returncode"] or 1

    # ✅ SUCCESS: send completion webhook
    elapsed = time.time() - t0
    payload = {
        "event": "sequence_completed",
        "status": "success",
        "video_name": video_name,
        "language": language,
        "worker": worker,
        "base_dir": str(BASE),
        "step_total": total,
        "elapsed_total_seconds": round(elapsed, 3),
        "body": {
            "stderr_tail": "",
            "stderr_tail_truncated": False,
        },
        "ts_epoch": int(time.time()),
    }
    sent = notify_n8n_webhook(payload)

    mins = int(elapsed // 60)
    secs = round(elapsed % 60, 2)
    _print_ts("✅ ALL PYTHON STEPS DONE")
    _print_ts(f"Total Time taken: {mins} minutes {secs} seconds.")
    if sent:
        _print_ts("↳ n8n webhook notified: sequence_completed")
    else:
        _print_ts("↳ n8n webhook not configured/not sent (N8N_WEBHOOK_URL missing)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
