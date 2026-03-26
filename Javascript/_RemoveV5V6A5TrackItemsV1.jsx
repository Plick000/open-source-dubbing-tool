(function () {
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

  function safeRemoveTrackItems(track, label) {
    var clipsRemoved = 0;
    var transitionsRemoved = 0;

    if (!track) return { clipsRemoved: 0, transitionsRemoved: 0 };

    // Remove clips backwards
    try {
      var clips = track.clips;
      if (clips && clips.numItems && clips.numItems > 0) {
        for (var i = clips.numItems - 1; i >= 0; i--) {
          var ti = clips[i];
          try { ti.remove(false, false); clipsRemoved++; }
          catch (e1) {
            try { ti.remove(0, 0); clipsRemoved++; } catch (e2) {}
          }
        }
      }
    } catch (e) {}

    // Remove transitions if exposed
    try {
      if (track.transitions && track.transitions.numItems && track.transitions.numItems > 0) {
        for (var t = track.transitions.numItems - 1; t >= 0; t--) {
          var tr = track.transitions[t];
          try { tr.remove(false, false); transitionsRemoved++; }
          catch (e3) {
            try { tr.remove(0, 0); transitionsRemoved++; } catch (e4) {}
          }
        }
      }
    } catch (e5) {}

    return { clipsRemoved: clipsRemoved, transitionsRemoved: transitionsRemoved };
  }

  try {
    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = "";

    if (!app.project) return fail("No project open.");
    var seq = app.project.activeSequence;
    if (!seq) return fail("No active sequence.");

    // 0-based:
    // V3=>2, V4=>3, V5=>4, V6=>5, V7=>6, V8=>7, A5=>4
    var vTracks = seq.videoTracks;
    var aTracks = seq.audioTracks;

    var v3 = (vTracks && vTracks.numTracks > 2) ? vTracks[2] : null;
    var v4 = (vTracks && vTracks.numTracks > 3) ? vTracks[3] : null;
    var v5 = (vTracks && vTracks.numTracks > 4) ? vTracks[4] : null;
    var v6 = (vTracks && vTracks.numTracks > 5) ? vTracks[5] : null;
    var v7 = (vTracks && vTracks.numTracks > 6) ? vTracks[6] : null;
    var v8 = (vTracks && vTracks.numTracks > 7) ? vTracks[7] : null;
    var a5 = (aTracks && aTracks.numTracks > 4) ? aTracks[4] : null;

    var r3 = safeRemoveTrackItems(v3, "V3");
    var r4 = safeRemoveTrackItems(v4, "V4");
    var r5 = safeRemoveTrackItems(v5, "V5");
    var r6 = safeRemoveTrackItems(v6, "V6");
    var r7 = safeRemoveTrackItems(v7, "V7");
    var r8 = safeRemoveTrackItems(v8, "V8");
    var rA = safeRemoveTrackItems(a5, "A5");

    return ok("Cleared V3/V4/V5/V6/V7/V8/A5 items.");

  } catch (e) {
    return fail("Crash: " + e);
  }
})();