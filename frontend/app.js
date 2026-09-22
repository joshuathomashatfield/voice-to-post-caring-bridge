/* One shared working state: conversation, editable source facts, and draft. */
(() => {
  const $ = id => document.getElementById(id);
  const RecorderState = window.RecorderState || {IDLE:'IDLE', LISTENING:'LISTENING', REQUESTING_PERMISSION:'REQUESTING_PERMISSION', STOPPING:'STOPPING'};
  const fields = {
    person: 'Who this is about', relationship: 'Your relationship',
    reason_for_page: 'Why you’re creating the page', current_situation: 'What’s happening',
    current_status: 'How things are going', important_updates: 'Important updates',
    support_requests: 'Support you’d welcome', communication_preferences: 'How to stay in touch',
    tone: 'Tone', privacy_constraints: 'Keep out of the post', additional_notes: 'Other notes / words to check',
  };
  const listFields = new Set(['important_updates', 'support_requests', 'privacy_constraints', 'additional_notes']);
  let sessionId = null, busy = false, recorder = null, settings = {};
  let shared = {}, contextDirty = false, lastKnownPost = '', editTimer = null;
  let savePromise = Promise.resolve(), latestReply = '', voices = [];
  const storage = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch {} },
    remove(key) { try { sessionStorage.removeItem(key); } catch {} },
  };
  const speech = new (window.ConversationSpeech || class { stop() {} speak() { status("Speech playback is unavailable. You can still type or record."); } })({
    onState(active) {
      $('stop-listen-btn').hidden = !active;
      $('speech-status').textContent = active ? 'Speaking — press Speak to interrupt' : '';
    },
    onError() { status('Speech could not play. Try “Hear last reply” or choose another voice. You can keep typing.'); },
  });
  function status(message) { $('post-status').textContent = message || ''; }
  async function request(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), path.includes("session") || path.includes("state/") ? 12000 : 180000);
    try {
    const response = await fetch(path, body === undefined ? {signal:controller.signal} : {
      signal:controller.signal, method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : 'That request could not be completed. Please try again.'); error.status = response.status; throw error; }
    return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The server is taking too long. Check the Python terminal and model connection; your text is still here.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function appendTurn(role, content, viaVoice = false) {
    if (!content) return;
    const div = document.createElement('div');
    div.className = `turn ${role}`;
    const label = document.createElement('strong');
    label.className = 'turn-label';
    label.textContent = role === 'user' ? (viaVoice ? 'You · spoken' : 'You') : 'Assistant';
    const body = document.createElement('div');
    body.textContent = content;
    div.append(label, body);
    $('transcript').append(div);
    $('transcript').scrollTop = $('transcript').scrollHeight;
  }
  function speak(text, direct = false) {
    if (!settings.enable_tts || recorder?.state === RecorderState.LISTENING) return;
    speech.speak(text, {voice: voices.find(v => v.voiceURI === $('voice-select').value),
      rate: $('speech-rate').value, delay: direct ? 0 : 240});
  }
  function loadVoices() {
    const previous = $('voice-select').value;
    voices = window.speechSynthesis?.getVoices() || [];
    const english = voices.filter(v => /^en\b/i.test(v.lang));
    const available = english.length ? english : voices;
    $('voice-select').replaceChildren(new Option('Browser default', ''));
    available.forEach(v => $('voice-select').add(new Option(`${v.name}${v.localService ? ' · on device' : ' · online'}`, v.voiceURI)));
    const preferred = available.find(v => v.voiceURI === previous) ||
      available.find(v => v.localService && v.default) || available.find(v => v.localService);
    $('voice-select').value = preferred?.voiceURI || '';
  }
  try {
    loadVoices();
    window.speechSynthesis?.addEventListener('voiceschanged', () => { try { loadVoices(); } catch {} });
  } catch { status('Voice selection is unavailable. Text and microphone input are still available.'); }

  for (const [key, title] of Object.entries(fields)) {
    const label = document.createElement('label');
    label.className = 'context-field';
    label.textContent = title;
    const input = document.createElement('textarea');
    input.id = `context-${key}`;
    input.rows = listFields.has(key) ? 2 : 1;
    input.placeholder = listFields.has(key) ? 'Not shared yet · one item per line' : 'Not shared yet';
    input.addEventListener('input', () => {
      contextDirty = true;
      $('context-status').textContent = 'Unsaved corrections — saved before your next turn or draft.';
    });
    label.append(input);
    $('context-fields').append(label);
  }
  function renderShared(state) {
    if (!state) return;
    shared = state;
    if (!contextDirty && state.context) {
      for (const key of Object.keys(fields)) {
        const value = state.context[key];
        $('context-' + key).value = Array.isArray(value) ? value.join('\n') : value || '';
      }
    }
    const count = Object.values(state.context || {}).filter(v => Array.isArray(v) ? v.length : v).length;
    $('context-count').textContent = `${count} topics shared`;
    $('stage-label').textContent = ({OPENING: 'Getting started', CURRENT_SITUATION: 'What’s happening',
      HOW_DOING: 'How things are going', SUPPORT: 'Support', COMMUNICATION: 'Staying in touch',
      CLOSING_TONE: 'Your tone', READY_TO_DRAFT: 'Ready to draft', DONE: 'Reviewing'})[state.stage] || 'Getting started';
    $('draft-stale').hidden = !state.draft_needs_update;
    $('version-label').textContent = state.version ? `Version ${state.version} · ${({'manual_edit':'your edit', generated:'generated draft', revised:'AI revision'})[state.source] || state.source}` : 'No draft yet';
    updateControls();
  }
  function updateControls() {
    const recording = recorder && recorder.state !== RecorderState.IDLE;
    document.querySelectorAll('[data-action]').forEach(button => { button.disabled = busy || !!recording || !sessionId; });
    $('undo-btn').disabled ||= !shared.can_undo;
    $('redo-btn').disabled ||= !shared.can_redo;
    $('mic-btn').disabled = busy || !recorder || (!sessionId && recorder.state === RecorderState.IDLE) || recorder?.state === RecorderState.STOPPING;
    $('mic-select').disabled = busy || !!recording;
    $('test-mic-btn').disabled = busy || !recorder || !!recording;
    $('post-textarea').disabled = busy;
    document.querySelectorAll('#context-fields textarea').forEach(input => { input.disabled = busy; });
    $('transcript').setAttribute('aria-busy', String(busy));
  }
  function applyResponse(data, {talk = true, replacePost = true} = {}) {
    if (data.intent === 'RESTART') {
      $('transcript').replaceChildren();
      storage.remove(`cb_draft_${sessionId}`);
      contextDirty = false;
    }
    const reply = [data.assistant_message, data.next_question].filter(Boolean).join(' ');
    if (reply) { appendTurn('assistant', reply); latestReply = reply; }
    if (replacePost && data.post_text != null) {
      $('post-textarea').value = data.post_text;
      lastKnownPost = data.post_text;
      cacheDraft();
    }
    if (data.privacy_flags) {
      $('privacy-flags-list').replaceChildren();
      data.privacy_flags.forEach(flag => {
        const li = document.createElement('li'); li.textContent = flag; $('privacy-flags-list').append(li);
      });
      $('privacy-flags').hidden = !data.privacy_flags.length;
    }
    renderShared(data.shared_state);
    if (data.error) status(data.error);
    if (data.intent === 'STOP_AUDIO') speech.stop();
    else if (talk && (data.intent === 'READ_POST' || $('speak-replies').checked)) {
      speak(data.speak || reply || data.error);
    }
  }
  function cacheDraft() {
    if (settings.enable_autosave && sessionId) storage.set(`cb_draft_${sessionId}`, $('post-textarea').value);
  }
  async function flushDraft() {
    clearTimeout(editTimer);
    const run = async () => {
      const text = $('post-textarea').value;
      if (!sessionId || text === lastKnownPost) return;
      const data = await request('/api/manual-edit', {session_id: sessionId, text});
      lastKnownPost = text; // Only mark saved after the server acknowledges it.
      applyResponse(data, {talk:false, replacePost:false});
      status('Your edit is saved for this session.');
    };
    savePromise = savePromise.catch(() => {}).then(run);
    return savePromise;
  }
  async function flushContext() {
    if (!contextDirty) return;
    const context = {};
    for (const key of Object.keys(fields)) {
      const text = $('context-' + key).value.trim();
      context[key] = listFields.has(key) ? text.split('\n').map(s => s.trim()).filter(Boolean) : text || null;
    }
    const data = await request('/api/context', {session_id:sessionId, context});
    contextDirty = false;
    applyResponse(data, {talk:false, replacePost:false});
    $('context-status').textContent = 'Corrections saved. Update the draft when you’re ready.';
  }
  async function action(task, {flush = true} = {}) {
    if (busy) { status('Please wait for the current request to finish.'); return; }
    if (!sessionId) { status('The session is not connected. Click Retry connection.'); return; }
    busy = true; speech.stop(); updateControls();
    try {
      if (flush) { await flushDraft(); await flushContext(); }
      await task();
    } catch (error) { status(error.message || 'Something went wrong. Please try again.'); }
    finally { busy = false; updateControls(); }
  }
  async function initSession() {
    busy = true; updateControls();
    $('connection-status').textContent = 'Connecting to the local app…';
    try {
      const previous = storage.get('cb_session_id');
      let data;
      if (previous) {
        try { data = await request(`/api/state/${previous}`); sessionId = previous; }
        catch (error) {
          if (error.status !== 404) throw error;
          // Do not silently discard the cached draft when the server restarted.
          const cached = storage.get(`cb_draft_${previous}`);
          if (cached) { $('post-textarea').value = cached; status('Recovered your draft in this tab. Please review the shared facts before generating again.'); }
        }
      }
      if (!data) {
        data = await request('/api/session', {});
        sessionId = data.session_id;
        latestReply = `${data.welcome_message} ${data.first_question || ''}`;
        appendTurn('assistant', latestReply);
      } else {
        data.conversation_history.forEach(turn => appendTurn(turn.role, turn.content, turn.via_voice));
        latestReply = [...data.conversation_history].reverse().find(t => t.role === 'assistant')?.content || '';
        applyResponse(data, {talk:false});
      }
      settings = {enable_tts:data.enable_tts, enable_autosave:data.enable_autosave};
      $('speak-replies').disabled = !settings.enable_tts || !window.speechSynthesis;
      if ($('speak-replies').disabled) $('speak-replies').checked = false;
      storage.set('cb_session_id', sessionId);
      const cached = storage.get(`cb_draft_${sessionId}`);
      if (cached !== null) $('post-textarea').value = cached;
      await flushDraft();
      const state = await request(`/api/state/${sessionId}`);
      applyResponse(state, {talk:false});
      $('connection-status').textContent = 'App connected · ready to type or record';
      $('startup-error').hidden = true;
      document.documentElement.dataset.appReady = 'true';
    } catch (error) {
      $('connection-status').textContent = 'Session connection failed';
      window.showStartupError(`Could not open the session. ${error.message}`);
      status('Click Retry connection after checking that python app.py is still running.');
    }
    finally { busy = false; updateControls(); }
  }
  async function sendTyped(text = $('typed-input').value.trim()) {
    if (!text) { status('Type a message, or click Record to speak.'); return; }
    status('Sending your message…');
    await action(async () => {
      appendTurn('user', text);
      status('Message sent. Waiting for the model…');
      const data = await request('/api/message', {session_id:sessionId, text, via_voice:false});
      $('typed-input').value = '';
      status(''); applyResponse(data);
    });
  }
  async function sendAudio(blob) {
    await action(async () => {
      status('Listening to your recording and preparing a reply…');
      const form = new FormData();
      form.append('session_id', sessionId);
      const ext = blob.type.includes('mp4') ? 'mp4' : blob.type.includes('ogg') ? 'ogg' : 'webm';
      form.append('file', blob, `turn.${ext}`);
      const response = await fetch('/api/audio', {method:'POST', body:form});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not process the recording.');
      if (data.transcript_reliable) appendTurn('user', data.clean_transcript, true);
      $('mic-status').textContent = data.transcript_reliable ? 'Mic off · transcription received' : 'Mic off · recording needs attention';
      status(''); applyResponse(data);
    });
  }
  let wave = null, testRecording = false, previewUrl = null;
  try { wave = $('waveform').getContext('2d'); } catch {}
  function drawLevel(level) {
    $('mic-level').value = level;
    if (!wave) return;
    wave.clearRect(0, 0, 600, 36); wave.fillStyle = '#6F9AAE';
    for (let i = 0; i < 32; i++) {
      const h = Math.max(2, level * 36 * (0.6 + 0.4 * Math.sin(i)));
      wave.fillRect(i * 19, (36-h)/2, 12, h);
    }
  }
  async function listMicrophones() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const chosen = $('mic-select').value;
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
      $('mic-select').replaceChildren(new Option('System default', ''));
      devices.filter(d => d.deviceId && d.deviceId !== 'default').forEach((d, i) => {
        $('mic-select').add(new Option(d.label || `Microphone ${i+1}`, d.deviceId));
      });
      if (devices.some(d => d.deviceId === chosen)) $('mic-select').value = chosen;
    } catch { /* Device enumeration is optional; default capture still works. */ }
  }
  if (navigator.mediaDevices?.getUserMedia && window.MediaRecorder && window.VoiceRecorder) {
    recorder = new VoiceRecorder({
      onAudioReady(blob) {
        if (testRecording) {
          testRecording = false;
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          previewUrl = URL.createObjectURL(blob);
          $('mic-preview').src = previewUrl;
          $('mic-preview').hidden = false;
          $('mic-status').textContent = 'Test complete — play the recording below. It was not sent to the model.';
          status('If the test is silent, choose another microphone and check its mute switch.');
        } else {
          $('mic-status').textContent = 'Recording captured. Uploading for transcription…';
          sendAudio(blob);
        }
      },
      onError(message) { testRecording = false; $('mic-status').textContent = message; status(message); },
      onStatus(message) { $('mic-status').textContent = message; },
      onDeviceReady:listMicrophones,
      onLevel:drawLevel,
      onStateChange(state) {
        const listening = state === RecorderState.LISTENING;
        $('mic-btn').dataset.state = listening ? 'listening' : 'idle';
        $('mic-btn').setAttribute('aria-pressed', String(listening));
        $('mic-label').textContent = listening ? (testRecording ? 'Finish test' : 'Finish & send') :
          state === RecorderState.REQUESTING_PERMISSION ? 'Cancel' : 'Record';
        if (!listening) drawLevel(0);
        updateControls();
      },
    });
    listMicrophones();
    navigator.mediaDevices.addEventListener?.('devicechange', listMicrophones);
  } else $('mic-status').textContent = 'Microphone unavailable. Open http://127.0.0.1:8000 in Chrome or Edge; you can still type.';
  function beginRecording(test = false) {
    speech.stop();
    if (busy) return;
    if (recorder?.state === RecorderState.REQUESTING_PERMISSION) { recorder.cancel(); $('mic-status').textContent = 'Microphone request canceled.'; return; }
    if (recorder?.state === RecorderState.LISTENING) { recorder.stop(); return; }
    testRecording = test;
    $('mic-preview').pause();
    recorder?.start({deviceId:$('mic-select').value, autoStop:!test && $('auto-send').checked});
  }
  $('mic-btn').onclick = () => beginRecording(false);
  $('test-mic-btn').onclick = () => beginRecording(true);
  $('retry-connection-btn').onclick = () => { if (!busy) initSession(); };
  $('check-connection-btn').onclick = async () => {
    $('diagnostics').textContent = 'Checking the audio tools and model connection…';
    try {
      const data = await request('/api/diagnostics');
      $('diagnostics').textContent = `Audio tools: ${data.audio}. Model: ${data.model}.`;
    } catch (error) { $('diagnostics').textContent = `Could not reach the app: ${error.message}`; }
  };
  $('send-btn').onclick = () => sendTyped();
  $('typed-input').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!busy && (!recorder || recorder.state === RecorderState.IDLE)) sendTyped(); }
  };
  $('post-textarea').oninput = () => {
    cacheDraft(); clearTimeout(editTimer); status('Saving your edit…');
    editTimer = setTimeout(() => flushDraft().catch(() => status('Your edit has not reached the server. It is still in the editor; try again.')), 700);
  };
  $('save-context-btn').onclick = () => action(async () => {});
  $('skip-btn').onclick = () => sendTyped('skip');
  $('back-btn').onclick = () => sendTyped('go back');
  for (const [id, endpoint] of [['regenerate-btn','generate'], ['undo-btn','undo'], ['redo-btn','redo']]) {
    $(id).onclick = () => action(async () => {
      status(endpoint === 'generate' ? 'Writing your draft…' : '');
      const data = await request(`/api/${endpoint}`, {session_id:sessionId});
      if (endpoint === 'generate') data.assistant_message = 'Your draft is ready. Does this sound like what you want to say?';
      status(''); applyResponse(data);
    });
  }
  $('clear-session-btn').onclick = () => {
    if (!confirm('Clear the conversation, shared facts, and draft?')) return;
    action(async () => {
      clearTimeout(editTimer); await savePromise.catch(() => {});
      await request('/api/clear', {session_id:sessionId});
      storage.remove(`cb_draft_${sessionId}`); storage.remove('cb_session_id');
      $('transcript').replaceChildren(); $('post-textarea').value = ''; lastKnownPost = '';
      contextDirty = false; shared = {}; status(''); $('context-status').textContent = '';
      $('privacy-flags').hidden = true;
      sessionId = null; await initSession();
    }, {flush:false});
  };
  $('hear-reply-btn').onclick = () => speak(latestReply, true);
  $('listen-btn').onclick = () => speak($('post-textarea').value, true);
  $('stop-listen-btn').onclick = () => speech.stop();
  $('speak-replies').onchange = () => { if (!$('speak-replies').checked) speech.stop(); else speak(latestReply, true); };
  $('copy-btn').onclick = async () => {
    try { await navigator.clipboard.writeText($('post-textarea').value); status('Copied.'); }
    catch { status('Please select and copy the draft manually.'); }
  };
  for (const format of ['txt','md']) {
    $(`save-${format}-btn`).onclick = () => {
      const url = URL.createObjectURL(new Blob([$('post-textarea').value], {type:'text/plain;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url;
      link.download = `caringbridge_first_post_${new Date().toISOString().slice(0,10)}.${format}`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  }
  window.addEventListener('beforeunload', event => {
    speech.stop(); recorder?.cancel();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    if (contextDirty || $('post-textarea').value !== lastKnownPost) { event.preventDefault(); event.returnValue = ''; }
  });
  initSession();
})();
