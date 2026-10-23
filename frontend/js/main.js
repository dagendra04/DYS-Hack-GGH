import * as api from './api.js';
import { CallSession } from './call.js';
import { Listener } from './speech.js';

const $ = (s) => document.querySelector(s);
const S = {
  cfg: null, session: null, stream: null, listener: null, peer: null,
  muted: false, speaking: false, queue: [], name: 'Guest', spoken: 'hi',
  output: 'en', timer: null, startedAt: 0, leaving: false,
  utterances: [], lastSummary: null, listenerOn: false,
};

const PIPE = {
  idle: 'Idle',
  loading: 'Loading',
  listening: 'Listening',
  translating: 'Translating',
  speaking: 'Speaking',
  disconnected: 'Disconnected',
  error: 'Error',
};

const lang = (c) => (c === 'original' ? { name: 'Original voice (no translation)' } : (S.cfg?.languages?.find((l) => l.code === c) || { name: c }));
const show = (v) => document.querySelectorAll('.view').forEach((e) => { e.hidden = e.id !== `view-${v}`; });

function setPipe(state, detail) {
  const p = $('#pipePill');
  if (p) {
    p.dataset.state = state;
    p.textContent = detail || PIPE[state] || state;
  }
}

function toast(msg) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.hidden = true; }, 5000);
}

function err(el, msg) {
  const e = $(el);
  if (!e) return;
  e.textContent = msg;
  e.hidden = !msg;
}

// ---------- Theme Management ----------
function initTheme() {
  const saved = localStorage.getItem('bhasha_theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
  } else {
    document.documentElement.setAttribute('data-theme', 'auto');
  }

  const toggle = $('#themeToggle');
  if (toggle) {
    toggle.onclick = () => {
      const current = document.documentElement.getAttribute('data-theme');
      let next = 'dark';
      if (current === 'dark') next = 'light';
      else if (current === 'light') next = 'dark';
      else {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        next = prefersDark ? 'light' : 'dark';
      }
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('bhasha_theme', next);
      toast(`Theme set to ${next} mode`);
    };
  }
}

