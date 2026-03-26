#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import time
import uuid
import subprocess
import sys
import ntpath
from pathlib import Path
from datetime import datetime


# =========================
# CONFIG
# =========================

DROP_DIR = Path("/mnt/c/VideoRenderAgain")

AME_LOG_REL = r"Documents\Adobe\Adobe Media Encoder\25.0\AMEEncodingErrorLog.txt"

LAUNCHER_WSL = "/home/vvruntime/viralverse-dubbing-automation/_queue_again_launcher__.py"
SEND_TO_AME_JSX_WSL = "/home/vvruntime/viralverse-dubbing-automation/Javascript/_SendToAME.jsx"

POLL_SECONDS = 5
HEARTBEAT_SECONDS = 60
MAX_READ_BYTES = 1024 * 1024  # 1MB
CARRY_MAX_CHARS = 200_000
SEEN_MAX = 1200

STATE_FILE = DROP_DIR / "_queue_again__state.json"


# =========================
# Utils
# =========================

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    atomic_write_json(STATE_FILE, state)


def strip_win_extended_prefix(p: str) -> str:
    p = (p or "").strip()
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[len("\\\\?\\UNC\\"):]
    if p.startswith("\\\\?\\"):
        return p[len("\\\\?\\"):]
    if p.startswith("\\??\\UNC\\"):
        return "\\\\" + p[len("\\??\\UNC\\"):]
    if p.startswith("\\??\\"):
        return p[len("\\??\\"):]
    return p


def sanitize_drive_path(p: str) -> str:
    p = (p or "").strip().strip('"')
    for _ in range(10):
        p2 = strip_win_extended_prefix(p)
        if p2 == p:
            break
        p = p2
    # fix malformed drive paths like Z:\!\...
    p = re.sub(r"^([A-Za-z]:)\\!\\", r"\1\\", p)
    p = p.replace("\u00A0", " ").strip()
    return p


def ps_read_one_line(cmd: str) -> str:
    """
    Runs a PowerShell command from WSL and returns first non-empty line (stripped).
    Safe for WSL because it calls powershell.exe.
    """
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in (out or "").splitlines():
            s = line.strip()
            if s:
                return s
        return ""
    except Exception:
        return ""


def build_ame_log_win() -> str:
    """
    Build AMEEncodingErrorLog.txt path for the *current Windows user* (no hardcode).
    Uses $env:USERPROFILE so it works even if the profile isn't exactly C:\\Users\\<name>.
    """
    userprofile = ps_read_one_line("$env:USERPROFILE")
    userprofile = sanitize_drive_path(userprofile)

    if userprofile:
        # Keep Windows-style backslashes
        return ntpath.join(userprofile, AME_LOG_REL)

    # Fallback to the old default if PowerShell isn't available for some reason
    # (prevents breaking existing setups)
    return r"C:\Users\IT\Documents\Adobe\Adobe Media Encoder\25.0\AMEEncodingErrorLog.txt"



def win_to_wsl_path(win_path: str) -> str:
    p = sanitize_drive_path(win_path)
    if re.match(r"^[A-Za-z]:\\", p):
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return p.replace("\\", "/")


def is_allowed_z_project_path(win_prproj: str) -> bool:
    p = sanitize_drive_path(win_prproj)
    lo = p.lower().replace("/", "\\")
    root = "z:\\automated dubbings\\projects\\"
    return lo.startswith(root)


