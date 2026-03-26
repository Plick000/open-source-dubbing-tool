/* ============================================================
   _AME_Master.jsx  (Single-file Master for AME)
   - Merges:
       1) _AMEListener.jsx
       2) _MediaEncoderPolling.jsx
   - Removes dependency on _RunBoth.jsx wrapper (which caused random breaks)
   - Does NOT change the underlying logic of either script
   ============================================================ */

/* ================================
   PART 1: AME Listener
   (Original logic preserved)
   ================================ */

(function () {

  // ----------------------------
  // Helpers
  // ----------------------------
  function nowIso() {
    var d = new Date();
    function p(n){ return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth()+1) + "-" + p(d.getDate()) + "T" +
           p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  // ExtendScript-safe trim (no String.prototype.trim dependency)
  function trimSafe(s) {
    s = (s === null || s === undefined) ? "" : String(s);
    // remove leading/trailing whitespace including tabs/newlines
    return s.replace(/^\s+/, "").replace(/\s+$/, "");
  }

  function readText(f) {
    if (!f.exists) return "";
    f.encoding = "UTF-8";
    if (!f.open("r")) return "";
    var s = f.read();
    f.close();
    return s;
  }

  function writeText(f, s) {
    try {
      f.encoding = "UTF-8";
      if (!f.open("w")) return false;
      f.write(String(s));
      f.close();
      return true;
    } catch (e) {
      try { f.close(); } catch (_) {}
      return false;
    }
  }

  // Minimal stringify (enough for our result payload)
  function esc(s) {
    return String(s)
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\t/g, "\\t");
  }
  function stringify(v) {
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t === "string") return '"' + esc(v) + '"';
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    if (v instanceof Array) {
      var a = [];
      for (var i=0;i<v.length;i++) a.push(stringify(v[i]));
      return "[" + a.join(",") + "]";
    }
    if (t === "object") {
      var parts = [];
      for (var k in v) if (v.hasOwnProperty(k)) parts.push(stringify(k) + ":" + stringify(v[k]));
      return "{" + parts.join(",") + "}";
    }
    return '"' + esc(String(v)) + '"';
  }

  // Safe parse without JSON global (trusted local file written by your python)
  function parseJson(s) {
    s = trimSafe(s);
    if (!s) return null;

    // Prefer JSON.parse if available
    try {
      if (typeof JSON !== "undefined" && JSON && typeof JSON.parse === "function") {
        return JSON.parse(s);
      }
    } catch (e1) {}

    // Fallback (trusted local)
    return eval("(" + s + ")");
  }

  function fileSizeSafe(f) {
    try { return f.length; } catch (e) { return -1; }
  }

  function nowMs() {
    return (new Date()).getTime();
  }

  // ----------------------------
  // Folder / files
  // ----------------------------
  var listenerFile = new File($.fileName);
  var baseDir = listenerFile.parent;

  var triggerFile   = new File(baseDir.fsName + "\\_trigger_encoder__.json");
  var resultFile    = new File(baseDir.fsName + "\\_trigger_encoder__result.json");
  var heartbeatFile = new File(baseDir.fsName + "\\_ame_listener_heartbeat.txt");

  // Default script (when trigger JSON has no scriptPath)
  var defaultScript = new File(baseDir.fsName + "\\_MediaEncoderItemAddedValidation.jsx");

  // ----------------------------
  // Guard (avoid double-start)
  // ----------------------------
  if ($.global.__VV_AME_LISTENER_RUNNING__) {
    return;
  }
  $.global.__VV_AME_LISTENER_RUNNING__ = true;

  // ----------------------------
  // Trigger stabilization state
  // ----------------------------
  $.global.__VV_TRIG_LAST_SIZE__ = -1;
  $.global.__VV_TRIG_STABLE_COUNT__ = 0;
  $.global.__VV_TRIG_FIRST_SEEN_MS__ = 0;

  function resetTriggerStab() {
    $.global.__VV_TRIG_LAST_SIZE__ = -1;
    $.global.__VV_TRIG_STABLE_COUNT__ = 0;
    $.global.__VV_TRIG_FIRST_SEEN_MS__ = 0;
  }

  function isTriggerStable() {
    // Wait until trigger file size is stable for 2 consecutive ticks
    var sz = fileSizeSafe(triggerFile);

    if ($.global.__VV_TRIG_FIRST_SEEN_MS__ === 0) {
      $.global.__VV_TRIG_FIRST_SEEN_MS__ = nowMs();
    }

    if (sz <= 0) {
      // empty or unreadable => not stable yet
      $.global.__VV_TRIG_STABLE_COUNT__ = 0;
      $.global.__VV_TRIG_LAST_SIZE__ = sz;
      return false;
    }

    if (sz === $.global.__VV_TRIG_LAST_SIZE__) {
      $.global.__VV_TRIG_STABLE_COUNT__ += 1;
    } else {
      $.global.__VV_TRIG_STABLE_COUNT__ = 0;
      $.global.__VV_TRIG_LAST_SIZE__ = sz;
    }

    return ($.global.__VV_TRIG_STABLE_COUNT__ >= 2);
  }

  // ----------------------------
  // NON-BLOCKING continuous tick
  // ----------------------------
  $.global.__VV_AME_LISTENER_TICK__ = function () {
    try {
      // Heartbeat always updates (proves listener is alive)
      writeText(
        heartbeatFile,
        "AME listener alive: " + nowIso() + "\nWatching: " + triggerFile.fsName + "\n"
      );

      if (!triggerFile.exists) {
        resetTriggerStab();
        app.scheduleTask('$.global.__VV_AME_LISTENER_TICK__()', 700, false);
        return;
      }

      // Trigger exists: ensure stable before reading/parsing
      var firstSeen = $.global.__VV_TRIG_FIRST_SEEN_MS__;
      if (!isTriggerStable()) {
        // If trigger is stuck half-written for too long, fail it gracefully
        if (firstSeen > 0 && (nowMs() - firstSeen) > 15000) {
          var failObj = {
            ok: false,
            scriptPath: "",
            startedAt: nowIso(),
            endedAt: nowIso(),
            error: "Trigger file not stable after 15s (likely empty/partial write)"
          };
          writeText(resultFile, stringify(failObj));
          try { triggerFile.remove(); } catch (eDel) {}
          resetTriggerStab();
        }

        app.scheduleTask('$.global.__VV_AME_LISTENER_TICK__()', 200, false);
        return;
      }

      // Now stable: read + parse + run
      var raw = readText(triggerFile);
      var started = nowIso();
      var ended = "";
      var ok = false;
      var errMsg = "";
      var scriptPath = "";

      // Immediate ACK (helps your python not time out on long evalFile)
      var ackObj = {
        ok: null,
        status: "running",
        scriptPath: "",
        startedAt: started,
        endedAt: "",
        error: ""
      };
      writeText(resultFile, stringify(ackObj));

      try {
        var cmd = parseJson(raw || "{}") || {};
        // IMPORTANT: avoid .trim() here as well
        scriptPath = (cmd && cmd.scriptPath !== undefined && cmd.scriptPath !== null)
          ? trimSafe(cmd.scriptPath)
          : "";

        var target = null;

        if (scriptPath) {
          target = new File(scriptPath);
        } else {
          target = defaultScript;
          scriptPath = defaultScript.fsName;
        }

        if (!target.exists) {
          throw new Error("Script not found: " + scriptPath);
        }

        // Execute target script (synchronous)
        $.evalFile(target);

        ok = true;
      } catch (eRun) {
        ok = false;
        errMsg = (eRun && eRun.message) ? String(eRun.message) : String(eRun);
      }

      ended = nowIso();

      var resultObj = {
        ok: ok,
        scriptPath: scriptPath,
        startedAt: started,
        endedAt: ended,
        error: errMsg
      };
      writeText(resultFile, stringify(resultObj));

      // Consume trigger AFTER run (or failure)
      try { triggerFile.remove(); } catch (e2) {}

      resetTriggerStab();

      // After trigger processing, tick sooner
      app.scheduleTask('$.global.__VV_AME_LISTENER_TICK__()', 300, false);
      return;

    } catch (eTop) {
      // Never crash the loop
      try {
        writeText(
          heartbeatFile,
          "AME listener ERROR: " + String(eTop) + "\nAt: " + nowIso() + "\nFile: " + $.fileName + "\n"
        );
      } catch (_) {}
    }

    app.scheduleTask('$.global.__VV_AME_LISTENER_TICK__()', 700, false);
  };

  // Start immediately
  app.scheduleTask('$.global.__VV_AME_LISTENER_TICK__()', 0, false);

})();