// ---------- Avatar Helpers ----------
function getInitials(name) {
  if (!name || name === 'Guest') return 'GU';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function updateAvatars() {
  const meAvatar = $('#meAvatar');
  if (meAvatar) meAvatar.textContent = getInitials(S.name);
  const peerAvatar = $('#peerAvatar');
  if (peerAvatar) {
    peerAvatar.textContent = S.peer ? getInitials(S.peer.name) : '?';
  }
}

// ---------- Boot ----------
async function init() {
  initTheme();
  if ('speechSynthesis' in window) {
    speechSynthesis.getVoices();
    speechSynthesis.onvoiceschanged = () => { speechSynthesis.getVoices(); };
  }

  try {
    S.cfg = await api.getConfig();
  } catch (e) {
    $('#modeBanner').textContent = `Server unreachable: ${e.message}`;
    $('#startBtn').disabled = true;
    return;
  }

  const c = S.cfg;
  const chips = $('#langChips');
  if (chips) {
    chips.innerHTML = '';
    c.languages.forEach((l) => {
      const s = document.createElement('span');
      s.className = 'chip';
      s.textContent = `${l.name} (${l.native})`;
      chips.append(s);
    });
  }

  for (const id of ['#spoken', '#output', '#spoken2', '#output2', '#summaryLang']) {
    const el = $(id);
    if (!el) continue;
    el.innerHTML = '';
    c.languages.forEach((l) => el.add(new Option(`${l.name} — ${l.native}`, l.code)));
    if (id.startsWith('#output')) el.add(new Option('Original voice (no translation)', 'original'));
  }

  $('#spoken').value = 'hi';
  $('#output').value = 'en';

  $('#spoken').onchange = () => {
    const s = $('#spoken').value;
    if (s === 'hi') $('#output').value = 'en';
    else if (s === 'en') $('#output').value = 'hi';
  };

  const p = c.providers;
  $('#modeBanner').textContent = c.demo_translation
    ? 'DEMO MODE: translation is a simulated phrasebook. Add GEMINI_API_KEY for real AI translation.'
    : `AI Providers — STT: ${p.stt}, Translate: ${p.translate}, TTS: ${p.tts}`;
  if (c.warnings?.length) $('#modeBanner').textContent += ' ⚠ ' + c.warnings.join(' ');

  const q = new URLSearchParams(location.search).get('room');
  if (q) {
    show('lobby');
    $('#roomInput').value = q.toUpperCase();
  } else {
    show('home');
  }
}

// Navigation Triggers
$('#startBtn').onclick = () => show('lobby');
const joinQuick = $('#joinQuickBtn');
if (joinQuick) {
  joinQuick.onclick = () => {
    show('lobby');
    setTimeout(() => $('#roomInput')?.focus(), 100);
  };
}
const backHome = $('#backToHomeBtn');
if (backHome) backHome.onclick = () => show('home');

$('#createBtn').onclick = async () => {
  try {
    const { room } = await api.createRoom();
    join(room);
  } catch (e) {
    err('#lobbyError', e.message);
  }
};

$('#joinBtn').onclick = () => {
  const code = $('#roomInput').value.trim().toUpperCase();
  if (!/^[A-Z0-9]{4,12}$/.test(code)) return err('#lobbyError', 'Enter a valid room code (4–12 letters/digits).');
  join(code);
};

function micMessage(e) {
  if (!window.isSecureContext) return 'Microphone needs a secure context: open http://localhost:8000 or use HTTPS.';
  if (e.name === 'NotAllowedError') return 'Microphone permission was denied. Allow it in the browser address bar and try again.';
  if (e.name === 'NotFoundError') return 'No microphone was found on this device.';
  if (e.name === 'NotReadableError') return 'The microphone is in use by another app.';
  return `Could not open the microphone: ${e.message}`;
}

// ---------- Join Call ----------
async function join(code) {
  err('#lobbyError', '');
  if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
    return err('#lobbyError', 'This browser lacks microphone or WebRTC support. Use a recent Chrome, Edge, or Firefox.');
  }
  if (S.cfg.providers.stt === 'browser' && !Listener.browserSupported()) {
    return err('#lobbyError', 'Browser speech recognition is unsupported here. Use Chrome/Edge, or configure GEMINI_API_KEY.');
  }

  S.name = $('#name').value.trim() || 'Guest';
  S.spoken = $('#spoken').value;
  S.output = $('#output').value;
  setPipe('loading', 'Loading — requesting microphone');

  try {
    S.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    setPipe('error');
    return err('#lobbyError', micMessage(e));
  }

  S.leaving = false;
  S.peer = null;
  S.muted = false;
  S.utterances = [];
  S.lastSummary = null;

  show('call');
  err('#callError', '');
  $('#roomCode').textContent = code;
  $('#meName').textContent = S.name;
  $('#meMic').textContent = 'Microphone: on';
  $('#spoken2').value = S.spoken;
  $('#output2').value = S.output;
  $('#muteBtn').setAttribute('aria-pressed', 'false');
  updateAvatars();

  resetTranscript();
  renderPeer();
  setConn('Connecting to server…', '');
  history.replaceState(null, '', `?room=${code}`);

  S.listener = new Listener({
    mode: S.cfg.providers.stt,
    stream: S.stream,
    lang: lang(S.spoken).stt,
    gate: () => !S.muted && !S.speaking && !S.leaving,
    onText: onLocalText,
    onAudio: onLocalAudio,
    onError: (m) => { err('#callError', m); setPipe('error'); },
    onLevel: (r) => {
      const meter = $('#meter');
      if (meter) meter.style.width = Math.min(100, r * 600) + '%';
      const mePulse = $('#meCard .speaking-pulse');
      if (mePulse) mePulse.classList.toggle('active', r > 0.035 && !S.muted);
    },
  });

  // Start speech recognition right away so speech and translation work even before a peer joins
  S.listener.start();
  S.listenerOn = true;
  setPipe('listening');

  S.session = new CallSession(code, S.cfg.ice_servers, S.stream, {
    getProfile: () => ({ name: S.name, spoken: S.spoken, output: S.output }),
    onOpen: () => setConn(S.peer ? 'Connected' : 'Waiting for someone to join… (Mic & translation active)', ''),
    onJoined: (m) => {
      if (m.peers > 0) setConn('Connecting to peer…', '');
      else setConn('Waiting for someone to join… (Mic & translation active)', '');
    },
    onPeerJoined: () => setConn('Participant joined, connecting…', ''),
    onPeerProfile: (p) => {
      S.peer = { name: String(p.name || 'Guest').slice(0, 24), spoken: p.spoken, output: p.output };
      renderPeer();
      updateAvatars();
      if (!S.listenerOn) { S.listener.start(); S.listenerOn = true; }
      applyVol();
      setPipe('listening');
    },
    onRemoteStream: (st) => {
      $('#remoteAudio').srcObject = st;
      applyVol();
    },
    onConn: onConn,
    onPeerLeft: () => {
      S.peer = null;
      renderPeer();
      updateAvatars();
      setConn('Participant left (Mic active)', '');
      setPipe('listening');
    },
    onClosed: (code2) => {
      if (code2 === 4003) return;
      setConn('Disconnected from server', 'bad');
      setPipe('disconnected');
      err('#callError', 'Signaling connection lost. Leave and rejoin the room.');
    },
    onError: (m) => { err('#callError', m); setPipe('error'); },
    onUtterance: onRemoteUtterance,
    onTranslationWarning: onTranslationWarning,
  });

  S.session.connect();
}

