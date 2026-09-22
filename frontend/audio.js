/**
 * Microphone recording as an explicit state machine (MASTER PROMPT #18),
 * using MediaRecorder + HTTP upload rather than WebRTC peer connections
 * (MASTER PROMPT #19: "reliability is more important than technically
 * impressive architecture" -- a short voice turn doesn't need a full
 * peer-to-peer connection lifecycle to manage).
 *
 * States: IDLE -> REQUESTING_PERMISSION -> LISTENING -> STOPPING -> ERROR
 * Uploading/transcribing/processing states are driven by app.js once it has
 * the audio blob, since that's a plain HTTP request rather than part of the
 * recording lifecycle itself.
 */

const RecorderState = Object.freeze({
  IDLE: "IDLE",
  REQUESTING_PERMISSION: "REQUESTING_PERMISSION",
  LISTENING: "LISTENING",
  STOPPING: "STOPPING",
  ERROR: "ERROR",
});

const VALID_TRANSITIONS = {
  IDLE: [RecorderState.REQUESTING_PERMISSION],
  REQUESTING_PERMISSION: [RecorderState.LISTENING, RecorderState.ERROR, RecorderState.IDLE],
  LISTENING: [RecorderState.STOPPING, RecorderState.ERROR],
  STOPPING: [RecorderState.IDLE, RecorderState.ERROR],
  ERROR: [RecorderState.IDLE],
};

class VoiceRecorder {
  /**
   * @param {object} opts
   * @param {(state:string)=>void} opts.onStateChange
   * @param {(blob:Blob)=>void} opts.onAudioReady
   * @param {(level:number)=>void} opts.onLevel - 0..1 amplitude, for waveform UI
   * @param {(message:string)=>void} opts.onError
   * @param {number} opts.trailingSilenceMs
   * @param {number} opts.maxRecordingMs
   * @param {number} opts.minSpeechMs
   */
  constructor(opts) {
    this.opts = Object.assign({
      trailingSilenceMs: 1200,
      maxRecordingMs: 120000,
      minSpeechMs: 500,
      silenceThreshold: 0.02,
    }, opts);
    this.state = RecorderState.IDLE;
    this.mediaStream = null;
    this.mediaRecorder = null;
    this.audioContext = null;
    this.analyser = null;
    this.chunks = [];
    this._silenceTimer = null;
    this._maxTimer = null;
    this._levelRAF = null;
    this._speechDetectedMs = 0;
    this._lastFrameTime = null;
  }

  _setState(next) {
    const allowed = VALID_TRANSITIONS[this.state] || [];
    if (!allowed.includes(next)) {
      console.warn(`Ignoring invalid recorder transition ${this.state} -> ${next}`);
      return;
    }
    this.state = next;
    if (this.opts.onStateChange) this.opts.onStateChange(next);
  }

  async start() {
    if (this.state !== RecorderState.IDLE) return;
    this._setState(RecorderState.REQUESTING_PERMISSION);
    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this._setState(RecorderState.ERROR);
      this.opts.onError && this.opts.onError(
        "We couldn't access your microphone. You can still type your response below."
      );
      this._setState(RecorderState.IDLE);
      return;
    }

    this.chunks = [];
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    this.mediaRecorder = new MediaRecorder(this.mediaStream, { mimeType });
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    this.mediaRecorder.onstop = () => this._finalize();
    this.mediaRecorder.start();

    this._setupLevelMonitoring();
    this._speechDetectedMs = 0;
    this._maxTimer = setTimeout(() => this.stop(), this.opts.maxRecordingMs);

    this._setState(RecorderState.LISTENING);
  }

  _setupLevelMonitoring() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    this.audioContext = new AudioCtx();
    const source = this.audioContext.createMediaStreamSource(this.mediaStream);
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 512;
    source.connect(this.analyser);

    const data = new Uint8Array(this.analyser.frequencyBinCount);
    this._lastFrameTime = performance.now();

    const tick = () => {
      if (this.state !== RecorderState.LISTENING) return;
      this.analyser.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i++) {
        const centered = (data[i] - 128) / 128;
        sumSquares += centered * centered;
      }
      const rms = Math.sqrt(sumSquares / data.length);
      if (this.opts.onLevel) this.opts.onLevel(Math.min(1, rms * 4));

      const now = performance.now();
      const dt = now - this._lastFrameTime;
      this._lastFrameTime = now;

      if (rms > this.opts.silenceThreshold) {
        this._speechDetectedMs += dt;
        clearTimeout(this._silenceTimer);
        this._silenceTimer = setTimeout(() => {
          if (this._speechDetectedMs >= this.opts.minSpeechMs) {
            this.stop();
          }
        }, this.opts.trailingSilenceMs);
      }

      this._levelRAF = requestAnimationFrame(tick);
    };
    this._levelRAF = requestAnimationFrame(tick);
  }

  stop() {
    if (this.state !== RecorderState.LISTENING) return;
    this._setState(RecorderState.STOPPING);
    clearTimeout(this._silenceTimer);
    clearTimeout(this._maxTimer);
    if (this._levelRAF) cancelAnimationFrame(this._levelRAF);
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      this.mediaRecorder.stop();
    } else {
      this._finalize();
    }
  }

  _finalize() {
    this.mediaStream && this.mediaStream.getTracks().forEach((t) => t.stop());
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    const blob = new Blob(this.chunks, { type: "audio/webm" });
    this.chunks = [];
    this._setState(RecorderState.IDLE);
    if (blob.size > 0 && this.opts.onAudioReady) this.opts.onAudioReady(blob);
  }

  cancel() {
    clearTimeout(this._silenceTimer);
    clearTimeout(this._maxTimer);
    if (this._levelRAF) cancelAnimationFrame(this._levelRAF);
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      this.mediaRecorder.onstop = null;
      this.mediaRecorder.stop();
    }
    this.mediaStream && this.mediaStream.getTracks().forEach((t) => t.stop());
    if (this.audioContext) this.audioContext.close().catch(() => {});
    this.chunks = [];
    this.state = RecorderState.IDLE;
  }
}

window.VoiceRecorder = VoiceRecorder;
window.RecorderState = RecorderState;
