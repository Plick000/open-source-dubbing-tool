/**
 * _DUMP_MOGRT_PARAMS.jsx
 * Dumps all MOGRT property displayNames + current values into output/JSON/__mogrt_params_dump__.json
 * (relative to this JSX file)
 */

(function () {
  function getOutFile() {
    var scriptFile = new File($.fileName);
    var folder = scriptFile.parent;
    return new File(folder.fsName + "/output/JSON/__mogrt_params_dump__.json");
  }

   function getTrackItem() {
     var seq = app.project.activeSequence;
     if (!seq) return null;
   
     var TRACK_INDEX = 3; // ✅ V4 (0=V1, 1=V2, 2=V3, 3=V4)
   
     try {
       var vt = seq.videoTracks[TRACK_INDEX];
       if (!vt) return null;
   
       // If something is selected, prefer selected item BUT ensure it is on V4
       try {
         var sel = seq.getSelection();
         if (sel && sel.length) {
           var s = sel[0];
           if (s && s.parentTrack && s.parentTrack === vt) return s;
         }
       } catch (e) {}
   
       // Fallback: return first clip on V4
       if (vt.clips && vt.clips.numItems > 0) return vt.clips[0];
     } catch (e2) {}
   
     return null;
   }

  function safeString(v) {
    try {
      if (v === null || v === undefined) return "";
      return String(v);
    } catch (e) { return ""; }
  }

  function writeJSON(obj) {
    // ExtendScript safe stringify
    function esc(s) {
      s = safeString(s);
      return s
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"')
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n")
        .replace(/\t/g, "\\t");
    }

    function ser(v) {
      if (v === null) return "null";
      var t = typeof v;
      if (t === "number") return String(v);
      if (t === "boolean") return v ? "true" : "false";
      if (t === "string") return '"' + esc(v) + '"';
      // fallback
      return '"' + esc(safeString(v)) + '"';
    }

    var out = "[\n";
    for (var i = 0; i < obj.length; i++) {
      var it = obj[i];
      out += "  {";
      out += '"i":' + ser(it.i) + ",";
      out += '"displayName":' + ser(it.displayName) + ",";
      out += '"type":' + ser(it.type) + ",";
      out += '"value":' + ser(it.value);
      out += "}";
      if (i < obj.length - 1) out += ",";
      out += "\n";
    }
    out += "]\n";
    return out;
  }

  try {
    var item = getTrackItem();
    if (!item) { $.writeln("Select one MOGRT/title clip first."); return; }

    var mgt = null;
    try { mgt = item.getMGTComponent(); } catch (e) { mgt = null; }
    if (!mgt || !mgt.properties) { $.writeln("No MGT properties found."); return; }

    var props = mgt.properties;
    var dump = [];

    for (var i = 0; i < props.numItems; i++) {
      var p = props[i];
      if (!p) continue;

      var dn = "";
      try { dn = String(p.displayName || ""); } catch (e0) { dn = ""; }

      var v = null;
      var typ = "unknown";
      try {
        v = p.getValue();
        typ = (v === null) ? "null" : (typeof v);
      } catch (e1) {
        v = "";
        typ = "error";
      }

      // keep value readable
      var vs = safeString(v);
      if (vs.length > 1200) vs = vs.substring(0, 1200);

      dump.push({ i: i, displayName: dn, type: typ, value: vs });
    }

    var f = getOutFile();
    f.encoding = "UTF8";
    if (!f.open("w")) { $.writeln("Cannot write dump file."); return; }
    f.write(writeJSON(dump));
    f.close();

    $.writeln("Dump written: " + f.fsName);
  } catch (e) {
    $.writeln("Dump crashed: " + e);
  }
})();
