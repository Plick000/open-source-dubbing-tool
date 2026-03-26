/**
 * _ReplaceTextAndRender.jsx
 *
 * Reads:  _LowerThird__job__.json
 * Writes: ___LowerThird_Prep_Log___.txt
 *         ___LowerThird_Prep_Done___.txt
 *
 * FLOW:
 * - Opens Project
 * - Updates Text
 * - Font is CONFIG-DRIVEN for ALL languages (Z:\...\__languages.json)
 *   - If font already has "-Style" suffix => use as-is
 *   - Else append ExtraBold variants
 *   - If font empty/missing => fallback Montserrat-ExtraBold
 * - Applies font to Source Text + Essential Properties text docs (Master Properties)
 * - Clears Render Queue & Adds new Item (MOV)
 * - SAVES the project (Crucial for aerender)
 * - Returns AEP path (READY_FOR_AERENDER)
 */

(function () {
  try { app.beginSuppressDialogs(); } catch (e) {}

  var scriptFile = new File($.fileName);
  var scriptDir  = scriptFile.parent;

  var JOB_JSON_NAME = "_LowerThird__job__.json";
  var LOG_NAME      = "___LowerThird_Prep_Log___.txt";
  var DONE_NAME     = "___LowerThird_Prep_Done___.txt";

  // ✅ Fonts config path
  var LANG_CONFIG_PATH = "Z:\\Automated Dubbings\\admin\\configs\\metadata\\__languages.json";

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
    try {
      if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(s);
    } catch (e0) {}
    return eval("(" + s + ")");
  }

  function _trim(s) {
    s = String(s == null ? "" : s);
    return s.replace(/^\s+|\s+$/g, "");
  }

  function safeLower(v) {
    return _trim(v).toLowerCase();
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

  function ensureMovExtension(winPath) {
    var p = (winPath || "");
    if (!p) return p;
    if (p.toLowerCase().match(/\.mov$/)) return p;
    if (p.match(/\.[A-Za-z0-9]+$/)) return p.replace(/\.[A-Za-z0-9]+$/, ".mov");
    return p + ".mov";
  }

  function findCompByName(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it && it instanceof CompItem && it.name === name) return it;
    }
    return null;
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

  function mustApplyTemplate(fnApply, templateName, label) {
    if (!templateName) throw new Error(label + " template is empty/missing.");
    try {
      fnApply(templateName);
      log("Applied " + label + " template: " + templateName);
    } catch (e) {
      throw new Error("FAILED to apply " + label + " template '" + templateName + "': " + e.toString());
    }
  }

  function detectLanguage(job, projectPath, outputPath) {
    var candidates = [
      job.language, job.lang, job.Language, job.LANG,
      job.locale, job.Locale, job.lc
    ];
    for (var i = 0; i < candidates.length; i++) {
      var s = safeLower(candidates[i]);
      if (s) return s;
    }

    var blob = safeLower(projectPath) + " " + safeLower(outputPath);

    if (blob.indexOf("\\korean\\") !== -1 || blob.indexOf("/korean/") !== -1) return "korean";
    if (blob.indexOf("\\russian\\") !== -1 || blob.indexOf("/russian/") !== -1) return "russian";

    if (blob.indexOf("\\ko\\") !== -1 || blob.indexOf("/ko/") !== -1) return "ko";
    if (blob.indexOf("\\ru\\") !== -1 || blob.indexOf("/ru/") !== -1) return "ru";

    return "";
  }

  // ---------------------------
  // CONFIG LOADING (CACHED)
  // ---------------------------
  var __LANG_CFG_CACHE__ = null;
  var __LANG_CFG_ERR__ = null;

  function loadLanguagesConfigCached() {
    if (__LANG_CFG_CACHE__ !== null) return __LANG_CFG_CACHE__;
    if (__LANG_CFG_ERR__ !== null) return null;

    try {
      var f = new File(LANG_CONFIG_PATH);
      if (!f.exists) {
        __LANG_CFG_ERR__ = "Missing languages config: " + LANG_CONFIG_PATH;
        log("WARN: " + __LANG_CFG_ERR__);
        return null;
      }

      var raw = readFileUtf8(f);
      var cfg = parseJson(raw);

      // Normalize array wrapper: [ { ... } ]
      if (cfg && (cfg instanceof Array) && cfg.length > 0) cfg = cfg[0];

      if (!cfg || typeof cfg !== "object") {
        __LANG_CFG_ERR__ = "Invalid languages config JSON structure.";
        log("WARN: " + __LANG_CFG_ERR__);
        return null;
      }

      __LANG_CFG_CACHE__ = cfg;
      log("INFO: Languages config loaded: " + LANG_CONFIG_PATH);
      return __LANG_CFG_CACHE__;
    } catch (e) {
      __LANG_CFG_ERR__ = "Failed to load languages config: " + e.toString();
      log("WARN: " + __LANG_CFG_ERR__);
      return null;
    }
  }

  function parseBrandIndex(k) {
    var m = String(k || "").match(/^brand_(\d+)$/i);
    if (!m) return 999999;
    return parseInt(m[1], 10);
  }

  function getFontBaseFromConfig(langRaw) {
    var lang = safeLower(langRaw);
    if (!lang) return "";

    var cfg = loadLanguagesConfigCached();
    if (!cfg) return "";

    var brandKeys = [];
    for (var k in cfg) {
      if (cfg.hasOwnProperty(k) && String(k).toLowerCase().indexOf("brand_") === 0) brandKeys.push(k);
    }
    brandKeys.sort(function (a, b) { return parseBrandIndex(a) - parseBrandIndex(b); });

    for (var i = 0; i < brandKeys.length; i++) {
      var bk = brandKeys[i];
      var brandObj = cfg[bk];
      if (!brandObj) continue;

      var langsObj = brandObj.languages || brandObj["languages"];
      if (!langsObj) continue;

      // direct match
      var entry = langsObj[lang];

      // case-insensitive fallback
      if (!entry) {
        for (var lk in langsObj) {
          if (!langsObj.hasOwnProperty(lk)) continue;
          if (safeLower(lk) === lang) { entry = langsObj[lk]; break; }
        }
      }

      if (entry) {
        var font = _trim(entry.font || entry.Font || entry.FONT || "");
        if (font) return font;
      }
    }

    return "";
  }

  function uniqCaseInsensitive(arr) {
    var out = [];
    var seen = {};
    for (var i = 0; i < arr.length; i++) {
      var v = _trim(arr[i]);
      if (!v) continue;
      var key = safeLower(v);
      if (seen[key]) continue;
      seen[key] = true;
      out.push(v);
    }
    return out;
  }

  // ✅ KEY FIX: ANY "-suffix" means style already attached (FuturaPT-Heavy, NotoSansKR-ExtraBold, etc.)
  function hasStyleSuffixAfterDash(fontName) {
    var s = _trim(fontName);
    if (!s) return false;
    var idx = s.lastIndexOf("-");
    return (idx > 0 && idx < (s.length - 1));
  }

  function buildFontCandidatesFromBase(fontBase) {
    var base = _trim(fontBase);
    if (!base) return [];

    // If style already attached (dash suffix), use as-is
    if (hasStyleSuffixAfterDash(base)) return [base];

    // Otherwise attach ExtraBold first + safe variants
    var c = [];
    c.push(base + "-ExtraBold");
    c.push(base + " ExtraBold");
    c.push(base + "-Extrabold");
    c.push(base + " Extrabold");
    c.push(base + "-Extra Bold");
    c.push(base + " Extra Bold");

    c.push(base + "-Black");
    c.push(base + " Black");

    c.push(base + "-Bold");
    c.push(base + " Bold");

    c.push(base);

    return uniqCaseInsensitive(c);
  }

  function legacyFontCandidatesForLang(langRaw) {
    var l = safeLower(langRaw);

    if (l === "korean" || l === "ko" || l.indexOf("korean") !== -1) {
      return uniqCaseInsensitive([
        "NotoSansKR-ExtraBold",
        "NotoSansKR-Bold",
        "NotoSansKR-Regular",
        "Noto Sans KR"
      ]);
    }

    if (l === "russian" || l === "ru" || l.indexOf("russian") !== -1) {
      return uniqCaseInsensitive([
        "NotoSans-ExtraBold",
        "NotoSans-Bold",
        "NotoSans-Regular",
        "Noto Sans"
      ]);
    }

    return null;
  }

  function getFontCandidates(job, langRaw) {
    var out = [];

    // Optional explicit override
    var explicit = _trim(job.font_ps || job.fontPostScriptName || job.fontPS || "");
    if (explicit) out.push(explicit);

    // Config-driven base
    var base = getFontBaseFromConfig(langRaw);
    if (base) {
      log("INFO: Config font for '" + safeLower(langRaw) + "' => '" + base + "'");
      out = out.concat(buildFontCandidatesFromBase(base));
    } else {
      log("INFO: No config font found for '" + (safeLower(langRaw) || "UNKNOWN") + "'.");
    }

    // Safety legacy KR/RU only if we still have nothing meaningful
    if (out.length === (explicit ? 1 : 0)) {
      var legacy = legacyFontCandidatesForLang(langRaw);
      if (legacy) {
        log("INFO: Using legacy font fallback for '" + safeLower(langRaw) + "'.");
        out = out.concat(legacy);
      }
    }

    // Final fallback
    out = out.concat([
      "Montserrat-ExtraBold",
      "Montserrat ExtraBold",
      "Montserrat-Bold",
      "Montserrat Bold",
      "Montserrat"
    ]);

    return uniqCaseInsensitive(out);
  }

  function applyFontCandidatesSafely(sourceTextProp, candidates) {
    if (!sourceTextProp || !candidates || !candidates.length) return false;

    var originalDoc = sourceTextProp.value;
    var originalFont = "";
    try { originalFont = originalDoc.font; } catch (e0) { originalFont = ""; }

    for (var i = 0; i < candidates.length; i++) {
      var cand = _trim(candidates[i]);
      if (!cand) continue;

      try {
        var doc = sourceTextProp.value;
        doc.font = String(cand);
        sourceTextProp.setValue(doc);

        var kept = "";
        try { kept = sourceTextProp.value.font; } catch (eK) { kept = ""; }

        if (safeLower(kept) === safeLower(cand)) {
          log("INFO: Font applied OK: '" + cand + "'");
          return true;
        } else {
          try { sourceTextProp.setValue(originalDoc); } catch (eR) {}
        }
      } catch (eTry) {
        try { sourceTextProp.setValue(originalDoc); } catch (eR2) {}
      }
    }

    try { sourceTextProp.setValue(originalDoc); } catch (eR3) {}
    var after = "";
    try { after = sourceTextProp.value.font; } catch (eA) { after = ""; }
    log("WARN: No font candidates worked. Kept font '" + after + "' (original '" + originalFont + "')");
    return false;
  }

  // ✅ Apply font to Essential Properties (Master Properties) too
  function applyFontToEssentialPropsTextInComp(renderComp, sourceCompName, candidates) {
    if (!(renderComp instanceof CompItem)) return;
    if (!candidates || !candidates.length) return;

    function collectTextDocProps(group, out) {
      if (!group || !group.numProperties) return;
      for (var i = 1; i <= group.numProperties; i++) {
        var p = group.property(i);
        if (!p) continue;
        try {
          if (p.propertyType === PropertyType.PROPERTY) {
            if (p.propertyValueType === PropertyValueType.TEXT_DOCUMENT) out.push(p);
          } else {
            collectTextDocProps(p, out);
          }
        } catch (e) {}
      }
    }

    function applyToTextDocProp(textDocProp) {
      var original = null;
      try { original = textDocProp.value; } catch (e0) { return false; }

      for (var i = 0; i < candidates.length; i++) {
        var cand = _trim(candidates[i]);
        if (!cand) continue;
        try {
          var td = textDocProp.value;
          td.font = String(cand);
          textDocProp.setValue(td);

          var kept = "";
          try { kept = textDocProp.value.font; } catch (eK) { kept = ""; }

          if (safeLower(kept) === safeLower(cand)) {
            log("INFO: Essential Props font applied OK: '" + cand + "'");
            return true;
          } else {
            try { textDocProp.setValue(original); } catch (eR) {}
          }
        } catch (eTry) {
          try { textDocProp.setValue(original); } catch (eR2) {}
        }
      }

      try { textDocProp.setValue(original); } catch (eR3) {}
      return false;
    }

    for (var l = 1; l <= renderComp.numLayers; l++) {
      var layer = renderComp.layer(l);
      if (!(layer instanceof AVLayer)) continue;

      try {
        if (!(layer.source instanceof CompItem)) continue;
        if (sourceCompName && layer.source.name !== sourceCompName) continue;
      } catch (eS) { continue; }

      var ep = null;
      try { ep = layer.property("Essential Properties"); } catch (e1) { ep = null; }
      if (!ep) {
        try { ep = layer.property("ADBE Essential Properties Group"); } catch (e2) { ep = null; }
      }
      if (!ep) continue;

      var textProps = [];
      collectTextDocProps(ep, textProps);

      for (var p = 0; p < textProps.length; p++) {
        applyToTextDocProp(textProps[p]);
      }
    }
  }

  // =========================
  // MAIN
  // =========================
  try {
    try { logFile.open("w"); logFile.close(); } catch (e) {}
    try { if (doneFile.exists) doneFile.remove(); } catch (e2) {}

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

    clearRenderQueue();

    var textComp = findCompByName(textCompName);
    if (!textComp) throw new Error("Text comp not found: " + textCompName);

    var textLayer = null;
    try { textLayer = textComp.layer(textLayerName); } catch (e0) { textLayer = null; }
    if (!textLayer) {
      for (var l = 1; l <= textComp.numLayers; l++) {
        var tl = textComp.layer(l);
        if (tl && tl.property("Source Text") != null) { textLayer = tl; break; }
      }
    }
    if (!textLayer) throw new Error("No text layer found in comp: " + textCompName);

    var srcText = textLayer.property("Source Text");
    if (!srcText) throw new Error("Source Text property not found on layer: " + textLayer.name);

    // Font override (config-driven)
    var langRaw = detectLanguage(job, projectPath, outputPath);
    var fontCandidates = getFontCandidates(job, langRaw);

    log("INFO: Language detected as '" + (safeLower(langRaw) || "UNKNOWN") + "'. Trying font override.");

    try { applyFontCandidatesSafely(srcText, fontCandidates); } catch (eFont) {
      log("WARN: Font override exception (ignored): " + eFont.toString());
    }

    // Text update
    var td = srcText.value;
    td.text = String(textValue == null ? "" : textValue);
    srcText.setValue(td);
    log("Text updated to: " + textValue);

    // Render comp
    var renderComp = renderCompName ? findCompByName(renderCompName) : textComp;
    if (!renderComp) throw new Error("Render comp not found.");

    // Apply font to Essential Props too (so visible UI matches)
    try { applyFontToEssentialPropsTextInComp(renderComp, textCompName, fontCandidates); } catch (eEP) {
      log("WARN: Essential Props font apply exception (ignored): " + eEP.toString());
    }

    // Add to render queue
    var rqItem = app.project.renderQueue.items.add(renderComp);

    mustApplyTemplate(function (t) { rqItem.applyTemplate(t); }, rsTemplate, "Render Settings");
    var om = rqItem.outputModule(1);
    mustApplyTemplate(function (t) { om.applyTemplate(t); }, omTemplate, "Output Module");

    om.file = new File(outputPath);
    log("Output file set to: " + outputPath);

    app.project.save();
    log("Project saved.");

    writeDone("OK|READY_FOR_AERENDER|" + projectPath);

    try { app.quit(); } catch (eQ) {}

  } catch (err) {
    log("FATAL ERROR: " + err.toString());
    writeDone("ERR|" + err.toString());
    try { app.quit(); } catch (e2) {}
  }
})();
