/* Premiere Pro 2025 — ExtendScript
   Reads XML path from run_config.json (same folder as this JSX) -> XMLPath
   ✅ Strong auto-dismiss Translation Report popup (focus + click OK)

   FIX (CRITICAL):
   - When "Translation Report" popup does NOT appear, blocking $.sleep polling can freeze Premiere
     and accessing app.project.sequences while import is still finalizing can crash.
   - We keep your popup logic, but move the post-import waiting into $.scheduleTask() (non-blocking),
     and we harden sequence access to avoid crashes.

   NOTE:
   - Does NOT break current logic.
   - Still opens/renames the created sequence exactly like your original behavior.
*/

(function () {
  // ==============================
  // CONFIG (EDIT THIS ONLY)
  // ==============================
  var TARGET_BIN_NAME = "Automated Timeline";
  var TARGET_SEQUENCE_NAME = "Automated Timeline";

  // Original polling (fast path)
  var POLL_TRIES = 12;
  var POLL_SLEEP_MS = 700;

  // Extended wait (fallback) for cases where import is slow and no popup appears
  var EXTENDED_WAIT_ENABLE = true;
  var EXTENDED_WAIT_MAX_MS = 60000;     // extra time window after fast poll
  var EXTENDED_WAIT_SLEEP_MS = 1000;    // check interval during extended wait

  // Uses run_config.json in same folder as JSX
  var RUN_CONFIG_FILENAME = "run_config.json";

  // Popup auto-close
  var AUTO_CLOSE_TRANSLATION_REPORT = true;

  // Arm 2 watchers (fast + slow) for reliability
  var AUTO_OK_DELAY_FAST_MS = 800;     // catches quick popups
  var AUTO_OK_DELAY_SLOW_MS = 4500;    // catches delayed popups
  var TRANSLATION_REPORT_TIMEOUT_MS = 120000;

  // ✅ NEW: Avoid blocking Premiere UI by using scheduled checks (non-blocking)
  var USE_SCHEDULE_TASK_WAIT = true;

  // Unique global key for scheduled state
  var GLOBAL_STATE_KEY = "__VV_IMPORTXML_STATE__";
  // ==============================

  function alertErr(msg) {
    alert("❌ " + msg);
    $.writeln("❌ " + msg);
  }
  function log(msg) { $.writeln(msg); }

  function isWindows() {
    try { return String($.os).toLowerCase().indexOf("windows") !== -1; } catch (e) { return false; }
  }

  function getMainBin() { return app.project.rootItem; }

  function isBin(item) {
    try { return item && item.type === ProjectItemType.BIN; } catch (e) { return false; }
  }

  function findChildBinByName(parentBin, name) {
    if (!parentBin || parentBin.children === undefined) return null;
    var num = parentBin.children.numItems;
    for (var i = 0; i < num; i++) {
      var child = parentBin.children[i];
      if (child && isBin(child) && String(child.name) === String(name)) return child;
    }
    return null;
  }

  function ensureBin(parentBin, name) {
    var existing = findChildBinByName(parentBin, name);
    if (existing) return existing;
    var created = parentBin.createBin(name);
    if (!created) {
      alertErr("Failed to create bin: " + name);
      return null;
    }
    return created;
  }

  // -------------------------------------------------------
  // File helpers
  // -------------------------------------------------------
  function writeTextFile(fsPath, content) {
    var f = new File(fsPath);
    if (!f.open("w")) return false;
    f.encoding = "UTF-8";
    f.lineFeed = "Windows";
    f.write(content);
    f.close();
    return true;
  }

  function readTextFile(fsPath) {
    var f = new File(fsPath);
    if (!f.exists) return null;
    if (!f.open("r")) return null;
    f.encoding = "UTF-8";
    var s = f.read();
    f.close();
    return s;
  }

  function parseJsonSafe(txt) {
    if (!txt) return null;
    try {
      if (typeof JSON !== "undefined" && JSON && typeof JSON.parse === "function") {
        return JSON.parse(txt);
      }
    } catch (e1) {}
    try { return eval("(" + txt + ")"); } catch (e2) {}
    return null;
  }

  function joinWin(a, b) {
    a = String(a || "").replace(/[\\\/]+$/g, "");
    b = String(b || "").replace(/^[\\\/]+/g, "");
    return a + "\\" + b;
  }

  function dirnameOfThisScript() {
    try { return new File($.fileName).parent.fsName; } catch (e) { return ""; }
  }

  function fileExists(p) {
    try { return p && (new File(p)).exists; } catch (e) { return false; }
  }

  // -------------------------------------------------------
  // RunConfig loader (same folder as JSX)
  // -------------------------------------------------------
  function resolveRunConfigPath() {
    var base = dirnameOfThisScript();
    if (base) {
      var p = joinWin(base, RUN_CONFIG_FILENAME);
      if (fileExists(p)) return p;
    }
    // fallback hard path
    var hard = "C:\\PPro_AutoRun\\" + RUN_CONFIG_FILENAME;
    if (fileExists(hard)) return hard;
    return "";
  }

  function loadRunConfig() {
    var p = resolveRunConfigPath();
    if (!p) {
      alertErr(
        "run_config.json not found.\n" +
        "Expected next to this JSX (same folder), or at:\n" +
        "C:\\PPro_AutoRun\\" + RUN_CONFIG_FILENAME
      );
      return null;
    }
    var raw = readTextFile(p);
    if (!raw) {
      alertErr("Could not read run_config.json:\n" + p);
      return null;
    }
    var cfg = parseJsonSafe(raw);
    if (!cfg) {
      alertErr("Could not parse run_config.json:\n" + p);
      return null;
    }
    cfg.__path = p;
    return cfg;
  }

  // -------------------------------------------------------
  // Hidden PowerShell runner (no terminal)
  // -------------------------------------------------------
  function runPSHiddenAsync(ps1Path, argsLine) {
    var tempFolder = Folder.temp.fsName;
    var stamp = (new Date()).getTime();
    var vbsPath = tempFolder + "\\ppro_run_hidden_" + stamp + ".vbs";

    var psCmd =
      'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
      ps1Path +
      '" ' + (argsLine || "");

    var safe = psCmd.replace(/"/g, '""');

    var vbs = [
      'Set WshShell = CreateObject("WScript.Shell")',
      'WshShell.Run "' + safe + '", 0, False'
    ].join("\r\n");

    if (!writeTextFile(vbsPath, vbs)) return false;
    return (new File(vbsPath)).execute(); // async
  }

  // -------------------------------------------------------
  // Strong Translation Report killer (focus + click OK)
  // -------------------------------------------------------
  function startTranslationReportAutoOk(delayMs, timeoutMs) {
    if (!AUTO_CLOSE_TRANSLATION_REPORT) return;
    if (!isWindows()) return;

    var ps = [
      "param([int]$DelayMs=800,[int]$TimeoutMs=60000)",
      "Add-Type @\"",
      "using System;",
      "using System.Text;",
      "using System.Runtime.InteropServices;",
      "public static class Win32 {",
      "  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);",
      "  public delegate bool EnumChildProc(IntPtr hWnd, IntPtr lParam);",
      "  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);",
      "  [DllImport(\"user32.dll\")] public static extern bool EnumChildWindows(IntPtr hWnd, EnumChildProc lpEnumFunc, IntPtr lParam);",
      "  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);",
      "  [DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int GetWindowTextLength(IntPtr hWnd);",
      "  [DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);",
      "  [DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);",
      "  [DllImport(\"user32.dll\")] public static extern IntPtr GetDlgItem(IntPtr hDlg, int nIDDlgItem);",
      "  [DllImport(\"user32.dll\")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);",
      "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);",
      "  [DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr hWnd);",
      "  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);",
      "  [DllImport(\"user32.dll\")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);",
      "  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();",
      "  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr lpdwProcessId);",
      "  [DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);",
      "  [DllImport(\"user32.dll\")] public static extern IntPtr SetFocus(IntPtr hWnd);",
      "}",
      "\"@",
      "Add-Type -AssemblyName System.Windows.Forms",
      "$BM_CLICK = 0x00F5",
      "$SW_RESTORE = 9",
      "$HWND_TOPMOST   = [IntPtr](-1)",
      "$HWND_NOTOPMOST = [IntPtr](-2)",
      "$SWP_NOMOVE = 0x0002",
      "$SWP_NOSIZE = 0x0001",
      "$SWP_SHOWWINDOW = 0x0040",
      "if($DelayMs -gt 0){ Start-Sleep -Milliseconds $DelayMs }",
      "",
      "function Get-Text([IntPtr]$h){",
      "  $len = [Win32]::GetWindowTextLength($h)",
      "  if($len -le 0){ return \"\" }",
      "  $sb = New-Object System.Text.StringBuilder ($len + 1)",
      "  [void][Win32]::GetWindowText($h, $sb, $sb.Capacity)",
      "  return $sb.ToString()",
      "}",
      "function Get-Class([IntPtr]$h){",
      "  $sb = New-Object System.Text.StringBuilder 256",
      "  [void][Win32]::GetClassName($h, $sb, $sb.Capacity)",
      "  return $sb.ToString()",
      "}",
      "",
      "$start = Get-Date",
      "while(((Get-Date) - $start).TotalMilliseconds -lt $TimeoutMs){",
      "  $dlg = [IntPtr]::Zero",
      "  [Win32]::EnumWindows({",
      "    param($h, $l)",
      "    if(-not [Win32]::IsWindowVisible($h)){ return $true }",
      "    $t = (Get-Text $h)",
      "    if($t -like '*Translation Report*'){ $script:dlg = $h; return $false }",
      "    return $true",
      "  }, [IntPtr]::Zero) | Out-Null",
      "",
      "  if($dlg -ne [IntPtr]::Zero){",
      "    [void][Win32]::ShowWindow($dlg, $SW_RESTORE)",
      "    [void][Win32]::SetWindowPos($dlg, $HWND_TOPMOST, 0,0,0,0, $SWP_NOMOVE -bor $SWP_NOSIZE -bor $SWP_SHOWWINDOW)",
      "    Start-Sleep -Milliseconds 40",
      "    [void][Win32]::SetWindowPos($dlg, $HWND_NOTOPMOST, 0,0,0,0, $SWP_NOMOVE -bor $SWP_NOSIZE -bor $SWP_SHOWWINDOW)",
      "    [void][Win32]::BringWindowToTop($dlg)",
      "",
      "    $fg = [Win32]::GetForegroundWindow()",
      "    $t1 = [Win32]::GetWindowThreadProcessId($fg, [IntPtr]::Zero)",
      "    $t2 = [Win32]::GetWindowThreadProcessId($dlg, [IntPtr]::Zero)",
      "    if($t1 -ne 0 -and $t2 -ne 0 -and $t1 -ne $t2){",
      "      [void][Win32]::AttachThreadInput($t2, $t1, $true)",
      "      [void][Win32]::SetForegroundWindow($dlg)",
      "      [void][Win32]::AttachThreadInput($t2, $t1, $false)",
      "    } else {",
      "      [void][Win32]::SetForegroundWindow($dlg)",
      "    }",
      "    Start-Sleep -Milliseconds 80",
      "",
      "    $ok = [Win32]::GetDlgItem($dlg, 1)",
      "    if($ok -ne [IntPtr]::Zero){",
      "      [void][Win32]::SetFocus($ok)",
      "      [void][Win32]::SendMessage($ok, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)",
      "      break",
      "    }",
      "",
      "    $btn = [IntPtr]::Zero",
      "    [Win32]::EnumChildWindows($dlg, {",
      "      param($ch, $l)",
      "      if((Get-Class $ch) -ne 'Button'){ return $true }",
      "      $tx = (Get-Text $ch)",
      "      if($tx -match '^\\s*&?OK\\s*$'){ $script:btn = $ch; return $false }",
      "      return $true",
      "    }, [IntPtr]::Zero) | Out-Null",
      "    if($btn -ne [IntPtr]::Zero){",
      "      [void][Win32]::SetFocus($btn)",
      "      [void][Win32]::SendMessage($btn, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)",
      "      break",
      "    }",
      "",
      "    try {",
      "      $ws = New-Object -ComObject WScript.Shell",
      "      [void]$ws.AppActivate('Translation Report')",
      "      Start-Sleep -Milliseconds 50",
      "      [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')",
      "      break",
      "    } catch {}",
      "  }",
      "",
      "  Start-Sleep -Milliseconds 120",
      "}"
    ].join("\r\n");

    var psPath = Folder.temp.fsName + "\\ppro_autook_translation_report_strong.ps1";
    if (!writeTextFile(psPath, ps)) {
      log("⚠️ Could not write popup helper PS1.");
      return;
    }

    runPSHiddenAsync(psPath, "-DelayMs " + Number(delayMs || 0) + " -TimeoutMs " + Number(timeoutMs || 60000));
    log("🛡️ Translation Report auto-OK armed (delay " + Number(delayMs || 0) + "ms, timeout " + Number(timeoutMs || 60000) + "ms).");
  }

  // -------------------------------------------------------
  // IMPORT
  // -------------------------------------------------------
  function importXmlToBin(xmlFsName, targetBin) {
    // Arm TWO watchers to avoid timing misses (unchanged logic)
    startTranslationReportAutoOk(AUTO_OK_DELAY_FAST_MS, TRANSLATION_REPORT_TIMEOUT_MS);
    startTranslationReportAutoOk(AUTO_OK_DELAY_SLOW_MS, TRANSLATION_REPORT_TIMEOUT_MS);

    var ok = app.project.importFiles([xmlFsName], true, targetBin, false);
    if (!ok) {
      alertErr("importFiles() returned false. Check XML validity/access.");
      return false;
    }
    log("✅ Imported XML into bin: " + targetBin.name);
    return true;
  }

  // -------------------------------------------------------
  // SAFE sequence access (prevents crash during heavy import finalization)
  // -------------------------------------------------------
  function safeNumSequences() {
    try {
      if (!app || !app.project || !app.project.sequences) return null;
      return app.project.sequences.numSequences;
    } catch (e) { return null; }
  }

  function safeGetSequenceAt(i) {
    try {
      return app.project.sequences[i];
    } catch (e) { return null; }
  }

  // -------- Sequence snapshot (reliable) --------
  function seqKey(seq, idx) {
    try {
      if (seq && seq.sequenceID !== undefined && seq.sequenceID !== null)
        return "ID::" + String(seq.sequenceID);
    } catch (e) {}
    return "NAMEIDX::" + String(seq ? seq.name : "") + "::" + String(idx);
  }

  function snapshotSequencesSafe() {
    var set = {};
    var n = safeNumSequences();
    if (n === null || n === undefined) return set;
    for (var i = 0; i < n; i++) {
      var s = safeGetSequenceAt(i);
      if (s) set[seqKey(s, i)] = true;
    }
    return set;
  }

  function getNewSequencesSafe(beforeSet) {
    var out = [];
    var n = safeNumSequences();
    if (n === null || n === undefined) return out; // not ready yet
    for (var i = 0; i < n; i++) {
      var s = safeGetSequenceAt(i);
      if (!s) continue;
      if (!beforeSet[seqKey(s, i)]) out.push(s);
    }
    return out;
  }

  function findSeqByExactNameSafe(name) {
    var n = safeNumSequences();
    if (n === null || n === undefined) return null;
    for (var i = 0; i < n; i++) {
      var s = safeGetSequenceAt(i);
      if (s && String(s.name) === String(name)) return s;
    }
    return null;
  }

  function renameSequence(seq, newName) {
    try { seq.name = String(newName); return true; } catch (e) { return false; }
  }

  function openSequenceFixed(seq) {
    if (!seq) return false;

    try {
      if (app.project && typeof app.project.openSequence === "function") {
        if (seq.sequenceID !== undefined && seq.sequenceID !== null) {
          app.project.openSequence(seq.sequenceID);
          log("✅ Opened sequence via app.project.openSequence(): " + seq.name);
          return true;
        }
      }
    } catch (e1) {}

    try {
      if (app.enableQE) app.enableQE();
      if (typeof qe !== "undefined" && qe.project && qe.project.getSequenceAt) {
        var count = null;
        try { count = qe.project.numSequences; } catch (e2) {}
        if (count === null || count === undefined) { try { count = qe.project.getNumSequences(); } catch (e3) {} }

        if (count !== null && count !== undefined) {
          for (var i = 0; i < count; i++) {
            var qSeq = qe.project.getSequenceAt(i);
            if (!qSeq) continue;

            var qName = "";
            try { qName = String(qSeq.name); } catch (e4) { try { qName = String(qSeq.getName()); } catch (e5) {} }

            if (qName === String(seq.name)) {
              try {
                if (typeof qSeq.openInTimeline === "function") qSeq.openInTimeline();
                else if (typeof qSeq.open === "function") qSeq.open();
                else if (typeof qSeq.activate === "function") qSeq.activate();
                log("✅ Opened sequence via QE DOM: " + qName);
                return true;
              } catch (e6) {}
            }
          }
        }
      }
    } catch (e7) {}

    alertErr("Failed to open sequence (no supported open method found).");
    return false;
  }

  // -------------------------------------------------------
  // ✅ NON-BLOCKING wait logic using $.scheduleTask()
  // -------------------------------------------------------
  function scheduleAvailable() {
    try { return (typeof $ !== "undefined" && $.scheduleTask && typeof $.scheduleTask === "function"); }
    catch (e) { return false; }
  }

  function clearGlobalState() {
    try {
      if ($.global && $.global[GLOBAL_STATE_KEY]) {
        $.global[GLOBAL_STATE_KEY] = null;
      }
    } catch (e) {}
  }

  function scheduleTick(delayMs) {
    // Delay must be integer ms
    var d = Math.max(50, Number(delayMs || 250));
    var call = "try{ if($.global && $.global['" + GLOBAL_STATE_KEY + "'] && $.global['" + GLOBAL_STATE_KEY + "'].tick){ $.global['" + GLOBAL_STATE_KEY + "'].tick(); } }catch(e){}";
    try { $.scheduleTask(call, d, false); } catch (e) {}
  }

  function startNonBlockingSequenceWatcher(beforeSet) {
    if (!scheduleAvailable()) return false;

    // prevent overlapping watchers if script is triggered repeatedly
    clearGlobalState();

    var fastTotalMs = (POLL_TRIES * POLL_SLEEP_MS);
    var maxTotalMs  = fastTotalMs + (EXTENDED_WAIT_ENABLE ? EXTENDED_WAIT_MAX_MS : 0);
    var startMs     = (new Date()).getTime();

    $.global[GLOBAL_STATE_KEY] = {
      beforeSet: beforeSet || {},
      startMs: startMs,
      fastTotalMs: fastTotalMs,
      maxTotalMs: maxTotalMs,
      done: false,

      finish: function () {
        this.done = true;
        clearGlobalState();
      },

      tick: function () {
        if (this.done) return;

        // If project or sequences are not available, just try again soon.
        if (!app || !app.project) {
          scheduleTick(250);
          return;
        }

        var elapsed = (new Date()).getTime() - this.startMs;

        // Determine phase delay
        var phaseDelay = (elapsed < this.fastTotalMs) ? POLL_SLEEP_MS : EXTENDED_WAIT_SLEEP_MS;

        // Safe fetch of new sequences
        var newSeqs = getNewSequencesSafe(this.beforeSet);

        // If sequences collection isn't ready yet, keep waiting (non-blocking)
        if (!newSeqs || !(newSeqs instanceof Array)) newSeqs = [];

        // If target appears among new sequences, open immediately
        for (var i = 0; i < newSeqs.length; i++) {
          if (newSeqs[i] && String(newSeqs[i].name) === String(TARGET_SEQUENCE_NAME)) {
            openSequenceFixed(newSeqs[i]);
            this.finish();
            return;
          }
        }

        // If exactly one new seq, rename/open like your original behavior
        if (newSeqs.length === 1) {
          var only = newSeqs[0];
          if (only && String(only.name) !== String(TARGET_SEQUENCE_NAME)) renameSequence(only, TARGET_SEQUENCE_NAME);
          openSequenceFixed(only);
          this.finish();
          return;
        }

        // If none detected yet, try finding exact name in full list (sometimes import makes it appear without "new" delta timing)
        if (newSeqs.length === 0) {
          var exact = findSeqByExactNameSafe(TARGET_SEQUENCE_NAME);
          if (exact) {
            openSequenceFixed(exact);
            this.finish();
            return;
          }
        }

        // Multiple new sequences: same error behavior as before
        if (newSeqs.length > 1) {
          var names = [];
          for (var j = 0; j < newSeqs.length; j++) names.push(newSeqs[j].name);
          alertErr(
            "Imported XML created multiple sequences, none matched:\n" +
            '"' + TARGET_SEQUENCE_NAME + '"\n\nNew sequences:\n- ' + names.join("\n- ")
          );
          this.finish();
          return;
        }

        // Timeout handling
        if (elapsed >= this.maxTotalMs) {
          alertErr(
            "Imported XML, but no new sequences detected.\n" +
            "Tried fast poll (" + fastTotalMs + "ms)" +
            (EXTENDED_WAIT_ENABLE ? (" + extended wait (" + EXTENDED_WAIT_MAX_MS + "ms)") : "") +
            ".\n\nIf your XML import is extremely heavy, increase EXTENDED_WAIT_MAX_MS."
          );
          this.finish();
          return;
        }

        // Continue waiting (non-blocking)
        scheduleTick(phaseDelay);
      }
    };

    // First tick soon
    scheduleTick(200);
    return true;
  }

  // -------------------------------------------------------
  // Fallback blocking wait (only used if scheduleTask is unavailable)
  // -------------------------------------------------------
  function waitForImportedSequencesBlocking(beforeSet) {
    var newSeqs = [];
    // Fast path
    for (var t = 0; t < POLL_TRIES; t++) {
      try { $.sleep(POLL_SLEEP_MS); } catch (e) {}
      newSeqs = getNewSequencesSafe(beforeSet);

      for (var i = 0; i < newSeqs.length; i++) {
        if (String(newSeqs[i].name) === String(TARGET_SEQUENCE_NAME)) {
          openSequenceFixed(newSeqs[i]);
          return { opened: true, newSeqs: newSeqs };
        }
      }
      if (newSeqs.length > 0) return { opened: false, newSeqs: newSeqs };
    }

    if (EXTENDED_WAIT_ENABLE) {
      log("⏳ No new sequences yet (fast poll). Entering extended wait (popup may not appear)...");
      var start = (new Date()).getTime();
      while (((new Date()).getTime() - start) < EXTENDED_WAIT_MAX_MS) {
        try { $.sleep(EXTENDED_WAIT_SLEEP_MS); } catch (e2) {}
        newSeqs = getNewSequencesSafe(beforeSet);

        for (var k = 0; k < newSeqs.length; k++) {
          if (String(newSeqs[k].name) === String(TARGET_SEQUENCE_NAME)) {
            openSequenceFixed(newSeqs[k]);
            return { opened: true, newSeqs: newSeqs };
          }
        }
        if (newSeqs.length > 0) return { opened: false, newSeqs: newSeqs };
      }
    }

    return { opened: false, newSeqs: [] };
  }

  // ---- Main ----
  try {
    if (!app.project) { alertErr("No project is open. Please open a project first."); return; }

    var rc = loadRunConfig();
    if (!rc) return;

    var xmlPath = rc.XMLPath || rc.xmlPath || rc.xml_path || "";
    if (!xmlPath) {
      alertErr("XMLPath missing in run_config.json:\n" + (rc.__path || "(unknown path)"));
      return;
    }

    log("📌 run_config.json: " + (rc.__path || ""));
    log("📌 XMLPath: " + xmlPath);

    var xmlFile = new File(xmlPath);
    if (!xmlFile.exists) {
      alertErr("XML not found at XMLPath:\n" + xmlPath);
      return;
    }

    var mainBin = getMainBin();
    if (!mainBin) { alertErr("Could not access main bin (rootItem)."); return; }

    var automatedBin = ensureBin(mainBin, TARGET_BIN_NAME);
    if (!automatedBin) return;

    // snapshot BEFORE import (safe)
    var before = snapshotSequencesSafe();

    // import (popup logic unchanged)
    if (!importXmlToBin(xmlFile.fsName, automatedBin)) return;

    // ✅ FIX: use non-blocking watcher so Premiere doesn't freeze/crash when no popup appears
    if (USE_SCHEDULE_TASK_WAIT && startNonBlockingSequenceWatcher(before)) {
      log("✅ Post-import sequence watch started (non-blocking).");
      return; // IMPORTANT: exit immediately, scheduled watcher will open/rename sequence
    }

    // Fallback if scheduleTask unavailable
    log("⚠️ $.scheduleTask unavailable; using blocking wait fallback.");
    var waitRes = waitForImportedSequencesBlocking(before);
    if (waitRes.opened) return;

    var newSeqs = waitRes.newSeqs || [];

    if (newSeqs.length === 1) {
      var only = newSeqs[0];
      if (String(only.name) !== String(TARGET_SEQUENCE_NAME)) renameSequence(only, TARGET_SEQUENCE_NAME);
      openSequenceFixed(only);
      return;
    }

    var exact = findSeqByExactNameSafe(TARGET_SEQUENCE_NAME);
    if (exact) { openSequenceFixed(exact); return; }

    if (newSeqs.length > 1) {
      var names = [];
      for (var j = 0; j < newSeqs.length; j++) names.push(newSeqs[j].name);
      alertErr(
        "Imported XML created multiple sequences, none matched:\n" +
        '"' + TARGET_SEQUENCE_NAME + '"\n\nNew sequences:\n- ' + names.join("\n- ")
      );
      return;
    }

    alertErr(
      "Imported XML, but no new sequences detected.\n" +
      "Tried fast poll (" + (POLL_TRIES * POLL_SLEEP_MS) + "ms)" +
      (EXTENDED_WAIT_ENABLE ? (" + extended wait (" + EXTENDED_WAIT_MAX_MS + "ms)") : "") +
      ".\n\nIf your XML import is extremely heavy, increase EXTENDED_WAIT_MAX_MS."
    );

  } catch (e) {
    alertErr("Script crashed:\n" + e);
  }
})();