function setConn(text, cls) {
  const p = $('#connPill');
  if (p) {
    p.textContent = text;
    p.className = 'pill ' + (cls || '');
  }
}

function onConn(state) {
  if (state === 'connected') {
    setConn('Connected', 'ok');
    startTimer();
  } else if (state === 'connecting') {
    setConn('Connecting…', '');
  } else if (state === 'disconnected') {
    setConn('Connection interrupted', 'bad');
    setPipe('disconnected');
  } else if (state === 'failed') {
    setConn('Connection failed', 'bad');
    setPipe('error');
    err('#callError', 'WebRTC connection failed. Strict firewalls require a TURN server.');
  }
}

function startTimer() {
  if (S.timer) return;
  S.startedAt = Date.now();
  S.timer = setInterval(() => {
    const s = Math.floor((Date.now() - S.startedAt) / 1000);
    const m = String(Math.floor(s / 60)).padStart(2, '0');
    const sec = String(s % 60).padStart(2, '0');
    $('#timer').textContent = `${m}:${sec}`;
  }, 500);
}

function stopTimer() {
  clearInterval(S.timer);
  S.timer = null;
}

function renderPeer() {
  $('#peerName').textContent = S.peer ? S.peer.name : 'Waiting for someone to join…';
  $('#peerLang').textContent = S.peer
    ? `Speaks ${lang(S.peer.spoken).name}, hears ${lang(S.peer.output).name}`
    : 'Share room link with someone (you can speak to test mic & translation now)';
}

// ---------- Live Chat-Bubble Transcript ----------
function resetTranscript() {
  $('#transcript').innerHTML = '<li class="empty">Conversation and live translation will appear here.</li>';
}

function addLine({ who, own, original, source }) {
  const list = $('#transcript');
  list.querySelector('.empty')?.remove();
  const li = document.createElement('li');
  li.className = own ? 'line own' : 'line';

  const w = document.createElement('div');
  w.className = 'who';
  w.textContent = `${who} · ${lang(source).name}`;

  const o = document.createElement('div');
  o.className = 'orig';
  o.textContent = original;

  li.append(w, o);
  list.append(li);
  list.scrollTop = list.scrollHeight;
  return li;
}

function setTrans(li, text, target, note, isTranslating = false, isError = false) {
  let t = li.querySelector('.trans');
  if (!t) {
    t = document.createElement('div');
    li.append(t);
  }
  if (isTranslating) {
    t.className = 'trans translating';
    t.innerHTML = `<span class="trans-spin">✦</span> Translating to ${lang(target).name}…`;
  } else if (isError) {
    t.className = 'trans trans-error';
    t.innerHTML = `<span class="trans-err-icon">⚠</span> Translation error: ${text}`;
  } else {
    t.className = 'trans';
    t.textContent = `→ ${lang(target).name}: ${text}`;
    if (note) {
      const n = document.createElement('div');
      n.className = 'note';
      n.textContent = note;
      li.append(n);
    }
  }
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
}

// ---------- Local Speech -> Translate -> Send ----------
async function onLocalAudio(blob) {
  setPipe('loading', 'Loading — transcribing');
  try {
    const { text } = await api.stt(blob, S.spoken);
    if (text && text.trim().length > 1) await onLocalText(text.trim());
    else setPipe('listening');
  } catch (e) {
    // If server STT is disabled or quota exhausted, fall back to browser Web Speech API
    if (Listener.browserSupported() && (e.message.includes('Server STT is off') || e.message.includes('429') || e.message.includes('quota'))) {
      toast('Switching microphone to native browser speech recognition…');
      if (S.listener) {
        S.listener.stop();
        S.listener.mode = 'browser';
        S.listener.start();
        setPipe('listening');
        return;
      }
    }
    fail(e);
  }
}

