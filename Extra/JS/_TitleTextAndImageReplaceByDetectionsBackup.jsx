/**
 * prep_for_title.jsx (COMP-based text + SAFE font override)
 *
 * - reads _Title__job__.json
 * - opens AEP
 * - TEXT LOGIC:
 *    1) If Comp "NumberText" EXISTS AND Comp "BodyText" EXISTS:
 *         - update text inside NumberText  <- job.number
 *         - update text inside BodyText    <- job.body
 *    2) Else if ONLY Comp "BodyText" EXISTS:
 *         - update text inside BodyText    <- job.text
 *    3) Else fallback legacy:
 *         - update job.textCompName/job.textLayerName <- job.text
 *
 * - FONT OVERRIDE:
 *    - Korean  -> tries EXACT PostScript: NotoSansKR-ExtraBold (first)
 *    - Russian -> tries NotoSans (basic candidates)
 *
 * IMPORTANT:
 * - AE on your machine has no app.fonts, so we cannot enumerate fonts.
 * - We apply font first, verify, and revert if rejected (so it won't get stuck to Times).
 * - If Source Text has expression, it will override your changes.
 *   This script DISABLES Source Text expressions (and does NOT restore them).
 */

(function () {
  try { app.beginSuppressDialogs(); } catch (e) {}

  var scriptFile = new File($.fileName);
  var scriptDir  = scriptFile.parent;

  var LOG_PATH  = scriptDir.fsName + "\\___Title_Prep_Log___.txt";
  var DONE_PATH = scriptDir.fsName + "\\___Title_Prep_Done___.txt";
  var JOB_PATH  = scriptDir.fsName + "\\_Title__job__.json";

  var logFile  = new File(LOG_PATH);
  var doneFile = new File(DONE_PATH);

  function log(msg) {
    try {
      logFile.open("a");
      logFile.writeln("[" + (new Date()).toString() + "] " + msg);
      logFile.close();
    } catch (e) {}
  }
  function done(line) {
    try {
      doneFile.open("w");
      doneFile.writeln(line);
      doneFile.close();
    } catch (e) {}
  }
  function fail(msg) { throw new Error(msg); }

  // ES3-safe trim (ExtendScript)
  function _trim(s) {
    s = String(s == null ? "" : s);
    return s.replace(/^\s+|\s+$/g, "");
  }
  function safeLower(v) {
    return _trim(v).toLowerCase();
  }

  function normalizeWindowsPath(p) {
    var s = String(p || "");
    s = s.replace(/\//g, "\\");
    if (s.indexOf("\\\\") === 0) {
      s = "\\\\" + s.substr(2).replace(/\\{2,}/g, "\\");
    } else {
      s = s.replace(/\\{2,}/g, "\\");
    }
    return s;
  }

  function parseJsonLoose(str) {
    try { if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(str); } catch (e1) {}
    try { return eval("(" + str + ")"); }
    catch (e2) { fail("Failed to parse _Title__job__.json:\n" + e2.toString()); }
  }

  function findCompByNameExact(name) {
    if (!name) return null;
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it instanceof CompItem && it.name === name) return it;
    }
    return null;
  }

  function findCompByNameLoose(name) {
    if (!name) return null;
    var want = safeLower(name);
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it instanceof CompItem && safeLower(it.name) === want) return it;
    }
    return null;
  }

  function getComp(name) {
    return findCompByNameExact(name) || findCompByNameLoose(name);
  }

  function findTextLayer(cmp, layerName) {
    if (!(cmp instanceof CompItem)) return null;

    // 1) by explicit layer name
    if (layerName) {
      try {
        var byName = cmp.layer(layerName);
        if (byName && byName.property("Source Text") != null) return byName;
      } catch (e) {}
    }

    // 2) first text layer
    for (var i = 1; i <= cmp.numLayers; i++) {
      var lyr = cmp.layer(i);
      if (lyr && lyr.property("Source Text") != null) return lyr;
    }
    return null;
  }

  // Disable Source Text expressions so AE doesn't override the updated text at render time.
  function disableTextExpression(sourceTextProp) {
    try {
      if (sourceTextProp && sourceTextProp.canSetExpression && sourceTextProp.expressionEnabled) {
        sourceTextProp.expressionEnabled = false; // DO NOT restore
        log("INFO: Disabled Source Text expression to prevent override.");
      }
    } catch (e) {}
  }

  // =====================================================
  // LANGUAGE DETECTION + FONT CANDIDATES
  // =====================================================

  function detectLanguage(job, outputPath, projectPath) {
    var candidates = [
      job.language, job.lang, job.Language, job.LANG,
      job.locale, job.Locale, job.lc
    ];
    for (var i = 0; i < candidates.length; i++) {
      var s = safeLower(candidates[i]);
      if (s) return s;
    }

    var blob = safeLower(outputPath) + " " + safeLower(projectPath);

    if (blob.indexOf("\\korean\\") !== -1 || blob.indexOf("/korean/") !== -1) return "korean";
    if (blob.indexOf("\\russian\\") !== -1 || blob.indexOf("/russian/") !== -1) return "russian";

    if (blob.indexOf("\\ko\\") !== -1 || blob.indexOf("/ko/") !== -1) return "ko";
    if (blob.indexOf("\\ru\\") !== -1 || blob.indexOf("/ru/") !== -1) return "ru";

    return "";
  }

  function getFontCandidates(job, langRaw) {
    // Optional explicit override from JSON:
    // "font_ps": "NotoSansKR-ExtraBold"
    var explicit = job.font_ps || job.fontPostScriptName || job.fontPS || "";
    explicit = _trim(explicit);

    var l = safeLower(langRaw);
    var out = [];

    if (explicit) out.push(explicit);

    if (l === "korean" || l === "ko" || l.indexOf("korean") !== -1) {
      // YOUR exact font first (correct casing)
      out.push("NotoSansKR-ExtraBold");

      // A few fallback name variants (in case install exposes different PS name)
      // out.push("NotoSansKR-ExtraBoldItalic");
      // out.push("NotoSansKR-Bold");
      // out.push("NotoSansKR-Regular");
      // out.push("Noto Sans KR ExtraBold");
      // out.push("Noto Sans KR");
      return out;
    }

    if (l === "russian" || l === "ru" || l.indexOf("russian") !== -1) {
      // out.push("NotoSans-Regular");
      out.push("NotoSans-ExtraBold");
      // out.push("Noto Sans");
      // out.push("NotoSans-Medium");
      // out.push("NotoSans-Bold");
      return out;
    }

    return null;
  }

  // Try to set font on the SAME TextDocument and verify st.value.font.
  // If rejected, revert immediately so we never end up stuck with Times.
  function applyFontCandidatesSafely(st, candidates) {
    if (!candidates || !candidates.length) return false;

    var originalDoc = st.value; // snapshot
    var originalFont = "";
    try { originalFont = originalDoc.font; } catch (e0) { originalFont = ""; }

    for (var i = 0; i < candidates.length; i++) {
      var cand = _trim(candidates[i]);
      if (!cand) continue;

      try {
        var doc = st.value; // keep current styling/ranges
        try { doc.font = String(cand); } catch (eF) {}

        st.setValue(doc);

        var kept = "";
        try { kept = st.value.font; } catch (eK) { kept = ""; }

        if (safeLower(kept) === safeLower(cand)) {
          log("INFO: Font applied OK: '" + cand + "'");
          return true;
        } else {
          log("INFO: Font rejected: '" + cand + "' -> AE kept '" + kept + "'");
          // revert to original so we don't drift to Times
          try { st.setValue(originalDoc); } catch (eR) {}
        }
      } catch (eTry) {
        log("INFO: Font attempt error '" + cand + "': " + eTry.toString());
        try { st.setValue(originalDoc); } catch (eR2) {}
      }
    }

    // Final revert
    try { st.setValue(originalDoc); } catch (eR3) {}
    var after = "";
    try { after = st.value.font; } catch (eA) { after = ""; }
    log("WARN: No font candidates worked. Kept font '" + after + "' (original '" + originalFont + "')");
    return false;
  }

  // Set FONT first, then TEXT (important)
  function setTextOnLayer(textLayer, newText, fontCandidatesOrNull) {
    if (!textLayer) return false;

    var st = null;
    try { st = textLayer.property("Source Text"); } catch (e0) {}
    if (!st) return false;

    disableTextExpression(st);

    try {
      // 1) FONT FIRST (safe)
      if (fontCandidatesOrNull && fontCandidatesOrNull.length) {
        applyFontCandidatesSafely(st, fontCandidatesOrNull);
      }

      // 2) TEXT SECOND
      var doc = st.value;
      doc.text = String(newText == null ? "" : newText);
      st.setValue(doc);

      return true;

    } catch (e) {
      log("WARN: Failed setting text on layer: " + textLayer.name + " | " + e.toString());
      return false;
    }
  }

  // Update the text inside a COMP (CompItem), using layerName if provided, otherwise first text layer.
  function updateTextInsideComp(compName, layerName, newText, fontCandidatesOrNull) {
    var cmp = getComp(compName);
    if (!cmp) {
      log("WARN: Comp not found: " + compName);
      return false;
    }
    var lyr = findTextLayer(cmp, layerName);
    if (!lyr) {
      log("WARN: No text layer found inside comp: " + compName + (layerName ? (" | wanted layer=" + layerName) : ""));
      return false;
    }

    var ok = setTextOnLayer(lyr, newText, fontCandidatesOrNull);
    if (ok) log("Updated comp text: " + compName + " | layer=" + lyr.name);
    return ok;
  }

  // =========================================================
  // IMAGE REPLACE (UNCHANGED)
  // =========================================================

  function clearRenderQueue() {
    try {
      var rq = app.project.renderQueue;
      for (var i = rq.numItems; i >= 1; i--) {
        try { rq.item(i).remove(); } catch (e) {}
      }
    } catch (e2) {}
  }

  function removeAllKeys(prop) {
    try {
      if (prop && prop.isTimeVarying) {
        for (var k = prop.numKeys; k >= 1; k--) {
          try { prop.removeKey(k); } catch (e) {}
        }
      }
    } catch (e2) {}
  }

  function setPropValue(prop, val) {
    if (!prop) return;
    removeAllKeys(prop);
    try {
      if (prop.dimensionsSeparated) {
        var xP = prop.property("X Position");
        var yP = prop.property("Y Position");
        if (xP && yP && val && val.length >= 2) {
          removeAllKeys(xP); removeAllKeys(yP);
          xP.setValue(val[0]);
          yP.setValue(val[1]);
          return;
        }
      }
    } catch (e0) {}
    try { prop.setValue(val); } catch (e1) {}
  }

  function findBestImageLayer(imgComp, desiredIndex) {
    if (!(imgComp instanceof CompItem)) return null;
    if (desiredIndex && desiredIndex > 0 && desiredIndex <= imgComp.numLayers) {
      var L = imgComp.layer(desiredIndex);
      if (L && (L instanceof AVLayer) && (L.property("Source Text") == null) && !L.adjustmentLayer) return L;
    }
    for (var i = 1; i <= imgComp.numLayers; i++) {
      var lyr = imgComp.layer(i);
      if ((lyr instanceof AVLayer) && (lyr.property("Source Text") == null) && !lyr.adjustmentLayer) return lyr;
    }
    return null;
  }

  function replaceLayerImage(targetLayer, imgFile) {
    if (!targetLayer) fail("targetLayer is null");
    try {
      if (targetLayer.source && (targetLayer.source instanceof FootageItem)) {
        targetLayer.source.replace(imgFile);
        return;
      }
    } catch (eF) {}
    var opts = new ImportOptions(imgFile);
    var newFootage = app.project.importFile(opts);
    targetLayer.replaceSource(newFootage, false);
  }

  function fitLayerToCompCover(layer, comp) {
    if (!(layer instanceof AVLayer) || !(comp instanceof CompItem)) return;
    var t = layer.property("Transform");
    var pos = t.property("Position"), anc = t.property("Anchor Point"), scl = t.property("Scale");

    setPropValue(t.property("Rotation"), 0);
    setPropValue(t.property("Opacity"), 100);

    var srcW = layer.source ? layer.source.width : layer.sourceRectAtTime(comp.time, false).width;
    var srcH = layer.source ? layer.source.height : layer.sourceRectAtTime(comp.time, false).height;

    setPropValue(anc, [srcW / 2, srcH / 2]);
    setPropValue(pos, [comp.width / 2, comp.height / 2]);
    var scaleCover = Math.max(comp.width / srcW, comp.height / srcH) * 100;
    setPropValue(scl, [scaleCover, scaleCover]);
  }

  // =========================================================
  // MAIN
  // =========================================================

  try {
    try { logFile.open("w"); logFile.close(); } catch (e) {}
    log("prep_for_title.jsx started.");

    var jobFile = new File(JOB_PATH);
    if (!jobFile.exists) fail("_Title__job__.json missing");
    jobFile.encoding = "UTF-8";
    jobFile.open("r");
    var job = parseJsonLoose(jobFile.read());
    jobFile.close();

    var projectPath = normalizeWindowsPath(job.projectPath);
    var outputPath  = normalizeWindowsPath(job.output);

    var langRaw = detectLanguage(job, outputPath, projectPath);
    var fontCandidates = getFontCandidates(job, langRaw);

    if (fontCandidates) log("INFO: Language detected as '" + langRaw + "'. Font override requested.");
    else log("INFO: Language detected as '" + (langRaw || "UNKNOWN") + "'. No font override.");

    app.open(new File(projectPath));

    // =========================================================
    // TEXT UPDATE (COMPS: NumberText / BodyText)
    // =========================================================
    var numberComp = getComp("NumberText");
    var bodyComp   = getComp("BodyText");

    var hasNumberComp = (numberComp instanceof CompItem);
    var hasBodyComp   = (bodyComp instanceof CompItem);

    var didText = false;

    if (hasNumberComp && hasBodyComp) {
      log("Detected TWO-COMP setup: NumberText + BodyText. Using job.number + job.body (ignoring job.text).");

      if (job.hasOwnProperty("number")) {
        var okN = updateTextInsideComp("NumberText", job.numberLayerName, job.number, fontCandidates);
        didText = didText || okN;
      } else {
        log("WARN: JSON missing 'number' in two-comp mode.");
      }

      if (job.hasOwnProperty("body")) {
        var okB = updateTextInsideComp("BodyText", job.bodyLayerName, job.body, fontCandidates);
        didText = didText || okB;
      } else {
        log("WARN: JSON missing 'body' in two-comp mode.");
      }

    } else if (!hasNumberComp && hasBodyComp) {
      log("Detected SINGLE-COMP setup: only BodyText exists. Using job.text.");

      if (job.hasOwnProperty("text")) {
        var okOnly = updateTextInsideComp("BodyText", job.bodyLayerName, job.text, fontCandidates);
        didText = didText || okOnly;
      } else {
        log("WARN: JSON missing 'text' for BodyText-only mode.");
      }

    } else {
      // Fallback legacy
      log("No BodyText/NumberText comp setup found. Falling back to legacy job.textCompName/job.textLayerName.");

      var legacyCompName  = job.textCompName  || "viralverse_Trailer";
      var legacyLayerName = job.textLayerName || "ViralVerse_Title";

      if (job.hasOwnProperty("text")) {
        var okLegacy = updateTextInsideComp(legacyCompName, legacyLayerName, job.text, fontCandidates);
        didText = didText || okLegacy;
      } else {
        log("WARN: JSON missing 'text' for legacy fallback.");
      }
    }

    if (!didText) {
      log("WARN: Text update appears to have failed. Likely: comp/layer names mismatch OR Source Text is driven by expressions elsewhere.");
    }

    // =========================================================
    // IMAGE REPLACE (UNCHANGED)
    // =========================================================
    if (job.imageLocation) {
      var imgComp = getComp(job.imageCompName || "VV_IMAGE");
      var targetLayer = findBestImageLayer(imgComp, Number(job.imageLayerIndex || 1));
      if (targetLayer) {
        replaceLayerImage(targetLayer, new File(normalizeWindowsPath(job.imageLocation)));
        fitLayerToCompCover(targetLayer, imgComp);
      } else {
        log("WARN: No suitable image layer found in image comp.");
      }
    }

    app.project.save();

    // =========================================================
    // RENDER (UNCHANGED)
    // =========================================================
    clearRenderQueue();
    var renderComp = getComp(job.renderComp || "|| TRAILER VIRALVERSE");
    var rqItem = app.project.renderQueue.items.add(renderComp);

    if (job.rsTemplate) rqItem.applyTemplate(job.rsTemplate);
    var om = rqItem.outputModule(1);
    om.applyTemplate(job.omTemplate || "VV Title Render");
    om.file = new File(outputPath);

    app.project.renderQueue.queueInAME(true);

    done("OK|Queued to AME|OUT=" + outputPath);
    app.quit();

  } catch (err) {
    log("FATAL ERROR: " + err.toString());
    done("ERR|" + err.toString());
    app.quit();
  }
})();
