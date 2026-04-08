// local_logic.js — HTA client persistence & incremental merge
//
// SYSTEM_DESIGN.md §5-§6 に基づく。
//   - ETag ベースの差分同期(If-None-Match ヘッダ)
//   - FileSystemObject によるローカル `data/` 永続化
//   - Librarian Mode: 月単位(YYYY-MM)の物理分割で大容量 JSON のパース負荷軽減
//
// 本モジュールは JScript(IE11/Trident 互換)前提で書かれている。
// ES5 相当の構文のみを使用し、ES6 の let/const/arrow/Template Literal は使わない。

var LocalLogic = (function () {
  var DATA_DIR = "data";
  var CONFIG_FILE = "data\\config.json";

  // ----------------------------------------------------------------- //
  // File system helpers
  // ----------------------------------------------------------------- //

  function fso() {
    return new ActiveXObject("Scripting.FileSystemObject");
  }

  function ensureDir(path) {
    var f = fso();
    if (!f.FolderExists(path)) {
      // Create parent chain manually (FileSystemObject.CreateFolder only
      // accepts the direct parent as existing)
      var parts = path.split("\\");
      var cur = "";
      for (var i = 0; i < parts.length; i++) {
        cur = cur ? cur + "\\" + parts[i] : parts[i];
        if (cur && !f.FolderExists(cur)) {
          try { f.CreateFolder(cur); } catch (e) { /* ignore */ }
        }
      }
    }
  }

  function writeText(path, text) {
    ensureDir(path.substring(0, path.lastIndexOf("\\")));
    // ADODB.Stream supports UTF-8
    var stream = new ActiveXObject("ADODB.Stream");
    stream.Type = 2; // adTypeText
    stream.Charset = "utf-8";
    stream.Open();
    stream.WriteText(text);
    stream.SaveToFile(path, 2); // adSaveCreateOverWrite
    stream.Close();
  }

  function readText(path) {
    var f = fso();
    if (!f.FileExists(path)) return null;
    var stream = new ActiveXObject("ADODB.Stream");
    stream.Type = 2;
    stream.Charset = "utf-8";
    stream.Open();
    stream.LoadFromFile(path);
    var text = stream.ReadText();
    stream.Close();
    return text;
  }

  function listFiles(dir) {
    var f = fso();
    var out = [];
    if (!f.FolderExists(dir)) return out;
    var folder = f.GetFolder(dir);
    var en = new Enumerator(folder.files);
    for (; !en.atEnd(); en.moveNext()) {
      out.push(en.item().Name);
    }
    return out;
  }

  // ----------------------------------------------------------------- //
  // JSON parse (IE11 provides JSON native; fallback via eval)
  // ----------------------------------------------------------------- //

  function parseJson(text) {
    if (typeof JSON !== "undefined" && JSON.parse) return JSON.parse(text);
    return eval("(" + text + ")");
  }

  function stringifyJson(obj) {
    if (typeof JSON !== "undefined" && JSON.stringify) return JSON.stringify(obj);
    throw new Error("JSON.stringify unavailable");
  }

  // ----------------------------------------------------------------- //
  // HTTP with ETag
  // ----------------------------------------------------------------- //

  function fetchWithEtag(url, prevEtag) {
    var xhr;
    try { xhr = new ActiveXObject("MSXML2.XMLHTTP.6.0"); }
    catch (e) { xhr = new ActiveXObject("Microsoft.XMLHTTP"); }
    xhr.open("GET", url, false);
    if (prevEtag) {
      try { xhr.setRequestHeader("If-None-Match", prevEtag); } catch (e) { /* ignore */ }
    }
    try { xhr.send(); } catch (e) {
      return { status: 0, body: "", etag: prevEtag || "" };
    }
    var etag = "";
    try { etag = xhr.getResponseHeader("ETag") || ""; } catch (e) { etag = ""; }
    return { status: xhr.status, body: xhr.responseText || "", etag: etag };
  }

  // ----------------------------------------------------------------- //
  // Config persistence
  // ----------------------------------------------------------------- //

  function loadConfig() {
    var text = readText(CONFIG_FILE);
    if (!text) return null;
    try { return parseJson(text); } catch (e) { return null; }
  }

  function saveConfig(cfg) {
    writeText(CONFIG_FILE, stringifyJson(cfg));
  }

  // ----------------------------------------------------------------- //
  // ETag persistence (per-app)
  // ----------------------------------------------------------------- //

  function etagPath(app) {
    return DATA_DIR + "\\" + app + "\\etag.txt";
  }

  function loadEtag(app) {
    return readText(etagPath(app)) || "";
  }

  function saveEtag(app, etag) {
    writeText(etagPath(app), etag || "");
  }

  // ----------------------------------------------------------------- //
  // Librarian Mode: month-partitioned storage
  // ----------------------------------------------------------------- //

  function monthOf(item) {
    var d = item.published_at || item.generated_at || "";
    if (d && d.length >= 7) return d.substring(0, 7); // YYYY-MM
    return "unknown";
  }

  function partitionPath(app, month) {
    return DATA_DIR + "\\" + app + "\\items-" + month + ".json";
  }

  function loadPartition(app, month) {
    var text = readText(partitionPath(app, month));
    if (!text) return { items: [] };
    try { return parseJson(text); } catch (e) { return { items: [] }; }
  }

  function savePartition(app, month, doc) {
    writeText(partitionPath(app, month), stringifyJson(doc));
  }

  function loadAllLocal(app) {
    var dir = DATA_DIR + "\\" + app;
    var files = listFiles(dir);
    var all = [];
    var seenIds = {};
    for (var i = 0; i < files.length; i++) {
      var name = files[i];
      if (name.indexOf("items-") !== 0 || name.substring(name.length - 5) !== ".json") continue;
      var text = readText(dir + "\\" + name);
      if (!text) continue;
      var doc;
      try { doc = parseJson(text); } catch (e) { continue; }
      var items = doc.items || [];
      for (var j = 0; j < items.length; j++) {
        var it = items[j];
        if (it && it.id && !seenIds[it.id]) {
          seenIds[it.id] = true;
          all.push(it);
        }
      }
    }
    return all;
  }

  // ----------------------------------------------------------------- //
  // Merge remote items into local partitions (upsert by id+checksum)
  // ----------------------------------------------------------------- //

  function mergeAndPersist(app, remoteItems) {
    var byMonth = {};
    for (var i = 0; i < remoteItems.length; i++) {
      var it = remoteItems[i];
      var m = monthOf(it);
      (byMonth[m] = byMonth[m] || []).push(it);
    }

    var allIds = {};
    var all = [];

    for (var month in byMonth) {
      if (!byMonth.hasOwnProperty(month)) continue;
      var existing = loadPartition(app, month);
      var existingItems = existing.items || [];
      var idx = {};
      for (var k = 0; k < existingItems.length; k++) {
        var e0 = existingItems[k];
        if (e0 && e0.id) idx[e0.id] = e0;
      }
      var incoming = byMonth[month];
      for (var n = 0; n < incoming.length; n++) {
        var it2 = incoming[n];
        if (!it2 || !it2.id) continue;
        var prev = idx[it2.id];
        // Upsert: prefer incoming if checksum differs or no previous
        if (!prev || (it2.checksum && prev.checksum !== it2.checksum)) {
          idx[it2.id] = it2;
        }
      }
      var merged = [];
      for (var mid in idx) {
        if (idx.hasOwnProperty(mid)) merged.push(idx[mid]);
      }
      savePartition(app, month, { schema_version: "1.0", updated_at: new Date().toISOString(), items: merged });
    }

    // Reload full dataset for UI
    return loadAllLocal(app);
  }

  // ----------------------------------------------------------------- //
  // Public API
  // ----------------------------------------------------------------- //

  return {
    loadConfig: loadConfig,
    saveConfig: saveConfig,
    loadEtag: loadEtag,
    saveEtag: saveEtag,
    fetchWithEtag: fetchWithEtag,
    parseJson: parseJson,
    loadAllLocal: loadAllLocal,
    mergeAndPersist: mergeAndPersist
  };
})();