async function onLocalText(text) {
  if (S.leaving || !text || !text.trim()) return;
  const cleanText = text.trim();
  const src = S.spoken;

  // Smart target language resolution:
  let tgt = 'en';
  if (S.peer) {
    if (S.peer.output && S.peer.output !== 'original' && S.peer.output !== src) {
      tgt = S.peer.output;
    } else if (S.peer.spoken && S.peer.spoken !== src) {
      tgt = S.peer.spoken;
    } else if (S.output && S.output !== 'original' && S.output !== src) {
      tgt = S.output;
    } else {
      tgt = (src === 'hi' ? 'en' : 'hi');
    }
  } else {
    if (S.output && S.output !== 'original' && S.output !== src) {
      tgt = S.output;
    } else {
      tgt = (src === 'hi' ? 'en' : 'hi');
    }
  }

  // Record into in-memory transcript accumulator
  S.utterances.push({
    speaker: S.name || 'You',
    text: cleanText,
    lang: src,
  });

  const li = addLine({ who: `${S.name} (You)`, own: true, original: cleanText, source: src });

  // If both participants explicitly chose original voice and same language, skip translation
  if (S.output === 'original' && (!S.peer || S.peer.output === 'original') && tgt === src) {
    if (S.session && S.peer) {
      S.session.send({
        type: 'utterance',
        speaker: S.name,
        original: cleanText,
        translated: '',
        source: src,
        target: tgt,
        passthrough: true,
      });
    }
    return;
  }

  // Show immediate real-time translating indicator in the transcript bubble
  setTrans(li, '', tgt, '', true);
  setPipe('translating');

  try {
    const r = await api.translate(cleanText, src, tgt);
    const note = r.simulated
      ? (r.matched ? 'SIMULATED demo phrasebook' : 'Demo mode: no phrase match, not spoken')
      : '';
    setTrans(li, r.text, tgt, note);

    // Smart Misunderstanding Detector evaluation on own speech
    if (r.misunderstanding && r.misunderstanding.needs_confirmation) {
      renderMisunderstandingWarning(li, r.misunderstanding, cleanText, r.text, src, tgt, true);
    }

    if (S.session && S.peer) {
      S.session.send({
        type: 'utterance',
        speaker: S.name,
        original: cleanText,
        translated: r.text,
        source: src,
        target: tgt,
        simulated: r.simulated,
        matched: r.matched,
        provider: r.provider,
        misunderstanding: r.misunderstanding,
      });
    } else {
      // In single testing mode, play TTS preview if translation succeeded and audio not disabled
      if (r.text && S.output !== 'original') {
        S.queue.push({ text: r.text, lang: tgt });
        pump();
      }
    }
    setPipe('listening');
  } catch (e) {
    setTrans(li, e.message, tgt, '', false, true);
    fail(e);
  }
}

function fail(e) {
  setPipe('error');
  toast(e.message);
  setTimeout(() => {
    if (!S.leaving) setPipe('listening');
  }, 3500);
}

// ---------- Remote Utterance -> TTS ----------
async function onRemoteUtterance(m) {
  if (typeof m.original !== 'string') return;
  const originalText = m.original.slice(0, 1000);
  const src = m.source || (S.peer ? S.peer.spoken : 'hi');

  // Record remote utterance into in-memory transcript accumulator
  S.utterances.push({
    speaker: (S.peer && S.peer.name) || m.speaker || 'Participant',
    text: originalText,
    lang: src,
  });

  const li = addLine({
    who: (S.peer && S.peer.name) || m.speaker || 'Participant',
    own: false,
    original: originalText,
    source: src,
  });

  // Animate peer speaking wave
  const waveBars = $('.wave-bars');
  if (waveBars) {
    waveBars.classList.add('active');
    setTimeout(() => waveBars.classList.remove('active'), 2500);
  }

  // 1. If sender already provided a valid translation:
  if (m.translated && typeof m.translated === 'string' && m.translated.trim()) {
    const note = m.simulated
      ? (m.matched ? 'SIMULATED demo phrasebook' : 'Demo mode: no phrase match, not spoken')
      : '';
    setTrans(li, m.translated.slice(0, 1000), m.target || S.output, note);

    if (m.misunderstanding && m.misunderstanding.needs_confirmation) {
      renderMisunderstandingWarning(li, m.misunderstanding, originalText, m.translated, src, m.target || S.output, false);
    }

    if (m.matched === false) return;

    if (S.output !== 'original') {
      S.queue.push({ text: m.translated.slice(0, 1000), lang: m.target || S.output });
      pump();
    }
    return;
  }

  // 2. If sender sent empty translation or passthrough, but local user wants translation:
  const desiredOutput = S.output !== 'original' ? S.output : (src === 'hi' ? 'en' : 'hi');
  if (desiredOutput && desiredOutput !== 'original' && desiredOutput !== src) {
    setTrans(li, '', desiredOutput, '', true);
    try {
      const r = await api.translate(originalText, src, desiredOutput);
      const note = r.simulated
        ? (r.matched ? 'SIMULATED demo phrasebook' : 'Demo mode: no phrase match, not spoken')
        : '';
      setTrans(li, r.text, desiredOutput, note);

      if (r.misunderstanding && r.misunderstanding.needs_confirmation) {
        renderMisunderstandingWarning(li, r.misunderstanding, originalText, r.text, src, desiredOutput, false);
      }

      if (S.output !== 'original' && r.matched !== false) {
        S.queue.push({ text: r.text, lang: desiredOutput });
        pump();
      }
    } catch (e) {
      setTrans(li, e.message, desiredOutput, '', false, true);
    }
  }
}

