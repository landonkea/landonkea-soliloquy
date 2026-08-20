import os

import pytest

from soliloquy.analysis_store import AnalysisSnapshotStore
from soliloquy.analyzer import AnalysisResult, NoEntriesError
from soliloquy.entry import Entry
from soliloquy.object_storage import ObjectStore
from soliloquy.report_store import SavedReportStore
from soliloquy.scheduler import (
    _media_retention_days, run_media_retention_cleanup, run_scheduled_analysis, run_scheduled_monthly_report,
)
from soliloquy.storage import EntryStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


class FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def analyze(self, entries, instruction=""):
        if self.error:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE analysis_snapshots")
    with SavedReportStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE saved_reports")
    yield


def test_run_scheduled_analysis_saves_a_snapshot_and_returns_it():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry"))

    fake_result = AnalysisResult(entry_count=1, total_word_count=2, summary="s", mood_notes="m", key_topics=["t"])
    snapshot = run_scheduled_analysis(
        database_url=TEST_DATABASE_URL, days=1, analyzer=FakeAnalyzer(result=fake_result)
    )

    assert snapshot is not None
    assert snapshot.audience == "self"
    assert snapshot.result.summary == "s"

    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        assert store.latest().result.summary == "s"


def test_run_scheduled_analysis_skips_cleanly_when_there_are_no_entries():
    result = run_scheduled_analysis(
        database_url=TEST_DATABASE_URL, days=1, analyzer=FakeAnalyzer(error=NoEntriesError("empty"))
    )

    assert result is None
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        assert store.latest() is None


def test_run_scheduled_analysis_does_not_raise_when_the_provider_fails():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry"))

    result = run_scheduled_analysis(
        database_url=TEST_DATABASE_URL, days=1, analyzer=FakeAnalyzer(error=RuntimeError("provider down"))
    )

    assert result is None
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        assert store.latest() is None


# ── Scheduled monthly report ─────────────────────────────────────────

def test_run_scheduled_monthly_report_saves_a_markdown_report():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry for the month"))

    fake_result = AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=["t"])
    saved = run_scheduled_monthly_report(database_url=TEST_DATABASE_URL, analyzer=FakeAnalyzer(result=fake_result))

    assert saved is not None
    assert saved.days == 30
    assert saved.audience == "self"
    assert saved.source == "scheduled"
    assert saved.content.startswith("#")  # markdown

    with SavedReportStore(TEST_DATABASE_URL) as report_store:
        fetched = report_store.get(saved.id)
        assert fetched.content == saved.content


def test_run_scheduled_monthly_report_skips_cleanly_when_there_are_no_entries():
    result = run_scheduled_monthly_report(
        database_url=TEST_DATABASE_URL, analyzer=FakeAnalyzer(error=NoEntriesError("empty"))
    )

    assert result is None
    with SavedReportStore(TEST_DATABASE_URL) as report_store:
        assert report_store.recent() == []


def test_run_scheduled_monthly_report_does_not_raise_when_the_provider_fails():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry"))

    result = run_scheduled_monthly_report(
        database_url=TEST_DATABASE_URL, analyzer=FakeAnalyzer(error=RuntimeError("provider down"))
    )

    assert result is None


# ── Media retention cleanup ───────────────────────────────────────────

def test_media_retention_days_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("MEDIA_RETENTION_DAYS", raising=False)
    assert _media_retention_days() is None


def test_media_retention_days_parses_the_env_var(monkeypatch):
    monkeypatch.setenv("MEDIA_RETENTION_DAYS", "30")
    assert _media_retention_days() == 30


def test_run_media_retention_cleanup_is_a_no_op_when_retention_is_unset(monkeypatch):
    monkeypatch.delenv("MEDIA_RETENTION_DAYS", raising=False)
    assert run_media_retention_cleanup(database_url=TEST_DATABASE_URL) == 0


def test_run_media_retention_cleanup_removes_old_media_but_keeps_the_transcript():
    from datetime import datetime, timedelta, timezone
    import tempfile

    object_store = ObjectStore()
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(b"fake audio bytes")
        f.flush()
        key = object_store.upload_file(f.name, f"audio/retention-test-{os.getpid()}.wav")

    old_entry = Entry(
        transcript="an old entry with audio", audio_path=key,
        created_at=datetime.now(timezone.utc) - timedelta(days=100),
    )
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(old_entry)

        cleaned = run_media_retention_cleanup(database_url=TEST_DATABASE_URL, retention_days=90)

        assert cleaned == 1
        fetched = store.get(old_entry.id)
        assert fetched.transcript == "an old entry with audio"  # kept
        assert fetched.audio_path is None  # cleared

    with pytest.raises(Exception):
        object_store.download_to_temp(key)  # really gone from object storage


def test_run_media_retention_cleanup_leaves_recent_media_alone():
    with EntryStore(TEST_DATABASE_URL) as store:
        recent = Entry(transcript="recent entry with audio", audio_path="audio/recent-does-not-matter.wav")
        store.add(recent)

        cleaned = run_media_retention_cleanup(database_url=TEST_DATABASE_URL, retention_days=90)

        assert cleaned == 0
        assert store.get(recent.id).audio_path == "audio/recent-does-not-matter.wav"
