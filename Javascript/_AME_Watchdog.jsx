/*
  AME_Watchdog.jsx (RUN ONCE inside AME per AME launch)

  - Watches trigger:
      C:\media-encoder-validation\_trigger_ame_validation.json

  - Runs validator via evalFile() in the SAME running AME instance.

  - Writes separate log:
      C:\PPro_AutoRun\encoder_validation.log

  - Writes a heartbeat (updates every ~2s even if you deleted logs):
      C:\PPro_AutoRun\encoder_watchdog_heartbeat.json

  - FORCE START:
      If you run this again, it cancels the old scheduled task and reschedules.
*/

(function () {
  var TRIGGER_PATH  = "C:\\media-encoder-validation\\_trigger_ame_validation.json";
  var DEFAULT_VALIDATOR = "C:\\media-encoder-validation\\_MediaEncoderItemAddedValidation.jsx";

  var OUT_DIR   = "C:\\PPro_AutoRun";
  var LOG_PATH  = OUT_DIR + "\\encoder_validation.log";
  var HEARTBEAT = OUT_DIR + "\\encoder_watchdog_heartbeat.json";
  var LAST_RUN  = OUT_DIR + "\\encoder_validation_last_run.json";

  var TICK_MS = 500;
  var HEARTBEAT_EVERY_MS = 2000;

  function ensureFolder(winPath) {
    try {
      var f = new Folder(winPath);
      if (!f.exists) f.create();
      return f.exists;
    } catch (e) { return false; }
  }

  function isoNow() {
    var d = new Date();
    function pad(n, w) { n = String(n); while (n.length < w) n = "0" + n; return n; }
    return d.getUTCFullYear() + "-" +
      pad(d.getUTCMonth() + 1, 2) + "-" +
      pad(d.getUTCDate(), 2) + "T" +
      pad(d.getUTCHours(), 2) + ":" +
      pad(d.getUTCMinutes(), 2) + ":" +
      pad(d.getUTCSeconds(), 2) + "." +
      pad(d.getUTCMilliseconds(), 3) + "Z";
  }

  function log(msg) {
    try {
      ensureFolder(OUT_DIR);
      var f = new File(LOG_PATH);
      f.encoding = "UTF-8";
      if (!f.exists) { if (f.open("w")) f.close(); }
      if (f.open("a")) {
        f.writeln(isoNow() + " | AME_WATCHDOG | " + String(msg));
        f.close();
      }
    } catch (e) {}
  }

  function writeJson(path, obj) {
    try {
      ensureFolder(OUT_DIR);
      var f = new File(path);
      f.encoding = "UTF-8";
      if (f.open("w")) {
        f.write(JSON.stringify(obj));
        f.close();
        return true;
      }
    } catch (e) {}
    return false;
  }

  function readText(path) {
    try {
      var f = new File(path);
      if (!f.exists) return null;
      if (!f.open("r")) return "__OPEN_FAIL__";
      var s = f.read();
      f.close();
      return String(s || "");
    } catch (e) { return null; }
  }

  function deleteFile(path) {
    try {
      var f = new File(path);
      if (f.exists) f.remove();
    } catch (e) {}
  }

  function fileExists(p) {
    try { return (new File(p)).exists; } catch (e) { return false; }
  }

  // ===== FORCE-START: cancel previous scheduled task if any =====
  try {
    if (typeof $.global.__VV_AME_WD_TASK_ID__ === "number") {
      try { app.cancelTask($.global.__VV_AME_WD_TASK_ID__); } catch (eCancel) {}
    }
  } catch (e0) {}

  // heartbeat timing
  var lastHeartbeatAt = 0;
  var lastParseFailAt = 0;

  // ===== Tick =====
  $.global.__VV_AME_WATCHDOG_TICK__ = function () {
    try {
      // heartbeat every 2s so you can prove it is alive even if logs were deleted
      var now = (new Date()).getTime();
      if (now - lastHeartbeatAt >= HEARTBEAT_EVERY_MS) {
        lastHeartbeatAt = now;
        writeJson(HEARTBEAT, {
          alive: true,
          ts: isoNow(),
          trigger: TRIGGER_PATH
        });
      }

      var raw = readText(TRIGGER_PATH);
      if (raw === null) return;

      if (raw === "__OPEN_FAIL__") {
        // trigger exists but locked
        log("TRIGGER exists but cannot open (locked?)");
        return;
      }

      var payload = null;
      try {
        payload = JSON.parse(raw);
      } catch (eParse) {
        if (now - lastParseFailAt > 3000) {
          lastParseFailAt = now;
          log("TRIGGER_PARSE_FAIL err=" + String(eParse));
        }
        return; // do not delete trigger on parse fail
      }

      // consume trigger so it only runs once
      deleteFile(TRIGGER_PATH);

      var run_id = payload.run_id ? String(payload.run_id) : "no_run_id";
      var jsxPath = payload.jsx_path ? String(payload.jsx_path) : DEFAULT_VALIDATOR;

      log("TRIGGER RECEIVED run_id=" + run_id + " jsx_path=" + jsxPath);

      // write last-run marker so validator can include run_id
      writeJson(LAST_RUN, { run_id: run_id, ts: isoNow(), jsx_path: jsxPath });

      if (!fileExists(jsxPath)) {
        log("ERROR validator not found: " + jsxPath);
        return;
      }

      try {
        $.evalFile(jsxPath);
        log("EVALFILE DONE run_id=" + run_id);
      } catch (eEval) {
        log("ERROR evalFile failed run_id=" + run_id + " err=" + String(eEval));
      }

    } catch (eOuter) {
      // never throw
    }
  };

  // ===== Schedule =====
  try {
    var taskId = app.scheduleTask("__VV_AME_WATCHDOG_TICK__()", TICK_MS, true);
    $.global.__VV_AME_WD_TASK_ID__ = taskId;
    log("WATCHDOG STARTED taskId=" + taskId + " tick_ms=" + TICK_MS + " trigger=" + TRIGGER_PATH);
  } catch (eSched) {
    log("ERROR scheduleTask failed: " + String(eSched));
  }

})();
