import os

import pytest

from soliloquy.analysis_store import AnalysisSnapshot, AnalysisSnapshotStore
from soliloquy.analyzer import AnalysisResult

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


@pytest.fixture
def store():
    s = AnalysisSnapshotStore(TEST_DATABASE_URL)
    s._conn.execute("TRUNCATE TABLE analysis_snapshots")
    yield s
    s.close()


def _snapshot(audience="self", summary="s"):
    return AnalysisSnapshot(
        days=1, audience=audience,
        result=AnalysisResult(entry_count=2, total_word_count=10, summary=summary, mood_notes="m", key_topics=["t"]),
    )


def test_latest_returns_none_when_empty(store):
    assert store.latest() is None


def test_add_then_latest_round_trips_a_snapshot(store):
    snapshot = _snapshot()
    store.add(snapshot)

    fetched = store.latest()

    assert fetched.id == snapshot.id
    assert fetched.days == 1
    assert fetched.audience == "self"
    assert fetched.result.summary == "s"
    assert fetched.result.key_topics == ["t"]


def test_latest_returns_the_most_recently_added_snapshot(store):
    store.add(_snapshot(summary="first"))
    store.add(_snapshot(summary="second"))

    assert store.latest().result.summary == "second"


def test_latest_can_filter_by_audience(store):
    store.add(_snapshot(audience="self", summary="self summary"))
    store.add(_snapshot(audience="partner", summary="partner summary"))

    assert store.latest(audience="partner").result.summary == "partner summary"
    assert store.latest(audience="self").result.summary == "self summary"


def test_recent_returns_newest_first_and_respects_limit(store):
    store.add(_snapshot(summary="a"))
    store.add(_snapshot(summary="b"))
    store.add(_snapshot(summary="c"))

    recent = store.recent(limit=2)

    assert [s.result.summary for s in recent] == ["c", "b"]
