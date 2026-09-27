/* Local microphone capture. Recording and sending never depend on the level meter. */
const RecorderState = Object.freeze({IDLE:'IDLE', REQUESTING_PERMISSION:'REQUESTING_PERMISSION',
  LISTENING:'LISTENING', STOPPING:'STOPPING', ERROR:'ERROR'});
class VoiceRecorder {
  constructor(opts = {}) {
    this.opts = {trailingSilenceMs:1800, maxRecordingMs:120000, minSpeechMs:250,
      silenceThreshold:0.008, autoStop:false, ...opts};
    this.state = RecorderState.IDLE;
    this.generation = 0;
    this.chunks = [];
    this.mediaStream = null;
    this.mediaRecorder = null;
    this.audioContext = null;
  }
  _state(value) { this.state = value; this.opts.onStateChange?.(value); }
  _notice(message) { this.opts.onStatus?.(message); }
  static permissionMessage(error) {
    return ({
      NotAllowedError:'Microphone permission is blocked. Allow the microphone for this site in your browser, and check Windows microphone privacy settings.',
      NotFoundError:'No microphone was found. Connect one, then choose it from the Microphone list.',
      NotReadableError:'The microphone could not be opened. Check its Windows input settings and close any app holding exclusive access.',
      OverconstrainedError:'The selected microphone is unavailable. Choose System default or another microphone.',
      SecurityError:'Microphone access is blocked. Open this app at http://127.0.0.1:8000 in your browser.',
    })[error?.name] || 'Could not start recording. Check the selected microphone and its browser permissions.';
  }
  async start({deviceId = '', autoStop = false} = {}) {
    if (this.state !== RecorderState.IDLE) return;
    const generation = ++this.generation;
    this.opts.autoStop = autoStop;
    this.chunks = [];
    this.speechMs = 0;
    this.detectedSignal = false;
    this._state(RecorderState.REQUESTING_PERMISSION);
    this._notice('Allow microphone access in the browser prompt. Click Cancel if you want to type instead.');
    // Invoke resume during the click, before waiting for microphone permission.
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this.audioContext = new AudioCtx();
        if (this.audioContext.state === 'suspended') this.audioContext.resume().catch(() => {});
      }
    } catch { this.audioContext = null; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio:{
        ...(deviceId ? {deviceId:{exact:deviceId}} : {}),
        echoCancellation:true, noiseSuppression:true, autoGainControl:true,
      }});
      if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
      this.mediaStream = stream;
      const track = stream.getAudioTracks()[0];
      if (!track || track.readyState === 'ended') throw new Error('No live audio track');
      track.onended = () => {
        if (generation !== this.generation) return;
        this.cancel(); this.opts.onError?.('The microphone disconnected. Reconnect it, then try again.');
      };
      track.onmute = () => this._notice('The microphone is muted by the device or browser. Check its mute switch.');
      const mimeType = ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4']
        .find(type => MediaRecorder.isTypeSupported(type));
      this.mediaRecorder = new MediaRecorder(stream, mimeType ? {mimeType} : {});
      this.mediaRecorder.ondataavailable = event => {
        if (generation === this.generation && event.data?.size) this.chunks.push(event.data);
      };
      this.mediaRecorder.onerror = () => {
        if (generation !== this.generation) return;
        this.cancel(); this.opts.onError?.('Recording failed. Try another microphone, or type your response.');
      };
      this.mediaRecorder.onstop = () => { if (generation === this.generation) this._finalize(); };
      this.mediaRecorder.start(250);
      this._state(RecorderState.LISTENING);
      this._notice(`Mic on: ${track.label || 'selected microphone'}. Click Finish & send when done.`);
      this.opts.onDeviceReady?.();
      // Meter failure must not stop MediaRecorder or disable the finish button.
      try { this._monitor(generation); }
      catch { this._notice('Recording is on; the level meter is unavailable. Click Finish & send when done.'); }
      this.maxTimer = setTimeout(() => this.stop(), this.opts.maxRecordingMs);
      this.signalTimer = setTimeout(() => {
        if (this.state === RecorderState.LISTENING && !this.detectedSignal) {
          this._notice('No input level detected yet. Check the microphone selection and mute switch. You can still finish and test the recording.');
        }
      }, 5000);
    } catch (error) {
      if (generation !== this.generation) return;
      this.cancel(); this.opts.onError?.(VoiceRecorder.permissionMessage(error));
    }
  }
  _monitor(generation) {
    if (!this.audioContext) throw new Error('No audio meter');
    if (this.audioContext.state === 'suspended') this.audioContext.resume().catch(() => {});
    const source = this.audioContext.createMediaStreamSource(this.mediaStream);
    const analyser = this.audioContext.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    let previous = performance.now();
    const tick = () => {
      if (generation !== this.generation || this.state !== RecorderState.LISTENING) return;
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += ((sample - 128) / 128) ** 2;
      const rms = Math.sqrt(sum / samples.length);
      const now = performance.now();
      if (rms > this.opts.silenceThreshold) {
        this.detectedSignal = true;
        this.speechMs += Math.min(100, now - previous);
        clearTimeout(this.silenceTimer);
        if (this.opts.autoStop && this.speechMs >= this.opts.minSpeechMs) {
          this.silenceTimer = setTimeout(() => this.stop(), this.opts.trailingSilenceMs);
        }
      }
      previous = now;
      this.opts.onLevel?.(Math.min(1, rms * 10));
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }
  stop() {
    if (this.state !== RecorderState.LISTENING) return;
    this._state(RecorderState.STOPPING);
    this._clearTimers();
    this._notice('Finishing recording…');
    if (this.mediaRecorder?.state !== 'inactive') this.mediaRecorder.stop();
    else this._finalize();
  }
  _clearTimers() {
    clearTimeout(this.maxTimer); clearTimeout(this.signalTimer); clearTimeout(this.silenceTimer);
    if (this.frame) cancelAnimationFrame(this.frame);
  }
  _release() {
    this._clearTimers();
    this.mediaStream?.getTracks().forEach(track => { track.onended = null; track.onmute = null; track.stop(); });
    this.mediaStream = null;
    const context = this.audioContext; this.audioContext = null;
    if (context && context.state !== 'closed') context.close().catch(() => {});
    this.opts.onLevel?.(0);
  }
  _finalize() {
    const blob = new Blob(this.chunks, {type:this.mediaRecorder?.mimeType || 'audio/webm'});
    this.chunks = [];
    this._release(); this.mediaRecorder = null;
    this._state(RecorderState.IDLE);
    if (blob.size) this.opts.onAudioReady?.(blob);
    else this.opts.onError?.('The recording was empty. Check the selected microphone and try again.');
  }
  cancel() {
    this.generation++;
    if (this.mediaRecorder) {
      this.mediaRecorder.onstop = null; this.mediaRecorder.ondataavailable = null; this.mediaRecorder.onerror = null;
      if (this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop();
    }
    this._release(); this.mediaRecorder = null; this.chunks = [];
    this._state(RecorderState.IDLE);
  }
}
window.VoiceRecorder = VoiceRecorder;
window.RecorderState = RecorderState;
