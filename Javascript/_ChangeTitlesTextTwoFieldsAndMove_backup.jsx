/**
 * _V3__Text_Move_TrimEndFrame.jsx
 *
 * - Reads: output/JSON/A25__final_titles_merged_segments__.json (relative to this JSX)
 * - V3 only (videoTracks[2])
 * - id 1 => clip 1, etc (sort by id)
 * - Updates TWO MOGRT text params (preserves style) by editing JSON.textEditValue in-place via regex:
 *     1) First field  <- JSON.number
 *     2) Second field <- JSON.body
 * - Moves clip to JSON start_frame (absolute)
 * - Trims clip to end at JSON end_frame (absolute)
 *
 * FIXES:
 * - No alert() popups (prevents freezing)
 * - Logs to Events panel + optional log file via $.global.__LOG_PATH
 * - Supports multiple param names (first match wins) for BOTH fields
 * - STYLE-SAFE: does NOT JSON.stringify the mogrt param value; edits textEditValue in-place via regex
 * - Avoids fallback setValue(newText) which resets style
 */

(function () {
  var TARGET_TRACK_INDEX = 2; // V3
  var TICKS_PER_SECOND = 254016000000;

  // ✅ FIELD 1 (number): try multiple possible display names (first match wins)
  // Put your actual "number" layer names here.
  var NUMBER_PARAM_NAMES = [
    "Number",
    "NUMBER",
    "No",
    "NO",
    "No.",
    "NO.",
    "Index",
    "INDEX",
    "Counter",
    "COUNTER",
    "Rank",
    "RANK"
  ];

  // ✅ FIELD 2 (body): try multiple possible display names (first match wins)
  // Put your actual "body" layer names here.
  var BODY_PARAM_NAMES = [
    "History",
    "HISTORY",
    "Title",
    "TITLE",
    "Text",
    "TEXT",
    "Main Text",
    "MAIN TEXT",
    "Heading",
    "HEADING",
    "Body",
    "BODY",
    "Description",
    "DESCRIPTION"
  ];

  function trim(s) { return (s + "").replace(/^\s+|\s+$/g, ""); }
  function toInt(v, fb) { var n = parseInt(v, 10); return isNaN(n) ? fb : n; }

  // ----------------------------
  // LOGGING (Events panel + file)
  // ----------------------------
  function ts() {
    try {
      var d = new Date();
      function pad(n) { return (n < 10 ? "0" : "") + n; }
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
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

  // ✅ Mark pipeline failure so launcher stops (your launcher checks this)
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

  // ---------------------------------------------------------
  // JSON reading for segments file
  // Premiere ExtendScript sometimes lacks JSON; use eval parse.
  // ---------------------------------------------------------
  function parseJsonSafe(raw) {
    raw = String(raw || "");
    raw = raw.replace(/^\uFEFF/, ""); // remove BOM if any
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

  function findParam(mgtProps, name) {
    if (!mgtProps) return null;

    // direct
    try {
      var p = mgtProps.getParamForDisplayName(name);
      if (p) return p;
    } catch (e) { }

    // case-insensitive
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

  // ---------------------------------------------------------
  // ✅ STYLE-SAFE TEXT UPDATE WITHOUT JSON.stringify
  // We edit the existing JSON-like string in-place:
  // - replace "textEditValue":"..."
  // - keep everything else intact (preserves style)
  // - update run metadata fields if they exist
  // If we cannot find textEditValue, we RETURN FALSE (NO fallback that resets style)
  // ---------------------------------------------------------
  function escapeJsonString(s) {
    s = String(s == null ? "" : s);
    return s
       .replace(/\\/g, "\\\\")
       .replace(/"/g, '\\"')
       .replace(/\u0008/g, "\\b")   // backspace char (NOT regex \b)
       .replace(/\u000C/g, "\\f")   // form feed
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

  function setMogrtTextPreserveStyle(trackItem, displayName, newText) {
    var mgt = null;
    try { mgt = trackItem.getMGTComponent(); } catch (e) { return false; }
    if (!mgt || !mgt.properties) return false;

    var param = findParam(mgt.properties, displayName);
    if (!param) return false;

    try {
      var cur = param.getValue();
      if (typeof cur !== "string") return false;

      var t = trim(cur);
      if (!t.length) return false;

      // Only handle JSON-style values (preserves style).
      var first = t.charAt(0);
      if (!(first === "{" || first === "[")) return false;

      var escText = escapeJsonString(newText);

      var r1 = replaceJsonStringField(t, "textEditValue", escText);
      if (!r1.ok) return false; // no fallback: do not touch if can't preserve style

      var out = r1.out;

      // keep run metadata aligned if these fields exist
      var len = String(newText == null ? "" : newText).length;

      var rLen = replaceJsonNumberArrayField(out, "fontTextRunLength", len);
      out = rLen.out;

      var rStart = replaceJsonNumberArrayField(out, "fontTextRunStart", 0);
      out = rStart.out;

      var rCount = replaceJsonNumberField(out, "fontTextRunCount", 1);
      out = rCount.out;

      return param.setValue(out, true);

    } catch (e3) {
      return false;
    }
  }

  function setMogrtTextPreserveStyleAny(trackItem, displayNames, newText) {
    if (!displayNames || !displayNames.length) return false;

    for (var i = 0; i < displayNames.length; i++) {
      var name = displayNames[i];
      if (!name) continue;

      var ok = setMogrtTextPreserveStyle(trackItem, name, newText);
      if (ok) return true;
    }
    return false;
  }

  // Move by deltaTicks using seconds (TrackItem.move expects Time in seconds)
  function moveByTicks(trackItem, deltaTicks) {
    if (!deltaTicks) return true;
    var t = new Time();
    t.seconds = deltaTicks / TICKS_PER_SECOND;
    try {
      trackItem.move(t);
      return true;
    } catch (e) {
      return false;
    }
  }

  function trimClipToEndTicks(trackItem, desiredStartTicks, desiredEndTicks) {
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

    var segments = readJsonArray(jsonFile);

    var track = seq.videoTracks[TARGET_TRACK_INDEX];
    var clipCount = track.clips.numItems;
    var n = Math.min(segments.length, clipCount);

    var ticksPerFrame = Number(seq.timebase);
    if (!ticksPerFrame || isNaN(ticksPerFrame)) { fail("Could not read sequence.timebase"); return; }

    // Freeze clip references (order)
    var clips = [];
    for (var i = 0; i < clipCount; i++) clips.push(track.clips[i]);

    log("▶ Text(2 fields)+Move+Trim starting. Clips=" + clipCount + ", Segments=" + segments.length + ", Processing=" + n, "info");

    // Pass 1: update TWO text fields (STYLE-SAFE, no fallback that resets style)
    var okNum = 0, badNum = 0, okBody = 0, badBody = 0;

    for (var a = 0; a < n; a++) {
      var segA = segments[a];
      var clipA = clips[a];

      var newNumber = (segA && segA.number != null) ? String(segA.number) : "";
      var newBody   = (segA && segA.body   != null) ? String(segA.body)   : "";

      var ok1 = setMogrtTextPreserveStyleAny(clipA, NUMBER_PARAM_NAMES, newNumber);
      if (ok1) okNum++; else badNum++;

      var ok2 = setMogrtTextPreserveStyleAny(clipA, BODY_PARAM_NAMES, newBody);
      if (ok2) okBody++; else badBody++;
    }

    // Pass 2: move then trim to end_frame (unchanged)
    var okMove = 0, badMove = 0, okTrim = 0, badTrim = 0;

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
      var desiredEndTicks = endFrame * ticksPerFrame;

      var curStartTicks = Number(clip.start.ticks);
      var deltaTicks = desiredStartTicks - curStartTicks;

      var mvOK = moveByTicks(clip, deltaTicks);
      if (mvOK) okMove++; else badMove++;

      var trOK = trimClipToEndTicks(clip, desiredStartTicks, desiredEndTicks);
      if (trOK) okTrim++; else badTrim++;
    }

    log(
      "✅ DONE | Processed=" + n +
      " | Number OK=" + okNum + " Fail=" + badNum +
      " | Body OK=" + okBody + " Fail=" + badBody +
      " | Move OK=" + okMove + " Fail=" + badMove +
      " | Trim OK=" + okTrim + " Fail=" + badTrim,
      "info"
    );

    // If BOTH text fields failed for all clips, treat as pipeline failure
    if (n > 0 && okNum === 0 && okBody === 0) {
      fail("All text updates failed (both fields). Likely wrong param names or mogrt value not JSON-style.");
    }
  }

  try { main(); }
  catch (e) { fail("Script crashed: " + e); }

})();
