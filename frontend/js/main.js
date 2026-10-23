import * as api from './api.js';
import { CallSession } from './call.js';
import { Listener } from './speech.js';

const $ = (s) => document.querySelector(s);
const S = { cfg: null, session: null, stream: null, listener: null, peer: null, muted: false, speaking: false,
  queue: [], name: 'Guest', spoken: 'hi', output: 'en', timer: null, startedAt: 0, leaving: false };

const PIPE = { idle: 'Idle', loading: 'Loading', listening: 'Listening', translating: 'Translating', speaking: 'Speaking', disconnected: 'Disconnected', error: 'Error' };
const lang = (c) => (c === 'original' ? { name: 'Original voice (no translation)' } : (S.cfg.languages.find((l) => l.code === c) || { name: c }));
const show = (v) => document.querySelectorAll('.view').forEach((e) => { e.hidden = e.id !== `view-${v}`; });

function setPipe(state, detail) {
  const p = $('#pipePill'); p.dataset.state = state; p.textContent = detail || PIPE[state];
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 5000); }
function err(el, msg) { const e = $(el); e.textContent = msg; e.hidden = !msg; }

// ---------- boot ----------
async function init() {
  try { S.cfg = await api.getConfig(); }
  catch (e) { $('#modeBanner').textContent = `Server unreachable: ${e.message}`; $('#startBtn').disabled = true; return; }
  const c = S.cfg;
  $('#langChips').innerHTML = '';
  c.languages.forEach((l) => { const s = document.createElement('span'); s.className = 'chip'; s.textContent = `${l.name} (${l.native})`; $('#langChips').append(s); });
  for (const id of ['#spoken', '#output', '#spoken2', '#output2']) {
    $(id).innerHTML = '';
    if (id.startsWith('#output')) $(id).add(new Option('Original voice — as spoken (default)', 'original'));
    c.languages.forEach((l) => $(id).add(new Option(`${l.name} — ${l.native}`, l.code)));
  }
  $('#spoken').value = 'hi'; $('#output').value = 'original';
  const p = c.providers;
  $('#modeBanner').textContent = c.demo_translation
    ? 'DEMO MODE: translation is a tiny SIMULATED phrasebook, not AI. Add an API key for real translation.'
    : `Providers — speech-to-text: ${p.stt}, translation: ${p.translate}, text-to-speech: ${p.tts}`;
  if (c.warnings.length) $('#modeBanner').textContent += ' ⚠ ' + c.warnings.join(' ');
  const q = new URLSearchParams(location.search).get('room');
  if (q) { show('lobby'); $('#roomInput').value = q.toUpperCase(); } else show('home');
}

$('#startBtn').onclick = () => show('lobby');
$('#createBtn').onclick = async () => {
  try { const { room } = await api.createRoom(); join(room); } catch (e) { err('#lobbyError', e.message); }
};
$('#joinBtn').onclick = () => {
  const code = $('#roomInput').value.trim().toUpperCase();
  if (!/^[A-Z0-9]{4,12}$/.test(code)) return err('#lobbyError', 'Enter a valid room code (4–12 letters/digits).');
  join(code);
};

function micMessage(e) {
  if (!window.isSecureContext) return 'Microphone needs a secure context: open http://localhost:8000 (not a LAN IP) or use HTTPS.';
  if (e.name === 'NotAllowedError') return 'Microphone permission was denied. Allow it in the browser address bar and try again.';
  if (e.name === 'NotFoundError') return 'No microphone was found on this device.';
  if (e.name === 'NotReadableError') return 'The microphone is in use by another app.';
  return `Could not open the microphone: ${e.message}`;
}

