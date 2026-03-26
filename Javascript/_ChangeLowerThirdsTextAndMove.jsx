/**
 * _V3__Text_Move_FitToEnd.jsx  (FIXED: speed now applies to MOGRTs too, safely)
 *
 * ORIGINAL BEHAVIOR (kept):
 * - Reads: output/JSON/A18__computed_lowerthirds__.json (relative to this JSX)
 * - V4 only (videoTracks[3])  (your script uses V4)
 * - id 1 => clip 1, etc (sort by id)
 * - Updates MOGRT text param by editing JSON.textEditValue (preserves style)
 * - Moves clip to desired start frame (absolute)
 *
 * FIX:
 * - Previously, speed was NEVER attempted for MOGRT/Graphics items.
 * - Since lower thirds are usually MOGRTs, no clip speed changed at all.
 * - Now we try speed for ALL clips, verify it actually changed duration,
 *   and only then accept it. If speed does not really apply, we fallback
 *   to the old duration-fit method.
 *
 * FONT / DELETE / JSON / MOVE LOGIC remains unchanged.
 */

(function () {
  var TARGET_TRACK_INDEX = 3; // V4
  var TICKS_PER_SECOND = 254016000000;

  // ✅ Try multiple possible MOGRT text parameter display names (first match wins)
  var TARGET_PARAM_NAMES = ["History", "Source Text 01", "Source Text 01 ", "Title", "Text", "Main Text", "Text Main", "Heading", "TEXT_01_L12", "NOWWAY", "TEXT_01", "TEXT 01", "TEXT 01 ", "Text 01", "Text 01 ", "TEXT"];

  // EXACT PostScript/font names (must match Essential Graphics exactly)
  var KR_FONT_EXACT = "NotoSansKR-ExtraBold";
  var RU_FONT_EXACT = "NotoSans-ExtraBold"; // change if your RU font exact name is different

  function trim(s) { return (s + "").replace(/^\s+|\s+$/g, ""); }
  function toInt(v, fb) { var n = parseInt(v, 10); return isNaN(n) ? fb : n; }

  // ---------------------------------------------------------
  // Delete feature
  // If incoming text value is: null/undefined OR empty after trim OR
  // "remove"/"delete"/"empty" (case-insensitive), delete that clip.
  // ---------------------------------------------------------
  function shouldDeleteFromTextValue(v) {
    if (v == null) return true; // catches null + undefined

    if (typeof v === "string") {
      var t = v.replace(/^\s+|\s+$/g, "");
      if (t === "") return true;

      var tl = t.toLowerCase();
      if (tl === "remove" || tl === "delete" || tl === "empty") return true;
      if (tl === "null" || tl === "undefined") return true;
    }

    return false;
  }

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
  // Language -> font override (dynamic via __languages.json)
  // ---------------------------------------------------------
  var LANG_META_PATH = "Z:/Automated Dubbings/admin/configs/metadata/__languages.json";
  var DEFAULT_FONT_EXACT = "Montserrat-ExtraBold";

  function safeLower(s) { try { return (s + "").toLowerCase(); } catch (e) { return ""; } }

  function normLang(s) {
    var t = trim(s);
    if (!t.length) return "";
    return safeLower(t).replace(/[_\s]+/g, "-");
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

  // minimal ISO -> name bridge
  function isoToNameOrEmpty(n) {
    if (!n) return "";
    if (n === "ko") return "korean";
    if (n === "ru") return "russian";
    if (n === "fr") return "french";
    if (n === "es") return "spanish";
    if (n === "pl") return "polish";
    if (n === "cs" || n === "cz") return "czech";
    if (n === "pt" || n === "pt-br" || n === "ptbr" || n === "br") return "portuguese";
    if (n === "hr") return "croatian";
    if (n === "sr") return "serbian";
    if (n === "de") return "german";
    if (n === "it") return "italian";
    if (n === "nl") return "dutch";
    if (n === "ar") return "arabic";
    if (n === "hi") return "hindi";
    if (n === "ur") return "urdu";
    if (n === "tr") return "turkish";
    return "";
  }

  function buildExactFontName(fontFromMeta) {
    var f = trim(fontFromMeta);
    if (!f.length) return DEFAULT_FONT_EXACT;

    if (f.indexOf("-") >= 0) return f;
    return f + "-ExtraBold";
  }

  function readLanguagesMetaOrNull() {
    try {
      var f = new File(LANG_META_PATH);
      if (!f || !f.exists) return null;
      return readJsonArray(f);
    } catch (e) {
      return null;
    }
  }

  function getFontBaseFromMetaOrNull(langRaw) {
    var n = normLang(langRaw);
    if (!n) return null;

    var nIso = n.replace(/[^a-z-]/g, "");
    var mappedName = isoToNameOrEmpty(nIso);

    var rawUpper = trim(langRaw).toUpperCase();

    var data = readLanguagesMetaOrNull();
    if (!data || !(data instanceof Array)) return null;

    var brandKeys = [];
    for (var i = 1; i <= 30; i++) brandKeys.push("brand_" + i);

    for (var di = 0; di < data.length; di++) {
      var root = data[di];
      if (!root) continue;

      for (var bk = 0; bk < brandKeys.length; bk++) {
        var k = brandKeys[bk];
        if (!root.hasOwnProperty(k)) continue;

        var brand = root[k];
        if (!brand || !brand.languages) continue;

        var langs = brand.languages;

        if (langs.hasOwnProperty(n)) {
          var e1 = langs[n];
          return e1 ? e1["font"] : null;
        }
        if (mappedName && langs.hasOwnProperty(mappedName)) {
          var e2 = langs[mappedName];
          return e2 ? e2["font"] : null;
        }

        if (rawUpper && rawUpper.length) {
          for (var lname in langs) {
            if (!langs.hasOwnProperty(lname)) continue;
            var entry = langs[lname];
            if (!entry) continue;

            var code = entry["langcode"];
            if (code && (trim(code).toUpperCase() === rawUpper)) {
              return entry["font"];
            }
          }
        }
      }
    }

    return null;
  }

  function getFontOverrideBaseOrNull() {
    var cfg = readJsonObjectOrNull(getConfigJsonFile());
    var lang = pickLanguageFromConfig(cfg);
    var n = normLang(lang);

    if (!n) return null;

    var metaFont = getFontBaseFromMetaOrNull(lang);
    if (metaFont !== null) {
      return { language: lang, exact: buildExactFontName(metaFont) };
    }

    if (n === "korean" || n === "ko" || n.indexOf("korean") >= 0) return { language: lang, exact: KR_FONT_EXACT };
    if (n === "russian" || n === "ru" || n.indexOf("russian") >= 0) return { language: lang, exact: RU_FONT_EXACT };

    return null;
  }

  // ---------------------------------------------------------
  // MOGRT param access
  // ---------------------------------------------------------
  function findParam(mgtProps, name) {
    if (!mgtProps) return null;

    var wantRaw = trim(name || "");
    if (!wantRaw.length) return null;
    var want = wantRaw.toLowerCase();

    function isLeafParam(x) {
      try { return !!(x && typeof x.getValue === "function" && typeof x.setValue === "function"); }
      catch (e) { return false; }
    }

    function getChildrenCollection(x) {
      try { if (x && x.properties && x.properties.numItems !== undefined) return x.properties; } catch (e1) {}
      try { if (x && x.numItems !== undefined) return x; } catch (e2) {}
      return null;
    }

    function scoreParam(p) {
      if (!p || !isLeafParam(p)) return -1;
      var v = "";
      try { v = String(p.getValue()); } catch (e) { v = ""; }
      if (v && v.charAt(0) === "{" && v.indexOf('"textEditValue"') !== -1) return 1000;
      if (v && v.charAt(0) === "{" && v.indexOf("textEditValue") !== -1) return 900;
      if (v && v.charAt(0) === "{") return 200;
      return 10;
    }

    var best = null;
    var bestScore = -1;

    try {
      var direct = mgtProps.getParamForDisplayName(wantRaw);
      if (direct && isLeafParam(direct)) {
        var s0 = scoreParam(direct);
        if (s0 >= 900) return direct;
        best = direct; bestScore = s0;
      }
    } catch (e0) {}

    function scan(coll) {
      if (!coll) return;

      var n = 0;
      try { n = Number(coll.numItems); } catch (e1) { n = 0; }
      if (!n || isNaN(n)) return;

      for (var i = 0; i < n; i++) {
        var it = null;
        try { it = coll[i]; } catch (e2) { it = null; }
        if (!it) continue;

        var dn = "";
        try { dn = String(it.displayName || ""); } catch (e3) { dn = ""; }

        function normName(s) {
          try {
            return trim(String(s || "").replace(/[\u00A0\u202F]/g, " ").replace(/\s+/g, " ")).toLowerCase();
          } catch (e) { return ""; }
        }

        var dnNorm = normName(dn);
        var wantNorm = normName(wantRaw);

        if (dnNorm === wantNorm && isLeafParam(it)) {
          var sc = scoreParam(it);
          if (sc > bestScore) { best = it; bestScore = sc; }
          if (bestScore >= 1000) return;
        }

        var kids = getChildrenCollection(it);
        if (kids && kids !== coll) {
          scan(kids);
          if (bestScore >= 1000) return;
        }
      }
    }

    scan(mgtProps);
    return best;
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
  // STYLE-SAFE TEXT UPDATE
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

  function setMogrtTextPreserveStyleWithParam(trackItem, displayName, newText) {
    var mgt = null;
    try { mgt = trackItem.getMGTComponent(); } catch (e) { return null; }
    if (!mgt || !mgt.properties) return null;

    var param = findParam(mgt.properties, displayName);
    if (!param) return null;

    try {
      var cur = param.getValue();

      if (typeof cur !== "string") {
        log("TEXT FAIL (non-string) displayName=[" + displayName + "] typeof=" + (typeof cur), "warning");
        return null;
      }

      var t = trim(cur);
      t = t.replace(/^\uFEFF/, "");

      if (!t.length) {
        log("TEXT FAIL (empty) displayName=[" + displayName + "]", "warning");
        return null;
      }

      var first = t.charAt(0);
      if (!(first === "{" || first === "[")) {
        log("TEXT FAIL (not-json) displayName=[" + displayName + "] first=[" + first + "] sample=[" + t.substring(0, 40) + "]", "warning");
        return null;
      }

      if (!/"textEditValue"\s*:\s*"/.test(t)) {
        log("TEXT FAIL (missing textEditValue) displayName=[" + displayName + "] sample=[" + t.substring(0, 80) + "]", "warning");
        return null;
      }

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
  // SAFE FONT OVERRIDE
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

      if (!/"textEditValue"\s*:\s*"/.test(t)) return false;
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
  // Delete clip (SAFE)
  // ----------------------------
  function removeTrackItemSafe(trackItem) {
    if (!trackItem) return false;
    try { trackItem.remove(false, false); return true; } catch (e1) {}
    try { trackItem.remove(false); return true; } catch (e2) {}
    try { trackItem.remove(0, 0); return true; } catch (e3) {}
    try { trackItem.remove(0); return true; } catch (e4) {}
    try { trackItem.disabled = true; return true; } catch (e5) {}
    return false;
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
  // Speed / Fit helpers
  // ----------------------------
  function setSpeedCompat(trackItem, speedPct) {
    try { return trackItem.setSpeed(speedPct, false, false, false); } catch (e1) {}
    try { return trackItem.setSpeed(speedPct, false, false); } catch (e2) {}
    try { return trackItem.setSpeed(speedPct); } catch (e3) {}
    return false;
  }

  function getTrackItemDurationTicks(trackItem) {
    try {
      var s = Number(trackItem.start.ticks);
      var e = Number(trackItem.end.ticks);
      var d = e - s;
      if (isNaN(d)) return NaN;
      return d;
    } catch (e) {
      return NaN;
    }
  }

  function absNum(n) {
    return n < 0 ? -n : n;
  }

  function ticksCloseEnough(a, b, toleranceTicks) {
    if (isNaN(a) || isNaN(b)) return false;
    return absNum(a - b) <= toleranceTicks;
  }

  function pinClipEndSafely(trackItem, desiredStartTicks, desiredEndTicks) {
    try {
      if (desiredEndTicks <= desiredStartTicks) return false;

      var desiredDurTicks = desiredEndTicks - desiredStartTicks;

      try {
        trackItem.end = makeTimeTicks(desiredEndTicks);
      } catch (e1) {}

      try {
        var inTicks = Number(trackItem.inPoint.ticks);
        if (!isNaN(inTicks)) {
          var newOutTicks = inTicks + desiredDurTicks;
          trackItem.outPoint = makeTimeTicks(newOutTicks);
        }
      } catch (e2) {}

      var finalDur = getTrackItemDurationTicks(trackItem);
      return ticksCloseEnough(finalDur, desiredDurTicks, 2);
    } catch (e) {
      return false;
    }
  }

  function trySpeedToFit(trackItem, desiredStartTicks, desiredEndTicks) {
    try {
      if (desiredEndTicks <= desiredStartTicks) return false;

      var desiredDur = desiredEndTicks - desiredStartTicks;
      var curDur = getTrackItemDurationTicks(trackItem);
      if (!curDur || isNaN(curDur) || curDur <= 0) return false;

      var speedPct = 100 * (curDur / desiredDur);
      if (!speedPct || isNaN(speedPct)) return false;

      if (speedPct < 5) speedPct = 5;
      if (speedPct > 2000) speedPct = 2000;

      var speedCallOK = setSpeedCompat(trackItem, speedPct);
      if (!speedCallOK) return false;

      // Verify if speed really changed the timeline duration
      var afterDur = getTrackItemDurationTicks(trackItem);
      if (ticksCloseEnough(afterDur, desiredDur, 2)) {
        return true;
      }

      // Some Premiere builds return "true" but don't land exactly where we need.
      // Nudge the end and outPoint into place.
      if (pinClipEndSafely(trackItem, desiredStartTicks, desiredEndTicks)) {
        return true;
      }

      // Final verification
      afterDur = getTrackItemDurationTicks(trackItem);
      if (ticksCloseEnough(afterDur, desiredDur, 2)) {
        return true;
      }

      return false;
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

    var deleteFlags = [];
    for (var di = 0; di < n; di++) deleteFlags.push(false);
    var deleteCount = 0;

    // Pass 1: text update + SAFE font override on the same param
    var okText = 0, badText = 0;
    var fontAppliedClips = 0, fontFailedClips = 0;

    for (var a = 0; a < n; a++) {
      var segA = segments[a];
      var clipA = clips[a];

      var rawText = (segA && segA.sentenceText !== undefined) ? segA.sentenceText : null;

      if (shouldDeleteFromTextValue(rawText)) {
        deleteFlags[a] = true;
        deleteCount++;
        continue;
      }

      var newText = String(rawText);

      var usedParam = setMogrtTextPreserveStyleAnyWithParam(clipA, TARGET_PARAM_NAMES, newText);
      if (usedParam) okText++; else badText++;

      if (fontOv && usedParam) {
        var fOK = setFontEditValueExact(usedParam, fontOv.exact);
        if (fOK) fontAppliedClips++; else fontFailedClips++;
      }
    }

    // Pass 2: move + fit end
    // FIX: speed now attempted for ALL clips, not just non-MOGRT
    var okMove = 0, badMove = 0;
    var okSpeed = 0, badSpeed = 0;
    var okDurFit = 0, badDurFit = 0;

    for (var b = 0; b < n; b++) {
      var seg = segments[b];
      var clip = clips[b];
      if (deleteFlags[b]) continue;

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
      var spOK = trySpeedToFit(clip, desiredStartTicks, desiredEndTicks);

      if (spOK) {
        okSpeed++;
      } else {
        badSpeed++;
        var dfOK = durationFitToEnd(clip, desiredStartTicks, desiredEndTicks);
        if (dfOK) okDurFit++; else badDurFit++;
      }
    }

    // Pass 3: delete marked clips
    var okDel = 0, badDel = 0;
    for (var d = n - 1; d >= 0; d--) {
      if (!deleteFlags[d]) continue;
      var delOK = removeTrackItemSafe(clips[d]);
      if (delOK) okDel++; else badDel++;
    }

    log(
      "✅ DONE | Processed=" + n +
      " | DeleteRequested=" + deleteCount + " DeleteOK=" + okDel + " DeleteFail=" + badDel +
      " | Text OK=" + okText + " Fail=" + badText +
      (fontOv ? (" | FontAppliedClips=" + fontAppliedClips + " FontFailedClips=" + fontFailedClips) : "") +
      " | Move OK=" + okMove + " Fail=" + badMove +
      " | Speed OK=" + okSpeed + " Fail=" + badSpeed +
      " | DurationFit OK=" + okDurFit + " Fail=" + badDurFit,
      "info"
    );

    if (n > 0 && okText === 0 && deleteCount === 0) {
      fail("All text updates failed. Likely wrong param names or mogrt value not JSON-style.");
      return;
    }

    if (n > 0 && okSpeed === 0 && okDurFit === 0 && deleteCount === 0) {
      fail("All end-fit updates failed. Speed likely unsupported and duration-fit failed.");
      return;
    }

    if (fontOv && n > 0 && fontAppliedClips === 0) {
      log("⚠️ Font override ON but applied=0. This usually means the text param JSON does not expose 'fontEditValue' or the font name doesn't match Premiere's installed font PostScript name.", "warning");
    }
  }

  try { main(); }
  catch (e) { fail("Script crashed: " + e); }

})();