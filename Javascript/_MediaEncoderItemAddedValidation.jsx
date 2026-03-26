/**
 * VV_AME_EncoderValidation_Poll120s_WithBatchStatus.jsx
 *
 * No hardcoded SOURCE/PRESET.
 *
 * Poll up to 120 seconds:
 * - If any new item is received into the AME queue -> status = "fine"
 * - If NOT received by timeout:
 *     - check encoder batch status:
 *         - if stopped/paused/invalid -> status = "kill"
 *         - if running/stopping -> status = "wait_and_run_again"
 *
 * Always writes:
 *   C:\PPro_AutoRun\encoder_validation.json
 */

// -------------------------
// Safe stringify (no JSON dependency)
// -------------------------
function safeStringify(obj) {
  function esc(s) {
    return String(s)
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\t/g, "\\t");
  }
  function toJson(v) {
    if (v === null) return "null";
    var t = typeof v;
    if (t === "string") return '"' + esc(v) + '"';
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    if (t === "undefined") return "null";
    if (v && v.constructor === Array) {
      var a = [];
      for (var i = 0; i < v.length; i++) a.push(toJson(v[i]));
      return "[" + a.join(",") + "]";
    }
    var parts = [];
    for (var k in v) {
      if (!v.hasOwnProperty(k)) continue;
      parts.push('"' + esc(k) + '":' + toJson(v[k]));
    }
    return "{" + parts.join(",") + "}";
  }
  return toJson(obj);
}

// -------------------------
// Helpers
// -------------------------
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

function ensureFolder(winPath) {
  var f = new Folder(winPath);
  if (!f.exists) { try { f.create(); } catch (e) {} }
  return f.exists;
}

function writeTextFile(fullPath, text) {
  var outFile = new File(fullPath);
  if (!outFile.open("w")) return false;
  outFile.write(text);
  outFile.close();
  return true;
}

function nowMs() { return (new Date()).getTime(); }

function sleepMs(ms) {
  try { app.wait(ms); return; } catch (e) {}
  var start = nowMs();
  while ((nowMs() - start) < ms) {}
}

function getBatchStatusSafe() {
  // Returns one of: invalid | paused | running | stopped | stopping
  // or "unknown" if not available.
  try {
    var host = app.getEncoderHost();
    if (!host) return "unknown";
    var st = host.getBatchEncoderStatus();
    return st ? String(st) : "unknown";
  } catch (e) {
    return "unknown";
  }
}

// -------------------------
// Main
// -------------------------
(function main() {
  var OUT_DIR  = "C:\\PPro_AutoRun";
  var OUT_PATH = OUT_DIR + "\\encoder_validation.json";

  var WINDOW_MS = 120 * 1000;  // poll window
  var POLL_MS   = 250;         // poll interval

  // This object is written at the end (single overwrite)
  var result = {
    status: "",                 // "fine" | "kill" | "wait_and_run_again"
    ts_start: isoNow(),
    ts_end: "",
    window_seconds: 120,
    poll_interval_ms: POLL_MS,

    received_count: 0,
    failed_count: 0,
    last_failure: "",

    batch_status: "",           // invalid | paused | running | stopped | stopping | unknown
    reason: ""
  };

  if (!ensureFolder(OUT_DIR)) {
    result.status = "kill";
    result.reason = "Cannot create output directory: " + OUT_DIR;
    result.batch_status = getBatchStatusSafe();
    result.ts_end = isoNow();
    $.writeln(safeStringify(result));
    return;
  }

  var frontend = null;
  try { frontend = app.getFrontend(); } catch (e0) {}

  if (!frontend) {
    result.status = "kill";
    result.reason = "AME frontend not available (run inside Adobe Media Encoder)";
    result.batch_status = getBatchStatusSafe();
    result.ts_end = isoNow();
    writeTextFile(OUT_PATH, safeStringify(result));
    $.writeln(safeStringify(result));
    return;
  }

  // Attach receive listener
  try {
    frontend.addEventListener("onItemAddedToBatch", function (ev) {
      result.received_count++;
    }, false);
  } catch (e1) {
    result.status = "kill";
    result.reason = "Failed to attach onItemAddedToBatch listener: " + String(e1);
    result.batch_status = getBatchStatusSafe();
    result.ts_end = isoNow();
    writeTextFile(OUT_PATH, safeStringify(result));
    $.writeln(safeStringify(result));
    return;
  }

  // Optional: capture creation failures (if they occur during the window)
  try {
    frontend.addEventListener("onBatchItemCreationFailed", function (ev) {
      result.failed_count++;
      try { result.last_failure = (ev && ev.error) ? String(ev.error) : "unknown error"; } catch (e2) {}
    }, false);
  } catch (e3) {
    // ignore
  }

  // Poll window: exit early if received
  var start = nowMs();
  while ((nowMs() - start) < WINDOW_MS) {
    if (result.received_count > 0) break;
    sleepMs(POLL_MS);
  }

  // End + decide final status
  result.ts_end = isoNow();
  result.batch_status = getBatchStatusSafe();

  if (result.received_count > 0) {
    // ✅ Item received within polling window
    result.status = "fine";
    result.reason = "";
  } else {
    // ❌ No item received; decide action based on batch status
    // If encoding is running -> don't kill, wait and run again later.
    // If encoding is stopped/paused/invalid -> safe to kill/recover upstream.
    if (result.batch_status === "running" || result.batch_status === "stopping") {
      result.status = "wait_and_run_again";
      if (result.failed_count > 0 && result.last_failure) {
        result.reason = "No item received in 120s; failures observed: " + result.last_failure;
      } else {
        result.reason = "No item received in 120s; batch is active (" + result.batch_status + ")";
      }
    } else {
      // paused / stopped / invalid / unknown -> treat as kill
      result.status = "kill";
      if (result.failed_count > 0 && result.last_failure) {
        result.reason = "No item received in 120s; failures observed: " + result.last_failure;
      } else {
        result.reason = "No item received in 120s; batch not active (" + result.batch_status + ")";
      }
    }
  }

  // Write final validation
  writeTextFile(OUT_PATH, safeStringify(result));
  $.writeln(safeStringify(result));
})();
