"""Automated test suite for Smart Misunderstanding Detector in BhashaBridge AI.

Tests cover:
- Matching numbers vs different numbers (including Devanagari numerals and number words)
- Negation matching vs negation flipping on one side
- Missing measurement units and extra units
- Safe handling of empty or missing transcripts
- Equivalent punctuation and formatting
- Hindi-English and English-Hindi cross-lingual examples
- Multiple independent alerts returned together
- Normal translations with different word order (SOV vs SVO) without false positives
- Invalid dates, times, and AM/PM reversals
- Phone number and email discrepancies
- Backend API endpoints (/api/translate, /api/misunderstanding/check) and WebSocket relay
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.providers.misunderstanding import (
    check_dates_and_times,
    check_negation_presence,
    detect_misunderstanding,
    extract_numbers,
    extract_phone_numbers,
    extract_units,
    normalize_digits,
)
from backend.app.signaling import RoomManager

client = TestClient(app)


def test_matching_numbers_no_alert():
    """Matching numbers in original and translation should NOT trigger a warning."""
    res = detect_misunderstanding(
        "Take 15 mg of medicine",
        "15 मिलीग्राम दवा लें",
        source_lang="en",
        target_lang="hi",
    )
    assert not any(a["type"] == "number_mismatch" for a in res["alerts"])
    assert res["needs_confirmation"] is False


def test_different_numbers_triggers_alert():
    """Different numbers (e.g. 15 mg vs 50 mg) must trigger a high-severity number_mismatch."""
    res = detect_misunderstanding(
        "Take 15 mg daily",
        "रोजाना 50 मिलीग्राम लें",
        source_lang="en",
        target_lang="hi",
    )
    assert res["needs_confirmation"] is True
    alerts = [a for a in res["alerts"] if a["type"] == "number_mismatch"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "high"
    assert "15" in alerts[0]["message"] and "50" in alerts[0]["message"]


def test_devanagari_and_bengali_numerals_normalization():
    """Devanagari (१५) and Bengali (১৫) numerals should normalize and match standard 15."""
    assert extract_numbers("१५ गोलियां") == [15.0]
    assert extract_numbers("১৫ টি ট্যাবলেট") == [15.0]

    res = detect_misunderstanding("१५ मिलीग्राम", "15 mg", source_lang="hi", target_lang="en")
    assert not any(a["type"] == "number_mismatch" for a in res["alerts"])


def test_word_numbers_and_compound_numbers():
    """Number words in Hindi ('पंद्रह') and English ('fifteen') should match digit equivalents."""
    res1 = detect_misunderstanding("fifteen tablets", "पंद्रह गोलियां", source_lang="en", target_lang="hi")
    assert not any(a["type"] == "number_mismatch" for a in res1["alerts"])

    res2 = detect_misunderstanding("150 rupees", "एक सौ पचास रुपये", source_lang="en", target_lang="hi")
    assert not any(a["type"] == "number_mismatch" for a in res2["alerts"])


def test_negation_present_on_only_one_side():
    """Negation present in original but absent in translation triggers negation_mismatch."""
    res1 = detect_misunderstanding("Do not call him", "उसे कॉल करें", source_lang="en", target_lang="hi")
    assert res1["needs_confirmation"] is True
    neg_alerts1 = [a for a in res1["alerts"] if a["type"] == "negation_mismatch"]
    assert len(neg_alerts1) == 1
    assert "affirmative" in neg_alerts1[0]["message"] or "negation" in neg_alerts1[0]["message"]

    res2 = detect_misunderstanding("Please call him", "उसे कॉल मत करो", source_lang="en", target_lang="hi")
    assert res2["needs_confirmation"] is True
    neg_alerts2 = [a for a in res2["alerts"] if a["type"] == "negation_mismatch"]
    assert len(neg_alerts2) == 1


def test_negation_consistent_on_both_sides_no_alert():
    """Both sides having negation or both sides being affirmative must NOT trigger a warning."""
    # Both negative
    res1 = detect_misunderstanding("Do not call him", "उसे कॉल मत करो", source_lang="en", target_lang="hi")
    assert not any(a["type"] == "negation_mismatch" for a in res1["alerts"])

    # Both affirmative
    res2 = detect_misunderstanding("Please call him tomorrow", "कृपया कल उसे कॉल करें", source_lang="en", target_lang="hi")
    assert not any(a["type"] == "negation_mismatch" for a in res2["alerts"])


def test_no_false_positive_negation_on_hindi_words():
    """Words like 'नाम' (name) or 'धन्यवाद' (thanks) must not be falsely flagged as negation."""
    res = detect_misunderstanding("My name is Krishna", "मेरा नाम कृष्णा है", source_lang="en", target_lang="hi")
    assert not any(a["type"] == "negation_mismatch" for a in res["alerts"])


def test_missing_measurement_unit_detected():
    """Missing critical units like 'mg' or 'tablet' must trigger missing_unit alert."""
    res = detect_misunderstanding("Take 10 mg of this medicine", "इस दवा का 10 लें", source_lang="en", target_lang="hi")
    assert res["needs_confirmation"] is True
    unit_alerts = [a for a in res["alerts"] if a["type"] == "missing_unit"]
    assert len(unit_alerts) == 1
    assert "mg" in unit_alerts[0]["details"]["missing_units"]


def test_extra_measurement_unit_detected():
    """Invented units in translation should trigger unit_mismatch alert."""
    res = detect_misunderstanding("Take 5", "5 मिलीग्राम लें", source_lang="en", target_lang="hi")
    assert res["needs_confirmation"] is True
    unit_alerts = [a for a in res["alerts"] if a["type"] == "unit_mismatch"]
    assert len(unit_alerts) == 1


def test_empty_transcripts_handled_safely():
    """Empty or missing transcripts should be handled without exceptions."""
    # Both empty
    assert detect_misunderstanding("", "") == {"needs_confirmation": False, "alerts": []}
    assert detect_misunderstanding("   ", None) == {"needs_confirmation": False, "alerts": []}

    # Original present, translation empty
    res_no_trans = detect_misunderstanding("Hello world", "")
    assert res_no_trans["needs_confirmation"] is True
    assert res_no_trans["alerts"][0]["type"] == "empty_translation"

    # Original empty, translation present
    res_no_orig = detect_misunderstanding("", "नमस्ते")
    assert res_no_orig["needs_confirmation"] is True
    assert res_no_orig["alerts"][0]["type"] == "empty_original"


def test_punctuation_and_formatting_no_false_alarms():
    """Commas in numbers (1,000 vs 1000) and punctuation (!, ?, ।) should not trigger alerts."""
    res = detect_misunderstanding("Salary is 15,000 rupees!", "वेतन 15000 रुपये है।", source_lang="en", target_lang="hi")
    assert res["needs_confirmation"] is False
    assert len(res["alerts"]) == 0


def test_word_order_differences_not_flagged():
    """Normal SVO vs SOV differences (English vs Hindi) must not trigger number mismatches."""
    # English: Give 2 tablets at 8 pm (numbers: 2, 8)
    # Hindi: शाम 8 बजे 2 गोलियां दें (numbers: 8, 2)
    res = detect_misunderstanding(
        "Give 2 tablets at 8 pm",
        "शाम 8 बजे 2 गोलियां दें",
        source_lang="en",
        target_lang="hi",
    )
    assert not any(a["type"] == "number_mismatch" for a in res["alerts"])


def test_multiple_independent_alerts():
    """Multiple discrepancies (numbers AND negation) in a single sentence should all be returned."""
    # Original: "Do not take 15 mg" (negation: True, number: 15)
    # Translation: "50 मिलीग्राम लें" (negation: False, number: 50)
    res = detect_misunderstanding(
        "Do not take 15 mg",
        "50 मिलीग्राम लें",
        source_lang="en",
        target_lang="hi",
    )
    assert res["needs_confirmation"] is True
    alert_types = {a["type"] for a in res["alerts"]}
    assert "number_mismatch" in alert_types
    assert "negation_mismatch" in alert_types


def test_date_and_time_validation():
    """Suspicious calendar dates (>31), invalid months (>12), and AM/PM flips should be detected."""
    # Invalid day: 35/05/2024
    res_bad_day = detect_misunderstanding("Meeting on 35/05/2024", "बैठक 35/05/2024 को", source_lang="en", target_lang="hi")
    assert any(a["type"] == "invalid_date" for a in res_bad_day["alerts"])

    # Feb 30
    res_feb30 = detect_misunderstanding("See you on 30/02", "30 फ़रवरी को मिलते हैं", source_lang="en", target_lang="hi")
    assert any(a["type"] == "invalid_date" for a in res_feb30["alerts"])

    # Invalid time minute 10:75
    res_bad_time = detect_misunderstanding("At 10:75", "10:75 पर", source_lang="en", target_lang="hi")
    assert any(a["type"] == "invalid_time" for a in res_bad_time["alerts"])

    # AM to PM flip
    res_ampm = detect_misunderstanding("Meeting at 9 am", "बैठक रात 9 बजे है", source_lang="en", target_lang="hi")
    assert any(a["type"] == "time_mismatch" for a in res_ampm["alerts"])


def test_phone_number_mismatch():
    """Altered phone numbers between original and translation must trigger phone_mismatch."""
    res = detect_misunderstanding(
        "Call me at 9876543210",
        "मुझे 9123456789 पर कॉल करें",
        source_lang="en",
        target_lang="hi",
    )
    assert res["needs_confirmation"] is True
    assert any(a["type"] == "phone_mismatch" for a in res["alerts"])


def test_api_translate_includes_misunderstanding():
    """POST /api/translate should include the misunderstanding detector result in its response."""
    r = client.post("/api/translate", json={"text": "नमस्ते!", "source": "hi", "target": "en"})
    assert r.status_code == 200
    data = r.json()
    assert "misunderstanding" in data
    assert "needs_confirmation" in data["misunderstanding"]
    assert "alerts" in data["misunderstanding"]


def test_api_misunderstanding_check_endpoint():
    """POST /api/misunderstanding/check endpoint returns structured verification."""
    payload = {
        "source_text": "Please take 15 mg",
        "translated_text": "कृपया 50 मिलीग्राम लें",
        "source_lang": "en",
        "target_lang": "hi",
    }
    r = client.post("/api/misunderstanding/check", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["needs_confirmation"] is True
    assert any(a["type"] == "number_mismatch" for a in data["alerts"])


def test_websocket_relay_attaches_misunderstanding():
    """Relayed utterance over WebSocket should have misunderstanding analysis attached."""
    import asyncio

    class DummyWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    async def run():
        m = RoomManager()
        a, b = DummyWS(), DummyWS()
        pa = await m.join("WARN1", a)
        pb = await m.join("WARN1", b)

        # Send utterance with numeric discrepancy (15 mg vs 50 mg)
        await m.relay("WARN1", pa, {
            "type": "utterance",
            "original": "Take 15 mg",
            "translated": "50 मिलीग्राम लें",
            "source": "en",
            "target": "hi",
        })

        assert len(b.sent) >= 2
        relayed = b.sent[-1]
        assert relayed["type"] == "utterance"
        assert "misunderstanding" in relayed
        assert relayed["misunderstanding"]["needs_confirmation"] is True
        assert any(x["type"] == "number_mismatch" for x in relayed["misunderstanding"]["alerts"])

    asyncio.run(run())
