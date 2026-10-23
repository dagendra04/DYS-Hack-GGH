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
    assert j == {"text": "Hello", "provider": "gemini", "simulated": False, "matched": True}
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
