from datetime import datetime, timedelta, timezone

import pytest

from soliloquy.analyzer import AnalysisResult, NoEntriesError
from soliloquy.cli import add_entry, add_entry_from_audio, analyze_range, format_report, list_entries, main, report_range
from soliloquy.entry import Entry
from soliloquy.storage import EntryStore


class FakeTranscriber:
    def __init__(self, transcript: str = "a fake transcript"):
        self.transcript = transcript
        self.last_audio_path = None

    def transcribe(self, audio_path: str) -> str:
        self.last_audio_path = audio_path
        return self.transcript


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


def test_add_entry_persists_and_returns_the_entry(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        entry = add_entry(store, "A real entry via the CLI helper")
        assert entry.transcript == "A real entry via the CLI helper"
        assert store.get(entry.id) is not None
    finally:
        store.close()


def test_list_entries_returns_everything_added(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        add_entry(store, "first")
        add_entry(store, "second")
        assert [e.transcript for e in list_entries(store)] == ["first", "second"]
    finally:
        store.close()


def test_list_entries_is_empty_for_a_fresh_store(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        assert list_entries(store) == []
    finally:
        store.close()


def test_add_entry_from_audio_transcribes_and_persists_with_audio_path(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    transcriber = FakeTranscriber("This is what the recording said.")
    try:
        entry = add_entry_from_audio(store, transcriber, "/some/recording.wav")

        assert entry.transcript == "This is what the recording said."
        assert entry.audio_path == "/some/recording.wav"
        assert transcriber.last_audio_path == "/some/recording.wav"

        fetched = store.get(entry.id)
        assert fetched.transcript == "This is what the recording said."
        assert fetched.audio_path == "/some/recording.wav"
    finally:
        store.close()


def test_add_entry_from_audio_appears_in_list_entries_alongside_typed_ones(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        add_entry(store, "typed entry")
        add_entry_from_audio(store, FakeTranscriber("spoken entry"), "/rec.wav")

        transcripts = [e.transcript for e in list_entries(store)]
        assert transcripts == ["typed entry", "spoken entry"]
    finally:
        store.close()


def test_analyze_range_only_passes_entries_inside_the_window_to_the_analyzer(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        now = datetime.now(timezone.utc)
        store.add(Entry(transcript="too old", created_at=now - timedelta(days=30)))
        store.add(Entry(transcript="within range", created_at=now - timedelta(days=2)))

        analyzer = FakeAnalyzer()
        analyze_range(store, analyzer, days=7)

        assert [e.transcript for e in analyzer.last_entries] == ["within range"]
    finally:
        store.close()


def test_analyze_range_returns_the_analyzers_result(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
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


def test_analyze_range_raises_no_entries_error_when_window_is_empty(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        with pytest.raises(NoEntriesError):
            analyze_range(store, FakeAnalyzer(), days=7)
    finally:
        store.close()


# ── report_range / format_report ────────────────────────────────────

def test_report_range_self_audience_sees_every_entry_regardless_of_sharing_flags(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with partner", shareable_with_partner=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="self")

        assert {e.transcript for e in analyzer.last_entries} == {"private one", "shared with partner"}
    finally:
        store.close()


def test_report_range_partner_audience_only_sees_entries_shared_with_partner(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with partner", shareable_with_partner=True))
        store.add(Entry(transcript="shared with provider only", shareable_with_provider=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="partner")

        assert [e.transcript for e in analyzer.last_entries] == ["shared with partner"]
    finally:
        store.close()


def test_report_range_provider_audience_only_sees_entries_shared_with_provider(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        store.add(Entry(transcript="private one"))
        store.add(Entry(transcript="shared with provider", shareable_with_provider=True))

        analyzer = FakeAnalyzer()
        report_range(store, analyzer, days=7, audience="provider")

        assert [e.transcript for e in analyzer.last_entries] == ["shared with provider"]
    finally:
        store.close()


def test_report_range_raises_no_entries_error_when_nothing_matches_the_audience(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        store.add(Entry(transcript="private one"))
        with pytest.raises(NoEntriesError):
            report_range(store, FakeAnalyzer(), days=7, audience="partner")
    finally:
        store.close()


def test_report_range_rejects_an_unknown_audience(tmp_path):
    store = EntryStore(str(tmp_path / "test.db"))
    try:
        store.add(Entry(transcript="entry"))
        with pytest.raises(ValueError):
            report_range(store, FakeAnalyzer(), days=7, audience="stranger")
    finally:
        store.close()


def test_format_report_includes_summary_mood_topics_and_entry_transcripts():
    result = AnalysisResult(
        entry_count=1, total_word_count=3, summary="a summary", mood_notes="steady",
        key_topics=["work", "sleep"],
    )
    entries = [Entry(transcript="an entry to show")]

    text = format_report(result, entries, audience="partner", days=7)

    assert "a summary" in text
    assert "steady" in text
    assert "work, sleep" in text
    assert "an entry to show" in text
    assert "audience: partner" in text


# ── `share` CLI command ──────────────────────────────────────────────

def test_share_command_sets_the_requested_flags(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = EntryStore(db_path)
    entry = Entry(transcript="entry")
    store.add(entry)
    store.close()

    exit_code = main(["--db", db_path, "share", entry.id, "--partner"])

    assert exit_code == 0
    with EntryStore(db_path) as store:
        fetched = store.get(entry.id)
        assert fetched.shareable_with_partner is True
        assert fetched.shareable_with_provider is False


def test_share_command_can_unset_a_flag(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = EntryStore(db_path)
    entry = Entry(transcript="entry", shareable_with_partner=True)
    store.add(entry)
    store.close()

    exit_code = main(["--db", db_path, "share", entry.id, "--no-partner"])

    assert exit_code == 0
    with EntryStore(db_path) as store:
        assert store.get(entry.id).shareable_with_partner is False


def test_share_command_returns_nonzero_for_an_unknown_entry_id(tmp_path):
    db_path = str(tmp_path / "test.db")
    EntryStore(db_path).close()

    exit_code = main(["--db", db_path, "share", "does-not-exist", "--partner"])

    assert exit_code == 1


def test_share_command_returns_nonzero_when_no_flags_are_passed(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = EntryStore(db_path)
    entry = Entry(transcript="entry")
    store.add(entry)
    store.close()

    exit_code = main(["--db", db_path, "share", entry.id])

    assert exit_code == 1
