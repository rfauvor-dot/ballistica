"""Cost guardrails on the two endpoints that proxy paid OpenAI calls
(2026-09-19, found in the cost deep dive): /voice/speak and
/voice/transcribe used to be unauthenticated, with no input bound, behind
a per-IP rate limit that a forged X-Forwarded-For header defeated (26
requests, zero 429s, confirmed live). Now: a verified login is required
and each call's cost is bounded. The OpenAI client is faked throughout --
this suite must never make a paid call."""
import pytest
from fastapi.testclient import TestClient

import ballistica.api as api_module


class _FakeOpenAI:
    def __init__(self):
        self.tts_inputs = []
        self.transcribe_calls = 0
        outer = self

        class _Speech:
            def create(self, model, voice, input, speed):
                outer.tts_inputs.append(input)
                return type("R", (), {"content": b"ID3fake-mp3"})()

        class _Transcriptions:
            def create(self, **kwargs):
                outer.transcribe_calls += 1
                return type("R", (), {"text": "drop at four hundred yards"})()

        self.audio = type("A", (), {"speech": _Speech(), "transcriptions": _Transcriptions()})()


@pytest.fixture
def fake_openai(monkeypatch):
    fake = _FakeOpenAI()
    monkeypatch.setattr(api_module, "get_openai_client", lambda: fake)
    return fake


@pytest.fixture
def client():
    return TestClient(api_module.app)


@pytest.fixture
def signed_in():
    api_module.app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    yield
    api_module.app.dependency_overrides.pop(api_module._verify_bearer, None)


def test_speak_and_transcribe_reject_missing_and_forged_auth_without_calling_openai(client, fake_openai):
    import jwt as pyjwt

    forged = pyjwt.encode({"sub": "attacker", "aud": "authenticated"}, "not-the-real-secret", algorithm="HS256")
    for headers in ({}, {"Authorization": f"Bearer {forged}"}):
        r = client.post("/voice/speak", headers=headers, json={"text": "hello there"})
        assert r.status_code in (401, 422)
        r = client.post("/voice/transcribe", headers=headers, files={"audio": ("c.webm", b"x" * 100, "audio/webm")})
        assert r.status_code in (401, 422)
    assert fake_openai.tts_inputs == [] and fake_openai.transcribe_calls == 0


def test_speak_rejects_blank_text_before_any_openai_call(client, signed_in, fake_openai):
    r = client.post("/voice/speak", json={"text": "   "})
    assert r.status_code == 400
    assert fake_openai.tts_inputs == []


def test_speak_clips_oversized_text_instead_of_paying_for_it(client, signed_in, fake_openai):
    """A long reply still speaks its first part rather than going silent,
    but one call can never bill more than _TTS_MAX_CHARS."""
    r = client.post("/voice/speak", json={"text": "a" * 4000})
    assert r.status_code == 200
    assert len(fake_openai.tts_inputs) == 1
    assert len(fake_openai.tts_inputs[0]) == api_module._TTS_MAX_CHARS


def test_speak_passes_normal_replies_through_untouched(client, signed_in, fake_openai):
    reply = "Shot 3, 1162. Average 1155."
    assert client.post("/voice/speak", json={"text": reply}).status_code == 200
    assert fake_openai.tts_inputs == [reply]


def test_transcribe_rejects_oversized_upload_before_any_openai_call(client, signed_in, fake_openai):
    big = b"x" * (api_module._TRANSCRIBE_MAX_BYTES + 1)
    r = client.post("/voice/transcribe", files={"audio": ("c.webm", big, "audio/webm")})
    assert r.status_code == 413
    assert fake_openai.transcribe_calls == 0


def test_transcribe_accepts_a_normal_clip_and_the_exact_limit(client, signed_in, fake_openai):
    clip = b"x" * 60_000  # ~ a few seconds of opus
    r = client.post("/voice/transcribe", files={"audio": ("c.webm", clip, "audio/webm")})
    assert r.status_code == 200 and r.json()["text"] == "drop at four hundred yards"
    at_limit = b"x" * api_module._TRANSCRIBE_MAX_BYTES
    assert client.post("/voice/transcribe", files={"audio": ("c.webm", at_limit, "audio/webm")}).status_code == 200
    assert fake_openai.transcribe_calls == 2


def test_transcribe_still_rejects_an_empty_upload(client, signed_in, fake_openai):
    assert client.post("/voice/transcribe", files={"audio": ("c.webm", b"", "audio/webm")}).status_code == 400
    assert fake_openai.transcribe_calls == 0
