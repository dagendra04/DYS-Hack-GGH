"""Thin Gemini (Google AI Studio) client shared by STT, translation and TTS."""
from ..config import get_settings
from .errors import ProviderError, from_provider
from .http import post

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


async def generate(model: str, body: dict, timeout: float = 30) -> dict:
    key = get_settings().gemini_api_key
    if not key:
        raise ProviderError(503, "missing_api_key", "GEMINI_API_KEY is not set in .env.")
    # Key goes in a header (not the URL) so it can never end up in access logs.
    r = await post(f"{BASE}/{model}:generateContent", timeout=timeout, headers={"x-goog-api-key": key}, json=body)
    if r.status_code == 400 and "API_KEY_INVALID" in r.text:  # Gemini answers 400, not 401, for a bad key
        raise from_provider("Gemini", 401)
    if r.status_code != 200:
        raise from_provider("Gemini", r.status_code)
    return r.json()


def parts(resp: dict) -> list:
    try:
        return resp["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        reason = (resp.get("promptFeedback") or {}).get("blockReason") or "empty response"
        raise ProviderError(502, "provider_failed", f"Gemini returned no result ({reason}).")


def text(resp: dict) -> str:
    return "".join(p.get("text", "") for p in parts(resp) if not p.get("thought")).strip()
