import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import app
from backend.app.ratelimit import SlidingLimiter
from backend.app.signaling import RoomManager

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_defaults_to_demo_without_keys():
    j = client.get("/api/config").json()
    assert j["providers"] == {"stt": "browser", "translate": "demo", "tts": "browser"}
    assert j["demo_translation"] is True
    assert {l["code"] for l in j["languages"]} >= {"hi", "en"}
    assert "openai" not in str(j).lower().replace("openai_", "")  # no key material in config


def test_demo_translate_marks_simulated():
    j = client.post("/api/translate", json={"text": "नमस्ते!", "source": "hi", "target": "en"}).json()
    assert j["text"] == "Hello" and j["simulated"] is True and j["matched"] is True


def test_demo_translate_no_match_is_flagged():
    j = client.post("/api/translate", json={"text": "random sentence", "source": "en", "target": "hi"}).json()
    assert j["matched"] is False and j["simulated"] is True


def test_translate_validation():
    assert client.post("/api/translate", json={"text": "", "source": "hi", "target": "en"}).status_code == 422
    r = client.post("/api/translate", json={"text": "hi", "source": "xx", "target": "en"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unsupported_language"


def test_stt_and_tts_need_server_providers():
    r = client.post("/api/stt", files={"audio": ("a.webm", b"1234", "audio/webm")}, data={"language": "hi"})
    assert r.status_code == 400
    assert client.post("/api/tts", json={"text": "hi", "language": "en"}).status_code == 400


def test_create_room():
    code = client.post("/api/rooms").json()["room"]
    assert len(code) == 6 and code.isalnum()


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_room_manager_two_peers_and_third_rejected():
    async def run():
        m = RoomManager()
        a, b, c = FakeWS(), FakeWS(), FakeWS()
        pa = await m.join("R1", a)
        assert a.sent[0]["peers"] == 0
        pb = await m.join("R1", b)
        assert b.sent[0]["peers"] == 1 and a.sent[-1]["type"] == "peer_joined"
        assert await m.join("R1", c) is None  # third participant rejected
        await m.relay("R1", pa, {"type": "offer", "sdp": {}})
        assert b.sent[-1]["type"] == "offer" and b.sent[-1]["from"] == pa
        n = len(b.sent)
        await m.relay("R1", pa, {"type": "evil"})  # unknown type dropped
        await m.relay("R1", pa, {"type": "utterance", "original": "x" * 1001, "translated": "y"})  # too long dropped
        assert len(b.sent) == n
        await m.relay("R1", pa, {"type": "utterance", "original": "a", "translated": "b"})
        assert b.sent[-1]["type"] == "utterance"
        await m.leave("R1", pb)
        assert a.sent[-1]["type"] == "peer_left"
        await m.leave("R1", pa)
        assert "R1" not in m.rooms

    asyncio.run(run())


def test_ws_single_client_joins():
    with client.websocket_connect("/ws/SOLO99") as a:
        j = a.receive_json()
        assert j["type"] == "joined" and j["peers"] == 0


def test_ws_invalid_room():
    with client.websocket_connect("/ws/!!") as c:
        assert c.receive_json()["type"] == "error"


def test_provider_selection_and_warnings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = get_settings()
    assert (s.stt_provider, s.translate_provider, s.tts_provider) == ("openai", "openai", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TRANSLATE_PROVIDER", "openai")
    assert any("OPENAI_API_KEY" in w for w in get_settings().warnings())


def test_rate_limiter():
    l = SlidingLimiter()
    assert all(l.allow("ip", 3) for _ in range(3)) and not l.allow("ip", 3)


def test_mistral_selection(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "m-test")
    s = get_settings()
    assert (s.stt_provider, s.translate_provider, s.tts_provider) == ("browser", "mistral", "browser")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # both keys: translation prefers Mistral
    assert get_settings().translate_provider == "mistral"
    monkeypatch.setenv("STT_PROVIDER", "mistral")
    assert get_settings().stt_provider == "mistral"
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    assert any("MISTRAL_API_KEY" in w for w in get_settings().warnings())


def test_gemini_selection_and_warnings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # Gemini wins in auto mode
    monkeypatch.setenv("MISTRAL_API_KEY", "m-test")
    s = get_settings()
    assert (s.stt_provider, s.translate_provider, s.tts_provider) == ("gemini", "gemini", "gemini")
    monkeypatch.setenv("TRANSLATE_PROVIDER", "mistral")  # explicit choice still respected
    assert get_settings().translate_provider == "mistral"
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("STT_PROVIDER", "gemini")
    assert any("GEMINI_API_KEY" in w for w in get_settings().warnings())


def test_gemini_translate_stt_tts_with_mocked_http(monkeypatch):
    import base64
    import httpx

    from backend.app.providers import gemini as g

    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    seen = []

    async def fake_post(url, **kw):
        seen.append((url, kw))
        if "tts" in url:
            pcm = base64.b64encode(b"\x00\x01" * 100).decode()
            data = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000", "data": pcm}}]}}]}
        else:
            data = {"candidates": [{"content": {"parts": [{"text": " Hello "}]}}]}
        return httpx.Response(200, json=data)

    monkeypatch.setattr(g, "post", fake_post)
    j = client.post("/api/translate", json={"text": "नमस्ते", "source": "hi", "target": "en"}).json()
    assert {k: j[k] for k in ("text", "provider", "simulated", "matched")} == {"text": "Hello", "provider": "gemini", "simulated": False, "matched": True}
    assert "misunderstanding" in j
    assert client.post("/api/stt", files={"audio": ("a.wav", b"1234", "audio/wav")}, data={"language": "hi"}).json() == {"text": "Hello"}
    r = client.post("/api/tts", json={"text": "hello", "language": "en"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert all(kw["headers"]["x-goog-api-key"] == "g-test" and "g-test" not in url for url, kw in seen)  # key only in header


def test_gemini_bad_key_maps_to_clear_error(monkeypatch):
    import httpx

    from backend.app.providers import gemini as g

    monkeypatch.setenv("GEMINI_API_KEY", "bad")

    async def fake_post(url, **kw):
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})

    monkeypatch.setattr(g, "post", fake_post)
    r = client.post("/api/translate", json={"text": "hi", "source": "en", "target": "hi"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "bad_api_key" and "GEMINI_API_KEY" in r.json()["detail"]["message"]


def test_summarize_valid_mocked_http(monkeypatch):
    import json
    import httpx
    from backend.app.providers import gemini as g

    monkeypatch.setenv("GEMINI_API_KEY", "g-test")

    summary_payload = {
        "title": "BhashaBridge Project Sync",
        "summary": "Krishna and Alice discussed testing translation accuracy. They decided to ship the prototype by Friday. Next meeting was scheduled for tomorrow.",
        "key_points": ["Translation accuracy testing", "Ship date set to Friday"],
        "action_items": [{"task": "Refactor CSS design system", "owner": "Krishna"}],
        "decisions": ["Deploy prototype on Friday"],
    }

    async def fake_post(url, **kw):
        assert kw["headers"]["x-goog-api-key"] == "g-test"
        gemini_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(summary_payload)}
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=gemini_resp)

    monkeypatch.setattr(g, "post", fake_post)

    payload = {
        "utterances": [
            {"speaker": "Krishna", "text": "नमस्ते, क्या हम शुक्रवार तक शिप कर सकते हैं?", "lang": "hi"},
            {"speaker": "Alice", "text": "Yes, we can deploy by Friday once tests pass.", "lang": "en"}
        ],
        "output_language": "en"
    }
    r = client.post("/api/summarize", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "BhashaBridge Project Sync"
    assert "Krishna and Alice" in data["summary"]
    assert len(data["key_points"]) == 2
    assert data["action_items"] == [{"task": "Refactor CSS design system", "owner": "Krishna"}]
    assert data["decisions"] == ["Deploy prototype on Friday"]


def test_summarize_empty_transcript_returns_400():
    r = client.post("/api/summarize", json={"utterances": [], "output_language": "en"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "empty_transcript"


def test_summarize_too_long_returns_422():
    # Individual utterance text > 1000 characters triggers validation error (422)
    long_u = {"speaker": "User", "text": "a" * 1001, "lang": "en"}
    r1 = client.post("/api/summarize", json={"utterances": [long_u], "output_language": "en"})
    assert r1.status_code == 422

    # Total characters exceeding 30,000 triggers 422 ProviderError
    utterances = [{"speaker": f"User{i}", "text": "x" * 1000, "lang": "en"} for i in range(31)]
    r2 = client.post("/api/summarize", json={"utterances": utterances, "output_language": "en"})
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "too_long"


def test_summarize_bad_key_error(monkeypatch):
    import httpx
    from backend.app.providers import gemini as g

    monkeypatch.setenv("GEMINI_API_KEY", "bad_key")

    async def fake_post(url, **kw):
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})

    monkeypatch.setattr(g, "post", fake_post)

    payload = {
        "utterances": [{"speaker": "User", "text": "Hello world", "lang": "en"}],
        "output_language": "en"
    }
    r = client.post("/api/summarize", json=payload)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "bad_api_key"


def test_summarize_demo_mode_returns_400(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("MISTRAL_API_KEY", "")

    payload = {
        "utterances": [{"speaker": "User", "text": "Hello world", "lang": "en"}],
        "output_language": "en"
    }
    r = client.post("/api/summarize", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_api_key"


def test_misunderstanding_check_endpoint():
    res = client.post("/api/misunderstanding/check", json={
        "source_text": "I need 50 mg",
        "translated_text": "Mujhe 15 mg chahiye",
        "source_lang": "en",
        "target_lang": "hi",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["needs_confirmation"] is True
    assert any(a["type"] == "number_mismatch" for a in data["alerts"])

