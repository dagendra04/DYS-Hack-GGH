"""Supported languages across Indian and global speech systems.
Add or modify language metadata here; both frontend and backend read from this registry.
"""

LANGUAGES = {
    # Major Indian Languages
    "hi": {"name": "Hindi", "native": "हिन्दी", "stt": "hi-IN", "tts": "hi-IN"},
    "en": {"name": "English", "native": "English", "stt": "en-IN", "tts": "en-US"},
    "bn": {"name": "Bengali", "native": "বাংলা", "stt": "bn-IN", "tts": "bn-IN"},
    "te": {"name": "Telugu", "native": "తెలుగు", "stt": "te-IN", "tts": "te-IN"},
    "mr": {"name": "Marathi", "native": "मराठी", "stt": "mr-IN", "tts": "mr-IN"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "stt": "ta-IN", "tts": "ta-IN"},
    "gu": {"name": "Gujarati", "native": "ગુજરાતી", "stt": "gu-IN", "tts": "gu-IN"},
    "kn": {"name": "Kannada", "native": "ಕನ್ನಡ", "stt": "kn-IN", "tts": "kn-IN"},
    "ml": {"name": "Malayalam", "native": "മലയാളം", "stt": "ml-IN", "tts": "ml-IN"},
    "pa": {"name": "Punjabi", "native": "ਪੰਜਾਬੀ", "stt": "pa-IN", "tts": "pa-IN"},
    "ur": {"name": "Urdu", "native": "اردو", "stt": "ur-IN", "tts": "ur-IN"},
    "or": {"name": "Odia", "native": "ଓଡ଼ିଆ", "stt": "or-IN", "tts": "or-IN"},

    # Major Global Languages
    "es": {"name": "Spanish", "native": "Español", "stt": "es-ES", "tts": "es-ES"},
    "fr": {"name": "French", "native": "Français", "stt": "fr-FR", "tts": "fr-FR"},
    "de": {"name": "German", "native": "Deutsch", "stt": "de-DE", "tts": "de-DE"},
    "ar": {"name": "Arabic", "native": "العربية", "stt": "ar-SA", "tts": "ar-SA"},
    "ja": {"name": "Japanese", "native": "日本語", "stt": "ja-JP", "tts": "ja-JP"},
    "ru": {"name": "Russian", "native": "Русский", "stt": "ru-RU", "tts": "ru-RU"},
    "pt": {"name": "Portuguese", "native": "Português", "stt": "pt-BR", "tts": "pt-BR"},
}


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def language_list() -> list:
    return [{"code": c, **v} for c, v in LANGUAGES.items()]
