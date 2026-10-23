async function req(path, opts = {}) {
  let r;
  try { r = await fetch(path, opts); }
  catch { throw new Error('Network error: cannot reach the BhashaBridge server.'); }
  if (!r.ok) {
    let msg = `Server error (${r.status})`;
    try {
      const j = await r.json();
      if (j.detail && j.detail.message) msg = j.detail.message;
      else if (Array.isArray(j.detail)) msg = 'Invalid request.';
    } catch { /* keep default */ }
    throw new Error(msg);
  }
  return r;
}
const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const getConfig = async () => (await req('/api/config')).json();
export const createRoom = async () => (await req('/api/rooms', { method: 'POST' })).json();
export const translate = async (text, source, target) => (await req('/api/translate', json({ text, source, target }))).json();
// Gemini accepts wav/mp3/ogg/flac but not the browser's webm/mp4, so re-encode to 16 kHz mono WAV (also keeps uploads small).
async function toWav(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
    const off = new OfflineAudioContext(1, Math.ceil(buf.duration * 16000), 16000);
    const src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
    const pcm = (await off.startRendering()).getChannelData(0);
    const v = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const w = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVEfmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, 'data'); v.setUint32(40, pcm.length * 2, true);
    pcm.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 0x7fff, true));
    return new Blob([v], { type: 'audio/wav' });
  } finally { ctx.close().catch(() => {}); }
}
export const stt = async (blob, language) => {
  let file = blob, ext = blob.type.includes('mp4') ? 'mp4' : 'webm';
  try { file = await toWav(blob); ext = 'wav'; } catch { /* keep the original recording */ }
  const f = new FormData();
  f.append('audio', file, `speech.${ext}`);
  f.append('language', language);
  return (await req('/api/stt', { method: 'POST', body: f })).json();
};
export const tts = async (text, language) => (await req('/api/tts', json({ text, language }))).blob();
