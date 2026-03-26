#!/usr/bin/env python3
import json
import os
import time
import subprocess
import shutil
import re
from pathlib import Path, PureWindowsPath

# ============================================================
# __batch_lowerthirds_rendering__.py (WSL-friendly, UNC-PROOF)
#
# Reliability upgrades (NO extra packages):
# 1) Stream+capture BAT output so we can classify SUCCESS even if BAT exits non-zero
#    (e.g., "Input redirection is not supported" after queue).
# 2) Filter known noisy lines (asio 10061, ORT/GPU warnings) from console to keep logs clean.
#    RAW output is still written to C:\VV_LowerThirdRunner\_LowerThirdRunner_cmd_output.log
# 3) Keep current logic (MOV output, templates required) intact.
# ============================================================

HERE = Path(__file__).resolve().parents[2]

CONFIG_FILE   = HERE / "inputs" / "config" / "config.json"
SRC_LTS_JSON  = HERE / "output" / "JSON" / "A18__computed_lowerthirds__.json"
TEMPLATE_JSON = HERE / "output" / "JSON" / "A19__lowerthird__.json"

SRC_BAT = HERE / "Python" / "V2" / "Commands" / "_LowerThirdRender__.bat"
SRC_JSX = HERE / "Javascript" / "_ReplaceTextAndRender.jsx"

STAGING_DIR_LINUX = Path("/mnt/c/VV_LowerThirdRunner")
STAGING_BAT_LINUX = STAGING_DIR_LINUX / "_LowerThirdRender__.bat"
STAGING_JSX_LINUX = STAGING_DIR_LINUX / "_ReplaceTextAndRender.jsx"
JOB_FILE_LINUX    = STAGING_DIR_LINUX / "_LowerThird__job__.json"

WAIT_BETWEEN_JOBS_SECONDS = 6

# ---------------------- REQUIRED PRESET SETTINGS ----------------------
DEFAULT_RENDER_COMP  = "viralverse_LT_render"
DEFAULT_RS_TEMPLATE  = "Best Settings"
DEFAULT_OM_TEMPLATE  = "Lowerthird Render Preset"
# ---------------------------------------------------------------------

DEFAULT_TEXT_COMP_NAME  = "viralverse_LT"
DEFAULT_TEXT_LAYER_NAME = "TXT"

SUCCESS_PATTERNS = [
    re.compile(r"OK\|Queued to AME\|OUT=", re.I),
    re.compile(r"\bQueued to AME\b", re.I),
    re.compile(r"\bQueued\+Started\b", re.I),
    re.compile(r"✅\s*Queued", re.I),
]
NOISE_LINE_PATTERNS = [
    re.compile(r"GPU\d+\s+failed.*sanity test", re.I),
    re.compile(r"requested API version\s*\[\d+\].*only API versions", re.I),
    re.compile(r"Current ORT Version", re.I),
    re.compile(r"asio\s+async_connect error.*10061", re.I),
    re.compile(r"Error getting remote endpoint.*10057", re.I),
    re.compile(r"handle_connect error:.*refused", re.I),
]
REDIRECTION_BUG_PATTERN = re.compile(r"Input redirection is not supported", re.I)

RAW_OUT_LOG_LINUX = STAGING_DIR_LINUX / "_LowerThirdRunner_cmd_output.log"


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


def win_exists_ps(win_path: str) -> bool:
    win_path = (win_path or "").strip()
    if not win_path:
        return False
    escaped = win_path.replace("'", "''")
    ps = f"Test-Path -LiteralPath '{escaped}'"
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return (r.stdout or "").strip().lower() == "true"


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
    bat_win = linux_to_windows_path(str(STAGING_BAT_LINUX))
    if not win_exists_ps(bat_win):
        raise RuntimeError(f"Windows cannot see staged BAT at: {bat_win}")

    env = os.environ.copy()
    env.setdefault("ORT_LOG_SEVERITY_LEVEL", "4")
    env.setdefault("ORT_LOG_VERBOSITY_LEVEL", "0")

    p = subprocess.Popen(
        ["cmd.exe", "/C", bat_win],
        cwd="/mnt/c",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
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
        if not _should_hide_line(line):
            print(line.rstrip("\n"))

    rc = p.wait()
    _append_raw_log(
        header=f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] cmd.exe /C {bat_win}  (exit={rc})",
        raw_lines=raw_lines
    )

    return {"returncode": rc, "success_token_seen": success_seen, "redirection_bug_seen": redirection_bug_seen}


def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _first_str(v) -> str:
    return v.strip() if isinstance(v, str) and v.strip() else ""


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
    return re.sub(r"^\s*Video\s*\d+\s*(?:[-:–—]\s*)", "", s, flags=re.IGNORECASE).strip()


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