/* ================================
   PART 2: MediaEncoder Polling
   (Original logic preserved)
   ================================ */

(function () {

  var AME_WD = AME_WD || {};
  $.global.AME_WD = AME_WD; // IMPORTANT: make it resolvable from scheduleTask strings

  // ---------------- CONFIG ----------------
  AME_WD.TIMEOUT_MS   = 45 * 60 * 1000;  // 45 minutes per item
  AME_WD.COOLDOWN_MS  = 10 * 1000;       // pause before restarting batch
  AME_WD.TICK_MS      = 250;             // tick interval
  AME_WD.HB_MS        = 2000;            // log heartbeat interval
  AME_WD.HB_FILE_MS   = 1000;            // heartbeat file write interval

  AME_WD.BASE_DIR = "C:\\temp\\AME_Watchdog\\";
  AME_WD.LOG_PATH = AME_WD.BASE_DIR + "watchdog_per_item.log";
  AME_WD.HEARTBEAT_PATH = AME_WD.BASE_DIR + "polling_heartbeat.txt";

  // AME encoding log (optional boundary fallback)
  AME_WD.AME_LOG = "C:\\Users\\IT\\Documents\\Adobe\\Adobe Media Encoder\\25.0\\AMEEncodingLog.txt";

  // ---------------- STATE ----------------
  AME_WD.encoderHost = null;

  AME_WD.itemStartMs = 0;          // timer for CURRENT ITEM
  AME_WD.cooldownUntilMs = 0;

  AME_WD.lastHbMs = 0;             // for log heartbeat
  AME_WD.lastHbFileMs = 0;         // for heartbeat file

  // Progress-based marker
  AME_WD.hasBatchProgress = false;
  AME_WD.lastProgress = null;      // number 0..1

  // Log-based marker
  AME_WD.logFile = null;
  AME_WD.lastLogMtime = 0;

  // Guard to prevent multiple instances
  AME_WD.__RUNNING__ = AME_WD.__RUNNING__ || false;

  // ---------------- HELPERS ----------------
  AME_WD.nowMs = function () { return (new Date()).getTime(); };

  AME_WD.isoNow = function () {
    var d = new Date();
    function p(n){ return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth()+1) + "-" + p(d.getDate()) + " " +
           p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  };

  AME_WD.ensureDir = function () {
    try {
      var dir = new Folder(AME_WD.BASE_DIR);
      if (!dir.exists) dir.create();
    } catch (e) {}
  };

  AME_WD.appendFile = function (path, line) {
    try {
      AME_WD.ensureDir();
      var f = new File(path);
      f.encoding = "UTF-8";
      if (f.open("a")) { f.writeln(String(line)); f.close(); }
    } catch (e) {}
  };

  AME_WD.writeFile = function (path, text) {
    try {
      AME_WD.ensureDir();
      var f = new File(path);
      f.encoding = "UTF-8";
      if (f.open("w")) { f.write(String(text)); f.close(); }
    } catch (e) {}
  };

  AME_WD.log = function (msg) {
    var line = "[" + AME_WD.isoNow() + "] " + msg;
    try { $.writeln(line); } catch (e0) {}
    AME_WD.appendFile(AME_WD.LOG_PATH, line);
  };

  AME_WD.heartbeat = function (status, extra) {
    // file heartbeat for external supervisor
    var t = AME_WD.nowMs();
    if (t - AME_WD.lastHbFileMs >= AME_WD.HB_FILE_MS) {
      AME_WD.lastHbFileMs = t;
      var s = "POLLING ALIVE: " + AME_WD.isoNow() + "\n" +
              "status=" + status + "\n" +
              "itemStartMs=" + AME_WD.itemStartMs + "\n" +
              "cooldownUntilMs=" + AME_WD.cooldownUntilMs + "\n" +
              "progress=" + AME_WD.lastProgress + "\n" +
              (extra ? ("extra=" + extra + "\n") : "");
      AME_WD.writeFile(AME_WD.HEARTBEAT_PATH, s);
    }
  };

  AME_WD.getStatus = function () {
    try {
      if (AME_WD.encoderHost && AME_WD.encoderHost.getBatchEncoderStatus)
        return String(AME_WD.encoderHost.getBatchEncoderStatus());
    } catch (e1) {}
    try {
      if (AME_WD.encoderHost && AME_WD.encoderHost.getBatchStatus)
        return String(AME_WD.encoderHost.getBatchStatus());
    } catch (e2) {}
    return "unknown";
  };

  AME_WD.getBatchProgressSafe = function () {
    try {
      if (AME_WD.encoderHost && AME_WD.encoderHost.getBatchProgress) {
        var p = Number(AME_WD.encoderHost.getBatchProgress());
        if (!isNaN(p)) return p;
      }
    } catch (e) {}
    return null;
  };

  AME_WD.stopBatch = function () {
    try { if (AME_WD.encoderHost && AME_WD.encoderHost.stopBatch)  { AME_WD.encoderHost.stopBatch();  return "stopBatch"; } } catch (e1) {}
    try { if (AME_WD.encoderHost && AME_WD.encoderHost.pauseBatch) { AME_WD.encoderHost.pauseBatch(); return "pauseBatch"; } } catch (e2) {}
    return null;
  };

  AME_WD.runBatch = function () {
    try { if (AME_WD.encoderHost && AME_WD.encoderHost.runBatch)   { AME_WD.encoderHost.runBatch();   return "runBatch"; } } catch (e1) {}
    try { if (AME_WD.encoderHost && AME_WD.encoderHost.startBatch) { AME_WD.encoderHost.startBatch(); return "startBatch"; } } catch (e2) {}
    return null;
  };

  AME_WD.resetItemTimer = function (why) {
    AME_WD.itemStartMs = AME_WD.nowMs();
    AME_WD.log("ITEM TIMER RESET | " + why);
  };

  AME_WD.initLogFile = function () {
    try {
      AME_WD.logFile = new File(AME_WD.AME_LOG);
      if (AME_WD.logFile.exists) {
        AME_WD.lastLogMtime = AME_WD.logFile.modified.getTime();
        AME_WD.log("Log boundary enabled: " + AME_WD.AME_LOG);
      } else {
        AME_WD.log("WARN: AME log not found (boundary fallback disabled): " + AME_WD.AME_LOG);
        AME_WD.logFile = null;
      }
    } catch (e) {
      AME_WD.log("Log boundary init failed: " + e);
      AME_WD.logFile = null;
    }
  };

  AME_WD.pollLogBoundary = function () {
    if (!AME_WD.logFile) return false;
    try {
      if (!AME_WD.logFile.exists) return false;
      var mt = AME_WD.logFile.modified.getTime();
      if (mt > AME_WD.lastLogMtime) {
        AME_WD.lastLogMtime = mt;
        return true;
      }
    } catch (e) {}
    return false;
  };

  // ---------------- INIT ----------------
  AME_WD.ensureDir();
  AME_WD.log("==== WATCHDOG PER-ITEM START | TIMEOUT_MS=" + AME_WD.TIMEOUT_MS + " | COOLDOWN_MS=" + AME_WD.COOLDOWN_MS + " ====");

  try {
    if (app && app.getEncoderHost) AME_WD.encoderHost = app.getEncoderHost();
  } catch (e0) {}

  if (!AME_WD.encoderHost) {
    AME_WD.log("WARN: encoderHost unavailable at start; will retry in ticks.");
  }

  var p0 = AME_WD.getBatchProgressSafe();
  AME_WD.hasBatchProgress = (p0 !== null);
  AME_WD.lastProgress = p0;
  AME_WD.log("Capabilities | hasBatchProgress=" + AME_WD.hasBatchProgress + " | initialProgress=" + p0);

  AME_WD.initLogFile();
  AME_WD.itemStartMs = 0;

  // ---------------- TICK (NON-BLOCKING) ----------------
  AME_WD.__TICK__ = function () {
    var nextMs = AME_WD.TICK_MS;

    try {
      // reacquire encoder host if needed
      if (!AME_WD.encoderHost) {
        try {
          if (app && app.getEncoderHost) AME_WD.encoderHost = app.getEncoderHost();
        } catch (eH) {}
      }

      var t = AME_WD.nowMs();
      var st = AME_WD.getStatus();

      AME_WD.heartbeat(st, "");

      // cooldown handling
      if (AME_WD.cooldownUntilMs > 0) {
        if (t >= AME_WD.cooldownUntilMs) {
          AME_WD.cooldownUntilMs = 0;
          var startFn = AME_WD.runBatch();
          AME_WD.log("RESTART QUEUE | method=" + String(startFn));
          AME_WD.resetItemTimer("after restart");
        }
        // keep ticking during cooldown
        return;
      }

      // if not running, clear item timer
      if (st !== "running") {
        AME_WD.itemStartMs = 0;
        AME_WD.lastProgress = AME_WD.getBatchProgressSafe();
      } else {
        // running: ensure timer started
        if (AME_WD.itemStartMs === 0) {
          AME_WD.resetItemTimer("first running detected");
          AME_WD.lastProgress = AME_WD.getBatchProgressSafe();
        }

        // ---------- boundary detection ----------
        var boundary = false;

        // A) progress reset/decrease
        if (AME_WD.hasBatchProgress) {
          var p = AME_WD.getBatchProgressSafe();
          if (p !== null && AME_WD.lastProgress !== null) {
            if (p + 0.05 < AME_WD.lastProgress) boundary = true;
          }
          AME_WD.lastProgress = p;
        }

        // B) log-file changed
        if (!boundary) {
          if (AME_WD.pollLogBoundary()) {
            if (!AME_WD.hasBatchProgress) {
              boundary = true;
            } else {
              var pp = AME_WD.lastProgress;
              if (pp !== null && pp >= 0.95) boundary = true;
            }
          }
        }

        if (boundary) {
          AME_WD.resetItemTimer("boundary detected (next item)");
        }

        // ---------- timeout ----------
        var elapsed = t - AME_WD.itemStartMs;
        if (elapsed >= AME_WD.TIMEOUT_MS) {
          AME_WD.log("TIMEOUT HIT | itemElapsedMs=" + elapsed + " | stopping batch...");
          var stopFn = AME_WD.stopBatch();
          AME_WD.log("STOP QUEUE | method=" + String(stopFn));
          AME_WD.cooldownUntilMs = t + AME_WD.COOLDOWN_MS;
          AME_WD.itemStartMs = 0;
        }
      }

      // log heartbeat
      if (t - AME_WD.lastHbMs >= AME_WD.HB_MS) {
        AME_WD.lastHbMs = t;
        var runFor = (AME_WD.itemStartMs > 0) ? (t - AME_WD.itemStartMs) : 0;
        AME_WD.log("HB | status=" + st + " | itemRunMs=" + runFor + " | progress=" + AME_WD.lastProgress);
      }

    } catch (eTop) {
      try { AME_WD.log("WATCHDOG TICK ERROR: " + eTop); } catch (e2) {}
    } finally {
      // ALWAYS reschedule (critical for reliability)
      app.scheduleTask('$.global.AME_WD.__TICK__()', nextMs, false);
    }
  };

  // ---------------- START (GUARD) ----------------
  if (AME_WD.__RUNNING__) {
    AME_WD.log("WATCHDOG already running (guard).");
  } else {
    AME_WD.__RUNNING__ = true;
    app.scheduleTask('$.global.AME_WD.__TICK__()', 0, false);
  }

})();
