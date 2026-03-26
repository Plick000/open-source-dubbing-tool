#!/usr/bin/env python3
import sys
import subprocess
import time
import re
import json
import os
import traceback
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional
from urllib import request, error as urlerror

BASE = Path(__file__).resolve().parent



# ------------------------------------------------------------------
# N8N webhook reporting (non-breaking add-on)
# ------------------------------------------------------------------
CONFIG_PATH = BASE.parent.parent / "inputs" / "config" / "config.json"
ENV_CANDIDATES = [
    BASE / ".env",
    BASE.parent / ".env",
    BASE.parent.parent / ".env",
]

def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _print_ts(msg: str, is_err: bool = False):
    line = f"[{_ts()}] {msg}"
    if is_err:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    else:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

for env_path in ENV_CANDIDATES:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        _print_ts(f"Loaded .env from: {env_path}")
        break
else:
    load_dotenv(override=True)
    _print_ts("No explicit .env file found near runner. Falling back to default dotenv search.", is_err=True)


def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _get_webhook_url() -> str:
    for k in (
        "N8N_WEBHOOK_URL",
        "N8N_WEBHOOK",
        "WEBHOOK_URL",
        "VV_N8N_WEBHOOK_URL",
        "VV_N8N_WEBHOOK",
    ):
        v = os.getenv(k, "")
        if isinstance(v, str) and v.strip():
            _print_ts(f"Webhook env found in: {k}")
            return v.strip()

    _print_ts(
        "⚠ No webhook env variable found. Checked: "
        "N8N_WEBHOOK_URL, N8N_WEBHOOK, WEBHOOK_URL, VV_N8N_WEBHOOK_URL, VV_N8N_WEBHOOK",
        is_err=True,
    )
    return ""


def _post_json(url: str, payload: dict, timeout_sec: int = 20) -> bool:
    if not url:
        _print_ts("⚠ N8N webhook URL not found (config/env). Skipping webhook POST.", is_err=True)
        return False

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "sequence-runner/1.0",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
            _print_ts(f"✅ Webhook POST success. HTTP {status}")
            if body.strip():
                _print_ts(f"Webhook response: {body[:1000]}")
        return True

    except urlerror.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        _print_ts(f"⚠ Webhook HTTPError: {e.code} {e.reason}", is_err=True)
        if body:
            _print_ts(f"⚠ Webhook response: {body[:2000]}", is_err=True)
        return False

    except Exception as e:
        _print_ts(f"⚠ Webhook error: {e}", is_err=True)
        return False

def _build_base_payload(cfg: dict) -> dict:
    video_name = str(cfg.get("video_name", "") or "")
    language = str(cfg.get("language", "") or "")
    video_brand = str(cfg.get("video_brand", "") or "")
    return {
        "video_name": video_name,
        "language": language,
        "video_brand": video_brand,
        "sequence_file": str(Path(__file__).name),
        "config_path": str(CONFIG_PATH),
    }

def _limit_text(s: str, max_chars: int = 200000) -> tuple[str, bool]:
    s = s or ""
    if len(s) <= max_chars:
        return s, False
    # keep the end (most relevant)
    return s[-max_chars:], True


# Folder conventions (inside same folder as this runner)
TIMELINE_DIRS = [
    BASE / "TimelineEditors",
]
STATES_DIRS = [
    BASE / "states",
]

# Global search dirs (root + the known subfolders)
SEARCH_DIRS = [BASE] + TIMELINE_DIRS + STATES_DIRS

t0 = time.time()

MISSING_PATTERNS = [
    r"Missing images:\s*(\d+)",
    r"missing_images:\s*(\d+)",
    r"Missing language(?:s)?\s*:\s*(\d+)",
    r"missing_language(?:s)?\s*:\s*(\d+)",
]

OUTPUT_FILE_PATTERNS = [
    r"OUTPUT_FILE:\s*(.+)",
    r"Wrote .* into\s+(.+)",
    r"\(Disk path used:\s*(.+)\)",
]

def _parse_missing_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    for pat in MISSING_PATTERNS:
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        if matches:
            # take the LAST one (final summary is what we care about)
            try:
                return int(matches[-1])
            except Exception:
                continue
    return None