// ---------- Smart Misunderstanding Detector Warning UI ----------
function renderMisunderstandingWarning(li, detectorResult, origText, transText, srcLang, tgtLang, isOwn) {
  if (!detectorResult || !detectorResult.needs_confirmation || !detectorResult.alerts || !detectorResult.alerts.length) return;

  // Avoid duplicate cards on the same transcript item
  if (li.querySelector('.misunderstanding-warning')) return;

  const card = document.createElement('div');
  const isHigh = detectorResult.severity === 'high';
  card.className = `misunderstanding-warning ${isHigh ? 'severity-high' : ''}`;
  card.setAttribute('role', 'alert');
  card.setAttribute('aria-live', 'polite');

  // Header with Badges and Close button
  const header = document.createElement('div');
  header.className = 'warning-header';

  const badgeContainer = document.createElement('div');
  badgeContainer.className = 'warning-badges';

  const badgeMap = {
    number: { label: 'Numbers Differ', icon: '🔢', cls: 'badge-number' },
    negation: { label: 'Negation Reversed', icon: '⚠️', cls: 'badge-negation' },
    unit: { label: 'Missing Units', icon: '📏', cls: 'badge-unit' },
    datetime: { label: 'Date/Time Anomaly', icon: '🕒', cls: 'badge-datetime' },
    contact: { label: 'Contact Info Mismatch', icon: '📞', cls: 'badge-contact' },
    empty: { label: 'Empty Translation', icon: '❓', cls: 'badge-empty' },
  };

  detectorResult.alerts.forEach((alert) => {
    const meta = badgeMap[alert.category] || { label: alert.category, icon: '⚠️', cls: 'badge-number' };
    const badge = document.createElement('span');
    badge.className = `alert-badge ${meta.cls}`;
    badge.textContent = `${meta.icon} ${meta.label}`;
    badgeContainer.append(badge);
  });
  header.append(badgeContainer);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'warning-close-btn';
  closeBtn.title = 'Dismiss alert';
  closeBtn.setAttribute('aria-label', 'Dismiss alert');
  closeBtn.textContent = '✕';
  closeBtn.onclick = () => card.remove();
  header.append(closeBtn);
  card.append(header);

  // Explanatory message
  const msgEl = document.createElement('div');
  msgEl.className = 'warning-msg';
  msgEl.textContent = detectorResult.alerts.map((a) => a.message).join(' | ');
  card.append(msgEl);

  // Side-by-side comparison box
  const cmpBox = document.createElement('div');
  cmpBox.className = 'warning-comparison';

  const colOrig = document.createElement('div');
  colOrig.className = 'cmp-col';
  const lblOrig = document.createElement('span');
  lblOrig.className = 'cmp-label';
  lblOrig.textContent = `Original (${lang(srcLang).name || srcLang}):`;
  const valOrig = document.createElement('span');
  valOrig.className = 'cmp-val';
  valOrig.textContent = origText;
  colOrig.append(lblOrig, valOrig);

  const colTrans = document.createElement('div');
  colTrans.className = 'cmp-col';
  const lblTrans = document.createElement('span');
  lblTrans.className = 'cmp-label';
  lblTrans.textContent = `Translated (${lang(tgtLang).name || tgtLang}):`;
  const valTrans = document.createElement('span');
  valTrans.className = 'cmp-val';
  valTrans.textContent = transText;
  colTrans.append(lblTrans, valTrans);

  cmpBox.append(colOrig, colTrans);
  card.append(cmpBox);

  // Action Buttons
  const actions = document.createElement('div');
  actions.className = 'warning-actions';

  // 1. Confirm details button
  const confirmBtn = document.createElement('button');
  confirmBtn.type = 'button';
  confirmBtn.className = 'warning-action-btn confirm-btn';
  confirmBtn.textContent = '✓ Confirm details';
  confirmBtn.onclick = () => {
    card.classList.remove('severity-high');
    card.classList.add('confirmed');
    actions.innerHTML = '<span class="confirmed-tag">✓ Confirmed & Verified</span>';
    toast('Translation discrepancy confirmed as intended.');
    if (S.session && S.peer) {
      S.session.send({
        type: 'translation_warning',
        action: 'confirmed',
        speaker: S.name,
        original: origText,
        translated: transText,
      });
    }
  };
  actions.append(confirmBtn);

  // 2. Repeat original speech button
  const repeatBtn = document.createElement('button');
  repeatBtn.type = 'button';
  repeatBtn.className = 'warning-action-btn repeat-btn';
  repeatBtn.textContent = '🔊 Repeat original';
  repeatBtn.onclick = async () => {
    toast(`Replaying original in ${lang(srcLang).name || srcLang}…`);
    try {
      await speak(origText, srcLang);
    } catch {
      toast('Could not playback original speech.');
    }
  };
  actions.append(repeatBtn);

  // 3. Dismiss button
  const dismissBtn = document.createElement('button');
  dismissBtn.type = 'button';
  dismissBtn.className = 'warning-action-btn dismiss-btn';
  dismissBtn.textContent = 'Dismiss';
  dismissBtn.onclick = () => card.remove();
  actions.append(dismissBtn);

  card.append(actions);
  li.append(card);
}

