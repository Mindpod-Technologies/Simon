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
  var docsList = document.getElementById("docs-list");
  var jobsList = document.getElementById("jobs-list");
  var schedulesList = document.getElementById("schedules-list");

  var sessionId = null;
  var busy = false;

  function setStatus(text) { statusEl.textContent = text; }

  function orbThinking(on) {
    orb.classList.toggle("active", on);
    if (on) orb.classList.remove("speaking");
  }
  function orbSpeaking(on) {
    orb.classList.toggle("speaking", on);
    if (on) orb.classList.remove("active");
  }

  function addMsg(who, text, cssClass) {
    var div = document.createElement("div");
    div.className = "msg " + (cssClass || (who === "You" ? "user" : "simon"));
    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who.toUpperCase();
    div.appendChild(label);
    div.appendChild(document.createTextNode(text));
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    return div;
  }

  function systemMsg(text) {
    var div = addMsg("System", text, "system");
    return div;
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    attachBtn.disabled = on;
    input.disabled = on;
    if (!on) input.focus();
  }

  function playTts(text) {
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
          orbSpeaking(false);
          setStatus("ONLINE");
        };
        audio.play().catch(function () {
          orbSpeaking(false);
          setStatus("ONLINE");
        });
      })
      .catch(function () {
        orbSpeaking(false);
        setStatus("ONLINE");
      });
  }

  /* ---- documents panel ---- */

  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function docItem(name, size, actions) {
    var div = document.createElement("div");
    div.className = "doc-item";
    var nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = name;
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
    fetch("/api/uploads")
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
    fetch("/api/documents")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        docsList.innerHTML = "";
        if (!data.files || !data.files.length) {
          docsList.innerHTML =
            '<span class="docs-empty">Nothing created yet.</span>';
          return;
        }
        data.files.forEach(function (f) {
          docsList.appendChild(docItem(f.name, f.size, [
            actionLink("DOWNLOAD",
              "/api/documents/" + encodeURIComponent(f.name))
          ]));
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
            ". Click REVIEW in the Documents panel, or ask Simon about it.");
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
            replyDiv.lastChild.textContent = full;
            transcript.scrollTop = transcript.scrollHeight;
          } else if (evName === "done") {
            full = data;
            if (replyDiv) replyDiv.lastChild.textContent = full;
            else addMsg("Simon", full);
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
        refreshDocuments(); // Simon may have created a document this turn
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

  refreshDocuments();
  refreshActivity();
  setInterval(refreshActivity, 15000); // jobs/schedules update live
  addMsg("Simon", "Good day, sir. Simon online and at your service.");
})();
