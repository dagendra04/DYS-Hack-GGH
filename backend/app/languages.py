"""Add a new language by adding one entry here (and nothing else for the frontend)."""
LANGUAGES = {
    "hi": {"name": "Hindi", "native": "हिन्दी", "stt": "hi-IN", "tts": "hi-IN"},
    "en": {"name": "English", "native": "English", "stt": "en-IN", "tts": "en-US"},
}


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def language_list() -> list:
    return [{"code": c, **v} for c, v in LANGUAGES.items()]
