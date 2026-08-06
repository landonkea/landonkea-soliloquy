import os

# Must be set before soliloquy.web.app is imported / TestClient triggers
# the app's startup event -- otherwise every test run spins up a real
# background scheduler/MQTT connection against the test database.
os.environ.setdefault("SOLILOQUY_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SOLILOQUY_DISABLE_MQTT", "1")

import subprocess
import sys
import wave
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from soliloquy.analysis_store import AnalysisSnapshotStore
from soliloquy.storage import EntryStore
from soliloquy.web.app import app, get_analysis_store, get_store

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


def _override_get_store():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        yield store
    finally:
        store.close()


def _override_get_analysis_store():
    store = AnalysisSnapshotStore(TEST_DATABASE_URL)
    try:
        yield store
    finally:
        store.close()


app.dependency_overrides[get_store] = _override_get_store
app.dependency_overrides[get_analysis_store] = _override_get_analysis_store


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE analysis_snapshots")
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


def test_report_returns_a_clear_502_when_no_analyzer_provider_is_configured(client, monkeypatch):
    # Default provider chain is "free" (OpenRouter models, then
    # Gemini) -- with none of those keys set either, every provider in
    # the chain fails with a clear "no API key" message, and
    # FallbackAnalyzer aggregates them rather than hanging on a real
    # network call.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client.post("/entries", data={"text": "an entry to analyze"})

    response = client.post("/reports", data={"days": 7, "audience": "self"})

    assert response.status_code == 502
    assert "API key" in response.json()["detail"]


def test_report_returns_400_for_an_unknown_format(client, monkeypatch):
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    client.post("/entries", data={"text": "an entry"})

    response = client.post("/reports", data={"days": 7, "audience": "self", "format": "pptx"})

    assert response.status_code == 400


def _fake_claude_response():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "content": [{"text": '{"summary": "s", "mood_notes": "m", "key_topics": ["t"]}'}]
    }
    return fake


@pytest.mark.parametrize(
    "format,content_type,magic",
    [
        ("text", "text/plain", b""),
        ("markdown", "text/markdown", b""),
        ("html", "text/html", b"<!doctype html"),
        ("pdf", "application/pdf", b"%PDF"),
    ],
)
def test_report_returns_the_requested_format(client, monkeypatch, format, content_type, magic):
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    client.post("/entries", data={"text": "an entry to analyze"})

    with patch("requests.post", return_value=_fake_claude_response()):
        response = client.post("/reports", data={"days": 7, "audience": "self", "format": format})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert f'filename="soliloquy-report.{ {"text": "txt", "markdown": "md", "html": "html", "pdf": "pdf"}[format] }"' in response.headers["content-disposition"]
    if magic:
        assert response.content.startswith(magic)


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


def _synthesize_test_video(path: str, duration: float = 1.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", path,
        ],
        check=True,
        capture_output=True,
    )


def test_post_video_entry_stores_video_and_audio_and_transcribes(client, tmp_path):
    video_path = tmp_path / "entry.mp4"
    _synthesize_test_video(str(video_path))
    fake_module = _install_fake_faster_whisper([" transcribed from video "])

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        with open(video_path, "rb") as f:
            response = client.post(
                "/entries/video", files={"file": ("entry.mp4", f.read(), "video/mp4")}
            )

    assert response.status_code == 200
    body = response.json()
    assert body["video_path"].startswith("video/")
    assert body["audio_path"].startswith("audio/")
    assert body["transcript"] == "transcribed from video"

    # Both files really made it to object storage.
    assert client.get(f"/media/{body['video_path']}").status_code == 200
    assert client.get(f"/media/{body['audio_path']}").status_code == 200


def _synthesize_test_m4a(path: str, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac", path],
        check=True, capture_output=True,
    )


def test_post_audio_entry_accepts_a_real_m4a_file(client, tmp_path):
    # Not a hardcoded special case anywhere in the pipeline -- ffmpeg/
    # faster-whisper detect format from file contents, not extension.
    # This proves it with a real file rather than assuming it.
    m4a_path = tmp_path / "entry.m4a"
    _synthesize_test_m4a(str(m4a_path))
    fake_module = _install_fake_faster_whisper([" from an m4a file "])

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        with open(m4a_path, "rb") as f:
            response = client.post(
                "/entries/audio", files={"file": ("entry.m4a", f.read(), "audio/mp4")}
            )

    assert response.status_code == 200
    body = response.json()
    assert body["audio_path"].endswith(".m4a")
    assert body["transcript"] == "from an m4a file"

    media_response = client.get(f"/media/{body['audio_path']}")
    assert media_response.status_code == 200
    assert media_response.headers["content-type"] == "audio/mp4"


def test_post_video_entry_accepts_a_real_mkv_file(client, tmp_path):
    mkv_path = tmp_path / "entry.mkv"
    _synthesize_test_video(str(mkv_path))
    fake_module = _install_fake_faster_whisper([" from an mkv file "])

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        with open(mkv_path, "rb") as f:
            response = client.post(
                "/entries/video", files={"file": ("entry.mkv", f.read(), "video/x-matroska")}
            )

    assert response.status_code == 200
    body = response.json()
    assert body["video_path"].endswith(".mkv")
    assert body["transcript"] == "from an mkv file"

    media_response = client.get(f"/media/{body['video_path']}")
    assert media_response.status_code == 200
    assert media_response.headers["content-type"] == "video/x-matroska"


# ── HTML pages ───────────────────────────────────────────────────────

def test_entries_page_lists_entries_newest_first(client):
    client.post("/entries", data={"text": "older entry"})
    client.post("/entries", data={"text": "newer entry"})

    response = client.get("/")

    assert response.status_code == 200
    assert response.text.index("newer entry") < response.text.index("older entry")


def test_entries_page_shows_a_message_when_empty(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No entries yet" in response.text


def test_new_entry_page_loads(client):
    response = client.get("/new")
    assert response.status_code == 200
    assert "New entry" in response.text


def test_report_page_loads_and_lists_all_audiences(client):
    response = client.get("/report")
    assert response.status_code == 200
    for audience in ("self", "partner", "provider"):
        assert audience in response.text


def test_analysis_page_loads_and_shows_a_message_when_empty(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    assert "No automatic analysis yet" in response.text


def test_analysis_page_shows_saved_snapshots(client):
    from soliloquy.analysis_store import AnalysisSnapshot
    from soliloquy.analyzer import AnalysisResult

    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        store.add(AnalysisSnapshot(
            days=1, audience="self",
            result=AnalysisResult(entry_count=3, total_word_count=30, summary="a scheduled summary",
                                   mood_notes="steady", key_topics=["work"]),
        ))

    response = client.get("/analysis")

    assert response.status_code == 200
    assert "a scheduled summary" in response.text
