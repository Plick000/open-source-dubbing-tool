/**
 * _TitleTextAndImageReplaceByDetections.jsx
 *
 * Reads:  _Title__job__.json
 * Writes: ___Title_Prep_Log___.txt
 *         ___Title_Prep_Done___.txt
 *
 * FIXED:
 * - Font: CONFIG-DRIVEN for ALL languages (Z:\...\__languages.json)
 *   - If font already has "-Style" suffix => use as-is
 *   - Else append ExtraBold variants
 *   - If empty/missing => fallback Montserrat-ExtraBold
 * - Applies font to Source Text + Essential Properties (Master Properties)
 * - Text logic preserved:
 *    1) NumberText + BodyText comps => job.number + job.body
 *    2) BodyText only => job.text
 *    3) else legacy: job.textCompName/job.textLayerName => job.text
 * - Image replace:
 *    - uses job.imageLocation if present
 *    - else attempts to resolve from detections JSON in script folder / provided path keys
 * - Render bug fixed:
 *    - queues to AME if queueInAME exists
 *    - else renders in AE as fallback
 *    - if both fail => ERR
 */

(function () {
  try { app.beginSuppressDialogs(); } catch (e) {}

  var scriptFile = new File($.fileName);
  var scriptDir  = scriptFile.parent;

  var LOG_PATH  = scriptDir.fsName + "\\___Title_Prep_Log___.txt";
  var DONE_PATH = scriptDir.fsName + "\\___Title_Prep_Done___.txt";
  var JOB_PATH  = scriptDir.fsName + "\\_Title__job__.json";

  // ✅ Fonts config path
  var LANG_CONFIG_PATH = "Z:\\Automated Dubbings\\admin\\configs\\metadata\\__languages.json";

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

  function ensureMovExtension(p) {
    var s = String(p || "");
    if (!s) return s;
    var lower = s.toLowerCase();
    if (lower.match(/\.mov$/)) return s;
    if (lower.match(/\.[a-z0-9]+$/)) return s.replace(/\.[a-z0-9]+$/i, ".mov");
    return s + ".mov";
  }

  function fileExists(p) {
    try { return (new File(p)).exists; } catch (e) { return false; }
  }

  function parseJsonLoose(str) {
    try { if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(str); } catch (e1) {}
    return eval("(" + str + ")");
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

    if (layerName) {
      try {
        var byName = cmp.layer(layerName);
        if (byName && byName.property("Source Text") != null) return byName;
      } catch (e) {}
    }

    for (var i = 1; i <= cmp.numLayers; i++) {
      var lyr = cmp.layer(i);
      if (lyr && lyr.property("Source Text") != null) return lyr;
    }
    return null;
  }

  function disableTextExpression(sourceTextProp) {
    try {
      if (sourceTextProp && sourceTextProp.canSetExpression && sourceTextProp.expressionEnabled) {
        sourceTextProp.expressionEnabled = false;
        log("INFO: Disabled Source Text expression to prevent override.");
      }
    } catch (e) {}
  }

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

  // ---------------------------
  // CONFIG LOADING (CACHED)
  // ---------------------------
  var __LANG_CFG_CACHE__ = null;
  var __LANG_CFG_ERR__ = null;

  function readFileUtf8(f) {
    f.encoding = "UTF-8";
    f.open("r");
    var s = f.read();
    f.close();
    return s;
  }

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
      var cfg = parseJsonLoose(raw);

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

      var entry = langsObj[lang];
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

  // ✅ KEY FIX: ANY "-suffix" means style already attached
  function hasStyleSuffixAfterDash(fontName) {
    var s = _trim(fontName);
    if (!s) return false;
    var idx = s.lastIndexOf("-");
    return (idx > 0 && idx < (s.length - 1));
  }

  function buildFontCandidatesFromBase(fontBase) {
    var base = _trim(fontBase);
    if (!base) return [];

    if (hasStyleSuffixAfterDash(base)) return [base];

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

    var explicit = _trim(job.font_ps || job.fontPostScriptName || job.fontPS || "");
    if (explicit) out.push(explicit);

    var base = getFontBaseFromConfig(langRaw);
    if (base) {
      log("INFO: Config font for '" + safeLower(langRaw) + "' => '" + base + "'");
      out = out.concat(buildFontCandidatesFromBase(base));
    } else {
      log("INFO: No config font found for '" + (safeLower(langRaw) || "UNKNOWN") + "'.");
    }

    if (out.length === (explicit ? 1 : 0)) {
      var legacy = legacyFontCandidatesForLang(langRaw);
      if (legacy) {
        log("INFO: Using legacy font fallback for '" + safeLower(langRaw) + "'.");
        out = out.concat(legacy);
      }
    }

    out = out.concat([
      "Montserrat-ExtraBold",
      "Montserrat ExtraBold",
      "Montserrat-Bold",
      "Montserrat Bold",
      "Montserrat"
    ]);

    return uniqCaseInsensitive(out);
  }

  function applyFontCandidatesSafely(st, candidates) {
    if (!st || !candidates || !candidates.length) return false;

    var originalDoc = st.value;
    var originalFont = "";
    try { originalFont = originalDoc.font; } catch (e0) { originalFont = ""; }

    for (var i = 0; i < candidates.length; i++) {
      var cand = _trim(candidates[i]);
      if (!cand) continue;

      try {
        var doc = st.value;
        doc.font = String(cand);
        st.setValue(doc);

        var kept = "";
        try { kept = st.value.font; } catch (eK) { kept = ""; }

        if (safeLower(kept) === safeLower(cand)) {
          log("INFO: Font applied OK: '" + cand + "'");
          return true;
        } else {
          try { st.setValue(originalDoc); } catch (eR) {}
        }
      } catch (eTry) {
        try { st.setValue(originalDoc); } catch (eR2) {}
      }
    }

    try { st.setValue(originalDoc); } catch (eR3) {}
    var after = "";
    try { after = st.value.font; } catch (eA) { after = ""; }
    log("WARN: No font candidates worked. Kept font '" + after + "' (original '" + originalFont + "')");
    return false;
  }

  // ✅ Essential Properties (Master Properties) font apply
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

  // Set FONT first then TEXT
  function setTextOnLayer(textLayer, newText, fontCandidatesOrNull) {
    if (!textLayer) return false;

    var st = null;
    try { st = textLayer.property("Source Text"); } catch (e0) {}
    if (!st) return false;

    disableTextExpression(st);

    try {
      if (fontCandidatesOrNull && fontCandidatesOrNull.length) {
        applyFontCandidatesSafely(st, fontCandidatesOrNull);
      }

      var doc = st.value;
      doc.text = String(newText == null ? "" : newText);
      st.setValue(doc);

      return true;
    } catch (e) {
      log("WARN: Failed setting text on layer: " + textLayer.name + " | " + e.toString());
      return false;
    }
  }

  function updateTextInsideComp(compName, layerName, newText, fontCandidatesOrNull) {
    var cmp = getComp(compName);
    if (!cmp) { log("WARN: Comp not found: " + compName); return false; }
    var lyr = findTextLayer(cmp, layerName);
    if (!lyr) { log("WARN: No text layer in comp: " + compName); return false; }
    var ok = setTextOnLayer(lyr, newText, fontCandidatesOrNull);
    if (ok) log("Updated comp text: " + compName + " | layer=" + lyr.name);
    return ok;
  }

  // --------------------------------
  // IMAGE REPLACE + DETECTIONS FALLBACK
  // --------------------------------
  function resolveImageLocationFromDetections(job, scriptDirFolder) {
    function tryGetPath(obj, keys) {
      if (!obj) return "";
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        if (obj.hasOwnProperty(k) && _trim(obj[k])) return _trim(obj[k]);
      }
      return "";
    }

    var detPath = _trim(
      job.detectionsPath || job.detectionPath || job.detectionsFile || job.detectionFile ||
      job.detectionsJson || job.detectionJson || job.detectionsLocation || ""
    );

    if (!detPath) {
      try {
        var f = new Folder(scriptDirFolder);
        var files = f.getFiles(function (x) {
          if (!(x instanceof File)) return false;
          var n = safeLower(x.name);
          return (n.indexOf("detect") !== -1 && n.indexOf(".json") !== -1);
        });
        if (files && files.length) detPath = files[0].fsName;
      } catch (e) {}
    }

    if (!detPath) return "";

    detPath = normalizeWindowsPath(detPath);
    var detFile = new File(detPath);
    if (!detFile.exists) return "";

    try {
      detFile.encoding = "UTF-8";
      detFile.open("r");
      var raw = detFile.read();
      detFile.close();

      var data = parseJsonLoose(raw);
      var keys = ["imageLocation","image_path","imagePath","image","path","src","file","filepath","fullpath"];

      var p1 = tryGetPath(data, keys);
      if (p1) return normalizeWindowsPath(p1);

      if (data && (data instanceof Array)) {
        for (var i = 0; i < data.length; i++) {
          var p2 = tryGetPath(data[i], keys);
          if (p2) return normalizeWindowsPath(p2);

          var nested = data[i] && (data[i].data || data[i].payload || data[i].item);
          var p3 = tryGetPath(nested, keys);
          if (p3) return normalizeWindowsPath(p3);
        }
      }

      var containers = [data && data.items, data && data.replacements, data && data.detections];
      for (var c = 0; c < containers.length; c++) {
        var arr = containers[c];
        if (arr && (arr instanceof Array)) {
          for (var j = 0; j < arr.length; j++) {
            var p4 = tryGetPath(arr[j], keys);
            if (p4) return normalizeWindowsPath(p4);
          }
        }
      }
    } catch (e2) {}

    return "";
  }

  function clearRenderQueue() {
    try {
      var rq = app.project.renderQueue;
      for (var i = rq.numItems; i >= 1; i--) {
        try { rq.item(i).remove(); } catch (e) {}
      }
      log("INFO: Render Queue cleared.");
    } catch (e2) {}
  }

  function mustApplyTemplate(fnApply, templateName, label) {
    if (!templateName) throw new Error(label + " template is empty/missing.");
    fnApply(templateName);
    log("INFO: Applied " + label + " template: " + templateName);
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
    if (!targetLayer) throw new Error("targetLayer is null");
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

  // =========================
  // MAIN
  // =========================
  try {
    try { logFile.open("w"); logFile.close(); } catch (e0) {}
    try { if (doneFile.exists) doneFile.remove(); } catch (e1) {}

    log("prep_for_title.jsx started.");

    var jobFile = new File(JOB_PATH);
    if (!jobFile.exists) throw new Error("_Title__job__.json missing");
    jobFile.encoding = "UTF-8";
    jobFile.open("r");
    var job = parseJsonLoose(jobFile.read());
    jobFile.close();

    var projectPath = normalizeWindowsPath(job.projectPath || "");
    var outputPath  = normalizeWindowsPath(job.output || "");

    if (!projectPath) throw new Error("projectPath missing in job JSON");
    if (!outputPath)  throw new Error("output missing in job JSON");
    if (!fileExists(projectPath)) throw new Error("AEP not found: " + projectPath);

    outputPath = ensureMovExtension(outputPath);

    var langRaw = detectLanguage(job, outputPath, projectPath);
    var fontCandidates = getFontCandidates(job, langRaw);

    log("INFO: Opening AEP: " + projectPath);
    app.open(new File(projectPath));

    // TEXT UPDATE
    var numberComp = getComp("NumberText");
    var bodyComp   = getComp("BodyText");

    var hasNumberComp = (numberComp instanceof CompItem);
    var hasBodyComp   = (bodyComp instanceof CompItem);

    var didText = false;

    if (hasNumberComp && hasBodyComp) {
      log("INFO: TWO-COMP setup: NumberText + BodyText.");

      if (job.hasOwnProperty("number")) {
        didText = updateTextInsideComp("NumberText", job.numberLayerName, job.number, fontCandidates) || didText;
      } else {
        log("WARN: Missing job.number in two-comp mode.");
      }

      if (job.hasOwnProperty("body")) {
        didText = updateTextInsideComp("BodyText", job.bodyLayerName, job.body, fontCandidates) || didText;
      } else {
        log("WARN: Missing job.body in two-comp mode.");
      }

    } else if (!hasNumberComp && hasBodyComp) {
      log("INFO: SINGLE-COMP setup: BodyText only.");

      if (job.hasOwnProperty("text")) {
        didText = updateTextInsideComp("BodyText", job.bodyLayerName, job.text, fontCandidates) || didText;
      } else {
        log("WARN: Missing job.text in BodyText-only mode.");
      }

    } else {
      log("INFO: Legacy fallback mode.");

      var legacyCompName  = job.textCompName  || "viralverse_Trailer";
      var legacyLayerName = job.textLayerName || "ViralVerse_Title";

      if (job.hasOwnProperty("text")) {
        didText = updateTextInsideComp(legacyCompName, legacyLayerName, job.text, fontCandidates) || didText;
      } else {
        log("WARN: Missing job.text in legacy mode.");
      }
    }

    if (!didText) log("WARN: Text update appears to have failed (comp/layer mismatch or expression-driven).");

    // IMAGE REPLACE (job.imageLocation OR detections fallback)
    if (!job.imageLocation) {
      job.imageLocation = resolveImageLocationFromDetections(job, scriptDir.fsName);
      if (job.imageLocation) log("INFO: Image location resolved from detections: " + job.imageLocation);
    }

    if (job.imageLocation) {
      var imgPath = normalizeWindowsPath(job.imageLocation);
      var imgFile = new File(imgPath);
      if (imgFile.exists) {
        var imgComp = getComp(job.imageCompName || "VV_IMAGE");
        if (imgComp instanceof CompItem) {
          var targetLayer = findBestImageLayer(imgComp, Number(job.imageLayerIndex || 1));
          if (targetLayer) {
            replaceLayerImage(targetLayer, imgFile);
            fitLayerToCompCover(targetLayer, imgComp);
            log("INFO: Image replaced OK.");
          } else {
            log("WARN: No suitable image layer found in image comp.");
          }
        } else {
          log("WARN: Image comp not found: " + (job.imageCompName || "VV_IMAGE"));
        }
      } else {
        log("WARN: imageLocation file not found: " + imgPath);
      }
    } else {
      log("INFO: No imageLocation provided/resolved. Skipping image replace.");
    }

    // RENDER SETUP
    clearRenderQueue();

    var renderCompName = job.renderCompName || job.renderComp || job.comp || "|| TRAILER VIRALVERSE";
    var renderComp = getComp(renderCompName);
    if (!(renderComp instanceof CompItem)) throw new Error("Render comp not found: " + renderCompName);

    // Essential Props font apply to render comp too
    try {
      applyFontToEssentialPropsTextInComp(renderComp, (job.textCompName || ""), fontCandidates);
    } catch (eEP) {
      log("WARN: Essential Props font apply exception (ignored): " + eEP.toString());
    }

    var rqItem = app.project.renderQueue.items.add(renderComp);

    // Templates
    if (job.rsTemplate && _trim(job.rsTemplate)) {
      mustApplyTemplate(function (t) { rqItem.applyTemplate(t); }, job.rsTemplate, "Render Settings");
    } else {
      log("WARN: rsTemplate missing; using current render settings.");
    }

    var om = rqItem.outputModule(1);

    var omT = _trim(job.omTemplate || "VV Title Render");
    try { om.applyTemplate(omT); log("INFO: Applied Output Module template: " + omT); }
    catch (eOM) { log("WARN: Failed applying OM template '" + omT + "': " + eOM.toString()); }

    om.file = new File(outputPath);
    log("INFO: Output path: " + outputPath);

    // SAVE BEFORE RENDER
    try { app.project.save(); log("INFO: Project saved."); } catch (eSave) { log("WARN: Project save failed: " + eSave.toString()); }

    // RENDER BUG FIX:
    // - Prefer queue in AME if available
    // - Otherwise render in AE
    var rendered = false;
    try {
      if (app.project.renderQueue && app.project.renderQueue.queueInAME) {
        app.project.renderQueue.queueInAME(true);
        rendered = true;
        done("OK|Queued to AME|OUT=" + outputPath);
        log("INFO: Queued to AME.");
      }
    } catch (eAME) {
      log("WARN: queueInAME failed: " + eAME.toString());
      rendered = false;
    }

    if (!rendered) {
      try {
        // AE fallback render
        app.project.renderQueue.render();
        rendered = true;
        done("OK|Rendered in AE|OUT=" + outputPath);
        log("INFO: Rendered in AE (fallback).");
      } catch (eAE) {
        throw new Error("Render failed (AME + AE): " + eAE.toString());
      }
    }

    try { app.quit(); } catch (eQ) {}

  } catch (err) {
    log("FATAL ERROR: " + err.toString());
    done("ERR|" + err.toString());
    try { app.quit(); } catch (e2) {}
  }
})();
