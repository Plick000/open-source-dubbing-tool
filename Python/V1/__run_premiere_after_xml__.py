#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import subprocess
import base64
import os
from pathlib import Path

# ==========================================================
# EDIT THESE ONLY
# ==========================================================
PREMIERE_EXE_WIN = r"C:\Program Files\Adobe\Adobe Premiere Pro 2025\Adobe Premiere Pro.exe"

# ✅ NETWORK BASE (same as reference)
FAST_CONTENT_BASE_WIN = r"Z:\Automated Dubbings\Projects"

PROJECT_FILE_DIRNAME = "Project File"   # EXACT: "Project File"

XML_FILENAME = "V9__dubbing__.xml"
TRANSITIONS_JSON_REL = r"output\JSON\A22__transitions_segments__.json"

AME_PRESET_NAME = "Video Render Preset"   # must match exported .epr filename

# ✅ Runner folder (keep your existing)
RUNNER_DIR_WIN = r"C:\PPro_AutoRun"
LOG_FILENAME = "___ExtendScript_Log___.txt"
WAIT_PROJECT_READY_MS = 180_000

CONFIG_FILENAME = "config.json"

# ✅ JSX files live here (same as reference): viralverse-dubbing-automation/Javascript/
JSX_DIRNAME_AT_ROOT = "Javascript"

PIPELINE_SCRIPTS = [
    "_ImportXML.jsx",
    "_RemoveV5V6A5TrackItemsV1.jsx",
    "_ShiftOrMergeSequence.jsx",
    "_AdjustTransitions.jsx",
    "_ChangeTitlesTextDetectionFieldsAndMove.jsx",
    "_ChangeLowerThirdsTextAndMove.jsx", 
    "_ChangeUltraTextAndMove.jsx",
    "_SendToAME.jsx",
]

# ✅ This is what your JSX reads relative to the runner folder:
#    BASE_DIR/output/JSON/A25__final_titles_merged_segments__.json
RUNNER_TITLES_JSON_REL = r"output\JSON\A25__final_titles_merged_segments__.json"
RUNNER_LOWERTHIRDS_JSON_REL = r"output\JSON\A18__computed_lowerthirds__.json"
RUNNER_ULTRA_JSON_REL = r"output\JSON\A27__computed_ultra_texts__.json"

# ✅ Preferred source location inside viralverse-dubbing-automation/
SOURCE_TITLES_JSON_REL_PRIMARY  = "output/JSON/A25__final_titles_merged_segments__.json"
SOURCE_TITLES_JSON_REL_FALLBACK = "output/JSON/A25__final_merged_Segments__.json"
SOURCE_LOWERTHIRDS_JSON_REL = "output/JSON/A18__computed_lowerthirds__.json"
SOURCE_ULTRA_JSON_REL = "output/JSON/A27__computed_ultra_texts__.json"
# ==========================================================


def ps_out(cmd: str) -> str:
    # suppress progress (CLIXML) + stop on error
    cmd = "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';" + cmd

    enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    return subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
        text=True
    ).strip()


def win_to_wsl_path(path_win: str) -> str:
    m = re.match(r"^([A-Za-z]):\\(.*)$", str(path_win))
    if not m:
        return ""
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"

def ps_path(s: str) -> str:
    # PowerShell double-quoted literal suitable for paths (prevents $ expansion)
    s = str(s).replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{s}"'


def ps(cmd: str) -> None:
    # suppress progress (CLIXML) + stop on error
    cmd = "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';" + cmd

    enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    subprocess.check_call(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
    )


def ps_literal(s: str) -> str:
    # PowerShell single-quoted literal string. Escapes ' by doubling it.
    # This prevents $variable expansion and keeps the path EXACT.
    return "'" + str(s).replace("'", "''") + "'"


def ensure_dir_win(dir_win: str) -> None:
    wsl_path = win_to_wsl_path(dir_win)
    if wsl_path:
        Path(wsl_path).mkdir(parents=True, exist_ok=True)
        return
    ps(f"New-Item -Force -ItemType Directory -Path {ps_path(dir_win)} | Out-Null")


