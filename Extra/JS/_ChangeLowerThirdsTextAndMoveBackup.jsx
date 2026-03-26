/**
 * _V3__Text_Move_FitToEnd.jsx  (UPDATED: KR/RU font override, SAFE)
 *
 * ORIGINAL BEHAVIOR (kept):
 * - Reads: output/JSON/A18__computed_lowerthirds__.json (relative to this JSX)
 * - V4 only (videoTracks[3])  (your script uses V4)
 * - id 1 => clip 1, etc (sort by id)
 * - Updates MOGRT text param by editing JSON.textEditValue (preserves style)
 * - Moves clip to desired start frame (absolute)
 * - FITS end frame:
 *     - Tries speed first (setSpeed) ONLY for NON-MOGRT items
 *     - For MOGRT/Graphics, skips speed and uses duration-fit (outPoint+end)
 *
 * NEW FEATURE (added, without breaking logic):
 * - Reads language from: inputs/config/config.json (relative to this JSX folder)
 * - If language is Korean => set fontEditValue to NotoSerifKR (safe)
 * - If language is Russian => set fontEditValue to NotoSans (safe)
 * - If not KR/RU => no font changes, runs normally
 *
 * IMPORTANT SAFETY:
 * - We ONLY change "fontEditValue": ["..."] inside the SAME JSON blob that contains "textEditValue"
 * - We DO NOT touch fontSizeEditValue / fontFSBoldValue etc (prevents MOGRT corruption)
 */

