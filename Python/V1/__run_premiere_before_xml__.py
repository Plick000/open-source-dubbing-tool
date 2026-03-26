#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import os
import time
import subprocess
from pathlib import Path
import base64

# ==========================================================
# EDIT THESE ONLY
# ==========================================================
PREMIERE_EXE_WIN = r"C:\Program Files\Adobe\Adobe Premiere Pro 2025\Adobe Premiere Pro.exe"

# ✅ NETWORK BASE (your new location)
FAST_CONTENT_BASE_WIN = r"Z:\Automated Dubbings\Projects"

PROJECT_FILE_DIRNAME = "Project File"   # EXACT: "Project File"

# ✅ Runner folder (local is fine)
RUNNER_DIR_WIN = r"C:\PPro_BeforeXML"

LOG_FILENAME = "___ExtendScript_Log___.txt"
WAIT_PROJECT_READY_MS = 180_000

CONFIG_FILENAME = "config.json"

# JSX files live here: dubbing-flask/Javascript/
JSX_DIRNAME_AT_ROOT = "Javascript"

PIPELINE_SCRIPTS = [
    "_SequenceTimebase24FPS.jsx",
    "_ExtractV5V6A4ItemsV1.jsx",
    "_ExtractTitlesAndLowerThirds.jsx",
    "_ExportXMLSequence.jsx",
]
# ==========================================================


class WindowsAgentError(RuntimeError):
    pass


def _looks_like_agent_failure(stderr: str) -> bool:
    s = (stderr or "").lower()
    return (
        "[powershell shim]" in s
        or "failed to reach windows agent" in s
        or "urlopen error" in s
        or "network is unreachable" in s
        or "connection refused" in s
        or "timed out" in s
        or "host.docker.internal" in s
    )


PS_RETRIES = int(os.environ.get("WIN_AGENT_PS_RETRIES", "2"))
PS_RETRY_SLEEP = float(os.environ.get("WIN_AGENT_PS_RETRY_SLEEP", "0.6"))


def win_to_wsl_path(path_win: str) -> str:
    """
    Convert Windows drive path like C:\\Foo\\Bar to WSL path /mnt/c/Foo/Bar.
    Works for local drives. If /mnt/<drive> doesn't exist, caller can fallback.
    """
    m = re.match(r"^([A-Za-z]):\\(.*)$", str(path_win))
    if not m:
        return ""
    drive = m.group(1).lower()
    rest  = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def ps_out(cmd: str) -> str:
    # Make the whole thing safe for Unicode + prevent progress CLIXML noise
    ps_script = (
        "$ProgressPreference='SilentlyContinue';"
        "$ErrorActionPreference='Stop';"
        + cmd
    )

    enc = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True,
        text=True
    )

    if p.returncode != 0:
        raise RuntimeError(
            f"PowerShell failed (rc={p.returncode}).\n"
            f"Command: {cmd}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}"
        )

    return (p.stdout or "").strip()



def ps(cmd: str) -> None:
    ps_script = (
        "$ProgressPreference='SilentlyContinue';"
        "$ErrorActionPreference='Stop';"
        + cmd
    )
    enc = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True,
        text=True
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"PowerShell failed (rc={p.returncode}).\n"
            f"Command: {cmd}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}"
        )

def ps_path(s: str) -> str:
    # PowerShell double-quoted literal suitable for file paths:
    # escape: `, ", and $ (to prevent variable expansion)
    s = str(s)
    s = s.replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{s}"'



def agent_preflight(max_wait_sec: int = 90) -> None:
    """
    Wait until the Windows agent is reachable before proceeding.
    Prevents next-language failures when agent temporarily stalls.
    """
    start = time.time()
    last_err = None

    while (time.time() - start) < max_wait_sec:
        try:
            # This is the cheapest call possible
            _ = ps_out("$env:COMPUTERNAME")
            return
        except WindowsAgentError as e:
            last_err = e
            time.sleep(2.0)  # backoff, do not spam
            continue

    # If still down after waiting, fail clearly
    raise WindowsAgentError(
        f"Windows agent stayed unreachable for {max_wait_sec}s.\nLast error:\n{last_err}"
    )



