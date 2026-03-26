(function () {
    var project = app.project;
    var targetName = "Ready For Automation";
    var targetSequence = null;

    // -------------------------
    // JSON SAFE (same fixed version)
    // -------------------------
    function _hasNativeJSON() {
        try {
            return (typeof JSON !== "undefined") &&
                   JSON &&
                   (typeof JSON.parse === "function") &&
                   (typeof JSON.stringify === "function");
        } catch (e) { return false; }
    }
    function jsonParse(txt) {
        txt = (txt === null || txt === undefined) ? "" : String(txt);
        if (_hasNativeJSON()) return JSON.parse(txt);
        return eval("(" + txt + ")");
    }

    // -------------------------
    // Helpers
    // -------------------------
    function trimStr(s) {
        return String(s || "").replace(/^\s+|\s+$/g, "");
    }

    function sanitizeJsonText(s) {
        if (s === null || s === undefined) return null;
        s = String(s);
        if (s.length && s.charCodeAt(0) === 0xFEFF) s = s.substring(1); // BOM
        s = s.replace(/\u0000/g, "");
        return s;
    }

    function readTextFile(fileObj) {
        if (!fileObj || !fileObj.exists) return null;
        try {
            fileObj.encoding = "UTF-8";
            if (!fileObj.open("r")) return null;
            var s = fileObj.read();
            fileObj.close();
            return sanitizeJsonText(s);
        } catch (e) {
            try { if (fileObj && fileObj.opened) fileObj.close(); } catch (e2) {}
            return null;
        }
    }

    function sanitizeVideoName(raw) {
        var s = trimStr(raw);
        if (!s.length) return null;
        s = s.replace(/^video\s*\d+\s*-\s*/i, "");
        s = trimStr(s);
        if (/^video\s*\d+$/i.test(s)) return null;
        return s.length ? s : null;
    }

    function getVideoNameFromConfigObject(cfg) {
        if (!cfg) return null;
        var keys = ["VideoName", "videoName", "video_name", "videoname", "name", "title"];
        for (var i = 0; i < keys.length; i++) {
            try {
                var v = cfg[keys[i]];
                if (v !== undefined && v !== null) {
                    var cleaned = sanitizeVideoName(String(v));
                    if (cleaned) return cleaned;
                }
            } catch (e) {}
        }
        return null;
    }

    function tryLoadConfigJson_FIXED_LOCATION() {
        var candidates = [];
        try {
            var scriptFile = new File($.fileName);
            var scriptDir = scriptFile.parent;

            candidates.push(new File(scriptDir.fsName + "/inputs/config/config.json"));
            candidates.push(new File(scriptDir.parent.fsName + "/inputs/config/config.json"));
            candidates.push(new File(scriptDir.parent.parent.fsName + "/inputs/config/config.json"));
        } catch (e1) {}

        candidates.push(new File("inputs/config/config.json"));
        candidates.push(new File("C:/PPro_BeforeXML/inputs/config/config.json")); // Updated path

        for (var i = 0; i < candidates.length; i++) {
            var f = candidates[i];
            var txt = readTextFile(f);
            if (!txt) continue;
            try {
                var obj = jsonParse(txt);
                if (obj) return obj;
            } catch (e2) {}
        }
        return null;
    }

    function ensureFolderRecursive(folderPath) {
        var p = String(folderPath).replace(/\\/g, "/");
        var parts = p.split("/");
        if (parts.length < 2) return false;

        var cur = parts[0]; // "Z:"
        if (cur.slice(-1) !== ":") {
            var f0 = new Folder(p);
            if (!f0.exists) return f0.create();
            return true;
        }

        cur = cur + "/"; // "Z:/"
        for (var i = 1; i < parts.length; i++) {
            if (!parts[i].length) continue;
            cur += parts[i];
            var fld = new Folder(cur);
            if (!fld.exists) {
                if (!fld.create()) return false;
            }
            cur += "/";
        }
        return true;
    }

    // NEW: safe sleep wrapper
    function sleepMs(ms) {
        try { $.sleep(ms); } catch (e) {}
    }

    // NEW: save + quit (best-effort, no UI)
    function saveAndQuitPremiere() {
        // Save first (prevents prompt). If project was never saved, Premiere may still prompt.
        try {
            if (project && typeof project.save === "function") {
                project.save();
                try { $.writeln("Project saved."); } catch (eW0) {}
            }
        } catch (eS) {
            try { $.writeln("Project save failed: " + eS); } catch (eS2) {}
        }

        // Quit Premiere
        try {
            if (app && typeof app.quit === "function") {
                app.quit();
                return;
            }
        } catch (eQ) {}

        // Fallback attempts (different builds sometimes expose different names)
        try { if (app && typeof app.exit === "function") app.exit(); } catch (eQ2) {}
    }

    // -------------------------
    // Validation JSON writer (C:/PPro_BeforeXML/premiere_validation.json)
    // -------------------------
    function _jsonStringifySafe(obj) {
        try {
            if (_hasNativeJSON()) return JSON.stringify(obj);
        } catch (e) {}

        // Minimal fallback (simple primitives only)
        var s = "{";
        var first = true;
        for (var k in obj) {
            if (!obj.hasOwnProperty(k)) continue;
            if (!first) s += ",";
            first = false;

            var v = obj[k];
            var vs;

            if (typeof v === "string") {
                vs = "\"" + String(v).replace(/\\/g, "\\\\").replace(/"/g, "\\\"") + "\"";
            } else if (typeof v === "boolean") {
                vs = v ? "true" : "false";
            } else if (typeof v === "number") {
                vs = String(v);
            } else if (v === null || v === undefined) {
                vs = "null";
            } else {
                vs = "\"" + String(v) + "\"";
            }

            s += "\"" + String(k) + "\":" + vs;
        }
        s += "}";
        return s;
    }

    function ensureFolderExistsSimple(winFolderPath) {
        try {
            var f = new Folder(winFolderPath);
            if (!f.exists) return f.create();
            return true;
        } catch (e) {
            return false;
        }
    }

    function writePremiereValidation(statusBool, errMsg, extraObj) {
        var f = null;
        try {
            var outFolder = "C:/PPro_BeforeXML";
            if (!ensureFolderExistsSimple(outFolder)) return false;

            var payload = extraObj || {};
            payload.status = (statusBool === true);
            if (!payload.status) payload.error = String(errMsg || "Unknown error");

            // Optional metadata for debugging
            payload.tool = "ExportXMLSequence";
            try {
                payload.ts = (new Date()).toISOString ? (new Date()).toISOString() : String(new Date());
            } catch (eTs) {
                payload.ts = String(new Date());
            }

            f = new File(outFolder + "/premiere_validation.json");
            f.encoding = "UTF-8";
            if (!f.open("w")) return false;
            f.write(_jsonStringifySafe(payload));
            f.close();
            return true;
        } catch (e) {
            try { if (f && f.opened) f.close(); } catch (e2) {}
            return false;
        }
    }

    // -------------------------
    // 1) Find sequence by name
    // -------------------------
    try {
        var seqs = project.sequences;
        if (seqs && typeof seqs.numSequences === "number") {
            for (var i = 0; i < seqs.numSequences; i++) {
                if (seqs[i] && seqs[i].name === targetName) { targetSequence = seqs[i]; break; }
            }
        } else if (seqs && typeof seqs.numItems === "number") {
            for (var j = 0; j < seqs.numItems; j++) {
                if (seqs[j] && seqs[j].name === targetName) { targetSequence = seqs[j]; break; }
            }
        }
    } catch (eFind) {}

    if (!targetSequence) {
        try { $.writeln("Could not find sequence: " + targetName); } catch (eW) {}
        // Write FAIL validation so Python doesn't hang forever
        writePremiereValidation(false, "Sequence not found: " + targetName, { sequence: targetName });
        return;
    }

    // -------------------------
    // 2) Load config + get cleaned video name
    // -------------------------
    var cfg = tryLoadConfigJson_FIXED_LOCATION();
    if (!cfg) {
        try { $.writeln("Could not find/parse config.json"); } catch (eC) {}
        writePremiereValidation(false, "Could not find/parse config.json", {});
        return;
    }

    var videoNameClean = getVideoNameFromConfigObject(cfg);
    if (!videoNameClean) {
        try { $.writeln("VideoName invalid in config.json"); } catch (eV) {}
        writePremiereValidation(false, "VideoName invalid in config.json", {});
        return;
    }

    // -------------------------
    // 3) Build output path
    // -------------------------
    var folderPath = "Z:/Automated Dubbings/Projects/" + videoNameClean + "/English/XML"; // existing logic
    var fileName = "english.xml";
    var fullPath = folderPath + "/" + fileName;

    // -------------------------
    // 4) Ensure folder exists
    // -------------------------
    if (!ensureFolderRecursive(folderPath)) {
        try { $.writeln("Failed create folder: " + folderPath); } catch (eF) {}
        writePremiereValidation(false, "Failed create folder: " + folderPath, { folder: folderPath });
        return;
    }

    // -------------------------
    // 5) Export XML
    // -------------------------
    var exportFile = new File(fullPath);

    // suppress UI = 1
    var okExport = false;
    var result = null;

    try {
        result = targetSequence.exportAsFinalCutProXML(exportFile.fsName, 1);
    } catch (eExp) {
        result = null;
    }

    try {
        okExport = !!(result && exportFile.exists);
        if (okExport) $.writeln("Exported: " + exportFile.fsName);
        else $.writeln("Export failed or file not created: " + exportFile.fsName);
    } catch (eR) {
        okExport = false;
    }

    // -------------------------
    // 6) Wait, then write validation JSON, then save + close Premiere
    // -------------------------
    try { $.writeln("Waiting 15 seconds before closing Premiere..."); } catch (eW15) {}
    sleepMs(15000);

    // Write validation JSON BEFORE quitting (so Python validator can read it)
    var wrote = false;
    if (okExport) {
        wrote = writePremiereValidation(true, "", {
            export_path: exportFile.fsName,
            video_name: videoNameClean
        });
    } else {
        wrote = writePremiereValidation(false, "Export XML failed or file not created", {
            export_path: exportFile.fsName,
            video_name: videoNameClean
        });
    }
    try { $.writeln("Validation JSON written: " + (wrote ? "YES" : "NO")); } catch (eWV) {}

    saveAndQuitPremiere();

})();
