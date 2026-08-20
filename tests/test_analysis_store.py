import os

import psycopg
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


def test_mood_score_round_trips_when_present(store):
    snapshot = AnalysisSnapshot(
        days=1, audience="self",
        result=AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=[], mood_score=7),
    )
    store.add(snapshot)

    assert store.latest().result.mood_score == 7


def test_mood_score_is_none_when_not_provided(store):
    store.add(_snapshot())

    assert store.latest().result.mood_score is None


def test_a_snapshots_table_created_before_mood_score_existed_still_opens_and_defaults_to_none():
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS analysis_snapshots")
    conn.execute(
        "CREATE TABLE analysis_snapshots (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, days INTEGER NOT NULL, "
        "audience TEXT NOT NULL, entry_count INTEGER NOT NULL, total_word_count INTEGER NOT NULL, "
        "summary TEXT NOT NULL, mood_notes TEXT NOT NULL, key_topics TEXT NOT NULL)"
    )
    conn.close()

    with AnalysisSnapshotStore(TEST_DATABASE_URL) as s:
        s.add(_snapshot())
        assert s.latest().result.mood_score is None
