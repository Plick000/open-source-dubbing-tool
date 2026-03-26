/* ============================================================
   AME Listener (runs INSIDE Adobe Media Encoder)
   - Watches for: _trigger_encoder__.json (in same folder)
   - Executes: $.evalFile(scriptPath)
   - Writes:   _trigger_encoder__result.json
   - Continuous NON-BLOCKING loop via app.scheduleTask()
   - Stabilizes trigger file (prevents empty/partial read)
   - Safe JSON parsing without relying on String.trim()
   ============================================================ */

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