def clean_windows_filename(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r'[<>:"/\\|?*]+', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").strip()
    return s


def is_temp_prproj(win_path: str) -> bool:
    p = sanitize_drive_path(win_path)
    lo = p.lower().replace("/", "\\")
    return lo.endswith(".prproj") and ("\\appdata\\local\\temp\\" in lo)


def parse_temp_prproj_filename(win_path: str) -> tuple[str, str] | None:
    """
    Temp prproj pattern:
      ...\\AppData\\Local\\Temp\\<LANG> - <VideoTitle>.prproj
    """
    p = sanitize_drive_path(win_path)
    base = ntpath.basename(p)
    m = re.match(r"^\s*([A-Za-z0-9]+)\s*-\s*(.+?)\.prproj\s*$", base, flags=re.I)
    if not m:
        return None
    lang = m.group(1).strip().upper()
    clean_title = clean_windows_filename(m.group(2))
    if not clean_title:
        return None
    return lang, clean_title


LANG_MAP = {
    "HR": "Croatian",
    "FR": "French",
    "ES": "Spanish",
    "PL": "Polish",
    "KO": "Korean",
    "RU": "Russian",
    "PTBR": "Portuguese-BR",
    "CS": "Czech",
}


def resolve_project_path(source_file: str, output_file: str) -> tuple[str, str]:
    src = sanitize_drive_path(source_file or "")
    out = sanitize_drive_path(output_file or "")

    if is_temp_prproj(src):
        parsed = parse_temp_prproj_filename(src)
        if parsed:
            langcode, clean_video = parsed
            language = LANG_MAP.get(langcode, "")
            if language:
                cand = (
                    r"Z:\Automated Dubbings\Projects"
                    rf"\{clean_video}\{language}\Project File\{langcode} - {clean_video}.prproj"
                )
                cand = sanitize_drive_path(cand)
                if is_allowed_z_project_path(cand):
                    return cand, "temp_template"
        return "", "unresolved"

    if src.lower().endswith(".prproj") and is_allowed_z_project_path(src):
        return sanitize_drive_path(src), "direct"

    _ = out
    return "", "unresolved"


# =========================
# AME log parsing (matches YOUR uploaded log)
# =========================

# Each block starts at "- Source File:" and contains a line like:
# "01/09/2026 04:35:52 PM : Encoding Failed"
_FAIL_BLOCK_RX = re.compile(
    r"(?s)(^\s*-\s*Source\s*File\s*:.*?^\d{2}/\d{2}/\d{4}.*?:\s*Encoding Failed.*?)(?=^\s*-\s*Source\s*File\s*:|\Z)",
    re.M,
)

_TS_RX = re.compile(r"(?m)^\d{2}/\d{2}/\d{4}.*?:\s*Encoding Failed\s*$")
_SRC_RX = re.compile(r"(?im)^\s*-\s*Source\s*File\s*:\s*(.+?)\s*$")
_OUT_RX = re.compile(r"(?im)^\s*-\s*Output\s*File\s*:\s*(.+?)\s*$")
_PRESET_RX = re.compile(r"(?im)^\s*-\s*Preset\s*Used\s*:\s*(.+?)\s*$")
_MISSING_FONT_RX = re.compile(r"(?im)^\s*-\s*Missing\s*Font\s*:\s*(.+?)\s*$")


def detect_encoding(path: Path) -> tuple[str, bool]:
    """
    AMEEncodingErrorLog.txt is often UTF-16LE with BOM (FF FE).
    Returns (encoding, is_utf16).
    """
    try:
        with path.open("rb") as f:
            head = f.read(2)
        if head == b"\xff\xfe":
            return "utf-16-le", True
        return "utf-8", False
    except Exception:
        return "utf-8", False


def read_incremental(path: Path, last_pos: int, encoding: str, is_utf16: bool) -> tuple[str, int]:
    """
    Correct incremental reader:
    - Reads from last_pos forward (does not jump to end)
    - Caps to MAX_READ_BYTES but advances new_pos accordingly (won't skip)
    - For UTF-16LE ensures offsets are even
    """
    if not path.exists():
        return "", last_pos

    try:
        size = path.stat().st_size
    except Exception:
        return "", last_pos

    if last_pos < 0:
        return "", size

    if size < last_pos:
        return "", size

    delta = size - last_pos
    if delta <= 0:
        return "", last_pos

    # Align offsets for utf-16
    if is_utf16:
        if last_pos % 2 == 1:
            last_pos -= 1
        if size % 2 == 1:
            size -= 1
        delta = size - last_pos
        if delta <= 0:
            return "", last_pos

    read_bytes = min(delta, MAX_READ_BYTES)
    start = last_pos

    try:
        with path.open("rb") as f:
            f.seek(start)
            data = f.read(read_bytes)
        text = data.decode(encoding, errors="replace")
        new_pos = start + read_bytes
        return text, new_pos
    except Exception:
        return "", last_pos


def normalize_carry(text: str) -> str:
    if len(text) <= CARRY_MAX_CHARS:
        return text
    return text[-CARRY_MAX_CHARS:]


# =========================
# Job + launcher invocation
# =========================

def write_job_json(job: dict) -> Path:
    ensure_dir(DROP_DIR)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name = f"ame_fail_{ts}_{uuid.uuid4().hex}.json"
    path = DROP_DIR / name
    atomic_write_json(path, job)
    return path


def call_launcher(project_win: str, output_win: str) -> None:
    cmd = [
        sys.executable,
        LAUNCHER_WSL,
        "--project-win", project_win,
        "--send-jsx-wsl", SEND_TO_AME_JSX_WSL,
    ]
    # If your launcher supports --output-win (recommended), pass it:
    if output_win:
        cmd += ["--output-win", output_win]

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[{now_iso()}] ⚠️ Could not start launcher: {e!r}")


# =========================
# Main
# =========================

def main() -> None:
    ensure_dir(DROP_DIR)

    ame_log_win = build_ame_log_win()
    log_wsl = Path(win_to_wsl_path(ame_log_win))
    
    encoding, is_utf16 = detect_encoding(log_wsl)

    state = load_state()
    seen: list[str] = state.get("seen_failure_ids") or []
    carry: str = state.get("carry") or ""
    last_pos: int = int(state.get("last_pos") or -1)
    seeded: bool = bool(state.get("seeded") or False)

    print(f"[{now_iso()}] _queue_again__ started")
    print(f"[{now_iso()}] AME log (WIN): {ame_log_win}")
    print(f"[{now_iso()}] AME log (WSL): {log_wsl}")
    print(f"[{now_iso()}] AME log encoding: {encoding}")
    print(f"[{now_iso()}] Drop dir: {DROP_DIR}")
    print(f"[{now_iso()}] Launcher (WSL): {LAUNCHER_WSL}")
    print(f"[{now_iso()}] SendToAME JSX (WSL): {SEND_TO_AME_JSX_WSL}")

    if not seeded:
        # Seed offset so we only react to failures AFTER start
        if log_wsl.exists():
            try:
                last_pos = log_wsl.stat().st_size
                if is_utf16 and (last_pos % 2 == 1):
                    last_pos -= 1
            except Exception:
                last_pos = -1
        else:
            last_pos = -1

        state.update({
            "seeded": True,
            "last_pos": last_pos,
            "carry": "",
            "seen_failure_ids": seen[-SEEN_MAX:],
            "seeded_utc": now_iso(),
            "encoding": encoding,
        })
        save_state(state)
        print(f"[{now_iso()}] Seeded watcher at file offset: {last_pos}")

    last_heartbeat = time.time()

    while True:
        tick_start = time.time()
        if (tick_start - last_heartbeat) >= HEARTBEAT_SECONDS:
            print(f"[{now_iso()}] Heartbeat: watching AME log (no new failures)")
            last_heartbeat = tick_start

        chunk, new_pos = read_incremental(log_wsl, last_pos, encoding, is_utf16)
        if chunk:
            carry = normalize_carry(carry + chunk)

            for m in _FAIL_BLOCK_RX.finditer(carry):
                block = m.group(1)

                ts_m = _TS_RX.search(block)
                ts_line = (ts_m.group(0).strip() if ts_m else "")

                src_m = _SRC_RX.search(block)
                out_m = _OUT_RX.search(block)
                pre_m = _PRESET_RX.search(block)
                missing_fonts = _MISSING_FONT_RX.findall(block) or []

                src_file = sanitize_drive_path(src_m.group(1)) if src_m else ""
                out_file = sanitize_drive_path(out_m.group(1)) if out_m else ""
                preset = (pre_m.group(1).strip() if pre_m else "")

                failure_id = f"{ts_line}|{src_file}|{out_file}|{preset}"
                if failure_id in seen:
                    continue
                seen.append(failure_id)
                seen = seen[-SEEN_MAX:]

                resolved_proj, how = resolve_project_path(src_file, out_file)

                job = {
                    "ts_detected_utc": now_iso(),
                    "ts_line": ts_line,
                    "source_file": src_file,
                    "output_file": out_file,
                    "preset_used": preset,
                    "missing_fonts": missing_fonts,
                    "resolved_project": resolved_proj,
                    "resolve_how": how,
                    "allowed": bool(resolved_proj),
                }

                print(f"[{now_iso()}] ❌ Encoding Failed detected")
                print(f"[{now_iso()}]    TS     : {ts_line}")
                print(f"[{now_iso()}]    Source : {src_file}")
                print(f"[{now_iso()}]    Output : {out_file}")
                print(f"[{now_iso()}]    Preset : {preset}")
                if missing_fonts:
                    print(f"[{now_iso()}]    Missing fonts: {missing_fonts}")
                print(f"[{now_iso()}]    Resolved project: {resolved_proj} ({how})")

                job_path = write_job_json(job)
                print(f"[{now_iso()}]    Job JSON: {job_path}")

                if resolved_proj and is_allowed_z_project_path(resolved_proj):
                    print(f"[{now_iso()}] ▶ Handing off to launcher...")
                    call_launcher(resolved_proj, out_file)
                else:
                    print(f"[{now_iso()}] ⛔ Rejected (not in Z:\\Automated Dubbings\\Projects\\)")

        last_pos = new_pos
        state.update({
            "last_pos": last_pos,
            "carry": carry,
            "seen_failure_ids": seen[-SEEN_MAX:],
            "encoding": encoding,
        })
        save_state(state)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()