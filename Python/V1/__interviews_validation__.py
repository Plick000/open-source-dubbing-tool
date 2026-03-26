#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from dotenv import load_dotenv
from faster_whisper import WhisperModel


# =========================
# PATHS (match your project)
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Same folder your ffmpeg extraction writes to:
# OUTPUT_DIR = PROJECT_ROOT / "output" / "samples" / "interviews"
# :contentReference[oaicite:2]{index=2}
INTERVIEWS_DIR = PROJECT_ROOT / "output" / "samples" / "interviews"

DELETED_DIR_DEFAULT = INTERVIEWS_DIR / "_deleted"
TRANSCRIPTS_DIR_DEFAULT = PROJECT_ROOT / "output" / "samples" / "interviews__transcripts"
REPORT_JSON_DEFAULT = PROJECT_ROOT / "output" / "JSON" / "A6B__interviews_speech_validation__.json"


# =========================
# ENV + WHISPER (large-v3) – SAME STYLE AS YOUR SCRIPT
# =========================
load_dotenv()

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

def resolve_whisper_model_dir(shared_dir: str) -> str:
    """
    If WHISPER_LOCAL_CACHE is set, copy the model from shared_dir -> local cache once,
    then load from local for fast startup. Uses a simple lock folder to prevent overlap.
    """
    shared = Path(shared_dir)
    local_raw = (os.getenv("WHISPER_LOCAL_CACHE", "") or "").strip()
    if not local_raw:
        return str(shared)

    local = Path(local_raw)

    # Already cached?
    if (local / "model.bin").is_file():
        return str(local)

    # Simple lock to prevent multiple processes copying at once
    lock_dir = Path(str(local) + ".lock")

    while True:
        try:
            lock_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            time.sleep(0.5)
            if (local / "model.bin").is_file():
                return str(local)

    try:
        tmp = Path(str(local) + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(shared, tmp, dirs_exist_ok=True)

        if local.exists():
            shutil.rmtree(local, ignore_errors=True)

        tmp.rename(local)
        return str(local)
    finally:
        try:
            lock_dir.rmdir()
        except Exception:
            pass


def default_whisper_model_dir():
    # Windows default
    win_path = r"Z:\Automated Dubbings\_models\whisper\faster-whisper-large-v3"
    # WSL/Linux default path for the same Z: drive
    wsl_path = "/mnt/z/Automated Dubbings/_models/whisper/faster-whisper-large-v3"

    # If running on Windows, use Windows path; otherwise use WSL/Linux path
    if os.name == "nt":
        return win_path
    return wsl_path

WHISPER_MODEL_DIR_RAW = os.getenv("WHISPER_MODEL_DIR", default_whisper_model_dir())

print(f"Whisper shared model path: {WHISPER_MODEL_DIR_RAW}")

if not os.path.isdir(WHISPER_MODEL_DIR_RAW):
    raise FileNotFoundError(
        f"Whisper model directory not found: {WHISPER_MODEL_DIR_RAW}\n"
        "Make sure the shared drive is mounted (Windows: Z: exists; WSL: /mnt/z exists) "
        "or set WHISPER_MODEL_DIR to the correct path."
    )

WHISPER_MODEL_DIR = resolve_whisper_model_dir(WHISPER_MODEL_DIR_RAW)

print(f"Loading Faster-Whisper model from (effective): {WHISPER_MODEL_DIR}")

whisper_model = WhisperModel(
    WHISPER_MODEL_DIR,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
)

print("Faster-Whisper model loaded.")


# =========================
# HELPERS
# =========================
_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def count_words(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def ffprobe_duration_seconds(audio_path: Path) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out) if out else None
    except Exception:
        return None


def transcribe_with_whisper_large_v3(
    audio_path: Path,
    language_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns dict:
      text, speech_seconds, detected_language,
      mean_no_speech_prob, mean_avg_logprob, mean_compression_ratio,
      segment_count
    """
    lang = (language_code or "").strip().lower() or None

    segments, info = whisper_model.transcribe(
        str(audio_path),
        language=lang,                # None => auto
        beam_size=5,
        best_of=5,
        temperature=0.0,
        vad_filter=True,              # IMPORTANT: reduces hallucination
        vad_parameters={
            "min_silence_duration_ms": 400,
            "speech_pad_ms": 150,
        },
        condition_on_previous_text=False,  # IMPORTANT: reduces drift/hallucination
    )

    parts: List[str] = []
    speech_seconds = 0.0

    ns_probs: List[float] = []
    logprobs: List[float] = []
    comp_ratios: List[float] = []

    seg_count = 0

    for seg in segments:
        seg_count += 1

        # duration
        try:
            speech_seconds += max(0.0, float(seg.end) - float(seg.start))
        except Exception:
            pass

        # text
        t = (getattr(seg, "text", "") or "").strip()
        if t:
            parts.append(t)

        # metrics (may exist depending on version)
        try:
            ns = getattr(seg, "no_speech_prob", None)
            if ns is not None:
                ns_probs.append(float(ns))
        except Exception:
            pass

        try:
            lp = getattr(seg, "avg_logprob", None)
            if lp is not None:
                logprobs.append(float(lp))
        except Exception:
            pass

        try:
            cr = getattr(seg, "compression_ratio", None)
            if cr is not None:
                comp_ratios.append(float(cr))
        except Exception:
            pass

    text = " ".join(parts).strip()
    detected = getattr(info, "language", None) or "unknown"

    def mean(xs: List[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "text": text,
        "speech_seconds": speech_seconds,
        "detected_language": str(detected),
        "mean_no_speech_prob": mean(ns_probs),
        "mean_avg_logprob": mean(logprobs),
        "mean_compression_ratio": mean(comp_ratios),
        "segment_count": seg_count,
    }

def decide_keep(
    text: str,
    duration_sec: float,
    speech_seconds: float,
    min_words: int,
    min_chars: int,
    min_speech_seconds: float,
    min_speech_ratio: float,
    mean_no_speech_prob: float,
    mean_avg_logprob: float,
    mean_compression_ratio: float,
    max_no_speech_prob: float,
    min_avg_logprob: float,
    max_compression_ratio: float,
) -> Tuple[bool, str]:
    """
    KEEP only if:
      - transcript has enough words/chars
      - enough speech time
      - AND metrics do NOT indicate hallucination
    """
    words = count_words(text)
    chars = len((text or "").strip())
    ratio = (speech_seconds / duration_sec) if duration_sec > 0 else 0.0

    reasons = []

    # Basic gates
    if words < min_words:
        reasons.append(f"words<{min_words}")
    if chars < min_chars:
        reasons.append(f"chars<{min_chars}")
    if speech_seconds < min_speech_seconds:
        reasons.append(f"speech_seconds<{min_speech_seconds}")
    if ratio < min_speech_ratio:
        reasons.append(f"speech_ratio<{min_speech_ratio}")

    # Hallucination / no-speech gates (key fix)
    # Typical safe thresholds:
    # - mean_no_speech_prob high => likely no speech
    # - mean_avg_logprob too low => low confidence
    # - mean_compression_ratio too high => suspicious output
    if mean_no_speech_prob >= 0.60:
        reasons.append("no_speech_prob>=0.60")
    if mean_avg_logprob != 0.0 and mean_avg_logprob <= -1.00:
        reasons.append("avg_logprob<=-1.00")
    if mean_compression_ratio != 0.0 and mean_compression_ratio >= 2.40:
        reasons.append("compression_ratio>=2.40")

    if reasons:
        return False, ";".join(reasons)
    return True, "ok"

# =========================
# MAIN
# =========================
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate extracted interview MP3 clips: keep if real speech exists, otherwise delete/move."
    )

    ap.add_argument("--in-dir", default=str(INTERVIEWS_DIR), help="Folder containing extracted interview MP3s")
    ap.add_argument("--language", default="", help="Force language code (e.g. en). Empty = auto-detect.")

    # Basic thresholds
    ap.add_argument("--min-words", type=int, default=2)
    ap.add_argument("--min-chars", type=int, default=6)
    ap.add_argument("--min-speech-seconds", type=float, default=0.60)
    ap.add_argument("--min-speech-ratio", type=float, default=0.10)

    # Hallucination / no-speech thresholds (KEY FIX)
    ap.add_argument("--max-no-speech-prob", type=float, default=0.60)
    ap.add_argument("--min-avg-logprob", type=float, default=-1.00)
    ap.add_argument("--max-compression-ratio", type=float, default=2.40)

    # Actions
    ap.add_argument("--hard-delete", action="store_true", help="Permanently delete useless clips")
    ap.add_argument("--deleted-dir", default="", help="Move useless clips here (default: <in-dir>/_deleted)")
    ap.add_argument("--save-transcripts", action="store_true", help="Save transcript .txt per clip")
    ap.add_argument("--transcripts-dir", default=str(TRANSCRIPTS_DIR_DEFAULT))
    ap.add_argument("--report-json", default=str(REPORT_JSON_DEFAULT))

    args = ap.parse_args()

    in_dir = Path(args.in_dir).resolve()
    if not in_dir.exists():
        print(f"❌ Input folder not found: {in_dir}")
        return 2

    deleted_dir = Path(args.deleted_dir).resolve() if args.deleted_dir else (in_dir / "_deleted")
    if not args.hard_delete:
        deleted_dir.mkdir(parents=True, exist_ok=True)

    transcripts_dir = Path(args.transcripts_dir).resolve()
    if args.save_transcripts:
        transcripts_dir.mkdir(parents=True, exist_ok=True)

    report_path = Path(args.report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lang = (args.language or "").strip() or None

    mp3s = sorted(in_dir.glob("*.mp3"))
    if not mp3s:
        print(f"No MP3 files found in: {in_dir}")
        return 0

    print(f"\nFound {len(mp3s)} clip(s) in: {in_dir}")
    print(
        "Policy: KEEP if "
        f"words>={args.min_words}, chars>={args.min_chars}, "
        f"speech_seconds>={args.min_speech_seconds}, speech_ratio>={args.min_speech_ratio}, "
        f"no_speech_prob<={args.max_no_speech_prob}, avg_logprob>={args.min_avg_logprob}, "
        f"compression_ratio<={args.max_compression_ratio}\n"
    )

    kept = 0
    removed = 0
    report: List[Dict[str, Any]] = []

    for i, mp3 in enumerate(mp3s, start=1):
        print(f"\n[{i}/{len(mp3s)}] {mp3.name}")

        duration = ffprobe_duration_seconds(mp3) or 0.0

        try:
            asr = transcribe_with_whisper_large_v3(mp3, language_code=lang)
        except Exception as e:
            # Safety: do NOT delete if ASR fails
            print(f"  ❌ Whisper failed: {e} -> KEEP (safety)")
            report.append({
                "file": mp3.name,
                "keep": True,
                "reason": "whisper_failed_keep_for_safety",
                "duration_seconds": duration,
            })
            kept += 1
            continue

        text = asr.get("text", "") or ""
        speech_seconds = float(asr.get("speech_seconds", 0.0) or 0.0)
        detected = asr.get("detected_language", "unknown")

        mean_no_speech_prob = float(asr.get("mean_no_speech_prob", 0.0) or 0.0)
        mean_avg_logprob = float(asr.get("mean_avg_logprob", 0.0) or 0.0)
        mean_compression_ratio = float(asr.get("mean_compression_ratio", 0.0) or 0.0)
        segment_count = int(asr.get("segment_count", 0) or 0)

        # Apply validation decision
        keep, reason = decide_keep(
            text=text,
            duration_sec=duration,
            speech_seconds=speech_seconds,
            min_words=args.min_words,
            min_chars=args.min_chars,
            min_speech_seconds=args.min_speech_seconds,
            min_speech_ratio=args.min_speech_ratio,
            mean_no_speech_prob=mean_no_speech_prob,
            mean_avg_logprob=mean_avg_logprob,
            mean_compression_ratio=mean_compression_ratio,
            # thresholds for hallucination gates
            max_no_speech_prob=args.max_no_speech_prob,
            min_avg_logprob=args.min_avg_logprob,
            max_compression_ratio=args.max_compression_ratio,
        )

        if args.save_transcripts:
            (transcripts_dir / f"{mp3.stem}.txt").write_text(text + "\n", encoding="utf-8")

        # Action
        if keep:
            print("  ✔ KEEP")
            kept += 1
        else:
            if args.hard_delete:
                mp3.unlink(missing_ok=True)
                print(f"  🗑 DELETE (hard) reason={reason}")
            else:
                dst = deleted_dir / mp3.name
                shutil.move(str(mp3), str(dst))
                print(f"  ➜ MOVE to {dst} reason={reason}")
            removed += 1

        # Report (include metrics so you can confirm hallucination)
        report.append({
            "file": mp3.name,
            "keep": keep,
            "reason": reason,
            "detected_language": detected,
            "duration_seconds": duration,
            "speech_seconds": speech_seconds,
            "speech_ratio": (speech_seconds / duration) if duration > 0 else 0.0,
            "word_count": count_words(text),
            "char_count": len(text.strip()),
            "segment_count": segment_count,
            "mean_no_speech_prob": mean_no_speech_prob,
            "mean_avg_logprob": mean_avg_logprob,
            "mean_compression_ratio": mean_compression_ratio,
            "transcript_preview": (text[:220] + ("..." if len(text) > 220 else "")),
        })

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== DONE ===")
    print(f"Kept   : {kept}")
    print(f"Removed: {removed}")
    print(f"Report : {report_path}")
    if not args.hard_delete:
        print(f"Deleted folder: {deleted_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
