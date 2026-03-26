(function () {

  // =========================
  // CONFIG (EDIT THESE ONLY)
  // =========================
  var FPS_NUM = 24000;
  var FPS_DEN = 1001;

  // Move setup from V5/V6 -> V7/V8 (0-based indexing: V1=0 ... V7=6, V8=7)
  var V7_INDEX = 6;
  var V8_INDEX = 7;
  var A4_INDEX = 3;

  // We will read TransitionsJSONPath from run_config.json
  // IMPORTANT: this must match your actual sequence name in Premiere
  var TARGET_SEQUENCE_TO_OPEN = "Transitions Titles - V3-4-5-6 A4";

  var TRANS_ATTACH_TOL_FRAMES = 2;
  var A4_MATCH_TOL_FRAMES = 18;

  var USE_A4_INDEX_SYNC = false;

  var ENABLE_NUDGE_FIX = true;
  var MAX_MOVE_ATTEMPTS = 3;
  // =========================

  function notify(msg, level) {
    try {
      if (app && typeof app.setSDKEventMessage === "function") {
        app.setSDKEventMessage(String(msg), level || "info");
        return;
      }
    } catch (e) {}
    $.writeln(String(msg));
  }

  function fail(msg) {
    $.global.__PIPELINE_LAST_OK = false;
    $.global.__PIPELINE_LAST_MSG = String(msg);
    notify("❌ " + msg, "error");
  }

  function ok(msg) {
    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = String(msg || "");
    notify("✅ " + (msg || "OK"), "info");
  }

  function safeParseJson(txt) {
    try {
      if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(txt);
    } catch (e) {}
    return eval("(" + txt + ")"); // trusted local file
  }

  function readRunConfig() {
    try {
      var here = new File($.fileName).parent.fsName;
      var f = new File(here + "/run_config.json");
      if (!f.exists) return null;
      f.open("r");
      var txt = f.read();
      f.close();
      return safeParseJson(txt);
    } catch (e) {
      return null;
    }
  }

  function loadJSON(absPath) {
    var f = new File(absPath);
    if (!f.exists) return null;
    f.open("r");
    var txt = f.read();
    f.close();
    try { return safeParseJson(txt); } catch (e) { return null; }
  }

  function isFiniteNumber(x) { return typeof x === "number" && isFinite(x); }
  function framesToSeconds(fr) { return (fr * FPS_DEN) / FPS_NUM; }
  function secondsToFrameIndex(sec) { return Math.round(sec * FPS_NUM / FPS_DEN); }
  function snapSecondsToFrame(sec) { var fr = secondsToFrameIndex(sec); return framesToSeconds(fr); }
  function abs(a) { return a < 0 ? -a : a; }

  function snapshotClips(track) {
    var arr = [];
    if (!track || !track.clips) return arr;
    var n = track.clips.numItems;
    for (var i = 0; i < n; i++) { var c = track.clips[i]; if (c) arr.push(c); }
    return arr;
  }

  function snapshotTransitions(track) {
    var arr = [];
    try {
      if (!track || !track.transitions) return arr;
      var n = track.transitions.numItems;
      for (var i = 0; i < n; i++) { var t = track.transitions[i]; if (t) arr.push(t); }
    } catch (e) { return []; }
    return arr;
  }

  function getTargetStartSec(obj) {
    if (!obj) return NaN;
    if (isFiniteNumber(obj.new_sequence_start_frames)) return framesToSeconds(Math.round(obj.new_sequence_start_frames));
    if (isFiniteNumber(obj.new_sequence_start_time_sec)) return snapSecondsToFrame(obj.new_sequence_start_time_sec);
    return NaN;
  }

  function moveItemToSecondsFrameSafe(item, targetStartSec) {
    if (!item || !item.start || !item.move) return false;
    if (!isFiniteNumber(targetStartSec)) return false;

    if (targetStartSec < 0) targetStartSec = 0;
    targetStartSec = snapSecondsToFrame(targetStartSec);

    for (var attempt = 0; attempt < MAX_MOVE_ATTEMPTS; attempt++) {
      var curSec = item.start.seconds;

      var curFr = secondsToFrameIndex(curSec);
      var tarFr = secondsToFrameIndex(targetStartSec);
      if (curFr === tarFr) return true;

      var dt = new Time();
      dt.seconds = (targetStartSec - curSec);

      try { item.move(dt); } catch (e) { return false; }

      var newFr = secondsToFrameIndex(item.start.seconds);
      if (newFr === tarFr) return true;

      if (ENABLE_NUDGE_FIX) {
        var diffFrames = tarFr - newFr;
        if (diffFrames !== 0 && abs(diffFrames) <= 2) {
          var nud = new Time();
          nud.seconds = framesToSeconds(diffFrames);
          try { item.move(nud); } catch (e2) {}
        }
      }

      if (secondsToFrameIndex(item.start.seconds) === tarFr) return true;
    }

    return (secondsToFrameIndex(item.start.seconds) === secondsToFrameIndex(targetStartSec));
  }

  function findBestA4Clip(a4Clips, usedMap, vStartSec, vEndSec) {
    var best = null;
    var bestScore = 9e18;

    var vStartFr = secondsToFrameIndex(vStartSec);
    var vEndFr   = secondsToFrameIndex(vEndSec);

    for (var i = 0; i < a4Clips.length; i++) {
      var a = a4Clips[i];
      if (!a) continue;
      if (usedMap[a.nodeId]) continue;

      var aStartFr = secondsToFrameIndex(a.start.seconds);
      var aEndFr   = secondsToFrameIndex(a.end.seconds);

      var overlaps = (aStartFr < vEndFr && aEndFr > vStartFr);
      var dist = abs(aStartFr - vStartFr);
      var score = overlaps ? (dist * 0.1) : dist;

      if (score < bestScore) { bestScore = score; best = a; }
    }

    if (!best) return null;

    var bStartFr = secondsToFrameIndex(best.start.seconds);
    var bEndFr   = secondsToFrameIndex(best.end.seconds);
    var overlaps2 = (bStartFr < vEndFr && bEndFr > vStartFr);
    if (!overlaps2 && abs(bStartFr - vStartFr) > A4_MATCH_TOL_FRAMES) return null;

    return best;
  }

  function buildTransitionBindings(trackClips, trackTransitions) {
    var binds = [];
    if (!trackTransitions || !trackTransitions.length) return binds;

    var tol = TRANS_ATTACH_TOL_FRAMES;

    for (var i = 0; i < trackTransitions.length; i++) {
      var tr = trackTransitions[i];
      if (!tr || !tr.start || !tr.end) continue;

      var trStartFr = secondsToFrameIndex(tr.start.seconds);
      var trEndFr   = secondsToFrameIndex(tr.end.seconds);

      var best = null;
      var bestDist = 999999;

      for (var c = 0; c < trackClips.length; c++) {
        var clip = trackClips[c];
        if (!clip) continue;

        var csFr = secondsToFrameIndex(clip.start.seconds);
        var ceFr = secondsToFrameIndex(clip.end.seconds);

        var dStart = abs(trEndFr - csFr);
        var dEnd   = abs(trStartFr - ceFr);
        var dEdit  = abs(((trStartFr + trEndFr) / 2) - ceFr);

        var d = Math.min(dStart, dEnd, dEdit);
        if (d < bestDist) {
          bestDist = d;
          best = { clip: clip, dStart: dStart, dEnd: dEnd, dEdit: dEdit };
        }
      }

      if (!best) continue;
      if (bestDist > tol) continue;

      var bind = { tr: tr, refClipId: best.clip.nodeId, refPoint: null, offsetSec: 0 };

      if (best.dStart <= best.dEnd && best.dStart <= best.dEdit) {
        bind.refPoint = "START";
        bind.offsetSec = tr.start.seconds - best.clip.start.seconds;
      } else {
        bind.refPoint = "END";
        bind.offsetSec = tr.start.seconds - best.clip.end.seconds;
      }

      binds.push(bind);
    }

    return binds;
  }

  function moveBoundTransitions(binds, clipById) {
    for (var i = 0; i < binds.length; i++) {
      var b = binds[i];
      var clip = clipById[b.refClipId];
      if (!b || !b.tr || !clip) continue;

      var refSec = (b.refPoint === "START") ? clip.start.seconds : clip.end.seconds;
      var desiredTrStart = refSec + b.offsetSec;

      moveItemToSecondsFrameSafe(b.tr, desiredTrStart);
    }
  }

  function findSequenceByName(name) {
    var n = app.project.sequences.numSequences;
    for (var i = 0; i < n; i++) {
      var s = app.project.sequences[i];
      if (s && String(s.name) === String(name)) return s;
    }
    return null;
  }

  function openSequenceFixed(seq) {
    if (!seq) return false;

    try {
      if (app.project && typeof app.project.openSequence === "function") {
        if (seq.sequenceID !== undefined && seq.sequenceID !== null) {
          app.project.openSequence(seq.sequenceID);
          return true;
        }
      }
    } catch (e1) {}

    try {
      if (app.enableQE) app.enableQE();
      if (typeof qe !== "undefined" && qe.project && qe.project.getSequenceAt) {
        var count = null;
        try { count = qe.project.numSequences; } catch (e2) {}
        if (count === null || count === undefined) {
          try { count = qe.project.getNumSequences(); } catch (e3) {}
        }
        if (count !== null && count !== undefined) {
          for (var i = 0; i < count; i++) {
            var qSeq = qe.project.getSequenceAt(i);
            if (!qSeq) continue;

            var qName = "";
            try { qName = String(qSeq.name); } catch (e4) {
              try { qName = String(qSeq.getName()); } catch (e5) {}
            }

            if (qName === String(seq.name)) {
              try {
                if (typeof qSeq.openInTimeline === "function") qSeq.openInTimeline();
                else if (typeof qSeq.open === "function") qSeq.open();
                else if (typeof qSeq.activate === "function") qSeq.activate();
                else return false;
                return true;
              } catch (e6) {}
            }
          }
        }
      }
    } catch (e7) {}

    return false;
  }

  function ensureActiveSequenceNamed(name) {
    for (var k = 0; k < 20; k++) {
      try { $.sleep(250); } catch (e) {}
      var a = app.project.activeSequence;
      if (a && String(a.name) === String(name)) return true;
    }
    return false;
  }

  // =========================
  // MAIN
  // =========================
  try {
    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = "";

    if (!app.project) return fail("No project open.");

    var targetSeq = findSequenceByName(TARGET_SEQUENCE_TO_OPEN);
    if (!targetSeq) return fail("Target sequence not found: " + TARGET_SEQUENCE_TO_OPEN);

    if (!openSequenceFixed(targetSeq)) return fail("Failed to open target sequence: " + TARGET_SEQUENCE_TO_OPEN);
    if (!ensureActiveSequenceNamed(TARGET_SEQUENCE_TO_OPEN)) {
      // Not fatal, but helps correctness
      notify("⚠ activeSequence name did not confirm (continuing).", "warning");
    }

    var seq = app.project.activeSequence;
    if (!seq) return fail("No active sequence after opening.");

    // Read TransitionsJSONPath
    var cfg = readRunConfig();
    if (!cfg || !cfg.TransitionsJSONPath) return fail("run_config.json missing TransitionsJSONPath.");

    var jsonPath = String(cfg.TransitionsJSONPath);
    var transitionsJson = loadJSON(jsonPath);
    if (!transitionsJson || transitionsJson.length === undefined) {
      return fail("Transitions JSON not found/invalid: " + jsonPath);
    }

    // Build lists for V7/V8 (moved from V5/V6)
    var V7_LIST = [];
    var V8_LIST = [];
    for (var i = 0; i < transitionsJson.length; i++) {
      var t = transitionsJson[i];
      if (!t || !t.track) continue;
      if (t.track === "V7") V7_LIST.push(t);
      else if (t.track === "V8") V8_LIST.push(t);
    }

    var v7 = seq.videoTracks[V7_INDEX];
    var v8 = seq.videoTracks[V8_INDEX];
    var a4 = seq.audioTracks[A4_INDEX];

    if (!v7) return fail("Video track V7 missing at index " + V7_INDEX);

    var v7Clips = snapshotClips(v7);
    var v8Clips = v8 ? snapshotClips(v8) : [];
    var a4Clips = a4 ? snapshotClips(a4) : [];

    var v7Trans = snapshotTransitions(v7);
    var v8Trans = v8 ? snapshotTransitions(v8) : [];

    var v7Binds = buildTransitionBindings(v7Clips, v7Trans);
    var v8Binds = buildTransitionBindings(v8Clips, v8Trans);

    // ---- V7 plan ----
    var v7MoveCount = Math.min(v7Clips.length, V7_LIST.length);
    var planV7 = [];
    var usedA4 = {};

    for (var idx = 0; idx < v7MoveCount; idx++) {
      var v7Clip = v7Clips[idx];
      var vt = V7_LIST[idx];
      if (!v7Clip || !vt) continue;

      var targetSec = getTargetStartSec(vt);
      if (!isFiniteNumber(targetSec)) continue;

      var a4Partner = null;
      if (a4Clips.length) {
        if (USE_A4_INDEX_SYNC) {
          if (idx < a4Clips.length) a4Partner = a4Clips[idx];
        } else {
          a4Partner = findBestA4Clip(a4Clips, usedA4, v7Clip.start.seconds, v7Clip.end.seconds);
        }
      }
      if (a4Partner) usedA4[a4Partner.nodeId] = true;

      planV7.push({ vClip: v7Clip, aClip: a4Partner, oldStart: v7Clip.start.seconds, targetStart: targetSec });
    }

    // Execute right-to-left
    planV7.sort(function (a, b) { return b.oldStart - a.oldStart; });
    for (i = 0; i < planV7.length; i++) {
      var p = planV7[i];
      moveItemToSecondsFrameSafe(p.vClip, p.targetStart);
      if (p.aClip) moveItemToSecondsFrameSafe(p.aClip, p.targetStart);
    }

    // Lookup for transition bindings
    var clipById = {};
    for (i = 0; i < v7Clips.length; i++) if (v7Clips[i]) clipById[v7Clips[i].nodeId] = v7Clips[i];
    for (i = 0; i < v8Clips.length; i++) if (v8Clips[i]) clipById[v8Clips[i].nodeId] = v8Clips[i];

    if (v7Binds.length) moveBoundTransitions(v7Binds, clipById);

    // ---- V8 plan ----
    if (v8) {
      var v8MoveCount = Math.min(v8Clips.length, V8_LIST.length);
      var planV8 = [];

      for (idx = 0; idx < v8MoveCount; idx++) {
        var v8Clip = v8Clips[idx];
        var vt8 = V8_LIST[idx];
        if (!v8Clip || !vt8) continue;

        var targetSec8 = getTargetStartSec(vt8);
        if (!isFiniteNumber(targetSec8)) continue;

        planV8.push({ vClip: v8Clip, oldStart: v8Clip.start.seconds, targetStart: targetSec8 });
      }

      planV8.sort(function (a, b) { return b.oldStart - a.oldStart; });
      for (i = 0; i < planV8.length; i++) moveItemToSecondsFrameSafe(planV8[i].vClip, planV8[i].targetStart);

      if (v8Binds.length) moveBoundTransitions(v8Binds, clipById);
    }

    return ok("Transitions adjusted.");

  } catch (e) {
    return fail("Crash: " + e);
  }

})();