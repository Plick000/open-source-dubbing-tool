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

  try {
    $.global.__PIPELINE_LAST_OK = true;
    $.global.__PIPELINE_LAST_MSG = "";

    var project = app.project;
    if (!project) return fail("No project open.");

    var pickName = "Automated Timeline";
    var dropName = "Transitions Titles - V3-4-5-6 A4";

    var pickSeq = null, dropSeq = null;

    for (var i = 0; i < project.sequences.numSequences; i++) {
      var s = project.sequences[i];
      if (s.name === pickName) pickSeq = s;
      if (s.name === dropName) dropSeq = s;
    }

    if (!pickSeq || !dropSeq) {
      return fail("Missing sequences. pick=" + pickName + " drop=" + dropName);
    }

    var pickItem = pickSeq.projectItem;
    if (!pickItem) return fail("Pick sequence projectItem not found.");

    var targetTime = "0";

    // Insert video (creates nest) into V1
    dropSeq.videoTracks[0].insertClip(pickItem, targetTime);

    // Insert audio into A1
    if (dropSeq.audioTracks.numTracks > 0) {
      dropSeq.audioTracks[0].overwriteClip(pickItem, targetTime);
    }

    // Sync In/Out
    try {
      dropSeq.setInPoint(pickSeq.getInPoint());
      dropSeq.setOutPoint(pickSeq.getOutPoint());
    } catch (e1) {}

    return ok("Nested '" + pickName + "' into '" + dropName + "'.");

  } catch (e) {
    return fail("Crash: " + e);
  }
})();