function onTranslationWarning(m) {
  if (m.action === 'confirmed') {
    toast(`${m.speaker || 'Peer'} confirmed: "${m.original || m.translated || ''}" is correct.`);
    document.querySelectorAll('.misunderstanding-warning:not(.confirmed)').forEach((card) => {
      const origEl = card.querySelector('.cmp-val');
      if (origEl && m.original && origEl.textContent.trim() === m.original.trim()) {
        card.classList.remove('severity-high');
        card.classList.add('confirmed');
        const act = card.querySelector('.warning-actions');
        if (act) act.innerHTML = `<span class="confirmed-tag">✓ Confirmed by ${m.speaker || 'Peer'}</span>`;
      }
    });
  }
}

async function pump() {
  if (S.speaking) return;
  S.speaking = true;
  applyVol();
  const waveBars = $('.wave-bars');
  if (waveBars) waveBars.classList.add('active');

  while (!S.leaving) {
    while (S.queue.length && !S.leaving) {
      const it = S.queue.shift();
      setPipe('speaking');
      try {
        await speak(it.text, it.lang);
      } catch (e) {
        toast(e.message);
      }
    }
    await new Promise((r) => setTimeout(r, 400));
    if (!S.queue.length) break;
  }

  S.speaking = false;
  if (waveBars) waveBars.classList.remove('active');
  applyVol();
  if (!S.leaving) setPipe(S.peer ? 'listening' : 'idle');
}

// Rock-solid TTS playback with server support, browser fallback, and cross-language voice mapping
async function speak(text, code) {
  if (!code || code === 'original' || !text || !text.trim()) return;

  // 1. Attempt Server TTS if configured (with automatic fallback on error)
  if (S.cfg?.providers?.tts && S.cfg.providers.tts !== 'browser') {
    try {
      const blob = await api.tts(text, code);
      const url = URL.createObjectURL(blob);
      const a = $('#ttsAudio');
      a.src = url;
      await new Promise((res, rej) => {
        a.onended = res;
        a.onerror = () => rej(new Error('Could not play synthesized audio.'));
        a.play().catch(rej);
      });
      URL.revokeObjectURL(url);
      return;
    } catch (err) {
      console.warn('Server TTS failed or exhausted quota, using browser speech synthesis:', err);
      // Fall through to browser speech synthesis
    }
  }

  // 2. Browser Native SpeechSynthesis
  if (!('speechSynthesis' in window)) {
    throw new Error('Speech synthesis is not supported in this browser.');
  }

  try {
    speechSynthesis.cancel();
    speechSynthesis.resume();
  } catch { /* ignore */ }

  const tag = lang(code).tts || code;
  const u = new SpeechSynthesisUtterance(text.trim());
  u.lang = tag;
  u.rate = 1.0;
  u.pitch = 1.0;

  // Flexible voice matching across language tags and prefixes
  const voices = speechSynthesis.getVoices();
  if (voices && voices.length) {
    const codeLow = code.toLowerCase();
    const tagLow = tag.toLowerCase().replace('_', '-');
    const matchedVoice =
      voices.find((x) => x.lang.toLowerCase().replace('_', '-') === tagLow) ||
      voices.find((x) => x.lang.toLowerCase().replace('_', '-').startsWith(codeLow)) ||
      voices.find((x) => x.lang.toLowerCase().includes(codeLow));
    if (matchedVoice) {
      u.voice = matchedVoice;
    }
  }

  await new Promise((res) => {
    let resolved = false;
    const done = () => {
      if (!resolved) {
        resolved = true;
        res();
      }
    };
    u.onend = done;
    u.onerror = (e) => {
      console.warn('speechSynthesis utterance completed/errored:', e);
      done();
    };
    setTimeout(done, Math.max(3000, text.length * 120));
    speechSynthesis.speak(u);
  });
}

function translating() {
  return S.output !== 'original' && !!S.peer && S.peer.spoken !== S.output;
}

function applyVol() {
  const volEl = $('#origVol');
  const val = volEl ? volEl.value : 10;
  const volVal = $('#volVal');
  if (volVal) volVal.textContent = `${val}%`;
  $('#remoteAudio').volume = S.speaking ? 0 : translating() ? val / 100 : 1;
}
$('#origVol').oninput = applyVol;