// ---------- join ----------
async function join(code) {
  err('#lobbyError', '');
  if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) return err('#lobbyError', 'This browser lacks microphone or WebRTC support. Use a recent Chrome, Edge or Firefox.' + (window.isSecureContext ? '' : ' (Page is not a secure context.)'));
  if (S.cfg.providers.stt === 'browser' && !Listener.browserSupported()) return err('#lobbyError', 'Browser speech recognition is unsupported here. Use Chrome/Edge, or set GEMINI_API_KEY on the server.');
  S.name = $('#name').value.trim() || 'Guest';
  S.spoken = $('#spoken').value; S.output = $('#output').value;
  setPipe('loading', 'Loading — requesting microphone');
  try {
    S.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) { setPipe('error'); return err('#lobbyError', micMessage(e)); }

  S.leaving = false; S.peer = null; S.muted = false;
  show('call'); err('#callError', '');
  $('#roomCode').textContent = code; $('#meName').textContent = S.name; $('#meMic').textContent = 'Microphone: on';
  $('#spoken2').value = S.spoken; $('#output2').value = S.output; $('#muteBtn').textContent = 'Mute microphone'; $('#muteBtn').setAttribute('aria-pressed', 'false');
  resetTranscript(); renderPeer(); setConn('Connecting to server…', '');
  history.replaceState(null, '', `?room=${code}`);

  S.listener = new Listener({
    mode: S.cfg.providers.stt, stream: S.stream, lang: lang(S.spoken).stt,
    gate: () => !!S.peer && !S.muted && !S.speaking && !S.leaving,
    onText: onLocalText, onAudio: onLocalAudio,
    onError: (m) => { err('#callError', m); setPipe('error'); },
    onLevel: (r) => { $('#meter').style.width = Math.min(100, r * 600) + '%'; },
  });

  S.session = new CallSession(code, S.cfg.ice_servers, S.stream, {
    getProfile: () => ({ name: S.name, spoken: S.spoken, output: S.output }),
    onOpen: () => setConn('Waiting for the other person…', ''),
    onJoined: (m) => { if (m.peers > 0) setConn('Connecting…', ''); },
    onPeerJoined: () => setConn('Participant joined, connecting…', ''),
    onPeerProfile: (p) => { S.peer = { name: String(p.name || 'Guest').slice(0, 24), spoken: p.spoken, output: p.output }; renderPeer(); if (!S.listenerOn) { S.listener.start(); S.listenerOn = true; } applyVol(); setPipe('listening'); },
    onRemoteStream: (st) => { $('#remoteAudio').srcObject = st; applyVol(); },
    onConn: onConn,
    onPeerLeft: () => { S.peer = null; stopTimer(); renderPeer(); setConn('Participant left', 'bad'); setPipe('disconnected'); },
    onClosed: (code2) => { if (code2 === 4003) return; setConn('Disconnected from server', 'bad'); setPipe('disconnected'); err('#callError', 'Signaling connection lost. Leave and rejoin the room.'); },
    onError: (m) => { err('#callError', m); setPipe('error'); },
    onUtterance: onRemoteUtterance,
  });
  S.session.connect();
}

function setConn(text, cls) { const p = $('#connPill'); p.textContent = text; p.className = 'pill ' + cls; }

function onConn(state) {
  if (state === 'connected') { setConn('Connected', 'ok'); startTimer(); }
  else if (state === 'connecting') setConn('Connecting…', '');
  else if (state === 'disconnected') { setConn('Connection interrupted', 'bad'); setPipe('disconnected'); }
  else if (state === 'failed') { setConn('Connection failed', 'bad'); setPipe('error'); err('#callError', 'WebRTC connection failed. Networks with strict NAT/firewalls need a TURN server (see README).'); }
}

function startTimer() {
  if (S.timer) return; S.startedAt = Date.now();
  S.timer = setInterval(() => { const s = Math.floor((Date.now() - S.startedAt) / 1000); $('#timer').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; }, 500);
}
function stopTimer() { clearInterval(S.timer); S.timer = null; }

function renderPeer() {
  $('#peerName').textContent = S.peer ? S.peer.name : 'Waiting for someone to join…';
  $('#peerLang').textContent = S.peer ? `Speaks ${lang(S.peer.spoken).name}, hears ${lang(S.peer.output).name}` : '';
}

// ---------- transcript ----------
function resetTranscript() { $('#transcript').innerHTML = '<li class="empty">Conversation will appear here once both people are connected.</li>'; }
function addLine({ who, own, original, source }) {
  const list = $('#transcript'); list.querySelector('.empty')?.remove();
  const li = document.createElement('li'); li.className = own ? 'line own' : 'line';
  const w = document.createElement('div'); w.className = 'who'; w.textContent = `${who} · ${lang(source).name}`;
  const o = document.createElement('div'); o.textContent = original;
  li.append(w, o); list.append(li); list.scrollTop = list.scrollHeight; return li;
}
function setTrans(li, text, target, note) {
  let t = li.querySelector('.trans'); if (!t) { t = document.createElement('div'); t.className = 'trans'; li.append(t); }
  t.textContent = `→ ${lang(target).name}: ${text}`;
  if (note) { const n = document.createElement('div'); n.className = 'note'; n.textContent = note; li.append(n); }
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
}

// ---------- local speech -> translate -> send ----------
async function onLocalAudio(blob) {
  setPipe('loading', 'Loading — transcribing');
  try {
    const { text } = await api.stt(blob, S.spoken);
    if (text && text.trim().length > 1) await onLocalText(text.trim()); else setPipe('listening');
  } catch (e) { fail(e); }
}

async function onLocalText(text) {
  if (!S.peer) return;
  const src = S.spoken, tgt = S.peer.output;
  const li = addLine({ who: `${S.name} (You)`, own: true, original: text, source: src });
  if (tgt === 'original' || tgt === src) { // listener wants the original voice: no translation call, no cost
    S.session.send({ type: 'utterance', speaker: S.name, original: text, translated: '', source: src, target: tgt, passthrough: true });
    return;
  }
  setPipe('translating');
  try {
    const r = await api.translate(text, src, tgt);
    const note = r.simulated ? (r.matched ? 'SIMULATED demo phrasebook — not AI translation' : 'Demo mode: no phrase match, nothing sent to be spoken') : '';
    setTrans(li, r.text, tgt, note);
    S.session.send({ type: 'utterance', speaker: S.name, original: text, translated: r.text, source: src, target: tgt, simulated: r.simulated, matched: r.matched, provider: r.provider });
    setPipe('listening');
  } catch (e) { fail(e); }
}

