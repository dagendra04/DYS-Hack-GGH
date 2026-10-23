import json
import logging
import secrets
import string
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .languages import is_supported, language_list
from .providers import stt, summarize, translate, tts
from .providers.errors import ProviderError
from .providers.misunderstanding import detect_misunderstanding
from .ratelimit import limiter
from .signaling import MAX_MSG, ROOM_RE, rooms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bhashabridge")  # never log keys, audio or transcript text

app = FastAPI(title="BhashaBridge AI (prototype)")
app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().allowed_origins), allow_methods=["GET", "POST"], allow_headers=["*"])
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, e: ProviderError):
    log.warning("provider error code=%s status=%s", e.code, e.status)
    return JSONResponse(status_code=e.status, content={"detail": {"code": e.code, "message": e.message}})


async def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(ip, get_settings().rate_limit):
        raise ProviderError(429, "rate_limited", "Too many requests. Please slow down for a moment.")


class TranslateIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    source: str
    target: str


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    language: str


class UtteranceIn(BaseModel):
    speaker: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    lang: str = Field(default="en", max_length=10)


class SummarizeIn(BaseModel):
    utterances: list[UtteranceIn] = Field(default_factory=list, max_length=200)
    output_language: str = Field(default="en")


class MisunderstandingCheckIn(BaseModel):
    source_text: str = Field(default="", max_length=2000)
    translated_text: str = Field(default="", max_length=2000)
    source_lang: str = Field(default="hi")
    target_lang: str = Field(default="en")


def _check_lang(*codes):
    for c in codes:
        if not is_supported(c):
            raise ProviderError(400, "unsupported_language", f"Language '{c}' is not supported.")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def config():
    s = get_settings()
    return {
        "app_env": s.app_env,
        "providers": {"stt": s.stt_provider, "translate": s.translate_provider, "tts": s.tts_provider},
        "demo_translation": s.translate_provider == "demo",
        "warnings": s.warnings(),
        "languages": language_list(),
        "ice_servers": s.ice_servers(),
    }


@app.post("/api/rooms", dependencies=[Depends(rate_limit)])
async def create_room():
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    return {"room": "".join(secrets.choice(alphabet) for _ in range(6))}


@app.post("/api/translate", dependencies=[Depends(rate_limit)])
async def api_translate(body: TranslateIn):
    _check_lang(body.source, body.target)
    result = await translate.translate(body.text.strip(), body.source, body.target)
    log.info("translate %s->%s provider=%s chars=%d", body.source, body.target, result["provider"], len(body.text))
    try:
        check = detect_misunderstanding(body.text.strip(), result.get("text", ""), body.source, body.target)
        result["misunderstanding"] = check
    except Exception as e:
        log.warning("misunderstanding detector error: %s", type(e).__name__)
        result["misunderstanding"] = {"needs_confirmation": False, "alerts": []}
    return result


@app.post("/api/misunderstanding/check", dependencies=[Depends(rate_limit)])
async def api_misunderstanding_check(body: MisunderstandingCheckIn):
    return detect_misunderstanding(body.source_text, body.translated_text, body.source_lang, body.target_lang)


@app.post("/api/stt", dependencies=[Depends(rate_limit)])
async def api_stt(audio: UploadFile = File(...), language: str = Form(...)):
    _check_lang(language)
    data = await audio.read()
    if not data or len(data) > 2_000_000:
        raise ProviderError(400, "bad_audio", "Audio is empty or larger than 2 MB.")
    text = await stt.transcribe(data, audio.filename or "speech.webm", audio.content_type or "audio/webm", language)
    log.info("stt lang=%s bytes=%d chars=%d", language, len(data), len(text))
    return {"text": text}


@app.post("/api/tts", dependencies=[Depends(rate_limit)])
async def api_tts(body: TTSIn):
    _check_lang(body.language)
    audio, media_type = await tts.synthesize(body.text.strip())
    log.info("tts lang=%s chars=%d", body.language, len(body.text))
    return Response(content=audio, media_type=media_type)


@app.post("/api/summarize", dependencies=[Depends(rate_limit)])
async def api_summarize(body: SummarizeIn):
    if not body.utterances:
        raise ProviderError(400, "empty_transcript", "Transcript is empty. Nothing to summarize.")
    total_chars = sum(len(u.text) for u in body.utterances)
    if total_chars > 30000:
        raise ProviderError(422, "too_long", "Transcript exceeds the maximum limit of 30,000 characters.")
    _check_lang(body.output_language)
    result = await summarize.summarize([u.model_dump() for u in body.utterances], body.output_language)
    log.info("summarize utterances=%d chars=%d lang=%s", len(body.utterances), total_chars, body.output_language)
    return result


@app.websocket("/ws/{room_id}")
async def ws_room(ws: WebSocket, room_id: str):
    code = room_id.upper()
    await ws.accept()
    if not ROOM_RE.match(code):
        await ws.send_json({"type": "error", "message": "Invalid room code (4-12 letters/digits)."})
        await ws.close(code=4000)
        return
    pid = await rooms.join(code, ws)
    if pid is None:
        await ws.send_json({"type": "room_full", "message": "This room already has two participants."})
        await ws.close(code=4003)
        return
    log.info("room %s: peer joined", code)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_MSG:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await rooms.relay(code, pid, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await rooms.leave(code, pid)
        log.info("room %s: peer left", code)


app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
