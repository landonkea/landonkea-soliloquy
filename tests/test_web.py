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

from soliloquy.analysis_store import AnalysisSnapshot, AnalysisSnapshotStore
from soliloquy.analyzer import AnalysisResult
from soliloquy.report_store import SavedReportStore
from soliloquy.storage import EntryStore
from soliloquy.web.app import app, get_analysis_store, get_saved_report_store, get_store

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


def _override_get_saved_report_store():
    store = SavedReportStore(TEST_DATABASE_URL)
    try:
        yield store
    finally:
        store.close()


app.dependency_overrides[get_store] = _override_get_store
app.dependency_overrides[get_analysis_store] = _override_get_analysis_store
app.dependency_overrides[get_saved_report_store] = _override_get_saved_report_store


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE analysis_snapshots")
    with SavedReportStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE saved_reports")
    yield


@pytest.fixture
def client():
    # Must use TestClient as a context manager -- otherwise FastAPI's
    # lifespan (startup/shutdown) never runs at all, including
    # noise_reduction.preload(), which matters here: see its
    # docstring for why torch has to load on this thread, not a
    # request worker thread.
    with TestClient(app) as c:
        yield c


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


def test_update_transcript_corrects_an_existing_entry(client):
    created = client.post("/entries", data={"text": "a bad transcription"}).json()

    response = client.post(f"/entries/{created['id']}/transcript", data={"transcript": "corrected text"})

    assert response.status_code == 200
    assert response.json()["transcript"] == "corrected text"
    with EntryStore(TEST_DATABASE_URL) as store:
        assert store.get(created["id"]).transcript == "corrected text"


def test_update_transcript_returns_404_for_an_unknown_id(client):
    response = client.post("/entries/does-not-exist/transcript", data={"transcript": "text"})
    assert response.status_code == 404


def test_delete_entry_removes_it_from_the_database(client):
    created = client.post("/entries", data={"text": "entry to delete"}).json()

    response = client.delete(f"/entries/{created['id']}")

    assert response.status_code == 200
    with EntryStore(TEST_DATABASE_URL) as store:
        assert store.get(created["id"]) is None


def test_delete_entry_returns_404_for_an_unknown_id(client):
    response = client.delete("/entries/does-not-exist")
    assert response.status_code == 404


def test_delete_entry_also_deletes_its_audio_from_object_storage(client):
    fake_module = _install_fake_faster_whisper([" to be deleted "])
    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        created = client.post(
            "/entries/audio", files={"file": ("entry.wav", _silent_wav_bytes(), "audio/wav")}
        ).json()
    audio_key = created["audio_path"]
    assert client.get(f"/media/{audio_key}").status_code == 200  # really there beforehand

    response = client.delete(f"/entries/{created['id']}")

    assert response.status_code == 200
    assert client.get(f"/media/{audio_key}").status_code == 404  # really gone afterward


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
    # Not byte-identical to the upload -- the stored audio goes through
    # voice isolation + normalization first (see noise_reduction.py),
    # so just confirm it's a real, non-empty WAV file.
    assert response.content.startswith(b"RIFF")
    assert len(response.content) > 0


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
    assert body["transcript"] == "Transcribed from video."

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
    # This proves it with a real file rather than assuming it. The
    # voice-isolation + normalization pass (noise_reduction.py) always
    # outputs WAV regardless of the input container, so the stored
    # path ends in .wav rather than preserving .m4a.
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
    assert body["audio_path"].endswith(".wav")
    assert body["transcript"] == "From an m4a file."

    media_response = client.get(f"/media/{body['audio_path']}")
    assert media_response.status_code == 200
    # The stored file is always the cleaned-up WAV, not the original
    # upload -- see noise_reduction.py.
    assert media_response.headers["content-type"] == "audio/wav"


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
    assert body["transcript"] == "From an mkv file."

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


def test_new_entry_page_shows_todays_prompt(client):
    from markupsafe import escape

    from soliloquy.prompts import get_daily_prompt

    response = client.get("/new")

    # Jinja2 autoescapes apostrophes (e.g. "What's" -> "What&#39;s"), so
    # compare against the same escaping instead of the raw string.
    assert str(escape(get_daily_prompt())) in response.text


def test_report_page_loads_and_lists_all_audiences(client):
    response = client.get("/report")
    assert response.status_code == 200
    for audience in ("self", "partner", "provider"):
        assert audience in response.text


def test_report_page_shows_todays_tip(client):
    from markupsafe import escape

    from soliloquy.tips import get_daily_tip

    response = client.get("/report")

    assert str(escape(get_daily_tip())) in response.text


