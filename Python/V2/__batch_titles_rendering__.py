#!/usr/bin/env python3
import json
import os
import time
import subprocess
import platform
import shutil
import re
from pathlib import Path, PureWindowsPath

# ============================================================
# __batch_titles_rendering__.py (WSL-friendly, UNC-PROOF)
#
# Reliability upgrades (NO extra packages):
# 1) Stream+capture BAT output so we can classify SUCCESS even if BAT exits non-zero
#    (e.g., "Input redirection is not supported" after queue).
# 2) Do NOT treat noisy stderr lines (asio 10061, remote endpoint, ORT warnings) as fatal.
# 3) Write RAW runner output to C:\VV_TitleRunner\_TitleRunner_cmd_output.log for audits.
# 4) Enforce non-empty RS/OM template names to avoid "previous preset reuse".
# ============================================================

HERE = Path(__file__).resolve().parents[2]

CONFIG_FILE     = HERE / "inputs" / "config" / "config.json"
SRC_TITLES_JSON = HERE / "output" / "JSON" / "A13__titles_segments__.json"
TEMPLATE_JSON   = HERE / "output" / "JSON" / "A14__title__.json"

SRC_BAT = HERE / "Python" / "V2" / "Commands" / "_TitleRender__.bat"
SRC_JSX = HERE / "Javascript" / "_TitleTextAndImageReplaceByDetections.jsx"

STAGING_DIR_LINUX = Path("/mnt/c/VV_TitleRunner")
STAGING_BAT_LINUX = STAGING_DIR_LINUX / "_TitleRender__.bat"
STAGING_JSX_LINUX = STAGING_DIR_LINUX / "_TitleTextAndImageReplaceByDetections.jsx"
JOB_FILE_LINUX    = STAGING_DIR_LINUX / "_Title__job__.json"

WAIT_BETWEEN_JOBS_SECONDS = 8

# ---- AME paths (PowerShell fallback search included) ----
AME_EXE_WIN_CANDIDATES = [
    r"C:\Program Files\Adobe\Adobe Media Encoder 2025\Adobe Media Encoder.exe",
    r"C:\Program Files\Adobe\Adobe Media Encoder 2025\Support Files\Adobe Media Encoder.exe",
    r"C:\Program Files (x86)\Adobe\Adobe Media Encoder 2025\Adobe Media Encoder.exe",
    r"C:\Program Files (x86)\Adobe\Adobe Media Encoder 2025\Support Files\Adobe Media Encoder.exe",
    r"C:\Program Files\Adobe\Adobe Media Encoder 2024\Adobe Media Encoder.exe",
    r"C:\Program Files\Adobe\Adobe Media Encoder 2024\Support Files\Adobe Media Encoder.exe",
    r"C:\Program Files (x86)\Adobe\Adobe Media Encoder 2024\Adobe Media Encoder.exe",
    r"C:\Program Files (x86)\Adobe\Adobe Media Encoder 2024\Support Files\Adobe Media Encoder.exe",
]
WAIT_AFTER_AME_LAUNCH_SECONDS = 20  # warm-up

# ----- AE Layer/Comp Defaults -----
DEFAULT_TEXT_COMP_NAME = "viralverse_Trailer"
DEFAULT_TEXT_LAYER_NAME = "ViralVerse_Title"
DEFAULT_IMAGE_COMP_NAME = "VV_IMAGE"
DEFAULT_IMAGE_LAYER_INDEX = 1

DEFAULT_RENDER_COMP = "|| TRAILER VIRALVERSE"

# IMPORTANT: must be non-empty (avoid previous preset reuse)
DEFAULT_OM_TEMPLATE = "VV Title Render"
DEFAULT_RS_TEMPLATE = "Best Settings"  # set to your RS template name if different