def _parse_output_json_path(text: str) -> Optional[Path]:
    if not text:
        return None
    for pat in OUTPUT_FILE_PATTERNS:
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        if matches:
            raw = str(matches[-1]).strip().strip('"').strip("'")
            # Some logs may have trailing punctuation; sanitize lightly
            raw = raw.rstrip().rstrip(".")
            try:
                return Path(raw).expanduser().resolve()
            except Exception:
                return Path(raw)
    return None


def _count_missing_from_titles_json(json_path: Path) -> Optional[int]:
    """
    Fallback if logs don't expose missing count reliably.
    Counts entries where image_name OR image_path is empty (common convention).
    """
    try:
        if not json_path.exists():
            return None
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None

        missing = 0
        for row in data:
            if not isinstance(row, dict):
                continue
            # Common keys used by titles image pipeline
            if ("image_name" in row and not str(row.get("image_name", "")).strip()) or \
               ("image_path" in row and not str(row.get("image_path", "")).strip()):
                missing += 1
        return missing
    except Exception:
        return None

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
      - Normal scripts are expected in BASE (sequence folder); we still allow fallback search in known subfolders.
      - __timeline_fcpxml_* prefers TimelineEditors/
      - __states_* prefers States/
    """
    for n in names:
        n = str(n).strip()

        # Prefer subfolders based on naming convention
        if n.startswith("__timeline_fcpxml_"):
            preferred_dirs = TIMELINE_DIRS + [BASE]  # prefer TimelineEditors, fallback root
        elif n.startswith("__states_"):
            preferred_dirs = STATES_DIRS + [BASE]    # prefer States, fallback root
        else:
            preferred_dirs = [BASE]                  # normal scripts in sequence folder

        for d in preferred_dirs:
            p = d / n
            if p.exists():
                return p

        # Optional fallback search (helps while reorganizing)
        for d in SEARCH_DIRS:
            p = d / n
            if p.exists():
                return p

    raise FileNotFoundError(
        "Could not find any of these:\n  - " + "\n  - ".join(names) +
        "\n\nPython files found:\n" + _list_py_files()
    )


# ==========================================================
# UPDATED PIPELINE (your exact order)
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

STEP24 = pick_path("__states_titles__.py")
STEP25 = pick_path("__batch_titles_rendering__.py")
STEP26 = pick_path("__extract_V3_V4_timestamps__.py")
STEP27 = pick_path("__timestamps_titles_cal__.py")

STEP28 = pick_path("__extract_lowerthirds__.py")
STEP29 = pick_path("__timestamps_lowerthirds_cal__.py")
STEP30 = pick_path("__states_lowerthirds__.py")
STEP31 = pick_path("__batch_lowerthirds_rendering__.py")
STEP32 = pick_path("__titles_validation__.py")
STEP33 = pick_path("__lowerthirds_validation__.py")

STEP34 = pick_path("__timeline_fcpxml_V5__.py")

STEP35 = pick_path("__timeline_fcpxml_V6__.py")

STEP36 = pick_path("__extract_A6_A7_timestamps__.py")
STEP37 = pick_path("__timestamps_SFX_cal__.py")
STEP38 = pick_path("__timeline_fcpxml_V7__.py")
STEP39 = pick_path("__timeline_fcpxml_V8__.py")

STEP40 = pick_path("__timeline_transitions_detection__.py")
STEP41 = pick_path("__transitions_timestamps_cal__.py")

STEP42 = pick_path("__copy_xml_to_windows__.py")
STEP43 = pick_path("__copy_transitions_segments_to_windows__.py")
STEP43_5 = pick_path("__premiere_hard_close__.py")
STEP44 = pick_path("__run_premiere_after_xml__.py")
STEP45 = pick_path("__premiere_execution_validation__.py")

PIPELINE = [
    STEP1, STEP1_5, STEP1_7,
    STEP2, STEP2_5, STEP3,
    STEP4, STEP5, STEP6, STEP6_5,
    STEP7,
    STEP8,
    STEP9, STEP10,
    STEP11, 
    STEP11_5, STEP12, 
    STEP13,
    STEP14, STEP15, STEP16, STEP17,
    STEP18, STEP19,
    STEP20, STEP21, STEP22, STEP22_5, STEP23,
    STEP24,
    STEP25,
    STEP26, STEP27, STEP28, STEP29,
    STEP30,
    STEP31,
    STEP32, STEP33, STEP34, STEP35,
    STEP36, STEP37, STEP38, STEP39,
    STEP40, STEP41,
    STEP42, STEP43, STEP43_5,
    STEP44, STEP45
]


class StepFailed(Exception):
    def __init__(self, script_path: Path, returncode: int, output: str):
        super().__init__(f"{script_path.name} failed with exit code {returncode}")
        self.script_path = script_path
        self.returncode = returncode
        self.output = output


def run_capture_tee(script_path: Path) -> str:
    """
    Runs script while:
      - showing live output in console
      - capturing combined stdout/stderr into a single string

    On non-zero exit, raises StepFailed with captured output.
    """
    rel = script_path.relative_to(BASE) if script_path.is_absolute() else script_path
    _print_ts("=" * 60)
    _print_ts(f"▶ RUNNING: {rel}")
    _print_ts("=" * 60)
    _print_ts("")

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    out_lines = []      # stamped (for webhook)
    raw_lines = []      # raw (for parsing)
    assert proc.stdout is not None
    for line in proc.stdout:
        raw = line.rstrip("\n")
        raw_lines.append(raw + "\n")
    
        stamped = f"[{_ts()}] {raw}"
        sys.stdout.write(stamped + "\n")
        sys.stdout.flush()
        out_lines.append(stamped + "\n")   # capture stamped for webhook
    
    rc = proc.wait()
    output_text = "".join(out_lines)       # stamped output (for webhook)
    raw_text = "".join(raw_lines)          # raw output (for parsing)
    

    if rc != 0:
        raise StepFailed(script_path=script_path, returncode=rc, output=output_text)
    
    return raw_text


def main():
    cfg = _load_config()
    webhook_url = _get_webhook_url()
    _print_ts(f"Webhook URL detected: {'YES' if webhook_url else 'NO'}")
    if webhook_url:
        _print_ts(f"Webhook URL preview: {webhook_url[:80]}...")    
    base_payload = _build_base_payload(cfg)

    failed = False
    failed_step = ""
    stderr_tail = ""
    stderr_truncated = False
    error_message = ""

    try:
        for p in PIPELINE:
            # ✅ Special validation right after STEP23 (__titles_objects_merged__.py)
            if p == STEP23:
                output_text = run_capture_tee(p)

                missing = _parse_missing_from_text(output_text)

                # If not found in logs, try to locate the output JSON and count missing
                out_json = _parse_output_json_path(output_text)
                if missing is None and out_json is not None:
                    missing = _count_missing_from_titles_json(out_json)

                if missing is None:
                    raise RuntimeError(
                        "STEP23 validation failed: Could not determine missing count from "
                        "__titles_objects_merged__.py output/logs, and JSON fallback did not work."
                    )

                if missing > 0:
                    raise RuntimeError(
                        f"STEP23 FAILED: __titles_objects_merged__.py reports missing={missing}. "
                        "Stopping pipeline to prevent bad renders."
                    )

                # missing == 0 -> continue pipeline
                continue

            # All other steps (same order/logic) but now tee+capture for webhook on failure
            run_capture_tee(p)

        elapsed = time.time() - t0
        mins = int(elapsed // 60)
        secs = round(elapsed % 60, 2)
        _print_ts("✅ ALL PYTHON STEPS DONE")
        _print_ts(f"Total Time taken: {mins} minutes {secs} seconds.")

    except StepFailed as e:
        failed = True
        failed_step = str(e.script_path.name)
        error_message = str(e)

        # The user asked to send the complete terminal output in body.stderr_tail
        stderr_tail, stderr_truncated = _limit_text(e.output)

    except Exception as e:
        failed = True
        failed_step = "unknown"
        error_message = str(e)

        # Send traceback to webhook as "terminal output"
        tb = traceback.format_exc()
        stderr_tail, stderr_truncated = _limit_text(tb)

    finally:
        elapsed = time.time() - t0
        payload = {
            **base_payload,
            "status": "failed" if failed else "success",
            "failed_step": failed_step if failed else "",
            "error": error_message if failed else "",
            # IMPORTANT: user requirement: variable named body.stderr_tail
            "body": {
                "stderr_tail": stderr_tail if failed else "",
                "stderr_tail_truncated": bool(stderr_truncated) if failed else False,
            },
            "duration_seconds": round(elapsed, 3),
            "timestamp": time.time(),
        }
        posted = _post_json(webhook_url, payload)
        if posted:
            _print_ts("✅ Notified to Webhook!")
        else:
            _print_ts("⚠ Failed to notify Webhook!", is_err=True)
    # propagate status to caller (other file)
    if failed:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
