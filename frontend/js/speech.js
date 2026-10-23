// Local microphone -> text. Only the LOCAL mic is ever transcribed (never remote audio or TTS output).
// mode 'browser': Web Speech API (Chrome/Edge).  mode 'openai' | 'gemini' | 'mistral' (any server STT): VAD + MediaRecorder -> /api/stt.
export class Listener {
  constructor({ mode, stream, lang, gate, onText, onAudio, onError, onLevel }) {
    Object.assign(this, { mode, stream, lang, gate, onText, onAudio, onError, onLevel });
    this.rec = null; this.recorder = null; this.fatal = false; this.timer = null; this.threshold = 0.02;
  }

  static browserSupported() { return !!(window.SpeechRecognition || window.webkitSpeechRecognition); }

  start() {
    if (this.mode !== 'browser') {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.an = this.ctx.createAnalyser(); this.an.fftSize = 1024;
      this.ctx.createMediaStreamSource(this.stream).connect(this.an);
      this.buf = new Float32Array(this.an.fftSize);
    }
    this.timer = setInterval(() => this.tick(), 100);
  }

  stop() {
    clearInterval(this.timer);
    this.abortBrowser(); this.discard();
    if (this.ctx) this.ctx.close().catch(() => {});
  }

  setLang(l) { this.lang = l; this.abortBrowser(); }

  tick() {
    const open = this.gate();
    if (this.mode === 'browser') {
      if (open && !this.rec && !this.fatal) this.startBrowser();
      else if (!open && this.rec) this.abortBrowser();
    } else this.tickVad(open);
  }

  // ---- browser speech recognition ----
  startBrowser() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const r = new SR();
    r.lang = this.lang; r.continuous = true; r.interimResults = false;
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (!e.results[i].isFinal) continue;
        const t = e.results[i][0].transcript.trim();
        if (t && this.gate()) this.onText(t);
      }
    };
    r.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { this.fatal = true; this.onError('Speech recognition was blocked. Allow microphone access and use Chrome/Edge.'); }
      else if (e.error === 'network') this.onError('Speech recognition network error (Chrome uses an online service).');
      else this.onError(`Speech recognition error: ${e.error}`);
    };
    r.onend = () => { if (this.rec === r) this.rec = null; };
    this.rec = r;
    try { r.start(); } catch { this.rec = null; }
  }

  abortBrowser() {
    if (this.rec) { const r = this.rec; this.rec = null; r.onresult = null; try { r.abort(); } catch { /* ignore */ } }
  }

  // ---- voice-activity detection + recording ----
  tickVad(open) {
    this.an.getFloatTimeDomainData(this.buf);
    let sum = 0; for (const v of this.buf) sum += v * v;
    const rms = Math.sqrt(sum / this.buf.length);
    if (this.onLevel) this.onLevel(rms);
    if (!open) { if (this.recorder) this.discard(); return; }
    const now = performance.now();
    if (rms > this.threshold) { this.lastVoice = now; if (!this.recorder) this.begin(now); }
    else if (this.recorder && now - this.lastVoice > 900) this.finish();
    if (this.recorder && now - this.recStart > 15000) this.finish();
  }

  begin(now) {
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m));
    try {
      const rec = new MediaRecorder(new MediaStream(this.stream.getAudioTracks()), mime ? { mimeType: mime } : {});
      this.chunks = []; this.recStart = now; this.lastVoice = now;
      rec.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data); };
      rec.start(); this.recorder = rec;
    } catch { this.onError('This browser cannot record audio (MediaRecorder unsupported).'); this.fatal = true; }
  }

  finish() {
    const rec = this.recorder; this.recorder = null;
    const voicedMs = this.lastVoice - this.recStart;
    rec.onstop = () => {
      const blob = new Blob(this.chunks, { type: rec.mimeType });
      if (voicedMs >= 350 && blob.size > 2000) this.onAudio(blob);
    };
    rec.stop();
  }

  discard() {
    if (!this.recorder) return;
    const rec = this.recorder; this.recorder = null; rec.onstop = null;
    try { rec.stop(); } catch { /* ignore */ }
  }
}
