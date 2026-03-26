(function () {
    // =========================================================
    // CONFIG
    // =========================================================
    var TARGET_SPEED_PERCENT = 50.0;   // 150 means 150%
    var SPEED_TRACK_INDEX    = 0;

    var FPS = 23.976;
    var TIMEDISPLAY_23976 = 110;

    // =========================================================
    // HELPERS
    // =========================================================
    function secondsToTimecode(seconds, fps) {
        var oneFrame = new Time();
        oneFrame.seconds = 1.0 / fps;

        var t = new Time();
        t.seconds = seconds;

        return t.getFormatted(oneFrame, TIMEDISPLAY_23976);
    }

    function safeClipName(clip) {
        try {
            if (clip && clip.name) return clip.name;
        } catch (_) {}
        return "Unknown";
    }

    // =========================================================
    // MAIN
    // =========================================================
    app.enableQE();

    var seq = app.project.activeSequence;
    if (!seq) {
        alert("No active sequence.");
        return;
    }

    var qeSeq = qe.project.getActiveSequence();
    if (!qeSeq) {
        alert("QE cannot see the active sequence.");
        return;
    }

    if (SPEED_TRACK_INDEX >= seq.videoTracks.numTracks) {
        alert("Invalid SPEED_TRACK_INDEX: " + SPEED_TRACK_INDEX);
        return;
    }

    var domTrack = seq.videoTracks[SPEED_TRACK_INDEX];
    var qeTrack  = qeSeq.getVideoTrackAt(SPEED_TRACK_INDEX);

    if (!domTrack || !qeTrack) {
        alert("Could not access the requested video track.");
        return;
    }

    if (domTrack.clips.numItems === 0) {
        alert("No clips found on V" + (SPEED_TRACK_INDEX + 1));
        return;
    }

    var changedSpeed = 0;
    var failedSpeed = 0;
    var log = [];

    for (var i = 0; i < domTrack.clips.numItems; i++) {
        var domClip = domTrack.clips[i];
        if (!domClip) {
            failedSpeed++;
            log.push("Clip #" + i + ": DOM clip missing");
            continue;
        }

        var oldDur = 0;
        try {
            oldDur = domClip.duration.seconds;
        } catch (e) {
            failedSpeed++;
            log.push("Clip #" + i + ": could not read duration | " + e);
            continue;
        }

        if (!oldDur || oldDur <= 0) {
            failedSpeed++;
            log.push("Clip #" + i + ": invalid duration");
            continue;
        }

        var newDur = oldDur / (TARGET_SPEED_PERCENT / 100.0);
        var tc = secondsToTimecode(newDur, FPS);

        var qeClip = null;
        try {
            qeClip = qeTrack.getItemAt(i);
        } catch (e2) {
            failedSpeed++;
            log.push("Clip #" + i + ": QE getItemAt failed | " + e2);
            continue;
        }

        if (!qeClip || !qeClip.setSpeed) {
            failedSpeed++;
            log.push("Clip #" + i + ": QE clip missing or setSpeed unavailable");
            continue;
        }

        try {
            // IMPORTANT:
            // Pass SPEED AS PERCENT, not multiplier.
            qeClip.setSpeed(TARGET_SPEED_PERCENT, tc, false, false, false);

            changedSpeed++;
            log.push(
                "Clip #" + i +
                " | name=" + safeClipName(domClip) +
                " | oldDur=" + oldDur.toFixed(3) +
                "s | newDur=" + newDur.toFixed(3) +
                "s | tc=" + tc +
                " | speed=" + TARGET_SPEED_PERCENT + "%"
            );
        } catch (e3) {
            failedSpeed++;
            log.push("Clip #" + i + ": setSpeed failed | " + e3);
        }
    }

    $.writeln("==================================================");
    $.writeln("SPEED CHANGE LOG");
    $.writeln("==================================================");
    for (var j = 0; j < log.length; j++) {
        $.writeln(log[j]);
    }

    alert(
        "Done.\n\n" +
        "Target speed: " + TARGET_SPEED_PERCENT + "%\n" +
        "Clips changed: " + changedSpeed + "\n" +
        "Failed: " + failedSpeed
    );
})();