# ----------------------------- Output parsing (noise vs success) -----------------------------
SUCCESS_PATTERNS = [
    re.compile(r"OK\|Queued to AME\|OUT=", re.I),
    re.compile(r"\bQueued to AME\b", re.I),
    re.compile(r"\bQueued\+Started\b", re.I),
    re.compile(r"✅\s*Queued to AME", re.I),
]
# Known non-fatal noise you asked to stop polluting results (we still log RAW to file)
NOISE_LINE_PATTERNS = [
    re.compile(r"GPU\d+\s+failed.*sanity test", re.I),
    re.compile(r"requested API version\s*\[\d+\].*only API versions", re.I),
    re.compile(r"Current ORT Version", re.I),
    re.compile(r"asio\s+async_connect error.*10061", re.I),
    re.compile(r"Error getting remote endpoint.*10057", re.I),
    re.compile(r"handle_connect error:.*refused", re.I),
]
# This one often happens AFTER queue and must NOT flip job to failed if success token seen
REDIRECTION_BUG_PATTERN = re.compile(r"Input redirection is not supported", re.I)

# Where we store raw cmd output
RAW_OUT_LOG_LINUX = STAGING_DIR_LINUX / "_TitleRunner_cmd_output.log"


# ----------------------------- OS / PATH UTILS -----------------------------
def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    rel = platform.uname().release.lower()
    return "microsoft" in rel or "wsl" in rel


def linux_to_windows_path(p: str) -> str:
    if p is None:
        return ""
    s = str(p).strip()
    if not s:
        return ""
    if len(s) >= 2 and s[1] == ":":
        return s.replace("/", "\\")
    if s.startswith("\\\\"):
        return s.replace("/", "\\")
    if s.startswith("/mnt/") and len(s) > 6 and s[5].isalpha() and s[6] == "/":
        drive = s[5].upper()
        rest = s[7:]
        return f"{drive}:\\" + rest.replace("/", "\\")
    return s.replace("/", "\\")


def windows_path_to_os_path(win_path: str) -> Path:
    s = (win_path or "").strip()
    if not s:
        return Path("")
    if os.name == "nt":
        return Path(s)
    p = PureWindowsPath(s)
    drive = p.drive.rstrip(":").lower()
    parts = list(p.parts)[1:]
    return Path("/mnt") / drive / Path(*parts)


# ----------------------------- WINDOWS HELPERS -----------------------------
def win_exists_ps(win_path: str) -> bool:
    win_path = (win_path or "").strip()
    if not win_path:
        return False
    escaped = win_path.replace("'", "''")
    ps = f"Test-Path -LiteralPath '{escaped}'"
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return (r.stdout or "").strip().lower() == "true"


def pick_first_existing_windows_path(candidates_win) -> str:
    for p in candidates_win:
        p = (p or "").strip()
        if p and win_exists_ps(p):
            return p
    return ""