function fail(e) { setPipe('error'); toast(e.message); setTimeout(() => { if (S.peer && !S.leaving) setPipe('listening'); }, 3500); }

// ---------- remote utterance -> TTS (dedicated playback element) ----------
function onRemoteUtterance(m) {
  if (typeof m.original !== 'string' || typeof m.translated !== 'string') return;
  const li = addLine({ who: S.peer ? S.peer.name : 'Participant', own: false, original: m.original.slice(0, 1000), source: m.source });
  if (m.passthrough) return; // listener hears the original voice; transcript only
  const note = m.simulated ? (m.matched ? 'SIMULATED demo phrasebook — not AI translation' : 'Demo mode: no phrase match, not spoken') : '';
  setTrans(li, m.translated.slice(0, 1000), m.target, note);
  if (m.matched === false) return;
  S.queue.push({ text: m.translated.slice(0, 1000), lang: S.output });
  pump();
}

async function pump() {
  if (S.speaking) return;
  S.speaking = true; applyVol();
  while (!S.leaving) {
    while (S.queue.length && !S.leaving) {
      const it = S.queue.shift(); setPipe('speaking');
      try { await speak(it.text, it.lang); } catch (e) { toast(e.message); }
    }
    await new Promise((r) => setTimeout(r, 400)); // let speaker tail die out before the mic reopens
    if (!S.queue.length) break;
  }
  S.speaking = false; applyVol();
  if (!S.leaving) setPipe(S.peer ? 'listening' : 'idle');
}

async function speak(text, code) {
  if (S.cfg.providers.tts !== 'browser') {
    const url = URL.createObjectURL(await api.tts(text, code));
    const a = $('#ttsAudio'); a.src = url;
    await new Promise((res, rej) => { a.onended = res; a.onerror = () => rej(new Error('Could not play synthesized audio.')); a.play().catch(rej); });
    URL.revokeObjectURL(url); return;
  }
  if (!('speechSynthesis' in window)) throw new Error('Speech synthesis is not supported in this browser.');
  const tag = lang(code).tts || code; const u = new SpeechSynthesisUtterance(text); u.lang = tag;
  const v = speechSynthesis.getVoices().find((x) => x.lang.toLowerCase().startsWith(code));
  if (v) u.voice = v; else toast(`No ${lang(code).name} voice installed on this device; the transcript text is still shown.`);
  await new Promise((res) => { u.onend = res; u.onerror = res; speechSynthesis.speak(u); });
}

function translating() { return S.output !== 'original' && !!S.peer && S.peer.spoken !== S.output; }
function applyVol() {
  $('#remoteAudio').volume = S.speaking ? 0 : translating() ? $('#origVol').value / 100 : 1; // full original voice unless translating
}
$('#origVol').oninput = applyVol;

// ---------- controls ----------
$('#muteBtn').onclick = () => {
  S.muted = !S.muted;
  S.stream.getAudioTracks().forEach((t) => { t.enabled = !S.muted; });
  $('#muteBtn').textContent = S.muted ? 'Unmute microphone' : 'Mute microphone';
  $('#muteBtn').setAttribute('aria-pressed', String(S.muted));
  $('#meMic').textContent = S.muted ? 'Microphone: muted' : 'Microphone: on';
};
function langChanged() {
  S.spoken = $('#spoken2').value; S.output = $('#output2').value;
  S.listener?.setLang(lang(S.spoken).stt); S.session?.sendProfile(); applyVol();
}
$('#spoken2').onchange = langChanged; $('#output2').onchange = langChanged;
$('#copyBtn').onclick = async () => {
  const link = `${location.origin}/?room=${$('#roomCode').textContent}`;
  try { await navigator.clipboard.writeText(link); toast('Invite link copied.'); } catch { toast(link); }
};
function leave() {
  S.leaving = true; stopTimer(); S.queue = [];
  S.listener?.stop(); S.session?.leave();
  S.stream?.getTracks().forEach((t) => t.stop());
  speechSynthesis?.cancel?.();
  $('#remoteAudio').srcObject = null; $('#timer').textContent = '00:00'; $('#meter').style.width = '0';
  S.listener = S.session = S.stream = S.peer = null; S.speaking = false; S.listenerOn = false;
  history.replaceState(null, '', location.pathname); setPipe('idle'); show('home');
}
$('#leaveBtn').onclick = leave;
window.addEventListener('beforeunload', () => S.session?.leave());

init();