// ---------- Controls ----------
$('#muteBtn').onclick = () => {
  S.muted = !S.muted;
  S.stream.getAudioTracks().forEach((t) => { t.enabled = !S.muted; });
  $('#muteBtn').setAttribute('aria-pressed', String(S.muted));
  $('#meMic').textContent = S.muted ? 'Microphone: muted' : 'Microphone: on';
  toast(S.muted ? 'Microphone muted' : 'Microphone unmuted');
};

function langChanged() {
  S.spoken = $('#spoken2').value;
  S.output = $('#output2').value;
  S.listener?.setLang(lang(S.spoken).stt);
  S.session?.sendProfile();
  applyVol();
}
$('#spoken2').onchange = langChanged;
$('#output2').onchange = langChanged;

$('#copyBtn').onclick = async () => {
  const link = `${location.origin}/?room=${$('#roomCode').textContent}`;
  const textEl = $('#copyBtnText');
  try {
    await navigator.clipboard.writeText(link);
    if (textEl) textEl.textContent = 'Copied!';
    toast('Room invite link copied to clipboard.');
    setTimeout(() => { if (textEl) textEl.textContent = 'Copy Link'; }, 2200);
  } catch {
    toast(link);
  }
};

// Leave Call Flow with Summary Offer
function handleLeaveRequest() {
  if (S.utterances.length > 0) {
    $('#leaveConfirmModal').hidden = false;
  } else {
    performLeave();
  }
}

function performLeave() {
  $('#leaveConfirmModal').hidden = true;
  S.leaving = true;
  stopTimer();
  S.queue = [];
  S.listener?.stop();
  S.session?.leave();
  S.stream?.getTracks().forEach((t) => t.stop());
  speechSynthesis?.cancel?.();

  $('#remoteAudio').srcObject = null;
  $('#timer').textContent = '00:00';
  $('#meter').style.width = '0';

  S.listener = S.session = S.stream = S.peer = null;
  S.speaking = false;
  S.listenerOn = false;

  history.replaceState(null, '', location.pathname);
  setPipe('idle');
  show('home');
}

$('#leaveBtn').onclick = handleLeaveRequest;
$('#confirmLeaveBtn').onclick = performLeave;
$('#cancelLeaveBtn').onclick = () => { $('#leaveConfirmModal').hidden = true; };
$('#cancelLeaveXBtn').onclick = () => { $('#leaveConfirmModal').hidden = true; };

$('#leaveAndSummarizeBtn').onclick = () => {
  $('#leaveConfirmModal').hidden = true;
  // Stop ongoing call audio & media tracks, but preserve S.utterances
  S.leaving = true;
  stopTimer();
  S.listener?.stop();
  S.session?.leave();
  S.stream?.getTracks().forEach((t) => t.stop());
  speechSynthesis?.cancel?.();
  $('#remoteAudio').srcObject = null;
  setPipe('idle');

  // Trigger Summary Modal
  openSummary();
};

window.addEventListener('beforeunload', () => S.session?.leave());

// ---------- Call Summary Feature ----------
async function openSummary(targetLang = null) {
  const modal = $('#summaryModal');
  modal.hidden = false;

  const langSelect = $('#summaryLang');
  if (targetLang) {
    langSelect.value = targetLang;
  } else if (!langSelect.value) {
    langSelect.value = (S.output === 'original' || !S.output) ? 'en' : S.output;
  }
  const langCode = langSelect.value;

  if (!S.utterances.length) {
    $('#summaryEmpty').hidden = false;
    $('#summaryLoading').hidden = true;
    $('#summaryError').hidden = true;
    $('#summaryContent').hidden = true;
    return;
  }

  $('#summaryEmpty').hidden = true;
  $('#summaryLoading').hidden = false;
  $('#summaryError').hidden = true;
  $('#summaryContent').hidden = true;

  try {
    const data = await api.summarize(S.utterances, langCode);
    S.lastSummary = data;
    renderSummary(data);
    $('#summaryLoading').hidden = true;
    $('#summaryContent').hidden = false;
  } catch (e) {
    $('#summaryLoading').hidden = true;
    $('#summaryErrorMsg').textContent = e.message || 'Could not generate summary.';
    $('#summaryError').hidden = false;
  }
}

