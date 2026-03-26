/**
 * _V3__Text_Move_FitToEndFrame.jsx
 *
 * - SMART text update:
 *     - If clip has 1 text field  => use JSON.text
 *     - If clip has 2+ text fields => use JSON.number + JSON.body (ignore JSON.text)
 * - KR/RU font override via fontEditValue (only when language is Korean/Russian)
 * - Moves clip to JSON start_frame (absolute)
 * - FITS end frame:
 *     - For NON-MOGRT clips: tries speed (setSpeed)
 *     - For MOGRT/Graphics: skips speed (prevents disappearing/rebuild), uses duration-fit (outPoint + end)
 *
 * Reads:
 *   output/JSON/A25__final_titles_merged_segments__.json
 *   inputs/config/config.json
 */

(function () {
  var TARGET_TRACK_INDEX = 2; // V3
  var TICKS_PER_SECOND = 254016000000;

  var SINGLE_PARAM_NAMES = ["History", "Title", "Text", "Main Text", "Heading", "body", "body ", "Body", "BODY", "Source Text", "Source Text "];

  var NUMBER_PARAM_NAMES = [
    "Number", "No", "No.", "Index", "Counter", "Rank",
    "NUMBER", "NO", "NO.", "INDEX", "COUNTER", "RANK"
  ];

  var BODY_PARAM_NAMES = [
    "Body", "BODY",
    "Text", "TEXT",
    "Main Text", "MAIN TEXT", "MainText", "MAINTEXT", "main_text", "MAIN_TEXT",
    "Heading", "HEADING",
    "History", "HISTORY",
    "Title", "TITLE",
    "Description", "DESCRIPTION",
    "Text_1", "TEXT_1", "text_1",
  ];

  var KR_BASE = "NotoSansKR";
  var RU_BASE = "NotoSans";
  var DEFAULT_FONT_EXPLICIT = "Montserrat-ExtraBold";
  var LANG_META_PATH = "Z:/Automated Dubbings/admin/configs/metadata/__languages.json";
  function trim(s) { return (s + "").replace(/^\s+|\s+$/g, ""); }
  function toInt(v, fb) { var n = parseInt(v, 10); return isNaN(n) ? fb : n; }

  // ----------------------------
  // LOGGING
  // ----------------------------
  function ts() {
    try {
      var d = new Date();
      function pad(n) { return (n < 10 ? "0" : "") + n; }
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
        pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    } catch (e) { return ""; }
  }

  function appendFileLog(line) {
    try {
      var p = ($.global && $.global.__LOG_PATH) ? $.global.__LOG_PATH : "";
      if (!p) return;
      var f = new File(p);
      f.open("a");
      f.writeln(ts() + " " + String(line));
      f.close();
    } catch (e) { }
  }

  function log(msg, level) {
    try { appendFileLog(msg); } catch (e0) { }
    try {
      if (app && typeof app.setSDKEventMessage === "function") {  
        app.setSDKEventMessage(String(msg), level || "info");
        return;
      }
    } catch (e1) { }
    try { $.writeln(String(msg)); } catch (e2) { }
  }

  function fail(msg) {
    try {
      if ($.global) {
        $.global.__PIPELINE_LAST_OK = false;
        $.global.__PIPELINE_LAST_MSG = String(msg || "Unknown failure");
      }
    } catch (e) { }
    log("❌ " + msg, "error");
  }

  function makeTimeTicks(ticksNumber) {
    var t = new Time();
    t.ticks = String(Math.round(ticksNumber));
    return t;
  }

  function getScriptJsonFile() {
    var scriptFile = new File($.fileName);
    var folder = scriptFile.parent;
    return new File(folder.fsName + "/output/JSON/A25__final_titles_merged_segments__.json");
  }

  function getConfigJsonFile() {
    var scriptFile = new File($.fileName);
    var folder = scriptFile.parent;
    return new File(folder.fsName + "/inputs/config/config.json");
  }

  // ----------------------------
  // JSON reading
  // ----------------------------
  function parseJsonSafe(raw) {
    raw = String(raw || "");
    raw = raw.replace(/^\uFEFF/, "");
    try {
      if (typeof JSON !== "undefined" && JSON && typeof JSON.parse === "function") {
        return JSON.parse(raw);
      }
    } catch (e1) { }
    return eval("(" + raw + ")");
  }

  function readJsonArray(f) {
    f.encoding = "UTF8";
    if (!f.open("r")) throw new Error("Cannot open JSON: " + f.fsName);
    var raw = f.read();
    f.close();

    var data = parseJsonSafe(raw);
    if (!data || !(data instanceof Array)) throw new Error("JSON root must be an array.");
    data.sort(function (a, b) { return toInt(a.id, 0) - toInt(b.id, 0); });
    return data;
  }

  function readJsonObjectOrNull(f) {
    try {
      if (!f || !f.exists) return null;
      f.encoding = "UTF8";
      if (!f.open("r")) return null;
      var raw = f.read();
      f.close();
      var obj = parseJsonSafe(raw);
      if (!obj || (obj instanceof Array)) return null;
      return obj;
    } catch (e) {
      try { if (f && f.opened) f.close(); } catch (e2) { }
      return null;
    }
  }

  function normLang(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/^\s+|\s+$/g, "")
      .replace(/[\s_\-]+/g, "");
  }

  function pickLanguageFromConfig(cfg) {
    if (!cfg) return "";
    return String(
      cfg.language ||
      cfg.lang ||
      cfg.language_code ||
      cfg.languageCode ||
      (cfg.config && (cfg.config.language || cfg.config.language_code)) ||
      ""
    );
  }

  function getLanguagesMetadataJsonFile() {
    // Use forward slashes to avoid Windows escape issues in JSX strings.
    return new File(LANG_META_PATH);
  }

  function readJsonArrayNoSort(f) {
    f.encoding = "UTF8";
    if (!f.open("r")) throw new Error("Cannot open JSON: " + f.fsName);
    var raw = f.read();
    f.close();

    var data = parseJsonSafe(raw);
    if (!data || !(data instanceof Array)) throw new Error("JSON root must be an array.");
    return data;
  }

  function canonicalLangToken(n) {
    n = normLang(n);
    // Common aliases / mismatched codes
    var m = {
      "ko": "korean",
      "kr": "korean",
      "kor": "korean",
      "ru": "russian",
      "rus": "russian",
      "ptbr": "portuguese",
      "pt-br": "portuguese"
    };
    return m[n] || n;
  }

  function findFontForLanguageFromMetadata(langToken) {
    // Returns:
    //   { found: true,  font: "<string>" }   // font may be "" (meaning default)
    //   { found: false, font: "" }
    try {
      var f = getLanguagesMetadataJsonFile();
      if (!f || !f.exists) return { found: false, font: "" };

      var arr = readJsonArrayNoSort(f);
      var want = canonicalLangToken(langToken);
      if (!want) return { found: false, font: "" };

      for (var i = 0; i < arr.length; i++) {
        var root = arr[i];
        if (!root) continue;

        for (var brandKey in root) {
          if (!root.hasOwnProperty(brandKey)) continue;
          var brandObj = root[brandKey];
          if (!brandObj || !brandObj.languages) continue;

          var langs = brandObj.languages;

          // 1) direct key match
          for (var k in langs) {
            if (!langs.hasOwnProperty(k)) continue;
            if (canonicalLangToken(k) === want) {
              var it1 = langs[k] || {};
              return { found: true, font: String(it1.font == null ? "" : it1.font) };
            }
          }

          // 2) langcode match (KR/FR/ES/...)
          for (var k2 in langs) {
            if (!langs.hasOwnProperty(k2)) continue;
            var it2 = langs[k2] || {};
            var code = it2.langcode || it2.langCode || it2.code || "";
            if (canonicalLangToken(code) === want) {
              return { found: true, font: String(it2.font == null ? "" : it2.font) };
            }
          }
        }
      }

      return { found: false, font: "" };
    } catch (e) {
      return { found: false, font: "" };
    }
  }

  function getFontOverrideBaseOrNull() {
    // NEW behavior:
    // - Read language from inputs/config/config.json
    // - Look up that language inside Z:\Automated Dubbings\admin\configs\metadata\__languages.json
    // - Use the FIRST match's "font" (any brand). If empty => DEFAULT_FONT_EXPLICIT
    // Fallback:
    // - If metadata missing/unreadable or language not found => legacy KR/RU only behavior.
    var cfg = readJsonObjectOrNull(getConfigJsonFile());
    var lang = pickLanguageFromConfig(cfg);
    var n = canonicalLangToken(lang);

    if (!n) return null;

    var meta = findFontForLanguageFromMetadata(n);

    if (meta.found) {
      var fnt = trim(meta.font);
      if (!fnt) fnt = DEFAULT_FONT_EXPLICIT;
      return { language: lang, base: fnt, source: "languages_metadata" };
    }

    // Legacy fallback (preserves current behavior if metadata lookup fails)
    if (n === "korean" || n.indexOf("korean") >= 0) return { language: lang, base: KR_BASE, source: "legacy" };
    if (n === "russian" || n.indexOf("russian") >= 0) return { language: lang, base: RU_BASE, source: "legacy" };

    return null;
  }

  // ----------------------------
  // MOGRT param helpers
  // ----------------------------
  function findParam(mgtProps, name) {
    if (!mgtProps) return null;

    try {
      var p = mgtProps.getParamForDisplayName(name);
      if (p) return p;
    } catch (e) { }

    var want = (name || "").toLowerCase();
    try {
      for (var i = 0; i < mgtProps.numItems; i++) {
        var it = mgtProps[i];
        if (!it) continue;
        var dn = (it.displayName || "").toString().toLowerCase();
        if (dn === want) return it;
      }
    } catch (e2) { }

    return null;
  }

  function pickFirstParamByNames(mgtProps, names) {
    if (!mgtProps || !names || !names.length) return null;
    for (var i = 0; i < names.length; i++) {
      var nm = names[i];
      if (!nm) continue;
      var p = findParam(mgtProps, nm);
      if (p) return p;
    }
    return null;
  }

  function getMogrtProps(trackItem) {
    try {
      var mgt = trackItem.getMGTComponent();
      if (!mgt || !mgt.properties) return null;
      return mgt.properties;
    } catch (e) { return null; }
  }

  function isMogrtItem(trackItem) {
    try {
      var m = trackItem.getMGTComponent();
      return !!(m && m.properties);
    } catch (e) {
      return false;
    }
  }

  // ----------------------------
  // STYLE-SAFE JSON string editing
  // ----------------------------
  function escapeJsonString(s) {
    s = String(s == null ? "" : s);
    return s
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\u0008/g, "\\b")
      .replace(/\u000C/g, "\\f")
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\t/g, "\\t");
  }

  function replaceJsonStringField(raw, fieldName, newEscapedValue) {
    var re = new RegExp('("' + fieldName + '"\\s*:\\s*")((?:\\\\.|[^"\\\\])*)(")', "g");
    if (!re.test(raw)) return { ok: false, out: raw };
    var out = raw.replace(re, '$1' + newEscapedValue + '$3');
    return { ok: true, out: out };
  }

  function replaceJsonStringArrayFirst(raw, fieldName, newEscapedValue) {
    var re = new RegExp('("' + fieldName + '"\\s*:\\s*\\[\\s*")((?:\\\\.|[^"\\\\])*)(")', "g");
    if (!re.test(raw)) return { ok: false, out: raw };
    var out = raw.replace(re, '$1' + newEscapedValue + '$3');
    return { ok: true, out: out };
  }

  function getJsonStringArrayFirst(raw, fieldName) {
    try {
      var re = new RegExp('"' + fieldName + '"\\s*:\\s*\\[\\s*"((?:\\\\.|[^"\\\\])*)"', "i");
      var m = String(raw || "").match(re);
      if (!m || !m[1]) return "";
      var s = m[1];
      s = s.replace(/\\"/g, '"').replace(/\\\\/g, "\\");
      return s;
    } catch (e) { return ""; }
  }

  function replaceJsonNumberArrayField(raw, fieldName, num) {
    var re = new RegExp('("' + fieldName + '"\\s*:\\s*)\\[[^\\]]*\\]', "g");
    if (!re.test(raw)) return { ok: false, out: raw };
    var out = raw.replace(re, '$1[' + String(num) + ']');
    return { ok: true, out: out };
  }

  function replaceJsonNumberField(raw, fieldName, num) {
    var re = new RegExp('("' + fieldName + '"\\s*:\\s*)(-?\\d+(?:\\.\\d+)?)', "g");
    if (!re.test(raw)) return { ok: false, out: raw };
    var out = raw.replace(re, '$1' + String(num));
    return { ok: true, out: out };
  }

  function isJsonishTextValue(str) {
    try {
      if (typeof str !== "string") return false;
      var t = trim(str);
      if (!t.length) return false;
      var first = t.charAt(0);
      if (!(first === "{" || first === "[")) return false;
      return /"textEditValue"\s*:\s*"/.test(t);
    } catch (e) { return false; }
  }

  function setMogrtTextPreserveStyleParam(param, newText) {
    if (!param) return false;
    try {
      var cur = param.getValue();
      if (!isJsonishTextValue(cur)) return false;

      var escText = escapeJsonString(newText);
      var r1 = replaceJsonStringField(cur, "textEditValue", escText);
      if (!r1.ok) return false;

      var out = r1.out;

      var len = String(newText == null ? "" : newText).length;
      out = replaceJsonNumberArrayField(out, "fontTextRunLength", len).out;
      out = replaceJsonNumberArrayField(out, "fontTextRunStart", 0).out;
      out = replaceJsonNumberField(out, "fontTextRunCount", 1).out;

      return param.setValue(out, true);
    } catch (e) {
      return false;
    }
  }

  function buildFontCandidates(base, currentFont) {
    var out = [];
    function push(v) {
      if (!v) return;
      var low = String(v).toLowerCase();
      for (var i = 0; i < out.length; i++) if (String(out[i]).toLowerCase() === low) return;
      out.push(v);
    }

    base = String(base || "");

    // If base already contains a style suffix (e.g. "GFSNeohellenic-Bold" or "Montserrat-Extrabold"),
    // treat it as an explicit font name and try it first (plus a couple of safe case variants).
    if (base.indexOf("-") >= 0) {
      push(base);

      // Case-variant safety for "ExtraBold"/"Extrabold"
      if (/-(extrabold)$/i.test(base)) {
        push(base.replace(/-(extrabold)$/i, "-ExtraBold"));
        push(base.replace(/-(extrabold)$/i, "-Extrabold"));
      }
      return out;
    }

    // Otherwise, base is a family name (e.g. "NotoSans").
    // We will try to apply the current style suffix first (if any), then common weights.
    var suffix = "";
    var idx = String(currentFont || "").lastIndexOf("-");
    if (idx > 0 && idx < String(currentFont).length - 1) suffix = String(currentFont).substring(idx + 1);

    if (suffix) push(base + "-" + suffix);

    // Prefer ExtraBold first as requested
    push(base + "-ExtraBold");
    push(base + "-Extrabold"); // some fonts use this casing
    push(base + "-SemiBold");
    push(base + "-Bold");
    push(base + "-Medium");
    push(base + "-Regular");
    push(base + "-Light");
    push(base);

    return out;
  }

  function setMogrtFontEditValuePreserveStyleParam(param, baseFontFamily) {
    if (!param || !baseFontFamily) return false;

    try {
      var cur = param.getValue();
      if (typeof cur !== "string") return false;

      var t = trim(cur);
      if (!t.length) return false;

      if (!/"fontEditValue"\s*:\s*\[/.test(t)) return false;

      var currentFont = getJsonStringArrayFirst(t, "fontEditValue");
      var cands = buildFontCandidates(baseFontFamily, currentFont);

      for (var i = 0; i < cands.length; i++) {
        var cand = cands[i];
        var esc = escapeJsonString(cand);

        var r = replaceJsonStringArrayFirst(t, "fontEditValue", esc);
        if (!r.ok) continue;

        try {
          var ok = param.setValue(r.out, true);
          if (ok) return true;
        } catch (eSet) { }
      }

      return false;
    } catch (e) {
      return false;
    }
  }

  function getAllJsonTextParams(mgtProps) {
    var out = [];
    if (!mgtProps) return out;
    try {
      for (var i = 0; i < mgtProps.numItems; i++) {
        var it = mgtProps[i];
        if (!it) continue;
        var val = null;
        try { val = it.getValue(); } catch (e1) { val = null; }
        if (isJsonishTextValue(val)) out.push(it);
      }
    } catch (e2) { }
    return out;
  }

  function looksLikeNumberName(name) {
    var s = String(name || "").toLowerCase();
    return /(number|no\.?|index|rank|counter)/.test(s);
  }

  function smartPickNumberBodyParams(mgtProps, textParams) {
    var numP = pickFirstParamByNames(mgtProps, NUMBER_PARAM_NAMES);
    var bodyP = pickFirstParamByNames(mgtProps, BODY_PARAM_NAMES);

    if (numP && bodyP && numP === bodyP) bodyP = null;

    if (!numP) {
      for (var i = 0; i < textParams.length; i++) {
        var dn = "";
        try { dn = String(textParams[i].displayName || ""); } catch (e) { dn = ""; }
        if (looksLikeNumberName(dn)) { numP = textParams[i]; break; }
      }
    }

    if (!bodyP) {
      for (var j = 0; j < textParams.length; j++) {
        if (textParams[j] !== numP) { bodyP = textParams[j]; break; }
      }
    }

    if (!numP && textParams.length > 0) numP = textParams[0];
    if (!bodyP && textParams.length > 1) bodyP = textParams[1];

    return { num: numP, body: bodyP };
  }

  // ----------------------------
  // MOVE
  // ----------------------------
  function moveByTicks(trackItem, deltaTicks) {
    if (!deltaTicks) return true;
    var t = new Time();
    t.seconds = deltaTicks / TICKS_PER_SECOND;
    try { trackItem.move(t); return true; } catch (e) { return false; }
  }

  // ----------------------------
  // FIT END
  // ----------------------------
  function setSpeedCompat(trackItem, speedPct) {
    try { return trackItem.setSpeed(speedPct, false, false, false); } catch (e1) { }
    try { return trackItem.setSpeed(speedPct, false, false); } catch (e2) { }
    try { return trackItem.setSpeed(speedPct); } catch (e3) { }
    return false;
  }

  function trySpeedToFit(trackItem, desiredStartTicks, desiredEndTicks) {
    try {
      if (desiredEndTicks <= desiredStartTicks) return false;

      var desiredDur = desiredEndTicks - desiredStartTicks;
      var curDur = Number(trackItem.end.ticks) - Number(trackItem.start.ticks);
      if (!curDur || isNaN(curDur) || curDur <= 0) return false;

      var speedPct = 100 * (curDur / desiredDur);
      if (!speedPct || isNaN(speedPct)) return false;

      if (speedPct < 5) speedPct = 5;
      if (speedPct > 2000) speedPct = 2000;

      return !!setSpeedCompat(trackItem, speedPct);
    } catch (e) {
      return false;
    }
  }

  function durationFitToEnd(trackItem, desiredStartTicks, desiredEndTicks) {
    try {
      if (desiredEndTicks <= desiredStartTicks) return false;

      var desiredDurTicks = desiredEndTicks - desiredStartTicks;

      var inTicks = Number(trackItem.inPoint.ticks);
      var newOutTicks = inTicks + desiredDurTicks;

      trackItem.outPoint = makeTimeTicks(newOutTicks);
      trackItem.end = makeTimeTicks(desiredEndTicks);

      return true;
    } catch (e) {
      return false;
    }
  }

  function main() {
    var seq = app.project.activeSequence;
    if (!seq) { fail("No active sequence."); return; }

    if (!seq.videoTracks || seq.videoTracks.numTracks <= TARGET_TRACK_INDEX) {
      fail("V3 not found (track index 2).");
      return;
    }

    var jsonFile = getScriptJsonFile();
    if (!jsonFile.exists) { fail("JSON not found: " + jsonFile.fsName); return; }

    var fontOv = getFontOverrideBaseOrNull();
    if (fontOv) log("🔤 Font override ON | language='" + fontOv.language + "' | font='" + fontOv.base + "' | source=" + (fontOv.source || "unknown"), "info");
    else log("🔤 Font override OFF | running normally", "info");

    var segments = readJsonArray(jsonFile);

    var track = seq.videoTracks[TARGET_TRACK_INDEX];
    var clipCount = track.clips.numItems;
    var n = Math.min(segments.length, clipCount);

    var ticksPerFrame = Number(seq.timebase);
    if (!ticksPerFrame || isNaN(ticksPerFrame)) { fail("Could not read sequence.timebase"); return; }

    var clips = [];
    for (var i = 0; i < clipCount; i++) clips.push(track.clips[i]);

    log("▶ SmartText+Move+FitEnd starting. Clips=" + clipCount + ", Segments=" + segments.length + ", Processing=" + n, "info");

    // ----------------------------
    // Pass 1: SMART text + font
    // ----------------------------
    var okSingle = 0, badSingle = 0, okNum = 0, badNum = 0, okBody = 0, badBody = 0;
    var okFont = 0, badFont = 0;

    for (var a = 0; a < n; a++) {
      var segA = segments[a];
      var clipA = clips[a];

      var props = getMogrtProps(clipA);
      if (!props) { badSingle++; continue; }

      var textParams = getAllJsonTextParams(props);
      var fieldCount = textParams.length;

      if (fieldCount <= 1) {
        var newText = (segA && segA.text != null) ? String(segA.text) : "";
        var pSingle = pickFirstParamByNames(props, SINGLE_PARAM_NAMES);

        var okS = false;
        var usedParam = null;

        if (pSingle) {
          okS = setMogrtTextPreserveStyleParam(pSingle, newText);
          usedParam = pSingle;
        } else if (textParams.length === 1) {
          okS = setMogrtTextPreserveStyleParam(textParams[0], newText);
          usedParam = textParams[0];
        } else {
          okS = false;
        }

        if (okS) okSingle++; else badSingle++;

        if (fontOv && usedParam) {
          var fk = setMogrtFontEditValuePreserveStyleParam(usedParam, fontOv.base);
          if (fk) okFont++; else badFont++;
        }

      } else {
        var newNumber = (segA && segA.number != null) ? String(segA.number) : "";
        var newBody   = (segA && segA.body   != null) ? String(segA.body)   : "";

        var picked = smartPickNumberBodyParams(props, textParams);

        var ok1 = picked.num  ? setMogrtTextPreserveStyleParam(picked.num,  newNumber) : false;
        var ok2 = picked.body ? setMogrtTextPreserveStyleParam(picked.body, newBody)   : false;

        if (ok1) okNum++; else badNum++;
        if (ok2) okBody++; else badBody++;

        if (fontOv && picked.num)  {
          var f1 = setMogrtFontEditValuePreserveStyleParam(picked.num,  fontOv.base);
          if (f1) okFont++; else badFont++;
        }
        if (fontOv && picked.body) {
          var f2 = setMogrtFontEditValuePreserveStyleParam(picked.body, fontOv.base);
          if (f2) okFont++; else badFont++;
        }
      }
    }

    // ----------------------------
    // Pass 2: move + fit end
    // ----------------------------
    var okMove = 0, badMove = 0;
    var okSpeed = 0, badSpeed = 0;
    var okDurFit = 0, badDurFit = 0;

    for (var b = 0; b < n; b++) {
      var seg = segments[b];
      var clip = clips[b];

      var startFrame = toInt(seg.start_frame, null);
      var endFrame = toInt(seg.end_frame, null);

      if (endFrame === null) {
        var durFrames = toInt(seg.duration_frames, null);
        if (durFrames !== null && startFrame !== null) endFrame = startFrame + durFrames;
      }

      if (startFrame === null || endFrame === null) continue;

      var desiredStartTicks = startFrame * ticksPerFrame;
      var desiredEndTicks   = endFrame   * ticksPerFrame;

      // move
      var curStartTicks = Number(clip.start.ticks);
      var deltaTicks = desiredStartTicks - curStartTicks;

      var mvOK = moveByTicks(clip, deltaTicks);
      if (mvOK) okMove++; else badMove++;

      // fit end
      // IMPORTANT: For MOGRT/Graphics, DO NOT use setSpeed (can cause clip to disappear/rebuild).
      var spOK = false;
      if (!isMogrtItem(clip)) {
        spOK = trySpeedToFit(clip, desiredStartTicks, desiredEndTicks);
      }

      if (spOK) {
        okSpeed++;
      } else {
        badSpeed++;
        var dfOK = durationFitToEnd(clip, desiredStartTicks, desiredEndTicks);
        if (dfOK) okDurFit++; else badDurFit++;
      }
    }

    // ----------------------------
    // Summary + guards
    // ----------------------------
    log(
      "✅ DONE | Processed=" + n +
      " | SingleText OK=" + okSingle + " Fail=" + badSingle +
      " | Number OK=" + okNum + " Fail=" + badNum +
      " | Body OK=" + okBody + " Fail=" + badBody +
      (fontOv ? (" | Font OK=" + okFont + " Fail=" + badFont) : "") +
      " | Move OK=" + okMove + " Fail=" + badMove +
      " | Speed OK=" + okSpeed + " Fail=" + badSpeed +
      " | DurationFit OK=" + okDurFit + " Fail=" + badDurFit,
      "info"
    );

    if (n > 0 && okSingle === 0 && okNum === 0 && okBody === 0) {
      fail("All text updates failed. Likely mogrt values are not JSON-style (no textEditValue) or params not accessible.");
      return;
    }

    if (n > 0 && okSpeed === 0 && okDurFit === 0) {
      fail("All end-fit updates failed. Speed likely unsupported and duration-fit failed.");
      return;
    }
  }

  try { main(); }
  catch (e) { fail("Script crashed: " + e); }
  
  // ✅ ensure eval result is simple, not an object/array
  true;

})();
