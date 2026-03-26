﻿(function () {
    // =========================
    // JSON SAFE LAYER (FIXED)
    // =========================
    function _hasNativeJSON() {
        try {
            return (typeof JSON !== "undefined") &&
                   JSON &&
                   (typeof JSON.parse === "function") &&
                   (typeof JSON.stringify === "function");
        } catch (e) { return false; }
    }

    // IMPORTANT: DO NOT use /\b/ here. \b in regex is WORD BOUNDARY.
    function _escapeString(s) {
        s = String(s);

        // escape actual control characters
        s = s.replace(/\\/g, "\\\\")
             .replace(/"/g, '\\"')
             .replace(/\r/g, "\\r")
             .replace(/\n/g, "\\n")
             .replace(/\t/g, "\\t")
             .replace(/\f/g, "\\f")
             // backspace char is \u0008 (NOT regex \b)
             .replace(/\u0008/g, "\\b");

        return '"' + s + '"';
    }

    function _isArray(x) {
        try { return x && typeof x === "object" && x.constructor === Array; }
        catch (e) { return false; }
    }

    function _stringifyFallback(value, indent) {
        var sp = (typeof indent === "number" && indent > 0) ? indent : 0;

        function pad(n) {
            var s = "";
            for (var i = 0; i < n; i++) s += " ";
            return s;
        }

        function recur(v, depth) {
            if (v === null || v === undefined) return "null";

            var t = typeof v;
            if (t === "boolean") return v ? "true" : "false";
            if (t === "number")  return (isFinite(v) ? String(v) : "null");
            if (t === "string")  return _escapeString(v);

            // Array
            if (_isArray(v)) {
                if (v.length === 0) return "[]";
                var arrParts = [];
                for (var i = 0; i < v.length; i++) arrParts.push(recur(v[i], depth + sp));

                if (sp <= 0) return "[" + arrParts.join(",") + "]";
                var p0 = pad(depth + sp), p1 = pad(depth);
                return "[\n" + p0 + arrParts.join(",\n" + p0) + "\n" + p1 + "]";
            }

            // Object
            if (t === "object") {
                var keys = [];
                for (var k in v) {
                    try {
                        if (v.hasOwnProperty(k)) keys.push(k);
                    } catch (eHas) {
                        keys.push(k);
                    }
                }
                if (keys.length === 0) return "{}";

                var objParts = [];
                for (var j = 0; j < keys.length; j++) {
                    var key = keys[j];
                    var val = null;
                    try { val = v[key]; } catch (eVal) { val = null; }
                    objParts.push(_escapeString(key) + ":" + (sp > 0 ? " " : "") + recur(val, depth + sp));
                }

                if (sp <= 0) return "{" + objParts.join(",") + "}";
                var q0 = pad(depth + sp), q1 = pad(depth);
                return "{\n" + q0 + objParts.join(",\n" + q0) + "\n" + q1 + "}";
            }

            return "null";
        }

        return recur(value, 0);
    }

    function jsonParse(txt) {
        txt = (txt === null || txt === undefined) ? "" : String(txt);
        if (_hasNativeJSON()) return JSON.parse(txt);
        return eval("(" + txt + ")");
    }

    function jsonStringify(obj, indent) {
        if (_hasNativeJSON()) return JSON.stringify(obj, null, indent || 0);
        return _stringifyFallback(obj, indent || 0);
    }

    // =========================
    // CONFIG
    // =========================
    var TARGET_SEQUENCE_NAME = "Ready For Automation";

    var V3_INDEX = 2; // V3 (0-based) = Titles
    var V4_INDEX = 3; // V4 (0-based) = LowerThirds
    var V5_INDEX = 4; // V5 (0-based)
    var V6_INDEX = 5; // V6 (0-based)
    var TARGET_TRACKS = [V3_INDEX, V4_INDEX, V5_INDEX, V6_INDEX];

    var TICKS_PER_SECOND = 254016000000.0;

    var SHOW_ALERTS = false;

    // =========================
    // LOGGING
    // =========================
    function log(msg, level) {
        try {
            if (app && typeof app.setSDKEventMessage === "function") {
                app.setSDKEventMessage(String(msg), level || "info");
                return;
            }
        } catch (e) {}
        try { $.writeln(String(msg)); } catch (e2) {}
    }

    function notifyAlert(msg) {
        if (!SHOW_ALERTS) return;
        try { alert(String(msg)); } catch (e) {}
    }

    function fail(msg) {
        $.global.__PIPELINE_LAST_OK = false;
        $.global.__PIPELINE_LAST_MSG = String(msg);
        log("❌ " + msg, "error");
        notifyAlert("❌ " + msg);
        return false;
    }

    function ok(msg) {
        $.global.__PIPELINE_LAST_OK = true;
        $.global.__PIPELINE_LAST_MSG = String(msg || "");
        log("✅ " + (msg || "OK"), "info");
        notifyAlert("✅ " + (msg || "OK"));
        return true;
    }

    // =========================
    // SEQUENCE FIND (Prefer MAIN BIN, then fallback)
    // =========================
    function normalizeName(s) {
        return String(s || "")
            .replace(/\s+/g, " ")
            .replace(/^\s+|\s+$/g, "")
            .toLowerCase();
    }

    function openProjectItemInTimeline(item) {
        try {
            if (item && typeof item.openInTimeline === "function") {
                item.openInTimeline();
                return app.project.activeSequence || null;
            }
        } catch (e) {}
        return null;
    }

    function safeOpenInTimelineBySequence(seq) {
        if (!seq) return false;
        try {
            if (seq.projectItem && typeof seq.projectItem.openInTimeline === "function") {
                seq.projectItem.openInTimeline();
                return true;
            }
        } catch (e) {}
        try {
            app.project.activeSequence = seq;
            return true;
        } catch (e2) {}
        return false;
    }

    function findSequencePreferMainBin(seqName) {
        var target = normalizeName(seqName);

        // A) MAIN BIN ONLY
        try {
            var root = app.project.rootItem;
            if (root && root.children) {
                for (var i = 0; i < root.children.numItems; i++) {
                    var item = root.children[i];
                    if (!item || !item.name) continue;
                    if (normalizeName(item.name) !== target) continue;

                    try {
                        if (item.type === ProjectItemType.SEQUENCE) {
                            var seqA = openProjectItemInTimeline(item);
                            if (seqA && normalizeName(seqA.name) === target) return seqA;
                        }
                    } catch (eType) {
                        var seqA2 = openProjectItemInTimeline(item);
                        if (seqA2 && normalizeName(seqA2.name) === target) return seqA2;
                    }
                }
            }
        } catch (e1) {}

        // B) FALLBACK: GLOBAL SEQUENCES LIST
        try {
            var seqs = app.project.sequences;

            if (seqs && typeof seqs.numSequences === "number") {
                for (var a = 0; a < seqs.numSequences; a++) {
                    var s = seqs[a];
                    if (s && normalizeName(s.name) === target) {
                        safeOpenInTimelineBySequence(s);
                        return app.project.activeSequence || s;
                    }
                }
            } else if (seqs && typeof seqs.numItems === "number") {
                for (var b = 0; b < seqs.numItems; b++) {
                    var s2 = seqs[b];
                    if (s2 && normalizeName(s2.name) === target) {
                        safeOpenInTimelineBySequence(s2);
                        return app.project.activeSequence || s2;
                    }
                }
            } else if (seqs && seqs.length) {
                for (var c = 0; c < seqs.length; c++) {
                    var s3 = seqs[c];
                    if (s3 && normalizeName(s3.name) === target) {
                        safeOpenInTimelineBySequence(s3);
                        return app.project.activeSequence || s3;
                    }
                }
            }
        } catch (e2) {}

        return null;
    }

    // =========================
    // HELPERS (unchanged)
    // =========================
    function safeGet(obj, key, fallback) {
        try { if (obj && obj[key] !== undefined && obj[key] !== null) return obj[key]; } catch (e) {}
        return fallback;
    }

    function asNumber(x) {
        var n = parseFloat(x);
        return isNaN(n) ? null : n;
    }

    function timeToTicksNumber(t) {
        try {
            if (!t) return null;
            if (t.ticks !== undefined && t.ticks !== null) return asNumber(t.ticks);
        } catch (e) {}
        return null;
    }

    function round3(x) {
        if (x === null || x === undefined) return null;
        return Math.round(x * 1000) / 1000;
    }

    function trackNameFromIndex(i) { return "V" + (i + 1); }
    function trimStr(s) { return String(s).replace(/^\s+|\s+$/g, ""); }

    function parseFrameRateValue(v) {
        if (v === null || v === undefined) return null;
        if (typeof v === "number") return v;

        var s = String(v).replace(/^\s+|\s+$/g, "");
        if (!s.length) return null;

        if (s.indexOf("/") !== -1) {
            var parts = s.split("/");
            if (parts.length === 2) {
                var a = parseFloat(parts[0]);
                var b = parseFloat(parts[1]);
                if (!isNaN(a) && !isNaN(b) && b !== 0) return a / b;
            }
        }

        var n = parseFloat(s);
        if (!isNaN(n)) return n;

        return null;
    }

    function isReasonableFps(fps) {
        return fps !== null && fps !== undefined && !isNaN(fps) && fps >= 1 && fps <= 240;
    }

    function inferFpsMaybeWeird(val) {
        var n = parseFrameRateValue(val);
        if (n === null) return null;

        if (isReasonableFps(n)) return n;

        if (n > 1000000 && n < TICKS_PER_SECOND) {
            var fpsFromTicksPerFrame = TICKS_PER_SECOND / n;
            if (isReasonableFps(fpsFromTicksPerFrame)) return fpsFromTicksPerFrame;
        }

        if (Math.abs(n - TICKS_PER_SECOND) < (TICKS_PER_SECOND * 0.05)) {
            return null;
        }
        return null;
    }

    function getSequenceFPS(seq) {
        var fps = null;

        try {
            if (seq && seq.getSettings) {
                var s = seq.getSettings();
                var vfr = safeGet(s, "videoFrameRate", null);
                fps = inferFpsMaybeWeird(vfr);
            }
        } catch (e1) {}

        if (!isReasonableFps(fps)) {
            try {
                if (seq && seq.timebase !== undefined && seq.timebase !== null) {
                    fps = inferFpsMaybeWeird(seq.timebase);
                }
            } catch (e2) {}
        }

        if (!isReasonableFps(fps)) fps = 25;
        return fps;
    }

    function looksLikeJsonString(s) {
        if (!s || typeof s !== "string") return false;
        var t = trimStr(s);
        return t.length >= 2 && (
            (t.charAt(0) === "{" && t.charAt(t.length - 1) === "}") ||
            (t.charAt(0) === "[" && t.charAt(t.length - 1) === "]")
        );
    }

    function isBase64Blob(s) {
        if (!s) return false;
        var t = trimStr(s);
        if (t.length < 24) return false;
        if (/\s/.test(t)) return false;
        return /^[A-Za-z0-9+\/]+={0,2}$/.test(t);
    }

    function isGuidList(s) {
        if (!s) return false;
        var t = trimStr(s);
        if (t.indexOf(";") === -1) return false;
        var uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
        var re = new RegExp("^(?:" + uuid + ")(?:;(?:" + uuid + "))*;?$");
        return re.test(t);
    }

    function containsJunkKeywords(s) {
        var t = trimStr(s).toLowerCase();
        return (
            t.indexOf("defdur") !== -1 ||
            t.indexOf("maxdur") !== -1 ||
            t.indexOf("fontreusegroupid") !== -1 ||
            t.indexOf("text-preset") !== -1 ||
            t.indexOf("preset") !== -1
        );
    }

    function isProbablyNotUserTextValue(s) {
        if (!s) return true;
        var t = trimStr(s);
        if (!t.length) return true;

        var low = t.toLowerCase();
        if (low.indexOf("file://") === 0) return true;
        if (low.indexOf(".mogrt") !== -1) return true;

        if (low.indexOf(".png") !== -1 || low.indexOf(".jpg") !== -1 || low.indexOf(".jpeg") !== -1 ||
            low.indexOf(".mp4") !== -1 || low.indexOf(".mov") !== -1) return true;

        if (low.indexOf(":\\") !== -1) return true;

        if (isGuidList(t)) return true;
        if (isBase64Blob(t)) return true;
        if (containsJunkKeywords(t)) return true;

        return false;
    }

    function collectStringsDeep(obj, arr) {
        try {
            if (obj === null || obj === undefined) return;
            if (typeof obj === "string") { arr.push(obj); return; }
            if (typeof obj !== "object") return;
            for (var k in obj) {
                if (!obj.hasOwnProperty(k)) continue;
                collectStringsDeep(obj[k], arr);
            }
        } catch (e) {}
    }

    function propLooksLikeTextControl(displayName, matchName) {
        var dn = (displayName || "").toLowerCase();
        var mn = (matchName || "").toLowerCase();

        // Broaden detection beyond literal "text" because many MOGRTs name fields like:
        // "Number", "Title", "Subtitle", "Line 1", "Header", etc.
        var goodWords = [
            "text",
            "title",
            "subtitle",
            "number",
            "header",
            "caption",
            "line",
            "top",
            "bottom",
            "name",
            "body",
            "TEXT"
        ];

        var hasGoodWord = false;
        for (var g = 0; g < goodWords.length; g++) {
            if (dn.indexOf(goodWords[g]) !== -1 || mn.indexOf(goodWords[g]) !== -1) {
                hasGoodWord = true;
                break;
            }
        }

        // Special-case common phrase
        if (!hasGoodWord) {
            if (dn.indexOf("source") !== -1 && dn.indexOf("text") !== -1) hasGoodWord = true;
            if (mn.indexOf("source") !== -1 && mn.indexOf("text") !== -1) hasGoodWord = true;
        }

        if (!hasGoodWord) return false;

        // Exclude styling/format/utility controls
        var badWords = [
            "font", "style", "preset", "group",
            "duration", "dur",
            "color", "fill", "stroke",
            "size", "tracking", "leading",
            "opacity"
        ];
        for (var i = 0; i < badWords.length; i++) {
            if (dn.indexOf(badWords[i]) !== -1) return false;
            if (mn.indexOf(badWords[i]) !== -1) return false;
        }
        return true;
    }

    function scoreCandidate(s) {
        if (!s) return -9999;
        var t = trimStr(s);
        if (!t.length) return -9999;
        if (isProbablyNotUserTextValue(t)) return -9999;

        var score = 0;
        var alpha = (t.match(/[A-Za-z\u00C0-\u024F\u0400-\u04FF\u0600-\u06FF]/g) || []).length;
        score += alpha * 3;

        var spaces = (t.match(/[\s]/g) || []).length;
        score += spaces * 2;

        if (spaces === 0 && /^[A-Za-z]+[-_][A-Za-z0-9]+$/.test(t)) score -= 120;

        if (t.length >= 2 && t.length <= 180) score += 40;
        if (t.length > 240) score -= 60;

        return score;
    }

    // ============================================================
    // FIXED: Extract BOTH text fields (Number + Body) from JSON:
    //   value = {"...","textEditValue":"NUMBER 15"}
    //   value = {"...","textEditValue":"Flood Barrier House, Netherlands"}
    // Result: "NUMBER 15\nFlood Barrier House, Netherlands"
    // ============================================================
function extractBestTextFromMGTComponent(mgtComponent, strictMode) {
    if (!mgtComponent) return null;
    var props = safeGet(mgtComponent, "properties", null);
    if (!props) return null;

    // Legacy fallback
    var best = null;
    var bestScore = -9999;

    // NEW merged text fields (in property order)
    var mergedTexts = [];
    var mergedSeen = {};

    function cleanText(x) {
        // Preserve internal line breaks; normalize Windows newlines only.
        return trimStr(String(x || "")).replace(/\r\n/g, "\n");
    }

    function isArrayLocal(x) {
        try { return x && typeof x === "object" && x.constructor === Array; } catch (e) { return false; }
    }

    // Extract "textEditValue" specifically (avoid collecting font names etc.)
    function findTextEditValueDeep(o) {
        try {
            if (o === null || o === undefined) return null;

            if (typeof o === "object") {
                try {
                    if (o.hasOwnProperty && o.hasOwnProperty("textEditValue")) {
                        var v = o["textEditValue"];
                        if (typeof v === "string") return v;
                        if (isArrayLocal(v) && v.length) {
                            var joined = "";
                            for (var i = 0; i < v.length; i++) {
                                if (typeof v[i] === "string") joined += (joined ? "\n" : "") + v[i];
                            }
                            if (joined) return joined;
                        }
                    }
                } catch (eHit) {}

                if (isArrayLocal(o)) {
                    for (var a = 0; a < o.length; a++) {
                        var gotA = findTextEditValueDeep(o[a]);
                        if (gotA) return gotA;
                    }
                    return null;
                }

                for (var k in o) {
                    if (!o.hasOwnProperty(k)) continue;
                    var got = findTextEditValueDeep(o[k]);
                    if (got) return got;
                }
            }
        } catch (e) {}
        return null;
    }

    function addMergedIfGood(s) {
        s = cleanText(s);
        if (!s) return;
        if (isProbablyNotUserTextValue(s)) return;
        var key = s.toLowerCase();
        if (mergedSeen[key]) return;
        mergedSeen[key] = true;
        mergedTexts.push(s);
    }

    // --- Deep-scan MOGRT properties (groups often contain the real text params) ---
    function isLeafParam(p) {
        try { return !!(p && typeof p.getValue === "function"); } catch (e) { return false; }
    }

    function getChildrenCollection(x) {
        // Groups usually expose children under `.properties`
        try { if (x && x.properties && x.properties.numItems !== undefined) return x.properties; } catch (e1) {}
        // Some items behave like collections directly
        try { if (x && x.numItems !== undefined) return x; } catch (e2) {}
        return null;
    }

    function processLeafParam(p) {
        var dn = safeGet(p, "displayName", "");
        var mn = safeGet(p, "matchName", "");
        var isTextish = propLooksLikeTextControl(dn, mn);

        // IMPORTANT: disable strict filtering here (so V4 behaves like V3)
        // If you still want strict behavior in the future, change this one line only.
        // if (strictMode && !isTextish) return;

        try { if (p.isTimeVarying && p.isTimeVarying()) return; } catch (eTV) {}

        var val = null;
        try { val = p.getValue(); } catch (eGet) { val = null; }
        if (typeof val !== "string" || !val.length) return;

        var valClean = cleanText(val);

        // If it looks like text control, try to extract real user text safely
        if (isTextish) {
            if (looksLikeJsonString(valClean)) {
                try {
                    var obj = jsonParse(valClean);
                    var tev = findTextEditValueDeep(obj);
                    if (tev) addMergedIfGood(tev);
                } catch (eJson) {}
            } else {
                addMergedIfGood(valClean);
            }
        }

        // Legacy scoring path (kept)
        var candidates = [];
        if (looksLikeJsonString(valClean)) {
            try {
                var obj2 = jsonParse(valClean);
                collectStringsDeep(obj2, candidates);
            } catch (eParse) {
                candidates.push(valClean);
            }
        } else {
            candidates.push(valClean);
        }

        for (var c = 0; c < candidates.length; c++) {
            var s = candidates[c];
            var sc = scoreCandidate(s);
            if (sc > bestScore) { bestScore = sc; best = s; }
        }
    }

    function scanPropsDeep(coll) {
        if (!coll) return;

        var count = safeGet(coll, "numItems", null);
        if (count === null || count === undefined) count = safeGet(coll, "length", 0);
        count = parseInt(count, 10) || 0;

        for (var ii = 0; ii < count; ii++) {
            var it = null;
            try { it = coll[ii]; } catch (e) { it = null; }
            if (!it) continue;

            // Recurse into groups first
            var kids = getChildrenCollection(it);
            if (kids && kids !== coll) {
                scanPropsDeep(kids);
            }

            // Process leaf params
            if (isLeafParam(it)) {
                processLeafParam(it);
            }
        }
    }
    // --- END Deep scan ---

    // ✅ FIX: actually run deep scan (replaces old top-level loop)
    scanPropsDeep(props);

    // Prefer merged text fields if present (this is the upgrade)
    if (mergedTexts.length >= 2) return mergedTexts.join("\n");
    if (mergedTexts.length === 1) return mergedTexts[0];

    // Fallback: original behavior
    if (!best) return null;
    best = cleanText(best);
    if (isProbablyNotUserTextValue(best)) return null;
    return best;
}

function extractClipText(clip, strictMode) {
    try {
        if (clip && clip.getMGTComponent) {
            var mgt = clip.getMGTComponent();
            return extractBestTextFromMGTComponent(mgt, strictMode);
        }
    } catch (e) {}
    return null;
}

// -------------------------
// REQUIRED TIME UTILITIES
// -------------------------

// Keep your normal function declaration (below), so no guard needed for it.
// Only guard ticksToFrame (since it was missing).
if (typeof ticksToFrame !== "function") {
    ticksToFrame = function (ticks, ticksPerFrame) {
        return Math.round(ticks / ticksPerFrame);
    };
}

function toSequenceRelativeTicks(rawTicks, zeroTicks) {
    if (rawTicks === null || rawTicks === undefined) return null;
    if (!zeroTicks) return rawTicks;
    if (rawTicks < zeroTicks) return rawTicks;
    var rel = rawTicks - zeroTicks;
    return (rel < 0) ? 0 : rel;
}


    // =========================
    // CONFIG.JSON + OUTPUT PATH
    // =========================
    function sanitizeJsonText(s) {
        if (s === null || s === undefined) return null;
        s = String(s);

        // remove BOM if any
        if (s.length && s.charCodeAt(0) === 0xFEFF) s = s.substring(1);

        // remove null bytes
        s = s.replace(/\u0000/g, "")
             .replace(/\u2028/g, "\n")
             .replace(/\u2029/g, "\n");

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
        var s = trimStr(String(raw || ""));
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
                    var cleaned = sanitizeVideoName(v);
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

        // hard fallback for runner
        candidates.push(new File("C:/PPro_BeforeXML/inputs/config/config.json"));  // Config path remains the same

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

    function writeJsonFile(path, obj) {
        var f = new File(path);
        f.encoding = "UTF-8";
        if (!f.open("w")) return false;
        f.write(jsonStringify(obj, 2));
        f.close();
        return true;
    }

    // =========================
    // RUN
    // =========================
    try {
        if (!app || !app.project) return fail("No Premiere project found.");

        var proj = app.project;
        var prevSeq = proj.activeSequence;

        var seq = findSequencePreferMainBin(TARGET_SEQUENCE_NAME);
        if (!seq) return fail("Sequence not found (MAIN BIN + fallback): " + TARGET_SEQUENCE_NAME);

        log("Using sequence: " + seq.name, "info");

        var fps = getSequenceFPS(seq);
        var ticksPerFrame = TICKS_PER_SECOND / fps;

        var zeroTicks = 0;
        try {
            if (seq.zeroPoint && seq.zeroPoint.ticks !== undefined && seq.zeroPoint.ticks !== null) {
                zeroTicks = asNumber(seq.zeroPoint.ticks) || 0;
            }
        } catch (eZ) { zeroTicks = 0; }

        var videoTracks = seq.videoTracks;
        if (!videoTracks) return fail("No video tracks found in sequence.");

        var totalTracks = videoTracks.numTracks;
        if (totalTracks === undefined || totalTracks === null) totalTracks = videoTracks.length;
        totalTracks = parseInt(totalTracks, 10) || 0;

        var output = [];
        var idCounter = 1;

        for (var ti = 0; ti < TARGET_TRACKS.length; ti++) {
            var trackIndex = TARGET_TRACKS[ti];
            if (trackIndex >= totalTracks) continue;

            var track = videoTracks[trackIndex];
            if (!track || !track.clips) continue;

            var clips = track.clips;
            var clipCount = safeGet(clips, "numItems", null);
            if (clipCount === null || clipCount === undefined) clipCount = safeGet(clips, "length", 0);
            clipCount = parseInt(clipCount, 10) || 0;

            var forcedType = "LowerThird";
            if (trackIndex === V3_INDEX) {
                forcedType = "Title";
            } else if (trackIndex === V4_INDEX) {
                forcedType = "LowerThird";
            } else if (trackIndex === V5_INDEX) {
                forcedType = "UltraText";
            } else if (trackIndex === V6_INDEX) {
                forcedType = "UltraExtraText";
            }
            
            var strictMode = (trackIndex === V4_INDEX || trackIndex === V5_INDEX || trackIndex === V6_INDEX);
            var trackName = trackNameFromIndex(trackIndex);

            for (var ci = 0; ci < clipCount; ci++) {
                var clip = clips[ci];
                if (!clip) continue;

                var rawStartTicks = timeToTicksNumber(clip.start);
                var rawEndTicks   = timeToTicksNumber(clip.end);
                if (rawStartTicks === null || rawEndTicks === null) continue;

                var startTicks = toSequenceRelativeTicks(rawStartTicks, zeroTicks);
                var endTicks   = toSequenceRelativeTicks(rawEndTicks, zeroTicks);
                if (endTicks <= startTicks) continue;

                var startSec = startTicks / TICKS_PER_SECOND;
                var endSec   = endTicks / TICKS_PER_SECOND;

                var startFrame = ticksToFrame(startTicks, ticksPerFrame);
                var endFrame   = ticksToFrame(endTicks, ticksPerFrame);

                var durationFrames = endFrame - startFrame;
                var durationSec = endSec - startSec;

                if (durationFrames <= 0 || durationSec <= 0) continue;

                var text = extractClipText(clip, strictMode);
                if (!text) continue;

                output.push({
                    id: idCounter++,
                    text: text,
                    type: forcedType,
                    track: trackName,
                    start_sec: round3(startSec),
                    end_sec: round3(endSec),
                    start_frame: startFrame,
                    end_frame: endFrame,
                    duration_sec: round3(durationSec),
                    duration_frames: durationFrames
                });
            }
        }

        if (output.length === 0) return fail("No valid text found on target tracks.");

        var cfg = tryLoadConfigJson_FIXED_LOCATION();
        if (!cfg) return fail("Could not find/parse inputs/config/config.json");

        var videoName = getVideoNameFromConfigObject(cfg);
        if (!videoName) return fail("VideoName not found/invalid in config.json");

        var outFolderPath = "Z:/Automated Dubbings/Projects/" + videoName + "/English/JSON";  // Updated path
        if (!ensureFolderRecursive(outFolderPath)) return fail("Failed to create output folder: " + outFolderPath);

        var outFilePath = outFolderPath + "/__titles_and_lowerthirds__.json";
        if (!writeJsonFile(outFilePath, output)) return fail("Could not write JSON: " + outFilePath);

        log("Saved " + output.length + " item(s) | FPS=" + fps + " | " + outFilePath, "info");

        if (prevSeq) {
            try { safeOpenInTimelineBySequence(prevSeq); } catch (eR) {}
        }

        return ok("Done. Exported titles/lowerthirds JSON.");

    } catch (err) {
        return fail("Script error: " + err.toString());
    }
})();