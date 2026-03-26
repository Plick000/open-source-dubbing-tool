/**
 * EXTRACT V5 + V6 + A4 (BY SEQUENCE NAME — NO ACTIVE SEQUENCE REQUIRED)
 *
 * ✅ Uses SOURCE_SEQUENCE_NAME to find the sequence from the PROJECT (not activeSequence)
 * ✅ Clones it, renames the clone to NEW_NAME
 * ✅ On the CLONE: keeps ONLY V5, V6 and A4 (clears all other tracks)
 * ✅ DOES NOT remove anything from the original sequence (the “remove from original” block stays commented)
 *
 * Notes:
 * - Track indexes are 0-based: V1=0, V2=1, ... A1=0, A2=1, ...
 */

(function () {
    // =========================
    // CONFIGURATION (EDIT THESE)
    // =========================
    var SOURCE_SEQUENCE_NAME = "Ready For Automation"; // <-- change easily
    var NEW_NAME = "Transitions Titles - V3-4-5-6 A4";

    var V3_IDX = 2; // Video 3 (0-based)
    var V4_IDX = 3; // Video 4 (0-based)
    var V5_IDX = 4; // Video 5 (0-based)
    var V6_IDX = 5; // Video 6 (0-based)
    var V7_IDX = 6; // Video 7 (0-based)
    var V8_IDX = 7; // Video 8 (0-based)
    var A4_IDX = 3; // Audio 4 (0-based)

    var SHOW_ALERTS = false; // keep false to avoid popups
    // =========================

    function log(msg, level) {
        try {
            if (app && typeof app.setSDKEventMessage === "function") {
                app.setSDKEventMessage(String(msg), level || "info");
            }
        } catch (e) {}
        try { $.writeln(String(msg)); } catch (e2) {}
    }

    function fail(msg) {
        $.global.__PIPELINE_LAST_OK = false;
        $.global.__PIPELINE_LAST_MSG = String(msg);
        if (SHOW_ALERTS) { try { alert("❌ " + msg); } catch (e) {} }
        log("❌ " + msg, "error");
    }

    function ok(msg) {
        $.global.__PIPELINE_LAST_OK = true;
        $.global.__PIPELINE_LAST_MSG = String(msg || "");
        if (SHOW_ALERTS) { try { alert("✅ " + (msg || "OK")); } catch (e) {} }
        log("✅ " + (msg || "OK"), "info");
    }

    function safeOpenInTimeline(seq) {
        if (!seq) return false;
        try {
            if (seq.projectItem && typeof seq.projectItem.openInTimeline === "function") {
                seq.projectItem.openInTimeline();
            } else {
                app.project.activeSequence = seq;
            }
            return true;
        } catch (e) {
            return false;
        }
    }

    function clearTrack(track) {
        if (!track) return;

        // Remove clips
        try {
            if (track.clips && track.clips.numItems) {
                for (var i = track.clips.numItems - 1; i >= 0; i--) {
                    try {
                        if (track.clips[i]) track.clips[i].remove(false, false);
                    } catch (e1) {}
                }
            }
        } catch (e2) {}

        // Remove transitions (if available)
        try {
            if (track.transitions && track.transitions.numItems) {
                for (var t = track.transitions.numItems - 1; t >= 0; t--) {
                    try {
                        if (track.transitions[t]) track.transitions[t].remove(false);
                    } catch (e3) {}
                }
            }
        } catch (e4) {}
    }

    function findSequenceByName(seqName) {
        try {
            var seqs = app.project.sequences;
            // Premiere's collection differs by version; handle both patterns
            if (seqs && typeof seqs.numSequences === "number") {
                for (var i = 0; i < seqs.numSequences; i++) {
                    var s = seqs[i];
                    if (s && s.name === seqName) return s;
                }
            } else if (seqs && typeof seqs.numItems === "number") {
                for (var j = 0; j < seqs.numItems; j++) {
                    var s2 = seqs[j];
                    if (s2 && s2.name === seqName) return s2;
                }
            } else if (seqs && seqs.length) {
                for (var k = 0; k < seqs.length; k++) {
                    var s3 = seqs[k];
                    if (s3 && s3.name === seqName) return s3;
                }
            }
        } catch (e) {}
        return null;
    }

    function renameClonedSequence(sourceName, clonedSeq) {
        // Best: rename via direct reference
        try {
            if (clonedSeq && clonedSeq.projectItem) {
                clonedSeq.projectItem.name = NEW_NAME;
                return true;
            }
        } catch (e0) {}

        // Fallback: search root bin for "sourceName Copy"
        try {
            var root = app.project.rootItem;
            if (!root || !root.children) return false;

            for (var i = 0; i < root.children.numItems; i++) {
                var item = root.children[i];
                if (!item || !item.name) continue;

                // match like: "Ready For Automation Copy"
                if (item.name.indexOf(sourceName) !== -1 && item.name.indexOf(" Copy") !== -1) {
                    item.name = NEW_NAME;
                    return true;
                }
            }
        } catch (e1) {}

        return false;
    }

    // =========================
    // MAIN
    // =========================
    try {
        var proj = app.project;
        if (!proj) return fail("No project found.");

        // 1) Find source sequence by NAME (not activeSequence)
        var sourceSeq = findSequenceByName(SOURCE_SEQUENCE_NAME);
        if (!sourceSeq) {
            return fail("Sequence not found in project: " + SOURCE_SEQUENCE_NAME);
        }

        var sourceName = sourceSeq.name;
        log("Using source sequence: " + sourceName, "info");

        // 2) Clone the sequence
        var clonedSeq = null;
        try {
            clonedSeq = sourceSeq.clone();
        } catch (eClone) {
            return fail("Failed to clone sequence: " + eClone.toString());
        }
        if (!clonedSeq) return fail("Clone returned null.");

        // 3) Rename the clone to NEW_NAME
        var renamed = renameClonedSequence(sourceName, clonedSeq);
        if (!renamed) {
            // still proceed, but warn
            log("⚠️ Could not confidently rename clone via projectItem/bin search.", "warning");
        } else {
            log("Renamed clone to: " + NEW_NAME, "info");
        }

        // 4) Switch to clone and clear all tracks EXCEPT V5, V6, A4
        safeOpenInTimeline(clonedSeq);
        var activeClone = proj.activeSequence; // should now be clone

        if (!activeClone) return fail("Could not activate cloned sequence in timeline.");

        // Video tracks
        try {
            if (activeClone.videoTracks && typeof activeClone.videoTracks.numTracks === "number") {
                for (var v = 0; v < activeClone.videoTracks.numTracks; v++) {
                    if (v !== V3_IDX && v !== V4_IDX && v !== V5_IDX && v !== V6_IDX && v !== V7_IDX && v !== V8_IDX) {
                        clearTrack(activeClone.videoTracks[v]);
                    }
                }
            }
        } catch (eVid) {
            return fail("Error cleaning clone video tracks: " + eVid.toString());
        }

        // Audio tracks
        try {
            if (activeClone.audioTracks && typeof activeClone.audioTracks.numTracks === "number") {
                for (var a = 0; a < activeClone.audioTracks.numTracks; a++) {
                    if (a !== A4_IDX) {
                        clearTrack(activeClone.audioTracks[a]);
                    }
                }
            }
        } catch (eAud) {
            return fail("Error cleaning clone audio tracks: " + eAud.toString());
        }

        // 5) Switch back to original (by name) — DO NOT REMOVE anything (kept commented)
        var originalAgain = findSequenceByName(SOURCE_SEQUENCE_NAME);
        if (originalAgain) safeOpenInTimeline(originalAgain);

        /*
        // ❌ KEEP THIS COMMENTED (as you requested)
        var activeOrig = proj.activeSequence;
        if (activeOrig && activeOrig.videoTracks) {
            if (activeOrig.videoTracks[V5_IDX]) clearTrack(activeOrig.videoTracks[V5_IDX]);
            if (activeOrig.videoTracks[V6_IDX]) clearTrack(activeOrig.videoTracks[V6_IDX]);
            if (activeOrig.audioTracks[A4_IDX]) clearTrack(activeOrig.audioTracks[A4_IDX]);
        }
        */

        ok("Done. Created cleaned clone: " + NEW_NAME + " (kept only V3, V4, V5, V6, V7, V8, A4).");

    } catch (err) {
        fail("Script Error: " + err.toString());
    }
})();