(function () {
  var TARGET_TRACK_INDEX = 3; // V4
  var TICKS_PER_SECOND = 254016000000;

  // ✅ Try multiple possible MOGRT text parameter display names (first match wins)
  var TARGET_PARAM_NAMES = ["History", "Title", "Text", "Main Text", "Heading", "TEXT_01_L12", "NOWWAY", "TEXT_01", "TEXT 01", "TEXT 01 ", "Text 01", "TEXT"];

// EXACT PostScript/font names (must match Essential Graphics exactly)
var KR_FONT_EXACT = "NotoSansKR-ExtraBold";
var RU_FONT_EXACT = "NotoSans-ExtraBold"; // change if your RU font exact name is different

  function trim(s) { return (s + "").replace(/^\s+|\s+$/g, ""); }
  function toInt(v, fb) { var n = parseInt(v, 10); return isNaN(n) ? fb : n; }

  function pickField(obj, keys) {
    try {
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        if (!k) continue;
        if (obj && obj[k] !== undefined && obj[k] !== null && String(obj[k]) !== "") return obj[k];
      }
    } catch (e) {}
    return null;
  }

  // ----------------------------
  // LOGGING (Events panel + file)
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
    } catch (e) {}
  }

  function log(msg, level) {
    try { appendFileLog(msg); } catch (e0) {}
    try {
      if (app && typeof app.setSDKEventMessage === "function") {
        app.setSDKEventMessage(String(msg), level || "info");
        return;
      }
    } catch (e1) {}
    try { $.writeln(String(msg)); } catch (e2) {}
  }

  function fail(msg) {
    try {
      if ($.global) {
        $.global.__PIPELINE_LAST_OK = false;
        $.global.__PIPELINE_LAST_MSG = String(msg || "Unknown failure");
      }
    } catch (e) {}
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
    return new File(folder.fsName + "/output/JSON/A18__computed_lowerthirds__.json");
  }

  function getConfigJsonFile() {
    // As you requested: current JSX folder -> inputs/config/config.json
    var scriptFile = new File($.fileName);
    var folder = scriptFile.parent;
    return new File(folder.fsName + "/inputs/config/config.json");
  }

  // ---------------------------------------------------------
  // JSON reading
  // ---------------------------------------------------------
  function parseJsonSafe(raw) {
    raw = String(raw || "");
    raw = raw.replace(/^\uFEFF/, "");
    try {
      if (typeof JSON !== "undefined" && JSON && typeof JSON.parse === "function") {
        return JSON.parse(raw);
      }
    } catch (e1) {}
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
      try { if (f && f.opened) f.close(); } catch (e2) {}
      return null;
    }
  }

  // ---------------------------------------------------------
  // Language -> font override
  // ---------------------------------------------------------
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

  function getFontOverrideBaseOrNull() {
    var cfg = readJsonObjectOrNull(getConfigJsonFile());
    var lang = pickLanguageFromConfig(cfg);
    var n = normLang(lang);

    if (!n) return null;

    if (n === "korean" || n === "ko" || n.indexOf("korean") >= 0) return { language: lang, exact: KR_FONT_EXACT };
    if (n === "russian" || n === "ru" || n.indexOf("russian") >= 0) return { language: lang, exact: RU_FONT_EXACT };

    return null;
  }

  // ---------------------------------------------------------
  // MOGRT param access
  // ---------------------------------------------------------
  function findParam(mgtProps, name) {
    if (!mgtProps) return null;

    try {
      var p = mgtProps.getParamForDisplayName(name);
      if (p) return p;
    } catch (e) {}

    var want = (name || "").toLowerCase();
    try {
      for (var i = 0; i < mgtProps.numItems; i++) {
        var it = mgtProps[i];
        if (!it) continue;
        var dn = (it.displayName || "").toString().toLowerCase();
        if (dn === want) return it;
      }
    } catch (e2) {}

    return null;
  }

  function isMogrtItem(trackItem) {
    try {
      var m = trackItem.getMGTComponent();
      return !!(m && m.properties);
    } catch (e) {
      return false;
    }
  }

  // ---------------------------------------------------------
  // ✅ STYLE-SAFE TEXT UPDATE (regex in-place)
  // ---------------------------------------------------------
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

  // NEW: return the param used (so we can apply font safely on same blob)
  function setMogrtTextPreserveStyleWithParam(trackItem, displayName, newText) {
    var mgt = null;
    try { mgt = trackItem.getMGTComponent(); } catch (e) { return null; }
    if (!mgt || !mgt.properties) return null;

    var param = findParam(mgt.properties, displayName);
    if (!param) return null;

    try {
      var cur = param.getValue();
      if (typeof cur !== "string") return null;

      var t = trim(cur);
      if (!t.length) return null;

      var first = t.charAt(0);
      if (!(first === "{" || first === "[")) return null;

      // Must be the JSON-style blob with textEditValue
      if (!/"textEditValue"\s*:\s*"/.test(t)) return null;

      var escText = escapeJsonString(newText);
      var r1 = replaceJsonStringField(t, "textEditValue", escText);
      if (!r1.ok) return null;

      var out = r1.out;

      var len = String(newText == null ? "" : newText).length;
      out = replaceJsonNumberArrayField(out, "fontTextRunLength", len).out;
      out = replaceJsonNumberArrayField(out, "fontTextRunStart", 0).out;
      out = replaceJsonNumberField(out, "fontTextRunCount", 1).out;

      var ok = param.setValue(out, true);
      return ok ? param : null;

    } catch (e3) {
      return null;
    }
  }

  function setMogrtTextPreserveStyleAnyWithParam(trackItem, displayNames, newText) {
    if (!displayNames || !displayNames.length) return null;
    for (var i = 0; i < displayNames.length; i++) {
      var name = displayNames[i];
      if (!name) continue;
      var p = setMogrtTextPreserveStyleWithParam(trackItem, name, newText);
      if (p) return p;
    }
    return null;
  }

  // ---------------------------------------------------------
  // ✅ SAFE FONT OVERRIDE: ONLY edits fontEditValue (array)
  // ---------------------------------------------------------
  function replaceJsonStringArrayFirstByKey(raw, key, newEscapedValue) {
    var re = new RegExp('("' + key + '"\\s*:\\s*\\[\\s*")((?:\\\\.|[^"\\\\])*)(")', "i");
    if (!re.test(raw)) return { ok: false, out: raw };
    var out = raw.replace(re, '$1' + newEscapedValue + '$3');
    return { ok: true, out: out };
  }

  function setFontEditValueExact(param, exactFontName) {
    if (!param || !exactFontName) return false;
  
    try {
      var cur = param.getValue();
      if (typeof cur !== "string") return false;
  
      var t = trim(cur);
      if (!t.length) return false;
  
      // Must be same JSON blob as text
      if (!/"textEditValue"\s*:\s*"/.test(t)) return false;
  
      // Must expose fontEditValue
      if (!/"fontEditValue"\s*:\s*\[/.test(t)) return false;
  
      var esc = escapeJsonString(exactFontName);
      var r = replaceJsonStringArrayFirstByKey(t, "fontEditValue", esc);
      if (!r.ok) return false;
  
      return !!param.setValue(r.out, true);
    } catch (e) {
      return false;
    }
  }
  
  // ----------------------------
  // Move
  // ----------------------------
  function moveByTicks(trackItem, deltaTicks) {
    if (!deltaTicks) return true;
    var t = new Time();
    t.seconds = deltaTicks / TICKS_PER_SECOND;
    try { trackItem.move(t); return true; } catch (e) { return false; }
  }

  // ----------------------------
  // FIT END: try speed (NON-MOGRT only), else duration-fit
  // ----------------------------
  function setSpeedCompat(trackItem, speedPct) {
    try { return trackItem.setSpeed(speedPct, false, false, false); } catch (e1) {}
    try { return trackItem.setSpeed(speedPct, false, false); } catch (e2) {}
    try { return trackItem.setSpeed(speedPct); } catch (e3) {}
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
      fail("V4 not found (track index 3).");
      return;
    }

    var jsonFile = getScriptJsonFile();
    if (!jsonFile.exists) { fail("JSON not found: " + jsonFile.fsName); return; }

    var fontOv = getFontOverrideBaseOrNull();
    if (fontOv) log("🔤 Font override ON | language='" + fontOv.language + "' | exact='" + fontOv.exact + "'", "info");
    else log("🔤 Font override OFF | running normally", "info");

    var segments = readJsonArray(jsonFile);

    var track = seq.videoTracks[TARGET_TRACK_INDEX];
    var clipCount = track.clips.numItems;
    var n = Math.min(segments.length, clipCount);

    var ticksPerFrame = Number(seq.timebase);
    if (!ticksPerFrame || isNaN(ticksPerFrame)) { fail("Could not read sequence.timebase"); return; }

    var clips = [];
    for (var i = 0; i < clipCount; i++) clips.push(track.clips[i]);

    log("▶ Text+Move+FitEnd starting. Clips=" + clipCount + ", Segments=" + segments.length + ", Processing=" + n, "info");

    // Pass 1: text update + SAFE font override on the same param
    var okText = 0, badText = 0;
    var fontAppliedClips = 0, fontFailedClips = 0;

    for (var a = 0; a < n; a++) {
      var segA = segments[a];
      var clipA = clips[a];

      // A18 lowerthirds: use sentenceText
      var newText = (segA && segA.sentenceText != null) ? String(segA.sentenceText) : "";

      // IMPORTANT: use the "WithParam" version so we know which param was actually modified
      var usedParam = setMogrtTextPreserveStyleAnyWithParam(clipA, TARGET_PARAM_NAMES, newText);
      if (usedParam) okText++; else badText++;

      if (fontOv && usedParam) {
        var fOK = setFontEditValueExact(usedParam, fontOv.exact);
        if (fOK) fontAppliedClips++; else fontFailedClips++;
      }
    }

    // Pass 2: move + fit end (speed only for NON-MOGRT, duration-fit for MOGRT)
    var okMove = 0, badMove = 0;
    var okSpeed = 0, badSpeed = 0;
    var okDurFit = 0, badDurFit = 0;

    for (var b = 0; b < n; b++) {
      var seg = segments[b];
      var clip = clips[b];

      var startFrame = toInt(pickField(seg, [
        "Final LT Timeline Start Frame",
        "final_start_frame",
        "start_frame"
      ]), null);

      var endFrame = toInt(pickField(seg, [
        "Final LT Timeline End Frame",
        "final_end_frame",
        "end_frame"
      ]), null);

      if (endFrame === null) {
        var durFrames = toInt(pickField(seg, ["duration_frames", "Duration Frames"]), null);
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
      var spOK = false;

      // IMPORTANT: For MOGRT/Graphics, DO NOT use setSpeed (can cause rebuild/disappear)
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

    log(
      "✅ DONE | Processed=" + n +
      " | Text OK=" + okText + " Fail=" + badText +
      (fontOv ? (" | FontAppliedClips=" + fontAppliedClips + " FontFailedClips=" + fontFailedClips) : "") +
      " | Move OK=" + okMove + " Fail=" + badMove +
      " | Speed OK=" + okSpeed + " Fail=" + badSpeed +
      " | DurationFit OK=" + okDurFit + " Fail=" + badDurFit,
      "info"
    );

    if (n > 0 && okText === 0) {
      fail("All text updates failed. Likely wrong param names or mogrt value not JSON-style.");
      return;
    }

    if (n > 0 && okSpeed === 0 && okDurFit === 0) {
      fail("All end-fit updates failed. Speed likely unsupported and duration-fit failed.");
      return;
    }

    // If font override ON but none applied, give a clear warning (does not fail the pipeline)
    if (fontOv && n > 0 && fontAppliedClips === 0) {
      log("⚠️ Font override ON but applied=0. This usually means the text param JSON does not expose 'fontEditValue' or the font name doesn't match Premiere's installed font PostScript name.", "warning");
    }
  }

  try { main(); }
  catch (e) { fail("Script crashed: " + e); }

})();
