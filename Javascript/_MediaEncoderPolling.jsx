/* ============================================================
   _MediaEncoderPolling.jsx  (Adobe Media Encoder ExtendScript)
   PER-ITEM WATCHDOG (NON-BLOCKING, RELIABLE)

   - Continuous tick via app.scheduleTask() (no while(true), no sleep loops)
   - Uses $.global.AME_WD so scheduled strings always resolve
   - Reschedules inside finally so tick never dies from exceptions
   - Writes heartbeat file so external supervisor can detect stalls
   - Keeps your existing per-item timeout + cooldown stop/restart logic
   ============================================================ */

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
