import os
from datetime import datetime, timedelta, timezone

import pytest

from soliloquy.actions import add_entry, analyze_range, list_entries, report_range
from soliloquy.analyzer import AnalysisResult, NoEntriesError
from soliloquy.entry import Entry
from soliloquy.storage import EntryStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    yield


class FakeAnalyzer:
    def __init__(self, result: AnalysisResult | None = None):
        self.result = result or AnalysisResult(
            entry_count=0, total_word_count=0, summary="fake summary",
            mood_notes="fake mood", key_topics=["fake"],
        )
        self.last_entries = None

    def analyze(self, entries):
        self.last_entries = entries
        if not entries:
            raise NoEntriesError("no entries")
        return self.result


def test_add_entry_persists_and_returns_the_entry():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        entry = add_entry(store, "A real entry via the shared action")
        assert entry.transcript == "A real entry via the shared action"
        assert store.get(entry.id) is not None
    finally:
        store.close()


def test_list_entries_returns_everything_added():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        add_entry(store, "first")
        add_entry(store, "second")
        assert [e.transcript for e in list_entries(store)] == ["first", "second"]
    finally:
        store.close()


def test_list_entries_is_empty_for_a_fresh_store():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        assert list_entries(store) == []
    finally:
        store.close()


def test_analyze_range_only_passes_entries_inside_the_window_to_the_analyzer():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        now = datetime.now(timezone.utc)
        store.add(Entry(transcript="too old", created_at=now - timedelta(days=30)))
        store.add(Entry(transcript="within range", created_at=now - timedelta(days=2)))

        analyzer = FakeAnalyzer()
        analyze_range(store, analyzer, days=7)

        assert [e.transcript for e in analyzer.last_entries] == ["within range"]
    finally:
        store.close()


def test_analyze_range_returns_the_analyzers_result():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="an entry"))
        expected = AnalysisResult(
            entry_count=1, total_word_count=2, summary="s", mood_notes="m", key_topics=["t"]
        )
        analyzer = FakeAnalyzer(result=expected)

        result = analyze_range(store, analyzer, days=7)

        assert result is expected
    finally:
        store.close()


def test_analyze_range_raises_no_entries_error_when_window_is_empty():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        with pytest.raises(NoEntriesError):
            analyze_range(store, FakeAnalyzer(), days=7)
    finally:
        store.close()


# ── report_range ─────────────────────────────────────────────────────

def test_report_range_self_audience_sees_every_entry_regardless_of_sharing_flags():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with partner", shareable_with_partner=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="self")

        assert {e.transcript for e in analyzer.last_entries} == {"private one", "shared with partner"}
    finally:
        store.close()


def test_report_range_partner_audience_only_sees_entries_shared_with_partner():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with partner", shareable_with_partner=True))
        store.add(Entry(transcript="shared with provider only", shareable_with_provider=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="partner")

        assert [e.transcript for e in analyzer.last_entries] == ["shared with partner"]
    finally:
        store.close()


def test_report_range_provider_audience_only_sees_entries_shared_with_provider():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with provider", shareable_with_provider=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="provider")

        assert [e.transcript for e in analyzer.last_entries] == ["shared with provider"]
    finally:
        store.close()


def test_report_range_raises_no_entries_error_when_nothing_matches_the_audience():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="private one"))
        with pytest.raises(NoEntriesError):
            report_range(store, FakeAnalyzer(), days=7, audience="partner")
    finally:
        store.close()


def test_report_range_rejects_an_unknown_audience():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="entry"))
        with pytest.raises(ValueError):
            report_range(store, FakeAnalyzer(), days=7, audience="stranger")
    finally:
        store.close()
