/**
 * _ReplaceTextAndRender.jsx
 *
 * Reads:  _LowerThird__job__.json
 * Writes: ___LowerThird_Prep_Log___.txt
 *         ___LowerThird_Prep_Done___.txt
 *
 * UPDATED FLOW:
 * - Opens Project
 * - Updates Text
 * - (NEW) Optional font override for Korean/Russian (safe)
 * - Clears Render Queue & Adds new Item (configured for MOV)
 * - SAVES the project (Crucial for aerender)
 * - Returns the AEP path to the Batch file
 */

(function () {
  try { app.beginSuppressDialogs(); } catch (e) {}

  var scriptFile = new File($.fileName);
  var scriptDir  = scriptFile.parent;

  var JOB_JSON_NAME = "_LowerThird__job__.json";
  var LOG_NAME      = "___LowerThird_Prep_Log___.txt";
  var DONE_NAME     = "___LowerThird_Prep_Done___.txt";

  var logFile  = new File(scriptDir.fsName + "/" + LOG_NAME);
  var doneFile = new File(scriptDir.fsName + "/" + DONE_NAME);

  function log(msg) {
    try {
      logFile.open("a");
      logFile.writeln("[" + (new Date()).toUTCString() + "] " + msg);
      logFile.close();
    } catch (e) {}
  }

  function writeDone(line) {
    try {
      doneFile.open("w");
      doneFile.writeln(line);
      doneFile.close();
    } catch (e) {}
  }

  function readFileUtf8(f) {
    f.encoding = "UTF-8";
    f.open("r");
    var s = f.read();
    f.close();
    return s;
  }

  function parseJson(s) {
    if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(s);
    return eval("(" + s + ")");
  }

  function normWinPath(p) {
    if (!p) return "";
    var s = ("" + p).replace(/\//g, "\\");
    s = s.replace(/\\\\+/g, "\\");
    return s;
  }

  function fileExists(winPath) {
    try { return (new File(winPath)).exists; } catch (e) { return false; }
  }

  function findCompByName(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it && it instanceof CompItem && it.name === name) return it;
    }
    return null;
  }

  function mustApplyTemplate(fnApply, templateName, label) {
    if (!templateName) throw new Error(label + " template is empty/missing.");
    try {
      fnApply(templateName);
      log("Applied " + label + " template: " + templateName);
    } catch (e) {
      throw new Error("FAILED to apply " + label + " template '" + templateName + "': " + e.toString());
    }
  }

  function clearRenderQueue() {
    try {
      var rq = app.project.renderQueue;
      for (var i = rq.numItems; i >= 1; i--) {
        try { rq.item(i).remove(); } catch (e) {}
      }
      log("Render Queue cleared.");
    } catch (e) {
      log("WARN: Could not clear Render Queue: " + e.toString());
    }
  }

  function ensureMovExtension(winPath) {
    var p = (winPath || "");
    if (!p) return p;
    if (p.toLowerCase().match(/\.mov$/)) return p;
    if (p.match(/\.[A-Za-z0-9]+$/)) return p.replace(/\.[A-Za-z0-9]+$/, ".mov");
    return p + ".mov";
  }

  // =========================================================
  // NEW: Language detection + safe font override (minimal)
  // =========================================================

  function _trim(s) {
    s = String(s == null ? "" : s);
    return s.replace(/^\s+|\s+$/g, "");
  }
  function safeLower(v) {
    return _trim(v).toLowerCase();
  }

  function detectLanguage(job, projectPath, outputPath) {
    // Prefer explicit fields from JSON
    var candidates = [
      job.language, job.lang, job.Language, job.LANG,
      job.locale, job.Locale, job.lc
    ];
    for (var i = 0; i < candidates.length; i++) {
      var s = safeLower(candidates[i]);
      if (s) return s;
    }

    // Fallback: infer from paths (same idea as title code)
    var blob = safeLower(projectPath) + " " + safeLower(outputPath);

    if (blob.indexOf("\\korean\\") !== -1 || blob.indexOf("/korean/") !== -1) return "korean";
    if (blob.indexOf("\\russian\\") !== -1 || blob.indexOf("/russian/") !== -1) return "russian";

    if (blob.indexOf("\\ko\\") !== -1 || blob.indexOf("/ko/") !== -1) return "ko";
    if (blob.indexOf("\\ru\\") !== -1 || blob.indexOf("/ru/") !== -1) return "ru";

    return "";
  }

  function getFontCandidatesForLang(langRaw) {
    var l = safeLower(langRaw);

    // Korean -> YOUR exact PS name first
    if (l === "korean" || l === "ko" || l.indexOf("korean") !== -1) {
      return [
        "NotoSansKR-ExtraBold", // exact casing
        "NotoSansKR-Bold",
        "NotoSansKR-Regular",
        "Noto Sans KR"
      ];
    }

    // Russian -> NotoSans candidates
    if (l === "russian" || l === "ru" || l.indexOf("russian") !== -1) {
      return [
        "NotoSans-Regular",
        "NotoSans",
        "Noto Sans",
        "NotoSans-Medium",
        "NotoSans-Bold"
      ];
    }

    return null;
  }

  // Apply font safely: try candidates, verify AE accepted, otherwise revert.
  function applyFontCandidatesSafely(sourceTextProp, candidates) {
    if (!sourceTextProp || !candidates || !candidates.length) return false;

    var originalDoc = sourceTextProp.value; // snapshot
    var originalFont = "";
    try { originalFont = originalDoc.font; } catch (e0) { originalFont = ""; }

    for (var i = 0; i < candidates.length; i++) {
      var cand = _trim(candidates[i]);
      if (!cand) continue;

      try {
        var doc = sourceTextProp.value; // preserve style/ranges
        try { doc.font = String(cand); } catch (eF) {}

        sourceTextProp.setValue(doc);

        var kept = "";
        try { kept = sourceTextProp.value.font; } catch (eK) { kept = ""; }

        if (safeLower(kept) === safeLower(cand)) {
          log("INFO: Font applied OK: '" + cand + "'");
          return true;
        } else {
          log("INFO: Font rejected: '" + cand + "' -> AE kept '" + kept + "'");
          // revert so we don't drift to Times
          try { sourceTextProp.setValue(originalDoc); } catch (eR) {}
        }
      } catch (eTry) {
        log("INFO: Font attempt error '" + cand + "': " + eTry.toString());
        try { sourceTextProp.setValue(originalDoc); } catch (eR2) {}
      }
    }

    // Final revert
    try { sourceTextProp.setValue(originalDoc); } catch (eR3) {}
    var after = "";
    try { after = sourceTextProp.value.font; } catch (eA) { after = ""; }
    log("WARN: No font candidates worked. Kept font '" + after + "' (original '" + originalFont + "')");
    return false;
  }

  // =========================================================
  // MAIN
  // =========================================================

  try {
    // reset log/done
    try { logFile.open("w"); logFile.close(); } catch (e) {}
    try { if (doneFile.exists) doneFile.remove(); } catch (e) {}

    // Read job
    var jobFile = new File(scriptDir.fsName + "/" + JOB_JSON_NAME);
    if (!jobFile.exists) throw new Error("Missing job JSON: " + JOB_JSON_NAME);

    var raw = readFileUtf8(jobFile);
    log("Raw Job JSON: " + raw);

    var job = parseJson(raw);

    var projectPath   = normWinPath(job.projectPath);
    var outputPathRaw = normWinPath(job.output);

    var textCompName   = job.textCompName  || "viralverse_LT";
    var textLayerName  = job.textLayerName || "TXT";
    var renderCompName = job.renderComp || "";
    var rsTemplate     = job.rsTemplate || "";
    var omTemplate     = job.omTemplate || "";
    var textValue      = job.text || "";

    if (!projectPath)    throw new Error("projectPath missing in job JSON");
    if (!outputPathRaw)  throw new Error("output missing in job JSON");
    if (!fileExists(projectPath)) throw new Error("AEP not found: " + projectPath);

    var outputPath = ensureMovExtension(outputPathRaw);

    log("Opening Project: " + projectPath);
    app.open(new File(projectPath));

    // 1. Clear RQ to prevent duplicates
    clearRenderQueue();

    // 2. Find text layer
    var textComp = findCompByName(textCompName);
    if (!textComp) throw new Error("Text comp not found: " + textCompName);

    var textLayer = textComp.layer(textLayerName);
    if (!textLayer) {
      for (var l = 1; l <= textComp.numLayers; l++) {
        var tl = textComp.layer(l);
        if (tl && tl.property("Source Text") != null) { textLayer = tl; break; }
      }
    }
    if (!textLayer) throw new Error("No text layer found in comp: " + textCompName);

    var srcText = textLayer.property("Source Text");
    if (!srcText) throw new Error("Source Text property not found on layer: " + textLayer.name);

    // NEW: Detect language and apply font override (FONT FIRST)
    var langRaw = detectLanguage(job, projectPath, outputPath);
    var fontCandidates = getFontCandidatesForLang(langRaw);

    if (fontCandidates) {
      log("INFO: Language detected as '" + langRaw + "'. Trying font override.");
      // Do NOT throw if it fails; keep workflow stable
      try { applyFontCandidatesSafely(srcText, fontCandidates); } catch (eFont) {
        log("WARN: Font override exception (ignored): " + eFont.toString());
      }
    } else {
      log("INFO: Language detected as '" + (langRaw || "UNKNOWN") + "'. No font override.");
    }

    // Now set TEXT (TEXT SECOND)
    var td = srcText.value;
    td.text = textValue;
    srcText.setValue(td);
    log("Text updated to: " + textValue);

    // 3. Add to Render Queue
    var renderComp = renderCompName ? findCompByName(renderCompName) : textComp;
    if (!renderComp) throw new Error("Render comp not found.");

    var rqItem = app.project.renderQueue.items.add(renderComp);

    // 4. Apply Templates
    mustApplyTemplate(function(t){ rqItem.applyTemplate(t); }, rsTemplate, "Render Settings");
    var om = rqItem.outputModule(1);
    mustApplyTemplate(function(t){ om.applyTemplate(t); }, omTemplate, "Output Module");
    om.file = new File(outputPath);
    log("Output file set to: " + outputPath);

    // 5. SAVE PROJECT (Required for aerender to see changes)
    app.project.save();
    log("Project saved.");

    // 6. Signal DONE - Pass the Project Path to the BAT file
    writeDone("OK|READY_FOR_AERENDER|" + projectPath);

    // Quit AE so aerender can take over
    try { app.quit(); } catch (e) {}

  } catch (err) {
    log("FATAL ERROR: " + err.toString());
    writeDone("ERR|" + err.toString());
    try { app.quit(); } catch (e2) {}
  }
})();
