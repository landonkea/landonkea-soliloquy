import os
from datetime import datetime, timedelta, timezone

import pytest

from soliloquy.actions import (
    add_entry, analyze_range, append_or_add_entry, journaling_streak, list_entries, on_this_day, report_range,
)
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
        self.last_instruction = None

    def analyze(self, entries, instruction=""):
        self.last_entries = entries
        self.last_instruction = instruction
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
        assert "partner" in analyzer.last_instruction
    finally:
        store.close()


def test_report_range_self_audience_passes_no_special_instruction():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        store.add(Entry(transcript="entry"))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="self")

        assert analyzer.last_instruction == ""
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


# ── append_or_add_entry (MQTT "append to today") ────────────────────

def test_append_or_add_entry_creates_a_new_entry_when_none_exists_today():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        entry, appended = append_or_add_entry(store, "first thought")

        assert appended is False
        assert entry.transcript == "first thought"
        assert len(list_entries(store)) == 1
    finally:
        store.close()


def test_append_or_add_entry_appends_to_a_recent_entry_from_today():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        first, _ = append_or_add_entry(store, "first thought")

        second, appended = append_or_add_entry(store, "also, one more thing")

        assert appended is True
        assert second.id == first.id
        assert second.transcript == "first thought also, one more thing"
        assert len(list_entries(store)) == 1
    finally:
        store.close()


def test_append_or_add_entry_creates_new_when_the_recent_entry_is_too_old():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        old = Entry(transcript="yesterday-ish", created_at=datetime.now(timezone.utc) - timedelta(minutes=90))
        store.add(old)

        entry, appended = append_or_add_entry(store, "new thought", within_minutes=60)

        assert appended is False
        assert entry.id != old.id
        assert len(list_entries(store)) == 2
    finally:
        store.close()


def test_append_or_add_entry_does_not_merge_across_different_speakers():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        append_or_add_entry(store, "from Landon", speaker="Landon")

        entry, appended = append_or_add_entry(store, "from someone else", speaker="Someone Else")

        assert appended is False
        assert len(list_entries(store)) == 2
    finally:
        store.close()


# ── journaling_streak ────────────────────────────────────────────────

def test_journaling_streak_counts_distinct_days_with_at_least_one_entry():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        now = datetime.now(timezone.utc)
        store.add(Entry(transcript="today", created_at=now))
        store.add(Entry(transcript="yesterday", created_at=now - timedelta(days=1)))
        store.add(Entry(transcript="also yesterday", created_at=now - timedelta(days=1, hours=1)))

        journaled, window = journaling_streak(store, window_days=7)

        assert journaled == 2
        assert window == 7
    finally:
        store.close()


def test_journaling_streak_is_zero_with_no_entries():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        journaled, window = journaling_streak(store, window_days=7)
        assert journaled == 0
    finally:
        store.close()


# ── on_this_day ──────────────────────────────────────────────────────

def test_on_this_day_uses_todays_date_by_default():
    store = EntryStore(TEST_DATABASE_URL)
    try:
        today = datetime.now(timezone.utc)
        store.add(Entry(transcript="a year ago today", created_at=today.replace(year=today.year - 1)))

        results = on_this_day(store)

        assert [e.transcript for e in results] == ["a year ago today"]
    finally:
        store.close()
