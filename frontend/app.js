/**
 * Frontend application logic: session lifecycle, conversation rendering,
 * voice/typed turn handling, post editing, TTS via the browser's
 * SpeechSynthesis API, undo/redo, export, and privacy-flag display.
 */

(() => {
  const API = {
    session: "/api/session",
    message: "/api/message",
    audio: "/api/audio",
    generate: "/api/generate",
    revise: "/api/revise",
    manualEdit: "/api/manual-edit",
    undo: "/api/undo",
    redo: "/api/redo",
    export: "/api/export",
    clear: "/api/clear",
  };

  const el = {
    transcript: document.getElementById("transcript"),
    typedInput: document.getElementById("typed-input"),
    sendBtn: document.getElementById("send-btn"),
    micBtn: document.getElementById("mic-btn"),
    micLabel: document.getElementById("mic-label"),
    micStatus: document.getElementById("mic-status"),
    waveform: document.getElementById("waveform"),
    postCard: document.getElementById("post-card"),
    postTextarea: document.getElementById("post-textarea"),
    postStatus: document.getElementById("post-status"),
    privacyFlags: document.getElementById("privacy-flags"),
    privacyFlagsList: document.getElementById("privacy-flags-list"),
    listenBtn: document.getElementById("listen-btn"),
    stopListenBtn: document.getElementById("stop-listen-btn"),
    undoBtn: document.getElementById("undo-btn"),
    redoBtn: document.getElementById("redo-btn"),
    regenerateBtn: document.getElementById("regenerate-btn"),
    restartBtn: document.getElementById("restart-btn"),
    copyBtn: document.getElementById("copy-btn"),
    saveTxtBtn: document.getElementById("save-txt-btn"),
    saveMdBtn: document.getElementById("save-md-btn"),
    clearSessionBtn: document.getElementById("clear-session-btn"),
  };

  let sessionId = null;
  let manualEditTimer = null;
  let lastKnownPostText = "";

  // ------------------------------------------------------------------- //
  // Transcript rendering
  // ------------------------------------------------------------------- //

  function appendTurn(role, content, viaVoice) {
    if (!content) return;
    const div = document.createElement("div");
    div.className = `turn ${role}`;
    div.textContent = content;
    if (viaVoice) {
      const tag = document.createElement("span");
      tag.className = "voice-tag";
      tag.textContent = "(spoken)";
      div.appendChild(tag);
    }
    el.transcript.appendChild(div);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  // ------------------------------------------------------------------- //
  // Session lifecycle
  // ------------------------------------------------------------------- //

  async function initSession() {
    const res = await fetch(API.session, { method: "POST" });
    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem("cb_session_id", sessionId);
    appendTurn("assistant", data.welcome_message, false);
    if (data.first_question) appendTurn("assistant", data.first_question, false);
    restoreDraftFromLocalStorage();
  }

  async function clearSession() {
    if (!sessionId) return;
    await fetch(API.clear, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    localStorage.removeItem(`cb_draft_${sessionId}`);
    localStorage.removeItem("cb_session_id");
    el.transcript.innerHTML = "";
    el.postTextarea.value = "";
    setPostVisible(false);
    setPostStatus("");
    await initSession();
  }

  // ------------------------------------------------------------------- //
  // Turn handling (shared response shape for typed + voice)
  // ------------------------------------------------------------------- //

  function handleTurnResponse(data) {
    if (data.error) {
      setPostStatus(data.error);
    }
    if (data.assistant_message) appendTurn("assistant", data.assistant_message, false);
    if (data.next_question) appendTurn("assistant", data.next_question, false);

    if (data.post_text) {
      setPostVisible(true);
      el.postTextarea.value = data.post_text;
      lastKnownPostText = data.post_text;
      saveDraftToLocalStorage(data.post_text);
    }
    if (data.privacy_flags) renderPrivacyFlags(data.privacy_flags);
    if (data.speak) speakText(data.speak);
  }

  async function sendTyped() {
    const text = el.typedInput.value.trim();
    if (!text || !sessionId) return;
    appendTurn("user", text, false);
    el.typedInput.value = "";
    autoGrow(el.typedInput);
    el.sendBtn.disabled = true;
    try {
      const res = await fetch(API.message, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text, via_voice: false }),
      });
      const data = await res.json();
      handleTurnResponse(data);
    } catch (err) {
      setPostStatus("Something went wrong sending that. Please try again.");
    } finally {
      el.sendBtn.disabled = false;
    }
  }

  async function sendAudio(blob) {
    if (!sessionId) return;
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("file", blob, "turn.webm");

    setMicState("processing", "Transcribing...");
    try {
      const res = await fetch(API.audio, { method: "POST", body: form });
      const data = await res.json();
      if (!data.transcript_reliable) {
        setMicState("idle", "");
        if (data.assistant_message) appendTurn("assistant", data.assistant_message, false);
        return;
      }
      appendTurn("user", data.clean_transcript, true);
      setMicState("processing", "Thinking...");
      handleTurnResponse(data);
    } catch (err) {
      setPostStatus("I couldn't process that recording. Your previous responses are still safe.");
    } finally {
      setMicState("idle", "");
    }
  }

  // ------------------------------------------------------------------- //
  // Microphone control
  // ------------------------------------------------------------------- //

  const waveCtx = el.waveform.getContext("2d");
  function drawLevel(level) {
    const w = el.waveform.width, h = el.waveform.height;
    waveCtx.clearRect(0, 0, w, h);
    waveCtx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--color-accent") || "#6F9AAE";
    const barCount = 24;
    for (let i = 0; i < barCount; i++) {
      const jitter = Math.sin(i * 1.7 + Date.now() / 120) * 0.15;
      const barLevel = Math.max(0.04, Math.min(1, level + jitter));
      const barH = barLevel * h;
      const x = (w / barCount) * i + 2;
      waveCtx.fillRect(x, (h - barH) / 2, (w / barCount) - 4, barH);
    }
  }

  function setMicState(state, statusText) {
    el.micBtn.dataset.state = state;
    el.micBtn.setAttribute("aria-pressed", state === "listening" ? "true" : "false");
    el.micBtn.disabled = state === "processing";
    const labels = { idle: "Speak", listening: "Stop", processing: "Please wait...", disabled: "Unavailable" };
    el.micLabel.textContent = labels[state] || "Speak";
    el.micStatus.textContent = statusText || "";
    if (state !== "listening") drawLevel(0);
  }

  let recorder = null;
  function initRecorder() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setMicState("disabled", "Voice input isn't available in this browser -- you can still type.");
      el.micBtn.disabled = true;
      return;
    }
    recorder = new VoiceRecorder({
      onStateChange: (state) => {
        if (state === RecorderState.LISTENING) setMicState("listening", "Listening...");
        else if (state === RecorderState.STOPPING) setMicState("processing", "Finishing up...");
        else if (state === RecorderState.REQUESTING_PERMISSION) setMicState("processing", "Requesting microphone access...");
        else if (state === RecorderState.ERROR) setMicState("idle", "");
      },
      onLevel: drawLevel,
      onAudioReady: (blob) => sendAudio(blob),
      onError: (message) => setPostStatus(message),
    });
  }

  el.micBtn.addEventListener("click", () => {
    if (!recorder) return;
    if (recorder.state === RecorderState.LISTENING) {
      recorder.stop();
    } else if (recorder.state === RecorderState.IDLE) {
      recorder.start();
    }
  });

  // ------------------------------------------------------------------- //
  // Post editor
  // ------------------------------------------------------------------- //

  function setPostVisible(visible) {
    el.postCard.classList.toggle("visible", visible);
  }

  function renderPrivacyFlags(flags) {
    if (!flags || flags.length === 0) {
      el.privacyFlags.hidden = true;
      el.privacyFlagsList.innerHTML = "";
      return;
    }
    el.privacyFlagsList.innerHTML = "";
    flags.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f;
      el.privacyFlagsList.appendChild(li);
    });
    el.privacyFlags.hidden = false;
  }

  function setPostStatus(text) {
    el.postStatus.textContent = text;
    if (text) setTimeout(() => { if (el.postStatus.textContent === text) el.postStatus.textContent = ""; }, 6000);
  }

  el.postTextarea.addEventListener("input", () => {
    saveDraftToLocalStorage(el.postTextarea.value);
    clearTimeout(manualEditTimer);
    manualEditTimer = setTimeout(async () => {
      if (!sessionId) return;
      const text = el.postTextarea.value;
      if (text === lastKnownPostText) return;
      lastKnownPostText = text;
      await fetch(API.manualEdit, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text }),
      });
      setPostStatus("Saved your edit.");
    }, 900);
  });

  el.regenerateBtn.addEventListener("click", async () => {
    setPostStatus("Generating your post...");
    const res = await fetch(API.generate, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) { setPostStatus("Couldn't generate the draft just now. Your responses have been preserved."); return; }
    const data = await res.json();
    setPostVisible(true);
    el.postTextarea.value = data.post_text;
    lastKnownPostText = data.post_text;
    saveDraftToLocalStorage(data.post_text);
    renderPrivacyFlags(data.privacy_flags);
    setPostStatus("New draft ready.");
  });

  el.undoBtn.addEventListener("click", async () => {
    const res = await fetch(API.undo, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    if (data.post_text != null) {
      el.postTextarea.value = data.post_text;
      lastKnownPostText = data.post_text;
      saveDraftToLocalStorage(data.post_text);
    }
  });

  el.redoBtn.addEventListener("click", async () => {
    const res = await fetch(API.redo, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    if (data.post_text != null) {
      el.postTextarea.value = data.post_text;
      lastKnownPostText = data.post_text;
      saveDraftToLocalStorage(data.post_text);
    }
  });

  el.restartBtn.addEventListener("click", () => {
    if (confirm("Start over? This will clear your answers and current draft in this session.")) {
      clearSession();
    }
  });

  el.copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(el.postTextarea.value);
      setPostStatus("Copied to clipboard.");
    } catch {
      setPostStatus("Couldn't copy automatically -- please select and copy the text manually.");
    }
  });

  function downloadAs(format) {
    const blob = new Blob([el.postTextarea.value], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const date = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `caringbridge_first_post_${date}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
  el.saveTxtBtn.addEventListener("click", () => downloadAs("txt"));
  el.saveMdBtn.addEventListener("click", () => downloadAs("md"));

  // ------------------------------------------------------------------- //
  // Text-to-speech (browser SpeechSynthesis -- starts instantly, is
  // trivially interruptible, and needs no server round trip; see
  // backend/tts/tts_engine.py for why this is the preferred default)
  // ------------------------------------------------------------------- //

  function speakText(text) {
    if (!("speechSynthesis" in window) || !text) {
      setPostStatus("Audio playback isn't available right now, but your post is still ready to read and edit.");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.98;
    el.listenBtn.hidden = true;
    el.stopListenBtn.hidden = false;
    utterance.onend = () => {
      el.listenBtn.hidden = false;
      el.stopListenBtn.hidden = true;
    };
    utterance.onerror = () => {
      el.listenBtn.hidden = false;
      el.stopListenBtn.hidden = true;
      setPostStatus("Audio playback isn't available right now, but your post is still ready to read and edit.");
    };
    window.speechSynthesis.speak(utterance);
  }

  el.listenBtn.addEventListener("click", () => speakText(el.postTextarea.value));
  el.stopListenBtn.addEventListener("click", () => {
    window.speechSynthesis.cancel();
    el.listenBtn.hidden = false;
    el.stopListenBtn.hidden = true;
  });

  // ------------------------------------------------------------------- //
  // Local draft autosave (client-side only -- see MASTER PROMPT #25;
  // no sensitive content is sent anywhere new by this, it just survives
  // an accidental tab close)
  // ------------------------------------------------------------------- //

  function saveDraftToLocalStorage(text) {
    if (!sessionId) return;
    try { localStorage.setItem(`cb_draft_${sessionId}`, text); } catch { /* ignore quota errors */ }
  }
  function restoreDraftFromLocalStorage() {
    if (!sessionId) return;
    const saved = localStorage.getItem(`cb_draft_${sessionId}`);
    if (saved) {
      setPostVisible(true);
      el.postTextarea.value = saved;
      lastKnownPostText = saved;
    }
  }

  // ------------------------------------------------------------------- //
  // Misc UI wiring
  // ------------------------------------------------------------------- //

  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(140, textarea.scrollHeight) + "px";
  }
  el.typedInput.addEventListener("input", () => autoGrow(el.typedInput));
  el.typedInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendTyped();
    }
  });
  el.sendBtn.addEventListener("click", sendTyped);
  el.clearSessionBtn.addEventListener("click", () => {
    if (confirm("Clear this session? This removes your transcript, draft, and answers.")) {
      clearSession();
    }
  });

  // ------------------------------------------------------------------- //
  // Boot
  // ------------------------------------------------------------------- //

  initRecorder();
  initSession();
})();