def find_ame_exe_by_powershell() -> str:
    ps = r"""
$ErrorActionPreference = "SilentlyContinue"
$roots = @("C:\Program Files\Adobe", "C:\Program Files (x86)\Adobe")
foreach ($r in $roots) {
  if (Test-Path $r) {
    $hit = Get-ChildItem -Path $r -Recurse -Filter "Adobe Media Encoder.exe" |
           Select-Object -First 1 -ExpandProperty FullName
    if ($hit) { $hit; exit 0 }
  }
}
exit 1
"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else ""


def _tasklist_contains(image_name: str) -> bool:
    image_name = (image_name or "").strip()
    if not image_name:
        return False
    cmd = f'tasklist /FI "IMAGENAME eq {image_name}"'
    r = subprocess.run(["cmd.exe", "/C", cmd], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    return image_name.lower() in out.lower()


def _start_win_exe_detached(exe_win_path: str) -> bool:
    exe_win_path = (exe_win_path or "").strip()
    if not exe_win_path:
        return False
    escaped = exe_win_path.replace("'", "''")
    ps = (
        f"$p='{escaped}'; "
        f"if (Test-Path -LiteralPath $p) {{ Start-Process -FilePath $p | Out-Null; exit 0 }} "
        f"else {{ exit 2 }}"
    )
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return r.returncode == 0


def ensure_media_encoder_running():
    AME_PROC = "Adobe Media Encoder.exe"
    if _tasklist_contains(AME_PROC):
        print("✅ AME already running.")
        time.sleep(2)
        return
    ame_exe = pick_first_existing_windows_path(AME_EXE_WIN_CANDIDATES) or find_ame_exe_by_powershell()
    if not ame_exe:
        print("⚠️  Could not auto-find Adobe Media Encoder.exe")
        print("    Open AME manually ONCE, then rerun.")
        return
    print(f"🚀 Launching AME: {ame_exe}")
    if not _start_win_exe_detached(ame_exe):
        print("⚠️  Failed to launch AME. Open manually once.")
        return
    print(f"⏳ Warming up AME for {WAIT_AFTER_AME_LAUNCH_SECONDS}s...")
    time.sleep(WAIT_AFTER_AME_LAUNCH_SECONDS)


# ----------------------------- RUNNER STAGING (UNC-PROOF) -----------------------------
def _sync_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst)
        return
    ss = src.stat()
    ds = dst.stat()
    if ss.st_size != ds.st_size or int(ss.st_mtime) != int(ds.st_mtime):
        shutil.copy2(src, dst)


def ensure_runner_staging():
    if not SRC_BAT.exists():
        raise FileNotFoundError(f"Missing {SRC_BAT}")
    if not SRC_JSX.exists():
        raise FileNotFoundError(f"Missing {SRC_JSX}")
    STAGING_DIR_LINUX.mkdir(parents=True, exist_ok=True)
    _sync_file(SRC_BAT, STAGING_BAT_LINUX)
    _sync_file(SRC_JSX, STAGING_JSX_LINUX)


def _append_raw_log(header: str, raw_lines):
    try:
        STAGING_DIR_LINUX.mkdir(parents=True, exist_ok=True)
        with RAW_OUT_LOG_LINUX.open("a", encoding="utf-8", errors="replace") as f:
            f.write("\n" + "=" * 78 + "\n")
            f.write(header.rstrip() + "\n")
            for ln in raw_lines:
                f.write(ln.rstrip("\n") + "\n")
    except Exception as e:
        print(f"⚠️  Could not write RAW output log: {e}")


def _should_hide_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    return any(p.search(s) for p in NOISE_LINE_PATTERNS)


def run_bat_from_wsl_staged_streaming() -> dict:
    """
    Run the BAT from C:\VV_TitleRunner and:
    - Stream output to console (filtered)
    - Capture RAW output for classification + logging
    Returns dict with: returncode, success_token_seen, redirection_bug_seen
    """
    bat_win = linux_to_windows_path(str(STAGING_BAT_LINUX))
    if not win_exists_ps(bat_win):
        raise RuntimeError(f"Windows cannot see staged BAT at: {bat_win}")

    # Reduce ORT noise if those messages come from ORT logging (best-effort)
    env = os.environ.copy()
    env.setdefault("ORT_LOG_SEVERITY_LEVEL", "4")
    env.setdefault("ORT_LOG_VERBOSITY_LEVEL", "0")

    p = subprocess.Popen(
        ["cmd.exe", "/C", bat_win],
        cwd="/mnt/c",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        universal_newlines=True,
        env=env,
    )

    raw_lines = []
    success_seen = False
    redirection_bug_seen = False

    assert p.stdout is not None
    for line in p.stdout:
        raw_lines.append(line)
        if REDIRECTION_BUG_PATTERN.search(line):
            redirection_bug_seen = True
        if any(sp.search(line) for sp in SUCCESS_PATTERNS):
            success_seen = True

        # Print filtered line to console
        if not _should_hide_line(line):
            print(line.rstrip("\n"))

    rc = p.wait()

    _append_raw_log(
        header=f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] cmd.exe /C {bat_win}  (exit={rc})",
        raw_lines=raw_lines
    )

    return {
        "returncode": rc,
        "success_token_seen": success_seen,
        "redirection_bug_seen": redirection_bug_seen,
    }


# ----------------------------- JSON / CONFIG UTILS -----------------------------
def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _first_str(v) -> str:
    return v.strip() if isinstance(v, str) and v.strip() else ""


def extract_title_text(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    t = _first_str(item.get("text")) or _first_str(item.get("exact_script_text"))
    return t


def sanitize_for_windows_folder(name: str) -> str:
    if not isinstance(name, str):
        return ""
    bad = '<>:"/\\|?*'
    out = name
    for ch in bad:
        out = out.replace(ch, "_")
    return out.strip().rstrip(".")


def strip_video_prefix(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.strip()
    s2 = re.sub(r"^\s*Video\s*\d+\s*(?:[-:–—]\s*)", "", s, flags=re.IGNORECASE)
    return s2.strip()


def normalize_lang_cap(lang: str) -> str:
    raw = (lang or "").strip()
    if not raw:
        return ""
    low = raw.lower().replace("_", "-")
    code_map = {
        "en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese",
        "pt-br": "Portuguese-BR", "ru": "Russian", "pl": "Polish", "cs": "Czech",
        "tr": "Turkish", "ko": "Korean", "hr": "Croatian", "sr": "Serbian",
        "de": "German", "it": "Italian", "nl": "Dutch", "ar": "Arabic",
        "hi": "Hindi", "ur": "Urdu",
    }
    if low in code_map:
        return code_map[low]
    parts = raw.replace("_", " ").split()
    return " ".join(p[:1].upper() + p[1:].lower() for p in parts)


def load_template_aep_from_final_location(template_json_path: Path) -> str:
    data = load_json(template_json_path)
    final_loc = ""
    if isinstance(data, dict):
        final_loc = _first_str(data.get("final_location"))
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                final_loc = _first_str(it.get("final_location"))
                if final_loc:
                    break
    if not final_loc:
        raise RuntimeError(f"Missing 'final_location' in {template_json_path}")
    return linux_to_windows_path(final_loc)


def extract_number_text(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    return _first_str(item.get("number"))


def extract_body_text(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    return _first_str(item.get("body"))


def extract_image_path(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    for k in ("imageLocation", "image_path", "imagePath", "image", "img", "image_file", "image_win"):
        v = _first_str(item.get(k))
        if v:
            return v
    return ""


# ----------------------------- MAIN -----------------------------
def main():
    t0 = time.time()

    # Enforce non-empty templates (avoid preset reuse)
    if not DEFAULT_OM_TEMPLATE:
        raise RuntimeError("DEFAULT_OM_TEMPLATE is empty. Set it to your AE Output Module template.")
    if not DEFAULT_RS_TEMPLATE:
        raise RuntimeError("DEFAULT_RS_TEMPLATE is empty. Set it to your AE Render Settings template.")

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing {CONFIG_FILE}")
    if not SRC_TITLES_JSON.exists():
        raise FileNotFoundError(f"Missing {SRC_TITLES_JSON}")
    if not TEMPLATE_JSON.exists():
        raise FileNotFoundError(f"Missing {TEMPLATE_JSON}")

    ensure_runner_staging()

    cfg = load_json(CONFIG_FILE)
    if not isinstance(cfg, dict):
        raise RuntimeError("inputs/config/config.json must be a JSON object")

    video_name_raw = _first_str(cfg.get("video_name"))
    lang_raw = _first_str(cfg.get("language"))
    if not video_name_raw:
        raise RuntimeError("ERROR: Missing 'video_name' in inputs/config/config.json")
    if not lang_raw:
        raise RuntimeError("ERROR: Missing 'language' in inputs/config/config.json")

    video_name_json = sanitize_for_windows_folder(strip_video_prefix(video_name_raw))
    lang_cap = normalize_lang_cap(lang_raw)
    if not video_name_json:
        raise RuntimeError("ERROR: VideoName became empty after sanitize/strip.")
    if not lang_cap:
        raise RuntimeError("ERROR: LanguageCap became empty after normalization.")

    items = load_json(SRC_TITLES_JSON)
    if not isinstance(items, list):
        raise RuntimeError(f"{SRC_TITLES_JSON} must be a JSON list")

    project_path_win = load_template_aep_from_final_location(TEMPLATE_JSON)

    print(f"📊 Found {len(items)} titles in {SRC_TITLES_JSON.name}")
    print(f"🎬 VideoName: {video_name_json}")
    print(f"🌍 Language : {lang_cap}")
    print(f"🧩 AEP (win): {project_path_win}")
    print(f"🎛️ RS Template: {DEFAULT_RS_TEMPLATE}")
    print(f"🎛️ OM Template: {DEFAULT_OM_TEMPLATE}")

    ensure_media_encoder_running()

    queued = 0
    failed = 0
    soft_ok = 0

    for seq_no, item in enumerate(items, start=1):
        entry_id = int(item.get("id", seq_no)) if isinstance(item, dict) else seq_no

        text = extract_title_text(item)
        number = extract_number_text(item)
        body = extract_body_text(item)

        image_any = extract_image_path(item)
        image_win = linux_to_windows_path(image_any) if image_any else ""

        output_mp4_win = rf"Z:\Automated Dubbings\Projects\{video_name_json}\{lang_cap}\Titles\VideoClips\title_{seq_no:03d}.mp4"
        output_mp4_os = windows_path_to_os_path(output_mp4_win)
        output_mp4_os.parent.mkdir(parents=True, exist_ok=True)

        job = {
            "projectPath": project_path_win,
            "text": text,
            "number": number,
            "body": body,
            "imageLocation": image_win,
            "output": output_mp4_win,
            "language": lang_cap,

            "textCompName": DEFAULT_TEXT_COMP_NAME,
            "textLayerName": DEFAULT_TEXT_LAYER_NAME,
            "imageCompName": DEFAULT_IMAGE_COMP_NAME,
            "imageLayerIndex": DEFAULT_IMAGE_LAYER_INDEX,

            "renderComp": DEFAULT_RENDER_COMP,
            "omTemplate": DEFAULT_OM_TEMPLATE,
            "rsTemplate": DEFAULT_RS_TEMPLATE,

            "textMarginFracX": 0.10,
            "textBoxHeightFrac": 0.60,
            "textBottomMarginFrac": 0.50,
        }

        with JOB_FILE_LINUX.open("w", encoding="utf-8") as jf:
            json.dump(job, jf, ensure_ascii=False, indent=2)

        print("\n" + "=" * 70)
        print(f"➡️  QUEUE title_{seq_no:03d}   (id={entry_id})")
        print(f"TEXT  : {text!r}")
        print(f"NUMBER: {number!r}")
        print(f"BODY  : {body!r}")
        print(f"IMG   : {image_win if image_win else '(empty)'}")
        print(f"OUT   : {output_mp4_win}")
        print("=" * 70)

        result = run_bat_from_wsl_staged_streaming()
        rc = result["returncode"]

        # Classification:
        # - If BAT returns 0 => OK
        # - If BAT returns non-zero but success token was printed => SOFT OK (queue happened, BAT later died)
        if rc == 0:
            queued += 1
            print(f"✅ Queued to AME: title_{seq_no:03d}")
        elif result["success_token_seen"]:
            soft_ok += 1
            queued += 1
            print(f"✅ Queued to AME (soft): title_{seq_no:03d}  (exit={rc})")
            if result["redirection_bug_seen"]:
                print("   ↳ Note: BAT hit 'Input redirection is not supported' AFTER queue. Fix BAT wait (no <nul).")
        else:
            failed += 1
            print(f"❌ Failed to queue title_{seq_no:03d} (exit={rc})")
            if result["redirection_bug_seen"]:
                print("   ↳ BAT reported redirection bug before success token. Fix BAT wait (no <nul).")

        time.sleep(WAIT_BETWEEN_JOBS_SECONDS)

    print("\n" + "=" * 70)
    print(f"✅ QUEUED: {queued}  (soft_ok={soft_ok})")
    print(f"❌ FAILED: {failed}")
    print(f"RAW CMD OUTPUT LOG: {linux_to_windows_path(str(RAW_OUT_LOG_LINUX))}")
    print("NOTE: AME will render in background after queueing.")
    dt = time.time() - t0
    print(f"Total Time taken: {int(dt // 60)} min {round(dt % 60, 2)} sec.")
    print("=" * 70)


if __name__ == "__main__":
    main()
