#!/usr/bin/env python3
# /app/Python/V1/__audio_conversion__.py  (or root-folder/Python/V1/__audio_conversion__.py)
#
# Converts inputs/audios/__dubbed__audio__.mp3 to STEREO (2 channels) using ffmpeg
# and overwrites the same file.
#
# Works inside Docker even when /tmp and /app are different devices:
#   - encode to /tmp
#   - copy to target directory as a stage file
#   - atomic replace within the same filesystem
#
# Shows a live progress bar (duration from ffprobe + progress from ffmpeg).

import os
import shutil
import subprocess
import time
from pathlib import Path


def run_capture_text(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.stdout
    except FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]} (install it and ensure it is in PATH)") from e
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip() or (e.stdout or "").strip()
        raise RuntimeError(f"Command failed ({cmd[0]}), exit={e.returncode}: {msg}") from e


def get_duration_seconds(audio_path: Path) -> float:
    out = run_capture_text([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(audio_path)
    ]).strip()
    try:
        return float(out)
    except Exception:
        return 0.0


def format_bar(pct: float, width: int = 30) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round((pct / 100.0) * width))
    return "[" + ("█" * filled) + ("░" * (width - filled)) + f"] {pct:7.2f}%"


def atomic_overwrite_cross_device(src_tmp: Path, target: Path) -> None:
    """
    Overwrite `target` with `src_tmp` even if they are on different devices.

    Strategy:
      1) Copy src_tmp into target directory as a stage file (same filesystem as target)
      2) os.replace(stage, target) => atomic within same filesystem
    """
    target_dir = target.parent
    stage = target_dir / (target.stem + f".{os.getpid()}.__stage__" + target.suffix)

    # Ensure we can write in the target directory
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy bytes to stage in same filesystem
    with src_tmp.open("rb") as fsrc, stage.open("wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)  # 1MB chunks

    # Atomic replace stage -> target (same filesystem)
    os.replace(str(stage), str(target))


def main() -> int:
    # In your Docker run, your project root appears to be /app
    root = Path(__file__).resolve().parents[2]

    audio_path = root / "inputs" / "audios" / "__dubbed__audio__.mp3"
    if not audio_path.exists():
        print(f"ERROR: Input audio not found: {audio_path}")
        return 1

    duration = get_duration_seconds(audio_path)
    if duration <= 0:
        print("WARNING: Could not read duration via ffprobe; progress may be less accurate.")

    tmp_out = Path("/tmp") / (audio_path.stem + f".{os.getpid()}.__tmp__" + audio_path.suffix)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-i", str(audio_path),
        "-ac", "2",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        "-progress", "pipe:1",
        str(tmp_out),
    ]

    proc = None
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            print("ERROR: ffmpeg not found. Install ffmpeg and ensure it is in PATH.")
            return 1

        if not proc.stdout:
            print("ERROR: Could not read ffmpeg progress output.")
            return 1

        print("Converting to stereo...")
        last_print = 0.0
        last_pct = -1.0

        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if not line:
                continue

            line = line.strip()
            if "=" not in line:
                continue

            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()

            if k == "out_time_ms":
                try:
                    out_time_ms = int(v)
                except Exception:
                    continue

                if duration > 0:
                    current_sec = out_time_ms / 1_000_000.0
                    pct = (current_sec / duration) * 100.0
                else:
                    pct = 0.0

                now = time.time()
                if now - last_print > 0.1 and (abs(pct - last_pct) >= 0.1 or last_pct < 0):
                    print("\r" + format_bar(pct), end="", flush=True)
                    last_print = now
                    last_pct = pct

            elif k == "progress" and v == "end":
                if duration > 0:
                    print("\r" + format_bar(100.0), end="", flush=True)
                break

        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read().strip() if proc.stderr else ""
            print("\nERROR: ffmpeg failed.")
            if err:
                print(err)
            return 1

        if not tmp_out.exists() or tmp_out.stat().st_size == 0:
            print("\nERROR: Conversion produced an empty output file.")
            return 1

        print("\nConversion complete.")

        # Cross-device safe overwrite
        try:
            atomic_overwrite_cross_device(tmp_out, audio_path)
        except PermissionError:
            print("ERROR: Permission denied writing in target directory:")
            print(f"  Target: {audio_path}")
            return 1
        except Exception as e:
            print(f"ERROR: Overwrite failed: {e}")
            return 1

        print(f"OK: Converted to stereo and overwrote: {audio_path}")
        return 0

    finally:
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except Exception:
            pass

        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