function renderSummary(data) {
  $('#summaryTitle').textContent = data.title || 'Call Summary';
  $('#summaryText').textContent = data.summary || 'No summary text available.';

  // Key Points
  const kp = $('#summaryKeyPoints');
  kp.innerHTML = '';
  if (data.key_points && data.key_points.length) {
    data.key_points.forEach((pt) => {
      const li = document.createElement('li');
      li.textContent = pt;
      kp.append(li);
    });
    $('#keyPointsSec').hidden = false;
  } else {
    $('#keyPointsSec').hidden = true;
  }

  // Action Items (Interactive Checkbox Style)
  const ai = $('#summaryActionItems');
  ai.innerHTML = '';
  if (data.action_items && data.action_items.length) {
    data.action_items.forEach((item, idx) => {
      const li = document.createElement('li');
      li.className = 'action-item';

      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.className = 'action-checkbox';
      chk.id = `action_item_${idx}`;
      chk.onchange = () => { li.classList.toggle('completed', chk.checked); };

      const details = document.createElement('div');
      details.className = 'action-details';

      const task = document.createElement('div');
      task.className = 'action-task';
      task.textContent = item.task;
      details.append(task);

      if (item.owner) {
        const owner = document.createElement('span');
        owner.className = 'action-owner';
        owner.textContent = `@${item.owner}`;
        details.append(owner);
      }

      li.append(chk, details);
      ai.append(li);
    });
    $('#actionItemsSec').hidden = false;
  } else {
    $('#actionItemsSec').hidden = true;
  }

  // Decisions
  const dec = $('#summaryDecisions');
  dec.innerHTML = '';
  if (data.decisions && data.decisions.length) {
    data.decisions.forEach((d) => {
      const li = document.createElement('li');
      li.textContent = d;
      dec.append(li);
    });
    $('#decisionsSec').hidden = false;
  } else {
    $('#decisionsSec').hidden = true;
  }
}

// Summary Modal Triggers
$('#summaryBtn').onclick = () => openSummary();
const headerSummary = $('#headerSummaryBtn');
if (headerSummary) headerSummary.onclick = () => openSummary();
$('#closeSummaryBtn').onclick = () => { $('#summaryModal').hidden = true; };
$('#retrySummaryBtn').onclick = () => openSummary($('#summaryLang').value);
$('#regenerateSummaryBtn').onclick = () => openSummary($('#summaryLang').value);
$('#summaryLang').onchange = () => openSummary($('#summaryLang').value);

// Summary Export Helpers
function getSummaryMarkdown(data) {
  if (!data) return '';
  let md = `# ${data.title || 'Call Summary'}\n\n`;
  md += `## Summary\n${data.summary}\n\n`;
  if (data.key_points && data.key_points.length) {
    md += `## Key Discussion Points\n`;
    data.key_points.forEach((k) => { md += `- ${k}\n`; });
    md += `\n`;
  }
  if (data.action_items && data.action_items.length) {
    md += `## Action Items\n`;
    data.action_items.forEach((a) => {
      const owner = a.owner ? ` (@${a.owner})` : '';
      md += `- [ ] ${a.task}${owner}\n`;
    });
    md += `\n`;
  }
  if (data.decisions && data.decisions.length) {
    md += `## Decisions Made\n`;
    data.decisions.forEach((d) => { md += `- ${d}\n`; });
    md += `\n`;
  }
  md += `---\n*Generated by BhashaBridge AI*\n`;
  return md;
}

function getSummaryText(data) {
  if (!data) return '';
  let txt = `${(data.title || 'Call Summary').toUpperCase()}\n`;
  txt += `${'='.repeat(40)}\n\n`;
  txt += `SUMMARY:\n${data.summary}\n\n`;
  if (data.key_points && data.key_points.length) {
    txt += `KEY DISCUSSION POINTS:\n`;
    data.key_points.forEach((k) => { txt += `• ${k}\n`; });
    txt += `\n`;
  }
  if (data.action_items && data.action_items.length) {
    txt += `ACTION ITEMS:\n`;
    data.action_items.forEach((a) => {
      const owner = a.owner ? ` (@${a.owner})` : '';
      txt += `[ ] ${a.task}${owner}\n`;
    });
    txt += `\n`;
  }
  if (data.decisions && data.decisions.length) {
    txt += `DECISIONS:\n`;
    data.decisions.forEach((d) => { txt += `• ${d}\n`; });
    txt += `\n`;
  }
  return txt;
}

function downloadFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

$('#copySummaryBtn').onclick = async () => {
  if (!S.lastSummary) return toast('No summary available to copy.');
  const md = getSummaryMarkdown(S.lastSummary);
  try {
    await navigator.clipboard.writeText(md);
    toast('Summary copied to clipboard as Markdown.');
  } catch {
    toast('Could not copy to clipboard.');
  }
};

$('#downloadSummaryMdBtn').onclick = () => {
  if (!S.lastSummary) return toast('No summary available to download.');
  const md = getSummaryMarkdown(S.lastSummary);
  const name = (S.lastSummary.title || 'call_summary').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  downloadFile(`${name}.md`, md, 'text/markdown');
  toast('Summary downloaded as .md');
};

$('#downloadSummaryTxtBtn').onclick = () => {
  if (!S.lastSummary) return toast('No summary available to download.');
  const txt = getSummaryText(S.lastSummary);
  const name = (S.lastSummary.title || 'call_summary').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  downloadFile(`${name}.txt`, txt, 'text/plain');
  toast('Summary downloaded as .txt');
};

init();
