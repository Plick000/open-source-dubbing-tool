/* Premiere Pro 2025 — ExtendScript
   ✅ Opens sequence named "Ready For Automation" (searched globally in project)
   ✅ Then updates its timebase to 23.976 (24000/1001)
   NOTE: Sequences are not “inside bins” in a way we can reliably traverse from rootItem;
         the correct “root-level” search is app.project.sequences.
*/

(function () {
  // ==============================
  // CONFIG (EDIT THIS ONLY)
  // ==============================
  var TARGET_SEQUENCE_NAME = "Ready For Automation";

  // 23.976 fps = 24000/1001 => seconds per frame = 1001/24000
  var FPS_NUM = 24000;
  var FPS_DEN = 1001;

  // Wait a bit after opening so activeSequence updates reliably
  var POLL_TRIES = 20;
  var POLL_SLEEP_MS = 200;
  // ==============================

  function log(msg) { $.writeln(msg); }
  function alertErr(msg) { alert("❌ " + msg); $.writeln("❌ " + msg); }

  function findSeqByExactName(name) {
    try {
      var n = app.project.sequences.numSequences;
      for (var i = 0; i < n; i++) {
        var s = app.project.sequences[i];
        if (s && String(s.name) === String(name)) return s;
      }
    } catch (e) {}
    return null;
  }

  function openSequenceFixed(seq) {
    if (!seq) return false;

    // Method 1: Official API (best when available)
    try {
      if (app.project && typeof app.project.openSequence === "function") {
        if (seq.sequenceID !== undefined && seq.sequenceID !== null) {
          app.project.openSequence(seq.sequenceID);
          log("✅ Opened sequence via app.project.openSequence(): " + seq.name);
          return true;
        }
      }
    } catch (e1) {}

    // Method 2: QE DOM fallback
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

    return false;
  }

  function getActiveSeqAfterOpen(targetName) {
    for (var t = 0; t < POLL_TRIES; t++) {
      try { $.sleep(POLL_SLEEP_MS); } catch (e) {}
      try {
        var a = app.project.activeSequence;
        if (a && String(a.name) === String(targetName)) return a;
      } catch (e2) {}
    }
    return null;
  }

  // ---- Main ----
  try {
    if (!app.project) { alertErr("No project is open. Please open a project first."); return; }

    var targetSeq = findSeqByExactName(TARGET_SEQUENCE_NAME);
    if (!targetSeq) {
      alertErr('Sequence not found: "' + TARGET_SEQUENCE_NAME + '"\n\nCheck spelling/case, and ensure it exists in the project.');
      return;
    }

    if (!openSequenceFixed(targetSeq)) {
      alertErr('Failed to open sequence: "' + TARGET_SEQUENCE_NAME + '"');
      return;
    }

    // Ensure we operate on the opened sequence (activeSequence can lag)
    var activeSeq = getActiveSeqAfterOpen(TARGET_SEQUENCE_NAME) || app.project.activeSequence;
    if (!activeSeq || String(activeSeq.name) !== String(TARGET_SEQUENCE_NAME)) {
      // Last-resort: still proceed with targetSeq, but warn
      log('⚠️ activeSequence did not switch reliably. Applying settings to found sequence object: ' + targetSeq.name);
      activeSeq = targetSeq;
    }

    // ---- Your original timebase-changing logic (preserved) ----
    var seqSettings = activeSeq.getSettings();

    var newFrameRate = new Time();
    newFrameRate.seconds = FPS_DEN / FPS_NUM; // 1001/24000

    seqSettings.videoFrameRate = newFrameRate;
    activeSeq.setSettings(seqSettings);

    log('✅ Sequence "' + String(activeSeq.name) + '" Timebase Updated: 23.976');

  } catch (e) {
    alertErr("Script crashed:\n" + e);
  }
})();