def ensure_dir_win(dir_win: str) -> None:
    ps(f"New-Item -Force -ItemType Directory -Path {ps_path(dir_win)} | Out-Null")

def write_text_win(path_win: str, content: str) -> None:
    """
    Write a text file to a Windows path.
    For local drives (C:, D:, etc), prefer WSL direct write via /mnt/<drive>/...
    to avoid PowerShell command-length truncation and Unicode mangling.
    Fallback to PowerShell only if /mnt mapping isn't available.
    """
    wsl_path = win_to_wsl_path(path_win)

    # Fast path: direct WSL write for local drives (fixes your current crash)
    if wsl_path and os.path.isdir(f"/mnt/{wsl_path.split('/')[2]}"):
        p = Path(wsl_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Keep content EXACT. Use UTF-8 without BOM.
        p.write_text(content, encoding="utf-8", newline="\n")
        return

    # Fallback: PowerShell method (keep your existing approach)
    content_escaped = content.replace("`", "``")
    ps(
        f"$p = {ps_path(path_win)}; "
        f"$c = @'\n{content_escaped}\n'@; "
        r"[System.IO.File]::WriteAllText($p, $c, (New-Object System.Text.UTF8Encoding($false)))"
    )


def test_path_win(path_win: str) -> bool:
    out = ps_out(f"Test-Path -LiteralPath {ps_path(path_win)}")
    return str(out).strip().lower() == "true"


def win_norm(p: str) -> str:
    return str(p).replace("/", "\\")


def clean_video_name(video_name: str) -> str:
    s = (video_name or "").strip()
    s = re.sub(r"^\s*Video\s*\d+\s*[-–—]\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_windows_name(name: str) -> str:
    if not name:
        return "Untitled"
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip().rstrip(".").rstrip()
    return name or "Untitled"


def language_cap(language_raw: str) -> str:
    lang = (language_raw or "").strip()
    if not lang:
        return "English"
    return lang[:1].upper() + lang[1:].lower()


def lang_prefix_upper(language_raw: str) -> str:
    m = {
        "english": "EN",
        "spanish": "ES",
        "french": "FR",
        "croatian": "HR",
        "czech": "CZ",
        "russian": "RU",
    
        "portuguese": "BR",
        "portuguese-br": "BR",
        "brazilian": "BR",
    
        "turkish": "TR",
        "korean": "KR",
        "arabic": "AR",
        "german": "DE",
        "italian": "IT",
        "polish": "PL",
        "danish": "DA",
        "dutch": "NL",
        "swedish": "SV",
        "norwegian": "NO",
        "japanese": "JA",
        "chinese": "ZH",
        "hindi": "HI",
        "urdu": "UR",
    
        "greek": "EL",
        "ukrainian": "UK",
        "vietnamese": "VI",
        "filipino": "TL",
        "hebrew": "HE",
        "finnish": "FI",
        "thai": "TH",
        "indonesian": "ID",
        "persian": "FA",
        "malay": "MS",
        "romanian": "RO",
        "hungarian": "HU",
        "serbian": "SR",
        "bulgarian": "BG",
        "tamil": "TM",
        "malayalam": "MA",
        "lithuanian": "LT",
        "latvian": "LV",
        "estonian": "ET",
        "kazakh": "KK",
        "georgian": "GO",
        "bangla": "BN",
        "telugu": "TE",
        "marathi": "MR",
        "gujarati": "GU",
        "hausa": "HA",
        "slovak": "SK",
        "swahili": "SW",
    }
    key = (language_raw or "").strip().lower()
    if key in m:
        return m[key]
    k = re.sub(r"[^a-z]", "", key)
    return (k[:2] or "XX").upper()


def build_project_prproj_win(video_name_raw: str, language_raw: str) -> str:
    r"""
    Builds:
    Z:\d\Automated Dubbings\Projects\{video}\{LanguageCap}\Project File\{PREFIX} - {video}.prproj
    """
    video_clean = sanitize_windows_name(clean_video_name(video_name_raw))
    langc = language_cap(language_raw)
    prefix = lang_prefix_upper(language_raw)

    prproj = (
        Path(FAST_CONTENT_BASE_WIN)
        / video_clean
        / langc
        / PROJECT_FILE_DIRNAME
        / f"{prefix} - {video_clean}.prproj"
    )
    return win_norm(str(prproj))


def read_inputs_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"config.json not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def main():
    # __run_premiere_before_xml__.py is here:
    # dubbing-flask/Python/V1/__run_premiere_before_xml__.py
    here = Path(__file__).resolve()
    project_root = here.parents[2]  # dubbing-flask/

    # Fixed paths based on your structure:
    jsx_dir = (project_root / JSX_DIRNAME_AT_ROOT).resolve()
    cfg_path = (project_root / "inputs" / "config" / CONFIG_FILENAME).resolve()

    agent_preflight() 

    # 1) Validate JSX scripts exist in dubbing-flask/Javascript/
    for fn in PIPELINE_SCRIPTS:
        p = (jsx_dir / fn).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Missing JSX in Javascript folder: {p}")

    # 2) Read config.json from dubbing-flask/inputs/config/config.json
    cfg = read_inputs_config(cfg_path)

    video_name_raw = (
        cfg.get("video_name")
        or cfg.get("VideoName")
        or cfg.get("videoName")
        or cfg.get("title")
        or ""
    )

    language_raw = (
        cfg.get("language")
        or cfg.get("Language")
        or cfg.get("lang")
        or ""
    )

    if not str(video_name_raw).strip():
        raise RuntimeError(
            "config.json missing a usable video name.\n"
            "Expected key like: video_name / VideoName / videoName / title"
        )

    if not str(language_raw).strip():
        raise RuntimeError(
            "config.json missing a usable language.\n"
            "Expected key like: language / Language / lang"
        )

    project_win = build_project_prproj_win(video_name_raw, language_raw)

    if not test_path_win(project_win):
        raise FileNotFoundError(
            "Project not found at expected location:\n"
            f"{project_win}\n\n"
            "Expected structure:\n"
            r"Z:\d\Automated Dubbings\Projects\{video}\{LanguageCap}\Project File\{PREFIX} - {video}.prproj"
        )

    # 3) Prepare runner folder
    ensure_dir_win(RUNNER_DIR_WIN)

    # 4) Copy config.json into runner path the JSX expects:
    #    C:\PPro_BeforeXML\inputs\config\config.json
    runner_cfg_dir = Path(RUNNER_DIR_WIN) / "inputs" / "config"
    ensure_dir_win(str(runner_cfg_dir))
    cfg_dst_win = win_norm(str(runner_cfg_dir / "config.json"))
    write_text_win(cfg_dst_win, cfg_path.read_text(encoding="utf-8"))

    # 5) Init log
    log_file_win = win_norm(str(Path(RUNNER_DIR_WIN) / LOG_FILENAME))
    write_text_win(log_file_win, "")

    # 6) Copy JSX scripts into runner (FROM dubbing-flask/Javascript/)
    win_script_paths = []
    for fn in PIPELINE_SCRIPTS:
        src = (jsx_dir / fn).resolve()
        dst_win = win_norm(str(Path(RUNNER_DIR_WIN) / fn))
        content = src.read_text(encoding="utf-8", errors="replace")
        write_text_win(dst_win, content)
        win_script_paths.append(dst_win)

    # 7) Launcher JSX
    launcher_win = win_norm(str(Path(RUNNER_DIR_WIN) / "launcher.jsx"))

    proj_jsx = project_win.replace("\\", "\\\\")
    wait_ms = int(WAIT_PROJECT_READY_MS)
    log_jsx = log_file_win.replace("\\", "\\\\")
    scripts_jsx = [p.replace("\\", "\\\\") for p in win_script_paths]
    scripts_array_literal = "[\n" + ",\n".join(['  "' + s + '"' for s in scripts_jsx]) + "\n]"

    launcher_jsx = f"""/* launcher.jsx (auto-generated)
   Logs to: {log_jsx}
*/
(function () {{
  var PROJECT_WIN = "{proj_jsx}";
  var WAIT_MS     = {wait_ms};
  var SCRIPTS     = {scripts_array_literal};
  var LOG_PATH    = "{log_jsx}";

  function sleep(ms){{ try{{$.sleep(ms);}}catch(e){{}} }}

  function ts() {{
    try {{
      var d = new Date();
      function pad(n){{ return (n<10?"0":"")+n; }}
      return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+" "+pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());
    }} catch(e) {{ return ""; }}
  }}

  function appendLog(line) {{
    try {{
      var f = new File(LOG_PATH);
      f.open("a");
      f.writeln(ts() + " " + String(line));
      f.close();
    }} catch(e) {{}}
  }}

  $.global.__LOG_PATH = LOG_PATH;
  $.global.__LOG = function(msg){{ appendLog(msg); }};

  function notify(msg, level) {{
    appendLog(msg);
    try {{
      if (app && typeof app.setSDKEventMessage === "function") {{
        app.setSDKEventMessage(String(msg), level || "info");
        return;
      }}
    }} catch (e) {{}}
    try {{ $.writeln(String(msg)); }} catch(e2) {{}}
  }}

  function waitReady(maxMs){{
    var t0 = new Date().getTime();
    while((new Date().getTime()-t0) < maxMs){{
      try {{
        if(app && app.project && app.project.rootItem) return true;
      }} catch(e) {{}}
      sleep(250);
    }}
    return false;
  }}

  function openProject() {{
    var projFile = new File(PROJECT_WIN);
    if(!projFile.exists) {{ notify("❌ Project not found: " + PROJECT_WIN, "error"); return false; }}
    try {{ app.openDocument(projFile.fsName); }}
    catch(e) {{ notify("❌ app.openDocument failed: " + e, "error"); return false; }}

    if(!waitReady(WAIT_MS)) {{
      notify("❌ Premiere not ready after opening project.", "error");
      return false;
    }}
    sleep(60000);
    return true;
  }}

  function runOne(scriptPath, idx) {{
    var f = new File(scriptPath);
    if(!f.exists) {{ notify("❌ Missing script: " + scriptPath, "error"); return false; }}

    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = "";

    try {{ $.evalFile(f); }}
    catch(e) {{
      notify("❌ Crash: " + e, "error");
      notify("❌ Step failed (" + (idx+1) + "): " + f.name + " :: crash", "error");
      return false;
    }}

    if ($.global.__PIPELINE_LAST_OK === false) {{
      notify("❌ Step failed (" + (idx+1) + "): " + f.name + " :: " + $.global.__PIPELINE_LAST_MSG, "error");
      return false;
    }}

    notify("✅ Step done (" + (idx+1) + "): " + f.name, "info");
    sleep(800);
    return true;
  }}

  notify("▶ Before-XML pipeline starting (" + SCRIPTS.length + " steps)...", "info");
  if (!openProject()) return;

  for (var i = 0; i < SCRIPTS.length; i++) {{
    if (!runOne(SCRIPTS[i], i)) {{
      notify("⛔ Pipeline stopped at step " + (i+1) + ".", "error");
      return;
    }}
  }}

  notify("🎉 Pipeline finished successfully (all steps).", "info");
}})();
"""
    write_text_win(launcher_win, launcher_jsx)

    # 8) Launch Premiere
    ps(
        'Start-Process -FilePath "{exe}" -ArgumentList \'/C\',\'es.processFile\',\'{launcher}\''
        .format(exe=PREMIERE_EXE_WIN, launcher=launcher_win)
    )

    print("✅ Launched Premiere BEFORE-XML runner")
    print("RunnerDir:", RUNNER_DIR_WIN)
    print("Project:", project_win)
    print("Language:", language_raw)
    print("Config copied to:", cfg_dst_win)
    print("Log file:", log_file_win)


if __name__ == "__main__":
    main()
