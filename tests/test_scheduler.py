import os

import pytest

from soliloquy.analysis_store import AnalysisSnapshotStore
from soliloquy.analyzer import AnalysisResult, NoEntriesError
from soliloquy.entry import Entry
from soliloquy.scheduler import run_scheduled_analysis
from soliloquy.storage import EntryStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


class FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def analyze(self, entries):
        if self.error:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    with AnalysisSnapshotStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE analysis_snapshots")
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
