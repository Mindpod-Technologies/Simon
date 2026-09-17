/* SIMON web UI — vanilla JS, no dependencies. */
(function () {
  "use strict";

  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var micBtn = document.getElementById("mic");
  var attachBtn = document.getElementById("attach");
  var fileInput = document.getElementById("file-input");
  var transcript = document.getElementById("transcript");
  var orb = document.getElementById("orb");
  var statusEl = document.getElementById("status");
  var docsPanel = document.getElementById("docs-panel");
  var docsToggle = document.getElementById("docs-toggle");
  var uploadsList = document.getElementById("uploads-list");
  var artifactsList = document.getElementById("artifacts-list");
  var jobsList = document.getElementById("jobs-list");
  var schedulesList = document.getElementById("schedules-list");
  var pluginsList = document.getElementById("plugins-list");
  var taskSelect = document.getElementById("task-select");
  var newTaskBtn = document.getElementById("new-task");
  var previewModal = document.getElementById("preview-modal");
  var previewTitle = document.getElementById("preview-title");
  var previewBody = document.getElementById("preview-body");
  var previewDownload = document.getElementById("preview-download");
  var previewClose = document.getElementById("preview-close");

  var sessionId = null;
  var busy = false;

  var IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".svg"];
  var TEXT_EXTS = [".md", ".txt", ".csv", ".json", ".log", ".py",
                   ".yaml", ".yml", ".html", ".sh"];

  function setStatus(text) { statusEl.textContent = text; }

  function orbThinking(on) {
    orb.classList.toggle("active", on);
    if (on) orb.classList.remove("speaking");
  }
  function orbSpeaking(on) {
    orb.classList.toggle("speaking", on);
    if (on) orb.classList.remove("active");
  }

  function extOf(name) {
    var i = name.lastIndexOf(".");
    return i >= 0 ? name.slice(i).toLowerCase() : "";
  }

  function artifactUrl(relpath, download) {
    var url = "/api/artifacts/" + relpath.split("/").map(encodeURIComponent).join("/");
    var params = [];
    if (sessionId) params.push("session_id=" + encodeURIComponent(sessionId));
    if (download) params.push("download=1");
    return params.length ? url + "?" + params.join("&") : url;
  }

  /* ---- message rendering (with inline artifacts) ---- */

  var ARTIFACT_RE = /\[artifact:([^\]\s]+)\]/g;

  function appendMarkdown(container, text) {
    /* Minimal, DOM-safe markdown: bullets, **bold**, *italic*, `code`.
       Never innerHTML — reply text is untrusted model output. */
    text.split("\n").forEach(function (line, idx) {
      if (idx) container.appendChild(document.createElement("br"));
      var bullet = /^\s*[-*+]\s+/.exec(line);
      if (bullet) {
        container.appendChild(document.createTextNode("• "));
        line = line.slice(bullet[0].length);
      }
      var re = /\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]*)`/g;
      var last = 0, m;
      while ((m = re.exec(line)) !== null) {
        if (m.index > last) {
          container.appendChild(
            document.createTextNode(line.slice(last, m.index)));
        }
        var el;
        if (m[1] !== undefined) {
          el = document.createElement("strong"); el.textContent = m[1];
        } else if (m[2] !== undefined) {
          el = document.createElement("em"); el.textContent = m[2];
        } else {
          el = document.createElement("code"); el.textContent = m[3];
        }
        container.appendChild(el);
        last = m.index + m[0].length;
      }
      if (last < line.length) {
        container.appendChild(document.createTextNode(line.slice(last)));
      }
    });
  }

  function appendRichText(container, text) {
    /* Render text with [artifact:path] markers as images / cards. */
    var last = 0;
    var match;
    ARTIFACT_RE.lastIndex = 0;
    var found = false;
    while ((match = ARTIFACT_RE.exec(text)) !== null) {
      found = true;
      var before = text.slice(last, match.index).trim();
      if (before) {
        appendMarkdown(container, before);
        container.appendChild(document.createElement("br"));
      }
      container.appendChild(artifactNode(match[1]));
      last = match.index + match[0].length;
    }
    var tail = text.slice(last).trim();
    if (tail || !found) {
      appendMarkdown(container, tail);
    }
  }

  function artifactNode(relpath) {
    var name = relpath.split("/").pop();
    var ext = extOf(name);
    if (IMAGE_EXTS.indexOf(ext) >= 0) {
      var img = document.createElement("img");
      img.className = "artifact-img";
      img.src = artifactUrl(relpath);
      img.alt = name;
      img.title = name + " — click to enlarge";
      img.addEventListener("click", function () { openPreview(relpath); });
      return img;
    }
    var card = document.createElement("div");
    card.className = "artifact-card";
    var icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = ext === ".docx" || ext === ".pdf" ? "\u{1F4C4}" : "\u{1F4CE}";
    var fname = document.createElement("span");
    fname.className = "fname";
    fname.textContent = name;
    card.appendChild(icon);
    card.appendChild(fname);
    card.title = "Preview " + name;
    card.addEventListener("click", function () { openPreview(relpath); });
    return card;
  }

  function addMsg(who, text, cssClass) {
    var div = document.createElement("div");
    div.className = "msg " + (cssClass || (who === "You" ? "user" : "simon"));
    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who.toUpperCase();
    div.appendChild(label);
    var body = document.createElement("span");
    div.appendChild(body);
    setMsgText(div, text);
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    return div;
  }

  function setMsgText(div, text) {
    /* Plain during streaming (fast), rich-rendered on completion. */
    var body = div.children[1];
    body.textContent = "";
    body.appendChild(document.createTextNode(text));
  }

  function renderMsgRich(div, text) {
    var body = div.children[1];
    body.textContent = "";
    appendRichText(body, text);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function systemMsg(text) {
    return addMsg("System", text, "system");
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    attachBtn.disabled = on;
    input.disabled = on;
    if (!on) input.focus();
  }

  /* ---- speech queue: replies wait their turn ----
     If Simon is still speaking when the next reply arrives, the new audio
     queues instead of talking over him. */

  var speechQueue = [];
  var speechPlaying = false;

  function playTts(text) {
    // Strip artifact markers — Simon shouldn't read file paths aloud.
    text = text.replace(ARTIFACT_RE, "").trim();
    if (!text) return;
    speechQueue.push(text);
    if (!speechPlaying) drainSpeech();
  }

  function drainSpeech() {
    var text = speechQueue.shift();
    if (text === undefined) {
      speechPlaying = false;
      orbSpeaking(false);
      setStatus("ONLINE");
      return;
    }
    speechPlaying = true;
    orbSpeaking(true);
    setStatus("SPEAKING");
    fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    })
      .then(function (res) {
        if (!res.ok) throw new Error("tts failed");
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        audio.onended = audio.onerror = function () {
          URL.revokeObjectURL(url);
          drainSpeech();
        };
        audio.play().catch(function () {
          drainSpeech();
        });
      })
      .catch(function () {
        drainSpeech();
      });
  }

  /* ---- files panel ---- */

  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  var KIND_ICON = {
    chart: "\u{1F4CA}", document: "\u{1F4C4}", code: "\u{1F4BB}",
    data: "\u{1F4C8}", other: "\u{1F4CE}"
  };

  function docItem(name, size, actions, badge) {
    var div = document.createElement("div");
    div.className = "doc-item";
    var nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = (badge ? badge + " " : "") + name;
    var meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = fmtSize(size);
    div.appendChild(nameEl);
    div.appendChild(meta);
    if (actions && actions.length) {
      var row = document.createElement("div");
      row.className = "actions";
      actions.forEach(function (a) { row.appendChild(a); });
      div.appendChild(row);
    }
    return div;
  }

  function actionButton(label, onClick) {
    var b = document.createElement("button");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function actionLink(label, href) {
    var a = document.createElement("a");
    a.textContent = label;
    a.href = href;
    return a;
  }

  function refreshDocuments() {
    var uploadsUrl = "/api/uploads";
    fetch(uploadsUrl)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        uploadsList.innerHTML = "";
        if (!data.files || !data.files.length) {
          uploadsList.innerHTML =
            '<span class="docs-empty">Nothing uploaded yet.</span>';
          return;
        }
        data.files.forEach(function (f) {
          uploadsList.appendChild(docItem(f.name, f.size, [
            actionButton("REVIEW", function () { askForReview(f.name); })
          ]));
        });
      })
      .catch(function () { /* panel is best-effort */ });
    var artUrl = "/api/artifacts" +
      (sessionId ? "?session_id=" + encodeURIComponent(sessionId) : "");
    fetch(artUrl)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        artifactsList.innerHTML = "";
        if (!data.files || !data.files.length) {
          artifactsList.innerHTML =
            '<span class="docs-empty">Nothing created yet.</span>';
          return;
        }
        data.files.forEach(function (f) {
          artifactsList.appendChild(docItem(f.path, f.size, [
            actionButton("PREVIEW", function () { openPreview(f.path); }),
            actionLink("SAVE", artifactUrl(f.path, true))
          ], KIND_ICON[f.kind] || KIND_ICON.other));
        });
      })
      .catch(function () { /* panel is best-effort */ });
  }

  function refreshActivity() {
    fetch("/api/jobs")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        jobsList.innerHTML = "";
        if (!data.jobs || !data.jobs.length) {
          jobsList.innerHTML =
            '<span class="docs-empty">No jobs yet.</span>';
          return;
        }
        data.jobs.forEach(function (j) {
          var div = document.createElement("div");
          div.className = "doc-item";
          var nameEl = document.createElement("div");
          nameEl.className = "name";
          nameEl.textContent = "#" + j.id + " " + j.description;
          var badge = document.createElement("span");
          badge.className = "job-status " + j.status;
          badge.textContent = j.status.toUpperCase();
          div.appendChild(nameEl);
          div.appendChild(badge);
          jobsList.appendChild(div);
        });
      })
      .catch(function () { /* best-effort */ });
    fetch("/api/schedules")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        schedulesList.innerHTML = "";
        if (!data.schedules || !data.schedules.length) {
          schedulesList.innerHTML =
            '<span class="docs-empty">No recurring automations yet.</span>';
          return;
        }
        data.schedules.forEach(function (s) {
          var div = document.createElement("div");
          div.className = "doc-item";
          var nameEl = document.createElement("div");
          nameEl.className = "name";
          nameEl.textContent = "#" + s.id + " " + s.description;
          var when = document.createElement("div");
          when.className = "sched-when";
          when.textContent = "⏱ " + s.when;
          div.appendChild(nameEl);
          div.appendChild(when);
          schedulesList.appendChild(div);
        });
      })
      .catch(function () { /* best-effort */ });
    fetch("/api/plugins")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        pluginsList.innerHTML = "";
        var rows = (data.plugins || []).map(function (p) {
          return { name: p.name.replace(/\.py$/, ""),
                   detail: p.tools.length ? p.tools.join(", ")
                                          : "no tools registered" };
        });
        (data.mcp_servers || []).forEach(function (s) {
          rows.push({ name: s, detail: "MCP connector" });
        });
        if (!rows.length) {
          pluginsList.innerHTML =
            '<span class="docs-empty">No plugins installed. Drop a .py ' +
            'file into plugins/ or an MCP server into mcp.json.</span>';
          return;
        }
        rows.forEach(function (r) {
          var div = document.createElement("div");
          div.className = "doc-item";
          var nameEl = document.createElement("div");
          nameEl.className = "name";
          nameEl.textContent = r.name;
          var detail = document.createElement("div");
          detail.className = "sched-when";
          detail.textContent = r.detail;
          div.appendChild(nameEl);
          div.appendChild(detail);
          pluginsList.appendChild(div);
        });
      })
      .catch(function () { /* best-effort */ });
  }

  function askForReview(name) {
    if (busy) return;
    input.value = "Please review the uploaded document \"" + name +
      "\" and give me your recommendations and any changes you would make.";
    input.focus();
    if (window.innerWidth <= 900) docsPanel.classList.remove("open");
  }

  function uploadFile(file) {
    if (!file || busy) return;
    setBusy(true);
    setStatus("INGESTING");
    systemMsg("Uploading " + file.name + "…");
    var fd = new FormData();
    fd.append("file", file, file.name);
    fetch("/api/upload", { method: "POST", body: fd })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) {
          systemMsg("Upload failed: " + data.error);
        } else {
          systemMsg("Uploaded " + data.name + " — " +
            data.chars.toLocaleString() + " chars ingested into Simon's memory" +
            (data.chunks ? " (" + data.chunks + " passages)" : "") +
            ". Click REVIEW in the Files panel, or ask Simon about it.");
        }
        refreshDocuments();
      })
      .catch(function () { systemMsg("Upload failed — connection error."); })
      .then(function () {
        setBusy(false);
        setStatus("ONLINE");
      });
  }

  attachBtn.addEventListener("click", function () { fileInput.click(); });
  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
    fileInput.value = "";
  });

  ["dragover", "dragleave", "drop"].forEach(function (evName) {
    docsPanel.addEventListener(evName, function (e) {
      e.preventDefault();
      docsPanel.classList.toggle("dragover", evName === "dragover");
      if (evName === "drop" && e.dataTransfer.files.length) {
        uploadFile(e.dataTransfer.files[0]);
      }
    });
  });

  docsToggle.addEventListener("click", function () {
    docsPanel.classList.toggle("open");
  });

  /* ---- artifact preview modal ---- */

  function openPreview(relpath) {
    var name = relpath.split("/").pop();
    var ext = extOf(name);
    previewTitle.textContent = relpath;
    previewDownload.href = artifactUrl(relpath, true);
    previewBody.textContent = "";
    if (IMAGE_EXTS.indexOf(ext) >= 0) {
      var img = document.createElement("img");
      img.src = artifactUrl(relpath);
      img.alt = name;
      previewBody.appendChild(img);
    } else if (ext === ".pdf") {
      var frame = document.createElement("iframe");
      frame.src = artifactUrl(relpath);
      previewBody.appendChild(frame);
    } else if (TEXT_EXTS.indexOf(ext) >= 0) {
      var pre = document.createElement("pre");
      pre.textContent = "Loading…";
      previewBody.appendChild(pre);
      fetch(artifactUrl(relpath))
        .then(function (res) { return res.text(); })
        .then(function (text) { pre.textContent = text; })
        .catch(function () { pre.textContent = "Preview unavailable."; });
    } else {
      var pre2 = document.createElement("pre");
      pre2.textContent = "No inline preview for this file type — " +
        "use DOWNLOAD instead.";
      previewBody.appendChild(pre2);
    }
    previewModal.classList.add("open");
  }

  function closePreview() { previewModal.classList.remove("open"); }
  previewClose.addEventListener("click", closePreview);
  previewModal.addEventListener("click", function (e) {
    if (e.target === previewModal) closePreview();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closePreview();
  });

  /* ---- tasks ---- */

  function refreshTasks(selectSlug) {
    fetch("/api/tasks")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var current = taskSelect.value;
        taskSelect.innerHTML = '<option value="">Main session</option>';
        (data.tasks || []).forEach(function (t) {
          var opt = document.createElement("option");
          opt.value = t.slug;
          opt.textContent = t.name + (t.files ? " (" + t.files + ")" : "");
          opt.dataset.session = t.session_id;
          taskSelect.appendChild(opt);
        });
        taskSelect.value = selectSlug !== undefined ? selectSlug : current;
      })
      .catch(function () { /* best-effort */ });
  }

  function loadSession(session) {
    transcript.innerHTML = "";
    var url = "/api/history" +
      (session ? "?session_id=" + encodeURIComponent(session) : "");
    fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        sessionId = data.session_id;
        var msgs = (data.messages || []).filter(function (m) {
          return m.role === "user" || m.role === "assistant";
        });
        if (!msgs.length) {
          addMsg("Simon", "Good day, sir. Simon online and at your service.");
          return;
        }
        msgs.slice(-30).forEach(function (m) {
          var div = addMsg(m.role === "user" ? "You" : "Simon", m.content);
          if (m.role === "assistant") renderMsgRich(div, m.content);
        });
      })
      .catch(function () {
        addMsg("Simon", "Good day, sir. Simon online and at your service.");
      });
  }

  taskSelect.addEventListener("change", function () {
    var opt = taskSelect.selectedOptions[0];
    var session = opt && opt.dataset.session ? opt.dataset.session : null;
    loadSession(session);
    refreshDocuments();
  });

  newTaskBtn.addEventListener("click", function () {
    var name = window.prompt("Name the new task workspace:", "");
    if (!name || !name.trim()) return;
    fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() })
    })
      .then(function (res) { return res.json(); })
      .then(function (task) {
        if (task.error) {
          systemMsg("Task creation failed: " + task.error);
          return;
        }
        refreshTasks(task.slug);
        loadSession(task.session_id);
        refreshDocuments();
        systemMsg("Task workspace \"" + task.name +
          "\" ready — this conversation and its files are now scoped to it.");
      })
      .catch(function () { systemMsg("Task creation failed."); });
  });

  /* ---- chat ---- */

  function send() {
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    addMsg("You", text);
    setBusy(true);
    orbThinking(true);
    setStatus("THINKING");

    var replyDiv = null;
    var full = "";

    var body = { text: text };
    if (sessionId) body.session_id = sessionId;

    // SSE over POST: parse the stream manually.
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (res) {
        if (!res.ok || !res.body) throw new Error("chat failed");
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";

        function handleEvent(evName, data) {
          if (evName === "chunk") {
            if (!replyDiv) {
              orbThinking(false);
              replyDiv = addMsg("Simon", "");
            }
            full += data;
            setMsgText(replyDiv, full);
            transcript.scrollTop = transcript.scrollHeight;
          } else if (evName === "done") {
            full = data;
            if (replyDiv) renderMsgRich(replyDiv, full);
            else {
              var div = addMsg("Simon", "");
              renderMsgRich(div, full);
            }
          } else if (evName === "session") {
            sessionId = data;
          }
        }

        function pump() {
          return reader.read().then(function (r) {
            if (r.done) return;
            buffer += decoder.decode(r.value, { stream: true });
            buffer = buffer.replace(/\r\n/g, "\n"); // tolerate CRLF SSE streams
            var parts = buffer.split("\n\n");
            buffer = parts.pop();
            parts.forEach(function (block) {
              var evName = "message";
              var dataLines = [];
              block.split("\n").forEach(function (line) {
                if (line.indexOf("event:") === 0) {
                  evName = line.slice(6).trim();
                } else if (line.indexOf("data:") === 0) {
                  dataLines.push(line.slice(5).replace(/^ /, ""));
                }
              });
              if (dataLines.length) handleEvent(evName, dataLines.join("\n"));
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () {
        orbThinking(false);
        addMsg("Simon",
          "I do apologise, sir — I seem unable to reach my own systems.");
      })
      .then(function () {
        orbThinking(false);
        setBusy(false);
        setStatus("ONLINE");
        refreshDocuments(); // Simon may have created an artifact this turn
        refreshActivity();  // …or queued a job / created an automation
        if (full) playTts(full);
      });
  }

  /* ---- mic: MediaRecorder → /api/stt → fill input ---- */
  var recorder = null;
  var chunks = [];

  function stopRecording() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  micBtn.addEventListener("click", function () {
    if (recorder && recorder.state === "recording") {
      stopRecording();
      return;
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setStatus("MIC UNAVAILABLE");
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(function (stream) {
        chunks = [];
        recorder = new MediaRecorder(stream);
        recorder.ondataavailable = function (e) {
          if (e.data.size) chunks.push(e.data);
        };
        recorder.onstop = function () {
          micBtn.classList.remove("recording");
          setStatus("TRANSCRIBING");
          stream.getTracks().forEach(function (t) { t.stop(); });
          var blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          var fd = new FormData();
          fd.append("file", blob, "voice.webm");
          fetch("/api/stt", { method: "POST", body: fd })
            .then(function (res) { return res.json(); })
            .then(function (data) {
              if (data.text) {
                input.value = data.text;
                input.focus();
              }
              setStatus("ONLINE");
            })
            .catch(function () { setStatus("ONLINE"); });
        };
        recorder.start();
        micBtn.classList.add("recording");
        setStatus("LISTENING");
      })
      .catch(function () { setStatus("MIC DENIED"); });
  });

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") send();
  });

  refreshTasks();
  loadSession(null);
  refreshDocuments();
  refreshActivity();
  setInterval(refreshActivity, 15000); // jobs/schedules update live
})();
