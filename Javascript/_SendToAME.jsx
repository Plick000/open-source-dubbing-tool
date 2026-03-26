/* _SendToAME.jsx  (reads OutputMp4Path + PresetEprPath from run_config.json in same folder)
   + queues to AME with preset FROM CONFIG (no hardcode)
   + starts batch
   + waits N seconds
   + saves project (to avoid prompts)
   + quits Premiere

   ✅ UPDATE:
   After sending to AME, writes "premiere_validation.json"
   with { status: true/false, ... } so WSL Python can poll it.
*/

(function () {
    // ------------------------------------------------------------
    // ✅ EASY SETTINGS (EDIT THESE ONLY)
    // ------------------------------------------------------------
    // ✅ PRESET_EPR is now loaded from run_config.json (PresetEprPath)
    var PRESET_EPR = "";
    var WAIT_BEFORE_QUIT_SECONDS = 40;
    // ✅ AME queue validation (onEncoderJobQueued)
    // Wait this long (seconds) after sending to AME for the job to be CONFIRMED queued.
    var QUEUE_CONFIRM_TIMEOUT_SECONDS = 60;
    // How many times to retry encodeSequence if not confirmed queued within timeout.
    var QUEUE_CONFIRM_MAX_RETRIES = 5;
    // Poll interval while waiting for onEncoderJobQueued (ms)
    var QUEUE_CONFIRM_POLL_MS = 250;
    // ------------------------------------------------------------

    // ------------------------------------------------------------
    // Paths (script folder)
    // ------------------------------------------------------------
    var SCRIPT_FILE = new File($.fileName);
    var BASE_DIR = new Folder(SCRIPT_FILE.path); // e.g. C:/PPro_AutoRun
    var CONFIG_PATH = BASE_DIR.fsName + "/run_config.json";
    var LOG_PATH = BASE_DIR.fsName + "/___ExtendScript_Log___.txt"; // same folder

    // Default validation file (but can be overridden via config)
    var VALIDATION_PATH_DEFAULT = BASE_DIR.fsName + "/premiere_validation.json";

    // runtime vars
    var outPath = "";
    var jobID = null;
    var validationPath = VALIDATION_PATH_DEFAULT;
    var okRunning = false;
    var cfg = null;

    // ------------------------------------------------------------
    // Basic log
    // ------------------------------------------------------------
    function log(msg) {
        try {
            var f = new File(LOG_PATH);
            f.open("a");
            f.writeln("[" + (new Date()) + "] " + msg);
            f.close();
        } catch (e) {}
        try { $.writeln(msg); } catch (e2) {}
    }

    function removeIfExists(pathFsName) {
        try {
            var f = new File(pathFsName);
            if (f.exists) f.remove();
        } catch (e) {}
    }

    // ------------------------------------------------------------
    // JSON read (simple, safe)
    // ------------------------------------------------------------
    function readJsonFile(pathFsName) {
        var f = new File(pathFsName);
        if (!f.exists) throw new Error("Config file not found: " + pathFsName);
        f.open("r");
        var txt = f.read();
        f.close();

        // ExtendScript doesn't always have JSON.parse in older modes, but Premiere does.
        try {
            return JSON.parse(txt);
        } catch (e) {
            // fallback: eval (dangerous but local config)
            try { return eval("(" + txt + ")"); } catch (e2) {}
            throw new Error("Failed to parse JSON config: " + e);
        }
    }

    function ensureParentFolderExists(filePath) {
        var fileObj = new File(filePath);
        var parent = fileObj.parent;
        if (parent && !parent.exists) {
            var ok = parent.create();
            if (!ok) throw new Error("Failed to create output folder: " + parent.fsName);
        }
    }

    function waitForEncoderRunning(maxMs) {
        var start = new Date().getTime();
        while (true) {
            try {
                if (app.encoder.isEncoderRunning()) return true;
            } catch (e) {
                $.sleep(1500);
                return true;
            }
            if ((new Date().getTime() - start) > maxMs) return false;
            $.sleep(500);
        }
    }

    function safeSaveProject() {
        try {
            if (app && app.project) {
                log("Saving Premiere project...");
                app.project.save();
            }
        } catch (e) {
            log("Save failed (ignored): " + e);
        }
    }

    function safeQuitPremiere() {
        log("Quitting Premiere now...");
        try {
            app.quit();
            return;
        } catch (eQuit) {
            log("app.quit() failed, trying app.exit(): " + eQuit);
        }
        try {
            app.exit();
        } catch (eExit) {
            log("app.exit() failed: " + eExit);
        }
    }

    // ------------------------------------------------------------
    // ✅ Validation JSON helpers
    // ------------------------------------------------------------
    function nowIso() {
        try { return (new Date()).toISOString(); } catch (e) { return "" + (new Date()); }
    }

    function safeString(v) {
        try { return String(v); } catch (e) { return ""; }
    }

    // ------------------------------------------------------------
    // ✅ AME queue confirmation (onEncoderJobQueued)
    // ------------------------------------------------------------
    var __queuedEventFired = false;
    var __queuedEventJobId = null;
    var __queuedEventTs = null;

    function __onEncoderJobQueued(jobId) {
        __queuedEventFired = true;
        __queuedEventJobId = jobId;
        __queuedEventTs = nowIso();
        log("onEncoderJobQueued fired | jobId=" + jobId);
    }

    function bindOnEncoderJobQueuedOnce() {
        try {
            if (app && app.encoder && app.encoder.bind) {
                // In Premiere ExtendScript, bind() registers callbacks by event name.
                app.encoder.bind("onEncoderJobQueued", __onEncoderJobQueued);
                log("Bound encoder event: onEncoderJobQueued");
                return true;
            }
        } catch (eBind) {
            log("Failed to bind onEncoderJobQueued: " + eBind);
        }
        return false;
    }

    function resetQueuedEventFlags() {
        __queuedEventFired = false;
        __queuedEventJobId = null;
        __queuedEventTs = null;
    }

    function waitForJobQueuedConfirm(expectedJobId, timeoutMs, pollMs) {
        var start = new Date().getTime();
        var exp = safeString(expectedJobId);
        var poll = Math.max(50, parseInt(pollMs, 10) || 250);
        while (true) {
            if (__queuedEventFired) {
                // If Premiere provides the jobId, confirm it matches. If it's blank/undefined, treat as confirmed.
                var got = safeString(__queuedEventJobId);
                if (!got || !exp || got === exp) return true;
                // Another job was queued; keep waiting for the expected one.
            }
            if ((new Date().getTime() - start) > timeoutMs) return false;
            $.sleep(poll);
        }
    }

    function writeTextFile(pathFsName, content) {
        var f = new File(pathFsName);
        f.open("w");
        f.write(content);
        f.close();
    }

    // Minimal JSON stringify (so we don't rely on JSON.stringify always)
    function escapeJsonString(s) {
        s = safeString(s);
        s = s.replace(/\\/g, "\\\\");
        s = s.replace(/"/g, "\\\"");
        s = s.replace(/\r/g, "\\r");
        s = s.replace(/\n/g, "\\n");
        s = s.replace(/\t/g, "\\t");
        return s;
    }

    function jsonStringify(value) {
        function _stringify(v) {
            var t = typeof v;
            if (v === null) return "null";
            if (t === "undefined") return "null";
            if (t === "number" || t === "boolean") return "" + v;
            if (t === "string") return '"' + escapeJsonString(v) + '"';

            // Arrays
            try {
                if (v && v.constructor === Array) {
                    var parts = [];
                    for (var i = 0; i < v.length; i++) parts.push(_stringify(v[i]));
                    return "[" + parts.join(", ") + "]";
                }
            } catch (eArr) {}

            // Objects
            if (t === "object") {
                var keys = [];
                for (var k in v) {
                    try {
                        if (v.hasOwnProperty && !v.hasOwnProperty(k)) continue;
                    } catch (eHas) {}
                    keys.push(k);
                }

                var kv = [];
                for (var j = 0; j < keys.length; j++) {
                    var key = keys[j];
                    kv.push('"' + escapeJsonString(key) + '": ' + _stringify(v[key]));
                }
                return "{\n  " + kv.join(",\n  ") + "\n}";
            }

            return "null";
        }

        return _stringify(value);
    }

    // Write validation ONLY ONCE
    function writeValidationOnce(pathFsName, payloadObj) {
        var f = new File(pathFsName);
        if (f.exists) {
            log("Validation already exists, skipping write: " + pathFsName);
            return;
        }
        var content = jsonStringify(payloadObj);
        writeTextFile(pathFsName, content);
        log("Wrote validation: " + pathFsName);
    }

    // ------------------------------------------------------------
    // MAIN
    // ------------------------------------------------------------
    try {
        log("=== SEND TO AME START ===");
        log("ConfigPath: " + CONFIG_PATH);
        log("WaitBeforeQuitSeconds: " + WAIT_BEFORE_QUIT_SECONDS);

        // Read config
        cfg = readJsonFile(CONFIG_PATH);

        // Output path
        if (!cfg.OutputMp4Path) throw new Error("OutputMp4Path missing in run_config.json");
        outPath = String(cfg.OutputMp4Path);
        log("OutputMp4Path(from config): " + outPath);

        // ✅ Preset path from config (REQUIRED now)
        PRESET_EPR = safeString(cfg.PresetEprPath || cfg.presetEprPath || "");
        if (!PRESET_EPR) throw new Error("PresetEprPath missing in run_config.json");
        log("PresetEprPath(from config): " + PRESET_EPR);

        // Optional preset name (logging only)
        var presetName = safeString(cfg.PresetName || cfg.presetName || "");
        if (presetName) log("PresetName(from config): " + presetName);

        // ✅ Optional override path from config (but default is premiere_validation.json)
        validationPath = safeString(cfg.PremiereValidationPath || cfg.premiereValidationPath || "") || VALIDATION_PATH_DEFAULT;
        log("ValidationPath: " + validationPath);

        // ✅ Remove stale validation file at start (very important)
        removeIfExists(validationPath);

        // Validate preset
        var presetFile = new File(PRESET_EPR);
        if (!presetFile.exists) throw new Error("Preset .epr not found: " + PRESET_EPR);

        // Must have an active sequence
        var seq = (app.project && app.project.activeSequence) ? app.project.activeSequence : null;
        if (!seq) throw new Error("No active sequence open in Premiere.");

        // Ensure output folder exists
        ensureParentFolderExists(outPath);

        // Launch AME
        app.encoder.launchEncoder();
        okRunning = waitForEncoderRunning(20000);
        log("AME running: " + okRunning);

        // Convert paths to fsName for safety
        var outFs = (new File(outPath)).fsName;
        var presetFs = (new File(PRESET_EPR)).fsName;

        // Work area export? default false.
        var workArea = 0; // 0 = entire sequence, 1 = work area
        var removeUponCompletion = false;
        var startQueueImmediately = false;

        // ------------------------------------------------------------
        // ✅ Queue to AME with confirmation (onEncoderJobQueued)
        // If Premiere/AME fails to actually queue (common intermittent issue),
        // retry encodeSequence until confirmed or retries exhausted.
        // ------------------------------------------------------------
        bindOnEncoderJobQueuedOnce();

        var attempt = 0;
        var maxRetries = Math.max(1, parseInt(QUEUE_CONFIRM_MAX_RETRIES, 10) || 1);
        var timeoutMs = Math.max(1000, (parseInt(QUEUE_CONFIRM_TIMEOUT_SECONDS, 10) || 30) * 1000);
        var pollMs = Math.max(50, parseInt(QUEUE_CONFIRM_POLL_MS, 10) || 250);
        var queuedConfirmed = false;

        while (attempt < maxRetries && !queuedConfirmed) {
            attempt++;
            resetQueuedEventFlags();
            log("Queueing encodeSequence... attempt " + attempt + " / " + maxRetries);

            jobID = app.encoder.encodeSequence(
                seq,
                outFs,
                presetFs,
                workArea,
                removeUponCompletion,
                startQueueImmediately
            );

            log("encodeSequence jobID: " + jobID);
            if (!jobID || jobID === 0) {
                throw new Error("encodeSequence failed (jobID=0). Check preset/output path.");
            }

            // Start AME batch (safe even if already running)
            log("Starting AME batch...");
            app.encoder.startBatch();

            // Wait for queue confirmation
            queuedConfirmed = waitForJobQueuedConfirm(jobID, timeoutMs, pollMs);
            log("Queued confirmed: " + queuedConfirmed + " | attempt=" + attempt + " | eventJobId=" + __queuedEventJobId + " | eventTs=" + __queuedEventTs);

            if (!queuedConfirmed) {
                // Best-effort cancel to prevent duplicates if AME actually queued but event didn't fire.
                try {
                    if (app && app.encoder && app.encoder.cancelJob) {
                        app.encoder.cancelJob(jobID);
                        log("Canceled job after missing queue confirm (best-effort): " + jobID);
                    }
                } catch (eCancel) {
                    log("cancelJob failed (ignored): " + eCancel);
                }

                // Small pause before retry
                $.sleep(500);
            }
        }

        if (!queuedConfirmed) {
            throw new Error("AME queue not confirmed (onEncoderJobQueued not received within " + QUEUE_CONFIRM_TIMEOUT_SECONDS + "s). Retries exhausted: " + maxRetries);
        }

        // AME batch already started (and queue confirmed) above.

        // ✅ Write SUCCESS validation immediately (do NOT wait for quit/close)
        writeValidationOnce(validationPath, {
            status: true,
            stage: "queued_to_ame_confirmed",
            ts: nowIso(),
            output_mp4: outPath,
            job_id: jobID,
            queued_confirmed: true,
            queued_attempts: attempt,
            ame_running: okRunning,
            wait_before_quit_seconds: WAIT_BEFORE_QUIT_SECONDS,
            preset_epr: PRESET_EPR
        });

        // Wait then close Premiere
        var waitMs = Math.max(0, parseInt(WAIT_BEFORE_QUIT_SECONDS, 10) || 0) * 1000;
        log("Waiting " + WAIT_BEFORE_QUIT_SECONDS + " seconds before closing Premiere...");
        $.sleep(waitMs);

        safeSaveProject();
        safeQuitPremiere();

        log("=== SEND TO AME END ===");
    } catch (err) {
        log("FAILED: " + err);

        // ✅ Write FAIL validation (only if not already written)
        try {
            writeValidationOnce(validationPath, {
                status: false,
                ts: nowIso(),
                error: safeString(err),
                output_mp4: outPath,
                job_id: jobID,
                queued_confirmed: false,
                queued_attempts: (typeof attempt !== "undefined" ? attempt : null),
                ame_running: okRunning,
                preset_epr: PRESET_EPR
            });
        } catch (e2) {}
    }
})();