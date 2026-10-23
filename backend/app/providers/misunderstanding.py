"""Smart Misunderstanding Detector for BhashaBridge AI.

Identifies potential critical discrepancies between original spoken transcripts
and their translated outputs (e.g. numeric differences, negation flips, missing units,
invalid date/time values, phone number discrepancies, email changes).
Works deterministically without external API calls or extra quota consumption.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

# Numeral translation tables
NUMERAL_TRANSLATION = str.maketrans({
    # Devanagari numerals
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
    # Bengali numerals
    "০": "0", "১": "1", "২": "2", "৩": "3", "৪": "4", "৫": "5", "৬": "6", "৭": "7", "৮": "8", "৯": "9",
    # Arabic-Indic numerals
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})

# Hindi number words to integers
HINDI_NUMBER_WORDS: dict[str, int] = {
    "शून्य": 0, "सिफर": 0,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "अट्ठारह": 18, "उन्नीस": 19, "बीस": 20,
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25, "छब्बीस": 26,
    "सत्ताईस": 27, "अट्ठाईस": 28, "उनतीस": 29, "तीस": 30, "इकत्तीस": 31, "बत्तीस": 32,
    "पैंतीस": 35, "चालीस": 40, "पैंतालीस": 45, "पचास": 50, "पचपन": 55, "साठ": 60,
    "पैंसठ": 65, "सत्तर": 70, "पचहत्तर": 75, "अस्सी": 80, "पचासी": 85, "नब्बे": 90,
    "पिचानवे": 95, "सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000,
    "करोड़": 10000000, "करोड": 10000000,
}

# English number words to integers
ENGLISH_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    "million": 1000000, "billion": 1000000000, "lakh": 100000, "crore": 10000000,
}

# Measurement unit patterns mapped to canonical unit names with lookbehind/lookahead to avoid substring overlaps
UNIT_MAPPINGS: dict[str, list[str]] = {
    "mg": [r"\bmg\b", r"\bmilligrams?\b", r"मिलीग्राम", r"एमजी"],
    "g": [r"(?<=\d|\s)g\b", r"(?<!milli)(?<!kilo)\bgrams?\b", r"(?<!मिली)(?<!किलो)ग्राम", r"\bgm\b"],
    "kg": [r"\bkgs?\b", r"\bkilograms?\b", r"किलोग्राम", r"किलो", r"केजी"],
    "ml": [r"\bml\b", r"\bmillilit[er]{2}s?\b", r"मिलीलीटर", r"मिली(?!ग्राम)(?!लीटर)", r"एमएल"],
    "liter": [r"(?<!milli)\blit[er]{2}s?\b", r"(?<!मिली)लीटर", r"\bltr\b"],
    "drops": [r"\bdrops?\b", r"बूंद[ेंे]?"],
    "tablet": [r"\btablets?\b", r"\btabs?\b", r"\bpills?\b", r"\bcapsules?\b", r"गोलि[याँयां]", r"गोली", r"कैप्सूल"],
    "dose": [r"\bdoses?\b", r"\bdosage\b", r"खुराक", r"डोज़"],
    "rupee": [r"₹", r"\brs\.?\b", r"\brupees?\b", r"\binr\b", r"रुपये?", r"रुपया"],
    "dollar": [r"\$", r"\bdollars?\b", r"\busd\b", r"डॉलर"],
    "km": [r"\bkms?\b", r"\bkilomet[er]{2}s?\b", r"किमी", r"किलोमीटर"],
    "meter": [r"(?<!kilo)(?<!centi)(?<!milli)\bmet[er]{2}s?\b", r"(?<!किलो)मीटर"],
    "percent": [r"%", r"\bpercentages?\b", r"\bpercent\b", r"प्रतिशत", r"फीसदी"],
    "hour": [r"\bhours?\b", r"\bhrs?\b", r"घंटे?", r"घण्टे?"],
    "minute": [r"\bminutes?\b", r"\bmins?\b", r"मिनट"],
    "day": [r"\bdays?\b", r"\bdaily\b", r"दिन", r"प्रतिदिन", r"रोजाना", r"हर दिन"],
    "week": [r"\bweeks?\b", r"\bweekly\b", r"हफ्ते?", r"सप्ताह", r"साप्ताहिक"],
    "month": [r"\bmonths?\b", r"\bmonthly\b", r"महीने?", r"माह", r"मासिक"],
    "year": [r"\byears?\b", r"\byearly\b", r"\byrs?\b", r"साल", r"वर्ष", r"वार्षिक"],
    "bp": [r"\bbp\b", r"\bblood pressure\b", r"बीपी", r"रक्तचाप"],
}

# Negation regular expressions
EN_NEGATION_RE = re.compile(
    r"\b(not|never|neither|nor|none|nobody|nothing|nowhere|without|cannot|can't|won't|shouldn't|wouldn't|doesn't|don't|didn't|isn't|aren't|wasn't|weren't)\b|\bno\b(?!\s*\d)",
    re.IGNORECASE,
)
# Devanagari negation boundary: must not be preceded or followed by Devanagari characters
HI_NEGATION_RE = re.compile(
    r"(?<![\u0900-\u097F])(नहीं|मत|बिना|कभी नहीं|कोई नहीं|कुछ नहीं|ना|न)(?![\u0900-\u097F])",
    re.IGNORECASE,
)
ES_NEGATION_RE = re.compile(r"\b(no|nunca|jamás|nada|nadie|ningún|ninguno|sin)\b", re.IGNORECASE)
FR_NEGATION_RE = re.compile(r"\b(ne|pas|non|jamais|rien|personne|aucun|sans)\b", re.IGNORECASE)
BN_NEGATION_RE = re.compile(r"(?<![\u0980-\u09FF])(না|নয়|নেই|কখনও না|কোনো নয়)(?![\u0980-\u09FF])")

# AM / PM indicators
AM_RE = re.compile(r"\b(am|a\.m\.|सुबह|प्रातः)\b", re.IGNORECASE)
PM_RE = re.compile(r"\b(pm|p\.m\.|शाम|दोपहर|रात)\b", re.IGNORECASE)


def normalize_digits(text: str) -> str:
    """Converts Devanagari, Bengali, and Arabic-Indic numerals to standard ASCII digits."""
    return text.translate(NUMERAL_TRANSLATION)


def extract_numbers(text: str, lang: str = "en") -> list[float]:
    """Extracts all numbers (digits and reliable number words) as normalized floats.

    Sorts the output to avoid false alarms due to word-order differences.
    """
    normalized = normalize_digits(text).lower()
    numbers: list[float] = []

    # 1. Clean out comma-grouping in numbers like 1,000 -> 1000
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", normalized)

    # 2. Extract digit-based numbers (including decimals and ordinals like 15th, 1st, 2nd)
    digit_matches = re.findall(r"\b(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\b", cleaned, re.IGNORECASE)
    for m in digit_matches:
        try:
            numbers.append(float(m))
        except ValueError:
            pass

    # 3. Extract word numbers
    words = re.findall(r"[\w\u0900-\u097F]+", normalized)
    word_tokens: list[tuple[str, int]] = []

    for w in words:
        if w in HINDI_NUMBER_WORDS:
            word_tokens.append((w, HINDI_NUMBER_WORDS[w]))
        elif w in ENGLISH_NUMBER_WORDS:
            word_tokens.append((w, ENGLISH_NUMBER_WORDS[w]))

    # Compound number word folding (e.g. "two thousand" -> 2000, "one hundred fifty" -> 150)
    if word_tokens:
        multipliers = (100, 1000, 100000, 1000000, 10000000)
        has_multiplier = any(val in multipliers for _, val in word_tokens)

        if has_multiplier:
            total = 0.0
            current = 0.0
            for _, val in word_tokens:
                if val in multipliers:
                    current = (current if current > 0 else 1.0) * val
                    total += current
                    current = 0.0
                else:
                    current += val
            folded = total + current
            if folded > 0 and folded not in numbers:
                numbers.append(folded)
        else:
            for _, val in word_tokens:
                f_val = float(val)
                if f_val not in numbers:
                    numbers.append(f_val)

    numbers.sort()
    return numbers


def extract_units(text: str) -> set[str]:
    """Extracts canonical measurement unit labels present in the text."""
    normalized = normalize_digits(text).lower()
    found: set[str] = set()
    for unit_name, patterns in UNIT_MAPPINGS.items():
        for pat in patterns:
            if re.search(pat, normalized, re.IGNORECASE):
                found.add(unit_name)
                break
    return found


def extract_phone_numbers(text: str) -> list[str]:
    """Extracts continuous sequences of digits that represent phone numbers."""
    normalized = normalize_digits(text)
    compact = re.sub(r"(?<=\d)[\s-](?=\d)", "", normalized)
    matches = re.findall(r"(?:\+?91)?(?:0)?([6-9]\d{9})\b", compact)
    if matches:
        return sorted(set(matches))
    generic = re.findall(r"\b\d{10,12}\b", compact)
    return sorted(set(generic))


def extract_emails(text: str) -> list[str]:
    """Extracts email addresses."""
    matches = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    return [m.lower() for m in matches]


def check_negation_presence(text: str, lang: str) -> Optional[bool]:
    """Returns True if explicit negation cues are present, False if affirmative,

    or None if language is not reliably covered.
    """
    normalized = normalize_digits(text).lower()
    if lang.startswith("hi") or any("\u0900" <= c <= "\u097f" for c in text):
        return bool(HI_NEGATION_RE.search(normalized))
    if lang.startswith("en"):
        no_cleaned = re.sub(r"\bno\.\s*(?=\d)", "number ", normalized, flags=re.IGNORECASE)
        return bool(EN_NEGATION_RE.search(no_cleaned))
    if lang.startswith("es"):
        return bool(ES_NEGATION_RE.search(normalized))
    if lang.startswith("fr"):
        return bool(FR_NEGATION_RE.search(normalized))
    if lang.startswith("bn") or any("\u0980" <= c <= "\u09ff" for c in text):
        return bool(BN_NEGATION_RE.search(normalized))
    return None


def check_dates_and_times(orig: str, trans: str) -> list[dict[str, Any]]:
    """Validates date and time formats, checking for impossible calendar values or AM/PM flips."""
    alerts: list[dict[str, Any]] = []
    combined = [("original", normalize_digits(orig)), ("translated", normalize_digits(trans))]

    for label, text in combined:
        if re.search(r"(?<!\d)(3[2-9]|[4-9]\d)[/\-.](0?[1-9]|1[0-2])(?!\d)", text):
            alerts.append({
                "type": "invalid_date",
                "severity": "high",
                "message": f"Suspicious calendar day (> 31) detected in {label} text.",
            })
        if re.search(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[/\-.](1[3-9]|[2-9]\d)(?!\d)", text):
            alerts.append({
                "type": "invalid_date",
                "severity": "high",
                "message": f"Suspicious calendar month (> 12) detected in {label} text.",
            })
        if re.search(r"(?<!\d)(30|31)[/\-.](?:0?2|feb|february|फ़रवरी|फरवरी)\b", text, re.IGNORECASE):
            alerts.append({
                "type": "invalid_date",
                "severity": "high",
                "message": f"Invalid February date (Feb 30/31) detected in {label} text.",
            })
        if re.search(r"(?<!\d)31[/\-.](?:0?[469]|11|apr|april|jun|june|sep|september|nov|november|अप्रैल|जून|सितंबर|नवंबर)\b", text, re.IGNORECASE):
            alerts.append({
                "type": "invalid_date",
                "severity": "high",
                "message": f"Invalid 31st date for a 30-day month detected in {label} text.",
            })
        if re.search(r"\b(2[4-9]|[3-9]\d):[0-5]\d\b", text):
            alerts.append({
                "type": "invalid_time",
                "severity": "high",
                "message": f"Invalid time hour (> 24) detected in {label} text.",
            })
        if re.search(r"\b([01]?\d|2[0-3]):([6-9]\d)\b", text):
            alerts.append({
                "type": "invalid_time",
                "severity": "high",
                "message": f"Invalid time minutes (>= 60) detected in {label} text.",
            })

    orig_am = bool(AM_RE.search(orig))
    orig_pm = bool(PM_RE.search(orig))
    trans_am = bool(AM_RE.search(trans))
    trans_pm = bool(PM_RE.search(trans))

    if orig_am and not orig_pm and trans_pm and not trans_am:
        alerts.append({
            "type": "time_mismatch",
            "severity": "high",
            "message": "Time period mismatch: original specifies AM (morning) but translation indicates PM (evening/night).",
        })
    elif orig_pm and not orig_am and trans_am and not trans_pm:
        alerts.append({
            "type": "time_mismatch",
            "severity": "high",
            "message": "Time period mismatch: original specifies PM (evening/night) but translation indicates AM (morning).",
        })

    return alerts


def detect_misunderstanding(
    original_text: str,
    translated_text: str,
    source_lang: str = "hi",
    target_lang: str = "en",
) -> dict[str, Any]:
    """Inspects original and translated transcripts for critical discrepancies.

    Returns structured dictionary with needs_confirmation and alerts.
    """
    orig = (original_text or "").strip()
    trans = (translated_text or "").strip()

    alerts: list[dict[str, Any]] = []

    # 1. Empty / Missing transcripts check
    if orig and not trans:
        return {
            "needs_confirmation": True,
            "alerts": [{
                "type": "empty_translation",
                "severity": "high",
                "message": "Translation is empty while original speech was present.",
            }],
        }
    if not orig and not trans:
        return {"needs_confirmation": False, "alerts": []}
    if not orig and trans:
        return {
            "needs_confirmation": True,
            "alerts": [{
                "type": "empty_original",
                "severity": "medium",
                "message": "Original speech was empty or unrecorded.",
            }],
        }

    # 2. Numeric mismatches
    orig_nums = extract_numbers(orig, source_lang)
    trans_nums = extract_numbers(trans, target_lang)

    numbers_match = (orig_nums == trans_nums)
    if not numbers_match and len(orig_nums) > 1 and len(trans_nums) == 1:
        if sum(orig_nums) == trans_nums[0]:
            numbers_match = True
    elif not numbers_match and len(trans_nums) > 1 and len(orig_nums) == 1:
        if sum(trans_nums) == orig_nums[0]:
            numbers_match = True

    if not numbers_match:
        orig_str = ", ".join(f"{int(n) if n.is_integer() else n}" for n in orig_nums) if orig_nums else "None"
        trans_str = ", ".join(f"{int(n) if n.is_integer() else n}" for n in trans_nums) if trans_nums else "None"

        alerts.append({
            "type": "number_mismatch",
            "severity": "high",
            "message": f"Numbers differ between original ({orig_str}) and translation ({trans_str}).",
            "details": {
                "original_numbers": orig_nums,
                "translated_numbers": trans_nums,
            },
        })

    # 3. Measurement units comparison
    orig_units = extract_units(orig)
    trans_units = extract_units(trans)

    missing_units = orig_units - trans_units
    extra_units = trans_units - orig_units

    if missing_units:
        missing_str = ", ".join(sorted(missing_units))
        alerts.append({
            "type": "missing_unit",
            "severity": "high" if any(u in {"mg", "g", "kg", "ml", "tablet", "dose", "bp"} for u in missing_units) else "medium",
            "message": f"Measurement unit ({missing_str}) present in original is missing in translation.",
            "details": {"missing_units": list(missing_units)},
        })
    elif extra_units and (orig_nums or len(orig.split()) <= 4):
        extra_str = ", ".join(sorted(extra_units))
        alerts.append({
            "type": "unit_mismatch",
            "severity": "medium",
            "message": f"Measurement unit ({extra_str}) appears in translation but was not specified in original.",
            "details": {"extra_units": list(extra_units)},
        })

    # 4. Negation consistency
    orig_neg = check_negation_presence(orig, source_lang)
    trans_neg = check_negation_presence(trans, target_lang)

    if orig_neg is not None and trans_neg is not None and orig_neg != trans_neg:
        if orig_neg and not trans_neg:
            desc = "Original contains negation (e.g. 'not' / 'नहीं' / 'मत'), but translation appears affirmative."
        else:
            desc = "Original appears affirmative, but translation contains negation (e.g. 'not' / 'नहीं')."

        alerts.append({
            "type": "negation_mismatch",
            "severity": "high",
            "message": f"Possible negation mismatch: {desc} Please verify intent.",
            "details": {
                "original_has_negation": orig_neg,
                "translated_has_negation": trans_neg,
            },
        })

    # 5. Date and Time format checks
    date_time_alerts = check_dates_and_times(orig, trans)
    alerts.extend(date_time_alerts)

    # 6. Phone number comparison
    orig_phones = extract_phone_numbers(orig)
    trans_phones = extract_phone_numbers(trans)
    if orig_phones and trans_phones:
        if orig_phones != trans_phones:
            alerts.append({
                "type": "phone_mismatch",
                "severity": "high",
                "message": f"Phone number mismatch: original ({', '.join(orig_phones)}) vs translation ({', '.join(trans_phones)}).",
                "details": {
                    "original_phones": orig_phones,
                    "translated_phones": trans_phones,
                },
            })
    elif orig_phones and not trans_phones:
        alerts.append({
            "type": "phone_missing",
            "severity": "high",
            "message": f"Phone number ({orig_phones[0]}) from original was omitted or altered in translation.",
            "details": {"original_phones": orig_phones},
        })

    # 7. Email address comparison
    orig_emails = extract_emails(orig)
    trans_emails = extract_emails(trans)
    if orig_emails and trans_emails:
        if orig_emails != trans_emails:
            alerts.append({
                "type": "email_mismatch",
                "severity": "high",
                "message": f"Email address mismatch: original ({', '.join(orig_emails)}) vs translation ({', '.join(trans_emails)}).",
                "details": {"original_emails": orig_emails, "translated_emails": trans_emails},
            })
    elif orig_emails and not trans_emails:
        alerts.append({
            "type": "email_missing",
            "severity": "high",
            "message": f"Email address ({orig_emails[0]}) present in original was omitted in translation.",
            "details": {"original_emails": orig_emails},
        })

    return {
        "needs_confirmation": len(alerts) > 0,
        "alerts": alerts,
    }
