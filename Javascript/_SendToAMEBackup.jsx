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
    var WAIT_BEFORE_QUIT_SECONDS = 60;
    // ------------------------------------------------------------

    // ------------------------------------------------------------
    // Paths (script folder)
    // ------------------------------------------------------------
    var SCRIPT_FILE = new File($.fileName);
    var BASE_DIR = new Folder(SCRIPT_FILE.path); // e.g. C:/PPro_AutoRun
    var CONFIG_PATH = BASE_DIR.fsName + "/run_config.json";
    var LOG_PATH = BASE_DIR.fsName + "/___ExtendScript_Log___.txt"; // same folder as script

    // ✅ Validation file
    var VALIDATION_FILENAME = "premiere_validation.json";
    var VALIDATION_PATH_DEFAULT = BASE_DIR.fsName + "/" + VALIDATION_FILENAME;

    function log(msg) {
        try { app.setSDKEventMessage("[SEND_AME] " + msg, "info"); } catch (e0) {}
        try {
            var f = new File(LOG_PATH);
            f.encoding = "UTF-8";
            if (!f.exists) { f.open("w"); f.writeln(""); f.close(); }
            f.open("a");
            f.writeln(new Date().toUTCString() + "  " + msg);
            f.close();
        } catch (e) {}
    }

    function readJsonFile(pathFsName) {
        var f = new File(pathFsName);
        if (!f.exists) throw new Error("run_config.json not found: " + pathFsName);
        f.encoding = "UTF-8";
        if (!f.open("r")) throw new Error("Cannot open run_config.json: " + pathFsName);
        var txt = f.read();
        f.close();

        var obj;
        try { obj = JSON.parse(txt); }
        catch (e1) { obj = eval("(" + txt + ")"); }

        if (!obj) throw new Error("Failed to parse run_config.json");
        return obj;
    }

    function ensureParentFolderExists(filePathStr) {
        var outFile = new File(filePathStr);
        var parent = outFile.parent;
        if (!parent.exists) {
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
            if (app.project) {
                log("Saving project to avoid quit prompts...");
                app.project.save();
                $.sleep(1000);
            }
        } catch (eSave) {
            log("Project save failed/ignored: " + eSave);
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

    function writeTextFile(pathFsName, content) {
        var f = new File(pathFsName);
        f.encoding = "UTF-8";
        if (!f.open("w")) return false;
        f.write(content);
        f.close();
        return true;
    }

    // ✅ Robust stringify for ExtendScript (fixes "[object Object]" issue)
    function escapeJsonString(s) {
        s = String(s);
        // Escape backslash, quote, and control chars
        s = s.replace(/\\/g, "\\\\")
             .replace(/"/g, '\\"')
             .replace(/\r/g, "\\r")
             .replace(/\n/g, "\\n")
             .replace(/\t/g, "\\t")
             .replace(/\f/g, "\\f")
             .replace(/\u0008/g, "\\b");
        return s;
    }

    function safeJSONStringify(value) {
        // Prefer native JSON.stringify if available (often missing/buggy in some ExtendScript hosts)
        try {
            if (typeof JSON !== "undefined" && JSON && typeof JSON.stringify === "function") {
                return JSON.stringify(value, null, 2);
            }
        } catch (e0) {}

        // Fallback serializer (handles plain objects/arrays/strings/numbers/bools/null)
        function _stringify(v) {
            if (v === null || v === undefined) return "null";

            var t = typeof v;

            if (t === "string") return '"' + escapeJsonString(v) + '"';
            if (t === "number") return (isFinite(v) ? String(v) : "null");
            if (t === "boolean") return (v ? "true" : "false");

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

    function writeJsonFile(pathFsName, obj) {
        var txt = "";
        try { txt = safeJSONStringify(obj); }
        catch (e1) { txt = safeString(obj); }
        return writeTextFile(pathFsName, txt);
    }

    function removeIfExists(pathFsName) {
        try {
            var f = new File(pathFsName);
            if (f.exists) f.remove();
        } catch (e) {}
    }

    function writeValidation(pathFsName, payload) {
        if (!pathFsName) return;
        try {
            // make sure parent exists
            ensureParentFolderExists(pathFsName);
        } catch (eMk) {
            // ignore if already exists / best-effort
        }
        var ok = writeJsonFile(pathFsName, payload);
        log("Validation write (" + (ok ? "OK" : "FAIL") + "): " + pathFsName);
    }

    // ✅ Write validation only once (avoid writing after quit / overwriting success)
    var validationWritten = false;
    function writeValidationOnce(pathFsName, payload) {
        if (validationWritten) {
            log("Validation already written; skipping overwrite.");
            return;
        }
        writeValidation(pathFsName, payload);
        validationWritten = true;
    }

    // ------------------------------------------------------------
    // MAIN
    // ------------------------------------------------------------
    var cfg = null;
    var outPath = "";
    var jobID = null;
    var okRunning = null;
    var validationPath = VALIDATION_PATH_DEFAULT;

    try {
        log("=== SEND TO AME START ===");
        log("BaseDir: " + BASE_DIR.fsName);
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
        var presetFs = presetFile.fsName;

        var workArea = app.encoder.ENCODE_ENTIRE;
        var removeUponCompletion = 0;
        var startQueueImmediately = false;

        log("Queueing encodeSequence...");
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

        // Start AME batch
        log("Starting AME batch...");
        app.encoder.startBatch();

        // ✅ Write SUCCESS validation immediately (do NOT wait for quit/close)
        writeValidationOnce(validationPath, {
            status: true,
            stage: "queued_to_ame",
            ts: nowIso(),
            output_mp4: outPath,
            job_id: jobID,
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
                ame_running: okRunning,
                preset_epr: PRESET_EPR
            });
        } catch (e2) {}
    }
})();