def test_analysis_page_loads_and_shows_a_message_when_empty(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    assert "No automatic analysis yet" in response.text


def test_analysis_page_shows_todays_tip(client):
    from markupsafe import escape

    from soliloquy.tips import get_daily_tip

    response = client.get("/analysis")

    assert str(escape(get_daily_tip())) in response.text


def test_entries_page_shows_todays_tip(client):
    from markupsafe import escape

    from soliloquy.tips import get_daily_tip

    response = client.get("/")

    assert str(escape(get_daily_tip())) in response.text


def test_analysis_page_shows_not_set_for_unconfigured_keys(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.get("/analysis")

    assert response.status_code == 200
    assert response.text.count('placeholder="Not set"') == 3
    assert "OPENROUTER_API_KEY" in response.text
    assert "GEMINI_API_KEY" in response.text


def test_analysis_page_masks_a_configured_key_without_exposing_it(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-supersecretvalue12345")

    response = client.get("/analysis")

    assert "sk-or-supersecretvalue12345" not in response.text
    assert "sk-o" in response.text  # first 4 chars shown
    assert "2345" in response.text  # last 4 chars shown


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


# ── Search ────────────────────────────────────────────────────────────

def test_entries_page_search_finds_matching_entries(client):
    client.post("/entries", data={"text": "I talked to my sister about the move"})
    client.post("/entries", data={"text": "nothing much happened today"})

    response = client.get("/", params={"q": "sister"})

    assert "I talked to my sister" in response.text
    assert "nothing much happened" not in response.text


def test_entries_page_search_shows_no_matches_message(client):
    client.post("/entries", data={"text": "an entry"})

    response = client.get("/", params={"q": "nonexistentword"})

    assert "No matching entries" in response.text


# ── Tags ─────────────────────────────────────────────────────────────

def test_update_tags_sets_the_tag_list(client):
    created = client.post("/entries", data={"text": "an entry"}).json()

    response = client.post(f"/entries/{created['id']}/tags", data={"tags": "work, family"})

    assert response.status_code == 200
    assert response.json()["tags"] == ["work", "family"]


def test_update_tags_returns_404_for_an_unknown_id(client):
    response = client.post("/entries/does-not-exist/tags", data={"tags": "work"})
    assert response.status_code == 404


def test_entries_page_can_filter_by_tag(client):
    tagged = client.post("/entries", data={"text": "about work"}).json()
    client.post(f"/entries/{tagged['id']}/tags", data={"tags": "work"})
    client.post("/entries", data={"text": "about family"})

    response = client.get("/", params={"tag": "work"})

    assert "about work" in response.text
    assert "about family" not in response.text


# ── Streak ───────────────────────────────────────────────────────────

def test_entries_page_shows_the_streak_line(client):
    client.post("/entries", data={"text": "an entry"})

    response = client.get("/")

    assert "Journaled 1 of the last 7 days" in response.text


# ── Saved reports + share links ──────────────────────────────────────

def test_save_report_creates_a_saved_report(client, monkeypatch):
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    client.post("/entries", data={"text": "an entry to save"})

    with patch("requests.post", return_value=_fake_claude_response()):
        response = client.post("/reports/save", data={"days": 30, "audience": "self"})

    assert response.status_code == 200
    report_id = response.json()["id"]

    saved_page = client.get("/reports/saved")
    assert report_id in saved_page.text or "last 30 days" in saved_page.text


def test_save_report_returns_404_when_nothing_matches(client):
    response = client.post("/reports/save", data={"days": 7, "audience": "partner"})
    assert response.status_code == 404


def test_share_link_resolves_to_the_report_content(client, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-session-secret")
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    client.post("/entries", data={"text": "an entry to share"})

    with patch("requests.post", return_value=_fake_claude_response()):
        saved = client.post("/reports/save", data={"days": 30, "audience": "self"}).json()

    link_response = client.post(f"/reports/saved/{saved['id']}/share-link", data={"expires_in_days": 7})
    assert link_response.status_code == 200
    share_url = link_response.json()["url"]

    shared_response = client.get(share_url)
    assert shared_response.status_code == 200
    assert "s" in shared_response.text  # the fake summary text from _fake_claude_response


def test_share_link_route_is_reachable_with_no_auth_session(client, monkeypatch):
    # Regression check for auth.py's _EXEMPT_PREFIXES -- a share link
    # is meant for someone with no login here at all. Setup (creating
    # the entry and saving/sharing the report) happens AS the logged-in
    # owner, since those routes are (correctly) still behind the gate;
    # only the final GET is checked unauthenticated.
    monkeypatch.setenv("AUTH_PASSWORD", "some-password")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-session-secret")
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    client.post("/login", data={"password": "some-password", "next": "/"})
    client.post("/entries", data={"text": "an entry to share"})

    with patch("requests.post", return_value=_fake_claude_response()):
        saved = client.post("/reports/save", data={"days": 30, "audience": "self"}).json()
    share_url = client.post(f"/reports/saved/{saved['id']}/share-link", data={"expires_in_days": 7}).json()["url"]

    client.cookies.clear()  # simulate a visitor with no session at all
    response = client.get(share_url, follow_redirects=False)
    assert response.status_code == 200  # not a 303 redirect to /login


def test_share_link_returns_404_for_a_bogus_token(client, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-session-secret")
    response = client.get("/reports/shared/not-a-real-token")
    assert response.status_code == 404


# ── Mood trend chart ──────────────────────────────────────────────────

def test_analysis_page_shows_the_mood_chart_with_enough_scored_snapshots():
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        for score in (4, 8):
            store.add(AnalysisSnapshot(
                days=1, audience="self",
                result=AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=[], mood_score=score),
            ))

    with TestClient(app) as client:
        response = client.get("/analysis")

    assert "<svg" in response.text
    assert "Mood trend" in response.text


def test_analysis_page_has_no_mood_chart_with_fewer_than_two_scored_snapshots(client):
    response = client.get("/analysis")
    assert "Mood trend" not in response.text
