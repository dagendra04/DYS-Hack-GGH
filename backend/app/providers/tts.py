import base64
import io
import wave

from ..config import get_settings
from . import gemini
from .errors import ProviderError, from_openai
from .http import post


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    """Gemini TTS returns raw 16-bit mono PCM; browsers need a WAV header to play it."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def synthesize(text: str) -> tuple:
    """Returns (audio_bytes, media_type)."""
    s = get_settings()
    if s.tts_provider == "gemini":
        body = {"contents": [{"parts": [{"text": f"Say in a natural, clear conversational tone: {text}"}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": s.gemini_tts_voice}}}}}
        resp = await gemini.generate(s.gemini_tts_model, body, timeout=40)
        for part in gemini.parts(resp):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return _pcm_to_wav(base64.b64decode(inline["data"])), "audio/wav"
        raise ProviderError(502, "provider_failed", "Gemini returned no audio.")
    if s.tts_provider != "openai":
        raise ProviderError(400, "tts_not_server_side", "Server TTS is off. The browser's speech synthesis is used instead.")
    if not s.openai_api_key:
        raise ProviderError(503, "missing_api_key", "OPENAI_API_KEY is not set in .env.")
    r = await post("https://api.openai.com/v1/audio/speech", timeout=40, headers={"Authorization": f"Bearer {s.openai_api_key}"},
                   json={"model": s.openai_tts_model, "voice": s.openai_tts_voice, "input": text, "response_format": "mp3"})
    if r.status_code != 200:
        raise from_openai(r.status_code)
    return r.content, "audio/mpeg"