def write_text_win(path_win: str, content: str) -> None:
    wsl_path = win_to_wsl_path(path_win)
    if wsl_path:
        p = Path(wsl_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        return

    content_escaped = content.replace("`", "``")
    ps(
        f"$p = {ps_path(path_win)}; "
        f"$c = @'\n{content_escaped}\n'@; "
        r"[System.IO.File]::WriteAllText($p, $c, (New-Object System.Text.UTF8Encoding($false)))"
    )

def test_path_win(path_win: str) -> bool:
    wsl_path = win_to_wsl_path(path_win)
    if wsl_path:
        return Path(wsl_path).exists()
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


def build_video_lang_dir_win(video_name_raw: str, language_raw: str) -> Path:
    clean = sanitize_windows_name(clean_video_name(video_name_raw))
    langc = language_cap(language_raw)
    return Path(FAST_CONTENT_BASE_WIN) / clean / langc


def build_project_prproj_win(video_name_raw: str, language_raw: str) -> str:
    """
    Builds:
    Z:\Automated Dubbings\Projects\{video}\{LanguageCap}\Project File\{PREFIX} - {video}.prproj
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


def read_inputs_config(cfg_path: Path) -> dict:
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.json not found: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def find_preset_epr_win(preset_name: str) -> str:
    target = preset_name + ".epr"
    ps_script = r"""
$target = "%s"
$roots = @()

if ($env:APPDATA) {
  $roots += (Join-Path $env:APPDATA "Adobe\Adobe Media Encoder")
  $roots += (Join-Path $env:APPDATA "Adobe\Adobe Media Encoder\Presets")
}
if ($env:USERPROFILE) {
  $roots += (Join-Path $env:USERPROFILE "Documents\Adobe\Adobe Media Encoder")
  $roots += (Join-Path $env:USERPROFILE "Documents\Adobe\Adobe Media Encoder\Presets")
}

$roots = $roots | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

foreach ($r in $roots) {
  try {
    $hit = Get-ChildItem -Path $r -Recurse -File -Filter $target -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { $hit.FullName; exit 0 }
  } catch {}
}

if ($env:APPDATA) {
  $wide = Join-Path $env:APPDATA "Adobe"
  if (Test-Path $wide) {
    try {
      $hit2 = Get-ChildItem -Path $wide -Recurse -File -Filter $target -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($hit2) { $hit2.FullName; exit 0 }
    } catch {}
  }
}
""".strip() % (target.replace('"', '""'))

    out = ps_out(ps_script)
    return out.strip()


def main():
    # Same as reference: viralverse-dubbing-automation/Python/V1/<this_file>.py => parents[2] is viralverse-dubbing-automation/
    here = Path(__file__).resolve()
    project_root = here.parents[2]

    # ✅ Same reference locations
    jsx_dir = (project_root / JSX_DIRNAME_AT_ROOT).resolve()
    cfg_path = (project_root / "inputs" / "config" / CONFIG_FILENAME).resolve()

    # 1) Validate JSX scripts exist in viralverse-dubbing-automation/Javascript/
    for fn in PIPELINE_SCRIPTS:
        p = (jsx_dir / fn).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Missing JSX in Javascript folder: {p}")

    # 2) Read config.json from viralverse-dubbing-automation/inputs/config/config.json
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

    video_clean = sanitize_windows_name(clean_video_name(video_name_raw))
    prefix = lang_prefix_upper(language_raw)

    # ✅ Auto-build project path (same structure as reference)
    project_win = build_project_prproj_win(video_name_raw, language_raw)

    if not test_path_win(project_win):
        raise FileNotFoundError(
            "Project not found at expected location:\n"
            f"{project_win}\n\n"
            "Expected structure:\n"
            r"Z:\Automated Dubbings\Projects\{video}\{LanguageCap}\Project File\{PREFIX} - {video}.prproj"
        )

    base_dir_win = build_video_lang_dir_win(video_name_raw, language_raw)

    xml_path_win = win_norm(str(base_dir_win / "XML" / XML_FILENAME))
    transitions_json_win = win_norm(str(base_dir_win / Path(TRANSITIONS_JSON_REL)))

    rendered_dir_win = win_norm(str(Path(FAST_CONTENT_BASE_WIN) / video_clean / "RenderedVideos"))
    output_mp4_win = win_norm(str(Path(rendered_dir_win) / f"{prefix} - {video_clean}.mp4"))

    # 3) Prepare runner folder + rendered folder
    ensure_dir_win(RUNNER_DIR_WIN)
    ensure_dir_win(rendered_dir_win)

    # 4) Copy config.json into runner path (same as reference runner)
    runner_cfg_dir = Path(RUNNER_DIR_WIN) / "inputs" / "config"
    ensure_dir_win(str(runner_cfg_dir))
    cfg_dst_win = win_norm(str(runner_cfg_dir / "config.json"))
    write_text_win(cfg_dst_win, cfg_path.read_text(encoding="utf-8"))

    # 5) Init log
    log_file_win = win_norm(str(Path(RUNNER_DIR_WIN) / LOG_FILENAME))
    write_text_win(log_file_win, "")

    # 6) Preset discovery (kept)
    preset_epr_win = find_preset_epr_win(AME_PRESET_NAME)
    preset_epr_win = win_norm(preset_epr_win) if preset_epr_win else ""

    # 7) Copy JSX scripts into runner (FROM viralverse-dubbing-automation/Javascript/)
    win_script_paths = []
    for fn in PIPELINE_SCRIPTS:
        src = (jsx_dir / fn).resolve()
        dst_win = win_norm(str(Path(RUNNER_DIR_WIN) / fn))
        content = src.read_text(encoding="utf-8", errors="replace")
        write_text_win(dst_win, content)
        win_script_paths.append(dst_win)

    # 8) ✅ Copy merged titles JSON into runner where JSX expects it
    src_titles_primary = (project_root / SOURCE_TITLES_JSON_REL_PRIMARY).resolve()
    src_titles_fallback = (project_root / SOURCE_TITLES_JSON_REL_FALLBACK).resolve()

    if src_titles_primary.exists():
        src_titles = src_titles_primary
    elif src_titles_fallback.exists():
        src_titles = src_titles_fallback
    else:
        raise FileNotFoundError(
            "Merged titles JSON not found in viralverse-dubbing-automation.\n"
            "Looked for:\n"
            f" - {src_titles_primary}\n"
            f" - {src_titles_fallback}\n\n"
            "Create it first, then re-run this automation."
        )

    runner_titles_json_win = win_norm(str(Path(RUNNER_DIR_WIN) / Path(RUNNER_TITLES_JSON_REL)))
    ensure_dir_win(win_norm(str(Path(RUNNER_DIR_WIN) / "output" / "JSON")))
    write_text_win(runner_titles_json_win, src_titles.read_text(encoding="utf-8"))


    # 8B) ✅ Copy computed lowerthirds JSON into runner where JSX expects it
    src_lowerthirds = (project_root / SOURCE_LOWERTHIRDS_JSON_REL).resolve()
    if not src_lowerthirds.exists():
        raise FileNotFoundError(
            "LowerThirds JSON not found in viralverse-dubbing-automation.\n"
            "Looked for:\n"
            f" - {src_lowerthirds}\n\n"
            "Create it first, then re-run this automation."
        )

    runner_lowerthirds_json_win = win_norm(
        str(Path(RUNNER_DIR_WIN) / Path(RUNNER_LOWERTHIRDS_JSON_REL))
    )

    ensure_dir_win(win_norm(str(Path(RUNNER_DIR_WIN) / "output" / "JSON")))
    write_text_win(runner_lowerthirds_json_win, src_lowerthirds.read_text(encoding="utf-8"))

    # 8C) ✅ Copy computed ultra texts JSON into runner where JSX expects it
    src_ultra = (project_root / SOURCE_ULTRA_JSON_REL).resolve()
    if not src_ultra.exists():
        raise FileNotFoundError(
            "Ultra Texts JSON not found in dubbing-flask.\n"
            "Looked for:\n"
            f" - {src_ultra}\n\n"
            "Create it first, then re-run this automation."
        )

    runner_ultra_json_win = win_norm(
        str(Path(RUNNER_DIR_WIN) / Path(RUNNER_ULTRA_JSON_REL))
    )

    ensure_dir_win(win_norm(str(Path(RUNNER_DIR_WIN) / "output" / "JSON")))
    write_text_win(runner_ultra_json_win, src_ultra.read_text(encoding="utf-8"))

    # 9) Write run_config.json (required by _ImportXML, _AdjustTransitions, _SendToAME, etc.)
    run_config_win = win_norm(str(Path(RUNNER_DIR_WIN) / "run_config.json"))
    run_cfg = {
        "XMLPath": xml_path_win,
        "TransitionsJSONPath": transitions_json_win,
        "PresetName": AME_PRESET_NAME,
        "PresetEprPath": preset_epr_win,
        "OutputMp4Path": output_mp4_win,
        "LogFilePath": log_file_win,
    
        # ✅ Safe extra paths (won’t break other JSX even if unused)
        "LowerThirdsJSONPath": runner_lowerthirds_json_win,
        "UltraTextsJSONPath": runner_ultra_json_win,
    }
    write_text_win(run_config_win, json.dumps(run_cfg, ensure_ascii=False, indent=2))

    # 10) Launcher JSX (unchanged logic)
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

  // ✅ Stabilization gate (prevents crash/hang when next JSX runs too soon after XML import)
  var ENABLE_STABILIZE_AFTER_IMPORT = true;
  var IMPORT_SCRIPT_NAME = "_ImportXML.jsx";
  var TARGET_SEQUENCE_NAME = "Automated Timeline";

  // Premiere 2025 can be slow after import; keep generous.
  var STABILIZE_TIMEOUT_MS = 12000;   // 12s
  var STABILIZE_POLL_MS    = 10000;
  var STABILIZE_EXTRA_SLEEP_MS = 2000;

  // ✅ Warmup: when script is executed at app startup, calling openDocument too early can hang.
  var STARTUP_WARMUP_MS = 20000;

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

  // -------------------------
  // Readiness gates
  // -------------------------
  function waitApp(maxMs){{
    var t0 = new Date().getTime();
    while((new Date().getTime()-t0) < maxMs){{
      try {{
        if(app && app.project) return true;
      }} catch(e) {{}}
      sleep(250);
    }}
    return false;
  }}

  function waitReady(maxMs){{
    var t0 = new Date().getTime();
    while((new Date().getTime()-t0) < maxMs){{
      try {{
        // rootItem exists once a project is properly loaded
        if(app && app.project && app.project.rootItem) return true;
      }} catch(e) {{}}
      sleep(250);
    }}
    return false;
  }}

  function desiredProjectAlreadyOpen() {{
    try {{
      if (app && app.project && app.project.rootItem && app.project.name) {{
        var desiredName = (new File(PROJECT_WIN)).name;
        return String(app.project.name) === String(desiredName);
      }}
    }} catch(e) {{}}
    return false;
  }}

  function openProject() {{
    // Warmup to avoid openDocument too early (common crash/hang when launched via CLI automation)
    if (!waitApp(WAIT_MS)) {{
      notify("❌ Premiere app not available in time.", "error");
      return false;
    }}
    sleep(STARTUP_WARMUP_MS);

    // ✅ If correct project already open, skip openDocument entirely (critical)
    if (desiredProjectAlreadyOpen()) {{
      notify("ℹ️ Project already open — skipping openDocument().", "info");
      return true;
    }}

    var projFile = new File(PROJECT_WIN);
    if(!projFile.exists) {{
      notify("❌ Project not found: " + PROJECT_WIN, "error");
      return false;
    }}

    // Extra safety: ensure app is responsive right before openDocument
    if(!waitApp(30000)) {{
      notify("❌ Premiere not responsive before opening project.", "error");
      return false;
    }}

    try {{
      app.openDocument(projFile.fsName);
    }} catch(e) {{
      notify("❌ app.openDocument failed: " + e, "error");
      return false;
    }}

    if(!waitReady(WAIT_MS)) {{
      notify("❌ Premiere not ready after opening project.", "error");
      return false;
    }}

    sleep(200000);
    return true;
  }}

  // -------------------------
  // Stabilization helpers
  // -------------------------
  function safeNumSequences(){{
    try {{
      if(!app || !app.project || !app.project.sequences) return null;
      return app.project.sequences.numSequences;
    }} catch(e) {{ return null; }}
  }}

  function isSequencePresentByName(name){{
    var n = safeNumSequences();
    if(n === null) return false;
    for (var i = 0; i < n; i++) {{
      try {{
        var s = app.project.sequences[i];
        if (s && String(s.name) === String(name)) return true;
      }} catch(e) {{}}
    }}
    return false;
  }}

  function isAnyActiveSequence(){{
    try {{ return !!(app && app.project && app.project.activeSequence); }}
    catch(e) {{ return false; }}
  }}

  function waitForSequenceStable(seqName, timeoutMs){{
    var t0 = new Date().getTime();
    while ((new Date().getTime() - t0) < timeoutMs) {{
      var exists = false;
      var active = false;
      try {{ exists = isSequencePresentByName(seqName); }} catch(e1) {{ exists = false; }}
      try {{ active = isAnyActiveSequence(); }} catch(e2) {{ active = false; }}
      if (exists && active) return true;
      sleep(STABILIZE_POLL_MS);
    }}
    return false;
  }}

  function runOne(scriptPath, idx) {{
    var f = new File(scriptPath);
    if(!f.exists) {{
      notify("❌ Missing script: " + scriptPath, "error");
      return false;
    }}

    // ✅ Pre-step readiness gate
    if(!waitApp(30000)) {{
      notify("❌ Premiere not responsive BEFORE step " + (idx+1) + " (" + f.name + ").", "error");
      return false;
    }}

    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = "";

    notify("➡️ Step " + (idx+1) + "/" + SCRIPTS.length + " starting: " + f.name, "info");

    try {{
      $.evalFile(f);
    }} catch(e) {{
      notify("❌ Crash: " + e, "error");
      notify("❌ Step failed (" + (idx+1) + "): " + f.name + " :: crash", "error");
      return false;
    }}

    if ($.global.__PIPELINE_LAST_OK === false) {{
      notify("❌ Step failed (" + (idx+1) + "): " + f.name + " :: " + $.global.__PIPELINE_LAST_MSG, "error");
      return false;
    }}

    // ✅ Stabilize ONLY after _ImportXML.jsx
    if (ENABLE_STABILIZE_AFTER_IMPORT && String(f.name) === String(IMPORT_SCRIPT_NAME)) {{
      notify("⏳ Stabilizing after XML import (sequence + active timeline)...", "info");

      var stable = waitForSequenceStable(TARGET_SEQUENCE_NAME, STABILIZE_TIMEOUT_MS);
      if (!stable) {{
        notify("❌ Stabilization timeout after XML import. Stopping pipeline to prevent Premiere crash.", "error");
        return false;
      }}

      sleep(STABILIZE_EXTRA_SLEEP_MS);

      // One more readiness check after stabilization
      if(!waitApp(30000)) {{
        notify("❌ Premiere not responsive after import stabilization. Aborting.", "error");
        return false;
      }}

      notify("✅ Stabilized after XML import. Continuing.", "info");
    }}

    // ✅ Post-step readiness gate
    if(!waitApp(30000)) {{
      notify("❌ Premiere not responsive AFTER step " + (idx+1) + " (" + f.name + ").", "error");
      return false;
    }}

    notify("✅ Step done (" + (idx+1) + "): " + f.name, "info");
    sleep(800);
    return true;
  }}

  notify("▶ Pipeline starting (" + SCRIPTS.length + " steps)...", "info");

  if (!openProject()) {{
    notify("⛔ Pipeline aborted (project open failed).", "error");
    return false;
  }}

  for (var i = 0; i < SCRIPTS.length; i++) {{
    if (!runOne(SCRIPTS[i], i)) {{
      notify("⛔ Pipeline stopped at step " + (i+1) + ".", "error");
      return false;
    }}
  }}

  notify("🎉 Pipeline finished successfully (all steps).", "info");
  true; // prevents "undefined" eval result
}})();
"""
    write_text_win(launcher_win, launcher_jsx)

    # 11) Launch Premiere
    ps(
        'Start-Process -FilePath "{exe}" -ArgumentList \'/C\',\'es.processFile\',\'{launcher}\''
        .format(exe=PREMIERE_EXE_WIN, launcher=launcher_win)
    )

    print("✅ Launched Premiere pipeline runner")
    print("RunnerDir:", RUNNER_DIR_WIN)
    print("Project:", project_win)
    print("Language:", language_raw)
    print("Config copied to:", cfg_dst_win)
    print("run_config.json:", run_config_win)
    print("Titles JSON copied to:", runner_titles_json_win)
    print("PresetEprPath:", preset_epr_win or "(NOT FOUND)")
    print("OutputMp4Path:", output_mp4_win)
    print("Log file:", log_file_win)
    print("LowerThirds JSON copied to:", runner_lowerthirds_json_win)


if __name__ == "__main__":
    main()