def extract_lowerthird_text(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    for k in ("sentenceText", "text", "exact_script_text", "render_text"):
        t = _first_str(item.get(k))
        if t:
            return t
    return ""


def ensure_mov_ext(win_path: str) -> str:
    if win_path.lower().endswith(".mov"):
        return win_path
    if "." in Path(win_path).name:
        return str(Path(win_path).with_suffix(".mov"))
    return win_path + ".mov"


def main():
    if not DEFAULT_RS_TEMPLATE or DEFAULT_RS_TEMPLATE == "YOUR_RS_NAME":
        raise RuntimeError("Set DEFAULT_RS_TEMPLATE to your AE Render Settings template name.")
    if not DEFAULT_OM_TEMPLATE or DEFAULT_OM_TEMPLATE == "YOUR_OM_MOV":
        raise RuntimeError("Set DEFAULT_OM_TEMPLATE to your AE Output Module template name (MOV).")

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing {CONFIG_FILE}")
    if not SRC_LTS_JSON.exists():
        raise FileNotFoundError(f"Missing {SRC_LTS_JSON}")
    if not TEMPLATE_JSON.exists():
        raise FileNotFoundError(f"Missing {TEMPLATE_JSON}")

    ensure_runner_staging()

    cfg = load_json(CONFIG_FILE)
    video_name_raw = _first_str(cfg.get("video_name"))
    lang_raw = _first_str(cfg.get("language"))

    if not video_name_raw:
        raise RuntimeError("Missing 'video_name' in inputs/config/config.json")
    if not lang_raw:
        raise RuntimeError("Missing 'language' in inputs/config/config.json")

    video_name_json = sanitize_for_windows_folder(strip_video_prefix(video_name_raw))
    lang_cap = normalize_lang_cap(lang_raw)

    items = load_json(SRC_LTS_JSON)
    if not isinstance(items, list):
        raise RuntimeError(f"{SRC_LTS_JSON} must be a JSON list")

    project_path_win = load_template_aep_from_final_location(TEMPLATE_JSON)

    print(f"📊 Found {len(items)} lowerthirds in {SRC_LTS_JSON.name}")
    print(f"🎬 VideoName: {video_name_json}")
    print(f"🌍 Language : {lang_cap}")
    print(f"🧩 AEP (win): {project_path_win}")
    print(f"🎛️ RS Template: {DEFAULT_RS_TEMPLATE}")
    print(f"🎛️ OM Template: {DEFAULT_OM_TEMPLATE}  (MOV)")

    queued = 0
    failed = 0
    soft_ok = 0
    t0 = time.time()

    for seq_no, item in enumerate(items, start=1):
        text = extract_lowerthird_text(item)

        output_win = rf"Z:\Automated Dubbings\Projects\{video_name_json}\{lang_cap}\LowerThirds\VideoClips\lowerthird_{seq_no:03d}.mov"
        output_win = ensure_mov_ext(output_win)

        output_os = windows_path_to_os_path(output_win)
        output_os.parent.mkdir(parents=True, exist_ok=True)

        job = {
            "projectPath": project_path_win,
            "text": text,
            "output": output_win,
            "language": lang_cap,

            "textCompName": DEFAULT_TEXT_COMP_NAME,
            "textLayerName": DEFAULT_TEXT_LAYER_NAME,

            "renderComp": DEFAULT_RENDER_COMP,
            "rsTemplate": DEFAULT_RS_TEMPLATE,
            "omTemplate": DEFAULT_OM_TEMPLATE,
        }

        with JOB_FILE_LINUX.open("w", encoding="utf-8") as jf:
            json.dump(job, jf, ensure_ascii=False, indent=2)

        print("\n" + "=" * 70)
        print(f"➡️  SEND lowerthird_{seq_no:03d}")
        print(f"OUT : {output_win}")
        print("=" * 70)

        result = run_bat_from_wsl_staged_streaming()
        rc = result["returncode"]

        if rc == 0:
            queued += 1
            print(f"✅ Queued+Started: lowerthird_{seq_no:03d}")
        elif result["success_token_seen"]:
            soft_ok += 1
            queued += 1
            print(f"✅ Queued+Started (soft): lowerthird_{seq_no:03d}  (exit={rc})")
            if result["redirection_bug_seen"]:
                print("   ↳ Note: BAT hit 'Input redirection is not supported' AFTER queue. Fix BAT wait (no <nul).")
        else:
            failed += 1
            print(f"❌ Failed: lowerthird_{seq_no:03d} (exit={rc})")
            if result["redirection_bug_seen"]:
                print("   ↳ BAT reported redirection bug before success token. Fix BAT wait (no <nul).")

        time.sleep(WAIT_BETWEEN_JOBS_SECONDS)

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"✅ DONE. Queued OK: {queued} (soft_ok={soft_ok}) | Failed: {failed}")
    print(f"RAW CMD OUTPUT LOG: {linux_to_windows_path(str(RAW_OUT_LOG_LINUX))}")
    print(f"Time: {int(dt//60)}m {round(dt%60,2)}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
