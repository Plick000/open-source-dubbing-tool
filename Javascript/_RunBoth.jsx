/* ============================================================
   _RunBoth.jsx  (Supervisor Wrapper)
   - Starts _AMEListener.jsx and _MediaEncoderPolling.jsx
   - Keeps them alive by monitoring heartbeat files
   - If stale: clears guard flags and re-evalFile()
   - NON-BLOCKING: uses app.scheduleTask() tick loop
   - Reschedules in finally (never dies on exceptions)
   ============================================================ */

(function () {

  // ----------------------------
  // CONFIG
  // ----------------------------
  var TICK_MS  = 500;     // supervisor tick frequency
  var STALE_MS = 12000;   // if heartbeat older than this -> restart that script
  var BOOT_DELAY_MS = 50; // delay between starting listener and polling

  // ----------------------------
  // Helpers (ExtendScript-safe)
  // ----------------------------
  function nowMs() { return (new Date()).getTime(); }

  function isoNow() {
    var d = new Date();
    function p(n){ return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth()+1) + "-" + p(d.getDate()) + "T" +
           p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function safeWrite(path, text) {
    try {
      var f = new File(path);
      f.encoding = "UTF-8";
      if (f.open("w")) { f.write(String(text)); f.close(); }
    } catch (e) {}
  }

  function safeAppend(path, line) {
    try {
      var f = new File(path);
      f.encoding = "UTF-8";
      if (f.open("a")) { f.writeln(String(line)); f.close(); }
    } catch (e) {}
  }

  function fileMtimeMs(path) {
    try {
      var f = new File(path);
      if (!f.exists) return 0;
      return f.modified.getTime();
    } catch (e) {
      return 0;
    }
  }

  function exists(path) {
    try { return (new File(path)).exists; } catch (e) { return false; }
  }

  function evalFileSafe(path) {
    try {
      var f = new File(path);
      if (!f.exists) return false;
      $.evalFile(f);
      return true;
    } catch (e) {
      return false;
    }
  }

  // ----------------------------
  // Resolve paths (same folder as this wrapper)
  // ----------------------------
  var here = new File($.fileName).parent;

  var listenerPath = here.fsName + "\\_AMEListener.jsx";
  var pollingPath  = here.fsName + "\\_MediaEncoderPolling.jsx";

  // Heartbeats written by the two scripts
  var listenerHbPath = here.fsName + "\\_ame_listener_heartbeat.txt";
  var pollingHbPath  = here.fsName + "\\polling_heartbeat.txt";

  // Supervisor debug/log
  var logPath = "C:\\temp\\AME_Watchdog\\watchdog_per_item.log";
  var runBothHbPath = here.fsName + "\\runboth_heartbeat.txt";

  // ----------------------------
  // Guard: prevent multiple supervisors
  // ----------------------------
  if ($.global.__VV_RUNBOTH_SUP_RUNNING__) {
    // already running
    return;
  }
  $.global.__VV_RUNBOTH_SUP_RUNNING__ = true;

  // Expose for scheduleTask string resolution
  $.global.__VV_RUNBOTH_TICK__ = function () {
    var next = TICK_MS;

    try {
      // Supervisor heartbeat
      safeWrite(
        runBothHbPath,
        "RUNBOTH alive: " + isoNow() + "\n" +
        "listener=" + listenerPath + "\n" +
        "polling=" + pollingPath + "\n"
      );

      // Basic existence check
      if (!exists(listenerPath)) {
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | ERROR | Missing listener: " + listenerPath);
        return;
      }
      if (!exists(pollingPath)) {
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | ERROR | Missing polling: " + pollingPath);
        return;
      }

      // --- Ensure both are started at least once ---
      // If flags are not set, start them.
      if (!$.global.__VV_AME_LISTENER_RUNNING__) {
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | start listener (flag missing)");
        evalFileSafe(listenerPath);
      }

      if (!$.global.__VV_AME_WD_RUNNING__ && !$.global.__VV_AME_WD_RUNNING__) {
        // your polling guard might be __VV_AME_WD_RUNNING__ (as in earlier versions)
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | start polling (flag missing)");
        evalFileSafe(pollingPath);
      }

      // --- Heartbeat-based restarts ---
      var now = nowMs();

      var lmt = fileMtimeMs(listenerHbPath);
      var pmt = fileMtimeMs(pollingHbPath);

      var listenerStale = (lmt > 0 && (now - lmt) > STALE_MS);
      var pollingStale  = (pmt > 0 && (now - pmt) > STALE_MS);

      // If heartbeat file missing entirely, treat as stale after some boot time
      if (lmt === 0) listenerStale = true;
      if (pmt === 0) pollingStale = true;

      if (listenerStale) {
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | listener stale -> restart");
        // Clear guard so re-evalFile can start it
        try { $.global.__VV_AME_LISTENER_RUNNING__ = false; } catch (e1) {}
        // Re-run
        evalFileSafe(listenerPath);
        // After restart, tick sooner
        next = 200;
      }

      if (pollingStale) {
        safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | polling stale -> restart");
        // Clear guards used by polling script
        try { $.global.__VV_AME_WD_RUNNING__ = false; } catch (e2) {}
        try { if ($.global.AME_WD) $.global.AME_WD.__RUNNING__ = false; } catch (e3) {}
        // Re-run
        evalFileSafe(pollingPath);
        // After restart, tick sooner
        next = 200;
      }

    } catch (eTop) {
      safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | TICK ERROR | " + eTop);
      // keep running anyway
    } finally {
      // ALWAYS reschedule (critical)
      app.scheduleTask('$.global.__VV_RUNBOTH_TICK__()', next, false);
    }
  };

  // ----------------------------
  // Boot: start both once + start supervisor tick
  // ----------------------------
  try {
    safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | boot");

    // Start listener first
    app.scheduleTask('$.evalFile("' + listenerPath.replace(/\\/g, "/") + '")', 0, false);

    // Then polling
    app.scheduleTask('$.evalFile("' + pollingPath.replace(/\\/g, "/") + '")', BOOT_DELAY_MS, false);

  } catch (eBoot) {
    safeAppend(logPath, "[" + isoNow() + "] RUNBOTH | BOOT ERROR | " + eBoot);
  }

  // Start supervisor tick
  app.scheduleTask('$.global.__VV_RUNBOTH_TICK__()', 0, false);

})();
