"""Natural Language Processing (NLP) module for real-time speech translation.
Provides text normalization, code-mixing detection (Hinglish/multilingual),
phonetic script identification, and semantic clause segmentation.
"""
import re
import unicodedata

# Common Hinglish/Romanized Hindi lexicon tokens
HINGLISH_KEYWORDS = {
    "kya", "hai", "hain", "nahi", "nahin", "theek", "thik", "haan", "accha", "achha",
    "kaise", "kaisa", "kaisi", "aap", "tum", "mera", "meri", "mere", "tera", "teri",
    "hum", "karna", "karo", "karenge", "raha", "rahi", "rahe", "batao", "bataiye",
    "shukriya", "dhanyawad", "namaste", "kal", "aaj", "ab", "kab", "kyun", "kyu",
    "lekin", "par", "aur", "toh", "to", "matlab", "suno", "samjhe", "chalo", "chal",
}

# Unicode Script Block Ranges
SCRIPT_RANGES = [
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("arabic", 0x0600, 0x06FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("cjk", 0x4E00, 0x9FFF),
    ("latin", 0x0041, 0x007A),
]

# Filler and stutter patterns in spoken speech
DISFLUENCIES = re.compile(
    r"\b(um+|uh+|err+|ahh+|ah+|hmm+|mm+|like\s+basically|you\s+know|matlab|yaani)\b",
    re.IGNORECASE,
)

# Repeated words stutter pattern (e.g., "I I I think" -> "I think", "haan haan" preserved up to 2)
STUTTER_PATTERN = re.compile(r"\b(\w+)(?:\s+\1){2,}\b", re.IGNORECASE)


def detect_script(text: str) -> str:
    """Detects the primary writing script of the given text."""
    counts: dict[str, int] = {}
    for char in text:
        cp = ord(char)
        for name, start, end in SCRIPT_RANGES:
            if start <= cp <= end:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return "latin"
    return max(counts, key=counts.get)


def detect_code_mixing(text: str, source_lang: str) -> bool:
    """Detects whether speech contains code-switching (e.g. Hinglish: Hindi in Latin script or mixed English)."""
    words = [w.lower() for w in re.findall(r"\b\w+\b", text)]
    if not words:
        return False

    script = detect_script(text)
    # If source is Hindi or Indian language, but written in Latin script with Hinglish markers
    if source_lang in ("hi", "ur", "mr", "gu", "bn", "pa") and script == "latin":
        hinglish_count = sum(1 for w in words if w in HINGLISH_KEYWORDS)
        if hinglish_count >= 1 or len(words) <= 3:
            return True

    # Mixed script detection (e.g. Devanagari mixed with English words)
    has_devanagari = any(0x0900 <= ord(c) <= 0x097F for c in text)
    has_latin = any(0x0041 <= ord(c) <= 0x007A for c in text)
    if has_devanagari and has_latin:
        return True

    return False


def normalize_speech(text: str) -> str:
    """Cleans disfluencies, excess whitespace, and normalizes unicode for spoken text."""
    if not text:
        return ""
    # Unicode canonical normalization
    text = unicodedata.normalize("NFC", text.strip())

    # Replace consecutive stutters (3+ repetitions -> single)
    text = STUTTER_PATTERN.sub(r"\1", text)

    # Clean redundant fillers while preserving natural flow
    text = DISFLUENCIES.sub("", text)

    # Collapse multiple spaces and trim
    text = re.sub(r"\s+", " ", text).strip()
    return text


def segment_clauses(text: str) -> list[str]:
    """Segments continuous speech into natural clauses or sentences for real-time translation."""
    if not text:
        return []
    # Split on sentence boundaries: periods, question marks, exclamation, Hindi purna viram (।)
    raw_segments = re.split(r"(?<=[.!?।])\s+", text)
    segments = [s.strip() for s in raw_segments if s.strip()]
    return segments if segments else [text.strip()]


def detect_speech_intent(text: str) -> str:
    """Categorizes intent (question, exclamation, statement) to assist TTS and translation prosody."""
    t = text.strip()
    if t.endswith("?") or any(w in t.lower().split() for w in ("kya", "why", "how", "what", "when", "who", "where", "kab", "kaise", "kyun")):
        return "question"
    if t.endswith("!") or any(w in t.lower().split() for w in ("wow", "arre", "arrey", "shabash", "great")):
        return "exclamation"
    return "statement"


def process_nlp(text: str, source_lang: str) -> dict:
    """Full NLP analysis of incoming spoken utterance."""
    normalized = normalize_speech(text)
    script = detect_script(normalized)
    is_code_mixed = detect_code_mixing(normalized, source_lang)
    intent = detect_speech_intent(normalized)
    clauses = segment_clauses(normalized)

    return {
        "original": text,
        "normalized": normalized,
        "script": script,
        "is_code_mixed": is_code_mixed,
        "intent": intent,
        "clause_count": len(clauses),
    }
