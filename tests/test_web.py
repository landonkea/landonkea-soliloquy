import os
import sys
import wave
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from soliloquy.storage import EntryStore
from soliloquy.web.app import app, get_store

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


def _override_get_store():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        yield store
    finally:
        store.close()


app.dependency_overrides[get_store] = _override_get_store


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _silent_wav_bytes(seconds: float = 1.0) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def test_post_entry_creates_a_text_entry(client):
    response = client.post("/entries", data={"text": "a web entry"})

    assert response.status_code == 200
    assert response.json()["transcript"] == "a web entry"


def test_get_entries_lists_everything_added_in_order(client):
    client.post("/entries", data={"text": "first"})
    client.post("/entries", data={"text": "second"})

    response = client.get("/entries")

    assert [e["transcript"] for e in response.json()] == ["first", "second"]


def test_get_entries_is_empty_for_a_fresh_db(client):
    assert client.get("/entries").json() == []


def test_share_entry_sets_the_requested_flag(client):
    created = client.post("/entries", data={"text": "entry"}).json()

    response = client.post(f"/entries/{created['id']}/share", data={"partner": "true"})

    assert response.status_code == 200
    with EntryStore(TEST_DATABASE_URL) as store:
        fetched = store.get(created["id"])
        assert fetched.shareable_with_partner is True
        assert fetched.shareable_with_provider is False


def test_share_entry_returns_404_for_an_unknown_id(client):
    response = client.post("/entries/does-not-exist/share", data={"partner": "true"})
    assert response.status_code == 404


def test_report_returns_404_when_nothing_matches_the_audience(client):
    client.post("/entries", data={"text": "a private entry, never shared"})

    response = client.post("/reports", data={"days": 7, "audience": "partner"})

    assert response.status_code == 404


def test_report_returns_400_for_an_unknown_audience(client):
    response = client.post("/reports", data={"days": 7, "audience": "stranger"})
    assert response.status_code == 400


def _install_fake_faster_whisper(segment_texts: list[str]):
    # Same technique as test_transcriber.py -- faster-whisper isn't
    # installed in CI (it needs a real model download on first use),
    # so transcription is faked at the module level, not the network
    # call. The upload -> object storage -> media route round trip
    # below is still exercised for real, against real Postgres+MinIO.
    fake_module = ModuleType("faster_whisper")
    fake_segments = [MagicMock(text=text) for text in segment_texts]
    fake_model_instance = MagicMock()
    fake_model_instance.transcribe.return_value = (fake_segments, MagicMock())
    fake_module.WhisperModel = MagicMock(return_value=fake_model_instance)
    return fake_module


def test_post_audio_entry_transcribes_and_stores_a_real_audio_key(client):
    fake_module = _install_fake_faster_whisper([" A fake transcript. "])

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        response = client.post(
            "/entries/audio", files={"file": ("entry.wav", _silent_wav_bytes(), "audio/wav")}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["audio_path"].startswith("audio/")
    assert body["audio_path"].endswith(".wav")
    assert body["transcript"] == "A fake transcript."

    response = client.get(f"/media/{body['audio_path']}")
    assert response.status_code == 200
    assert response.content == _silent_wav_bytes()
