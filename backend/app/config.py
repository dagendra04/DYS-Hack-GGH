"""Central configuration. Reads env at call time; secrets never leave the server."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


@dataclass(frozen=True)
class Settings:
    app_env: str
    host: str
    port: int
    openai_api_key: str
    google_translate_api_key: str
    mistral_api_key: str
    gemini_api_key: str
    gemini_stt_model: str
    gemini_translate_model: str
    gemini_tts_model: str
    gemini_tts_voice: str
    mistral_stt_model: str
    mistral_translate_model: str
    stt_pref: str
    translate_pref: str
    tts_pref: str
    openai_stt_model: str
    openai_translate_model: str
    openai_tts_model: str
    openai_tts_voice: str
    stun_url: str
    turn_url: str
    turn_user: str
    turn_cred: str
    rate_limit: int
    allowed_origins: tuple

    @property
    def stt_provider(self) -> str:
        if self.stt_pref != "auto":
            return self.stt_pref
        if self.gemini_api_key:
            return "gemini"
        return "openai" if self.openai_api_key else "browser"

    @property
    def translate_provider(self) -> str:
        if self.translate_pref != "auto":
            return self.translate_pref
        if self.gemini_api_key:
            return "gemini"
        if self.mistral_api_key:
            return "mistral"
        if self.openai_api_key:
            return "openai"
        if self.google_translate_api_key:
            return "google"
        return "demo"

    @property
    def tts_provider(self) -> str:
        if self.tts_pref != "auto":
            return self.tts_pref
        if self.gemini_api_key:
            return "gemini"
        return "openai" if self.openai_api_key else "browser"

    def warnings(self) -> list:
        w = []
        used = (self.stt_provider, self.translate_provider, self.tts_provider)
        if "openai" in used and not self.openai_api_key:
            w.append("A provider is set to 'openai' but OPENAI_API_KEY is missing in .env.")
        if "gemini" in used and not self.gemini_api_key:
            w.append("A provider is set to 'gemini' but GEMINI_API_KEY is missing in .env.")
        if "mistral" in used and not self.mistral_api_key:
            w.append("A provider is set to 'mistral' but MISTRAL_API_KEY is missing in .env.")
        if self.translate_provider == "google" and not self.google_translate_api_key:
            w.append("TRANSLATE_PROVIDER=google but GOOGLE_TRANSLATE_API_KEY is missing in .env.")
        if self.turn_url and not (self.turn_user and self.turn_cred):
            w.append("TURN_SERVER_URL is set but TURN_USERNAME/TURN_CREDENTIAL are missing.")
        return w

    def ice_servers(self) -> list:
        servers = []
        if self.stun_url:
            servers.append({"urls": self.stun_url})
        if self.turn_url:
            servers.append({"urls": self.turn_url, "username": self.turn_user, "credential": self.turn_cred})
        return servers


def get_settings() -> Settings:
    try:
        rate = int(_get("RATE_LIMIT_PER_MIN", "60"))
    except ValueError:
        rate = 60
    return Settings(
        app_env=_get("APP_ENV", "development"),
        host=_get("HOST", "127.0.0.1"),
        port=int(_get("PORT", "8000") or 8000),
        openai_api_key=_get("OPENAI_API_KEY"),
        google_translate_api_key=_get("GOOGLE_TRANSLATE_API_KEY"),
        mistral_api_key=_get("MISTRAL_API_KEY"),
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_stt_model=_get("GEMINI_STT_MODEL", "gemini-3.5-flash"),
        gemini_translate_model=_get("GEMINI_TRANSLATE_MODEL", "gemini-3.5-flash-lite"),
        gemini_tts_model=_get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"),
        gemini_tts_voice=_get("GEMINI_TTS_VOICE", "Kore"),
        mistral_stt_model=_get("MISTRAL_STT_MODEL", "voxtral-mini-latest"),
        mistral_translate_model=_get("MISTRAL_TRANSLATE_MODEL", "mistral-small-latest"),
        stt_pref=_get("STT_PROVIDER", "auto").lower(),
        translate_pref=_get("TRANSLATE_PROVIDER", "auto").lower(),
        tts_pref=_get("TTS_PROVIDER", "auto").lower(),
        openai_stt_model=_get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe"),
        openai_translate_model=_get("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini"),
        openai_tts_model=_get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        openai_tts_voice=_get("OPENAI_TTS_VOICE", "alloy"),
        stun_url=_get("STUN_SERVER_URL", "stun:stun.l.google.com:19302"),
        turn_url=_get("TURN_SERVER_URL"),
        turn_user=_get("TURN_USERNAME"),
        turn_cred=_get("TURN_CREDENTIAL"),
        rate_limit=rate,
        allowed_origins=tuple(o.strip() for o in _get("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()),
    )
