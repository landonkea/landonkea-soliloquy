import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from soliloquy.entry import Entry
from soliloquy.storage import EntryStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


@pytest.fixture
def store():
    s = EntryStore(TEST_DATABASE_URL)
    s._conn.execute("TRUNCATE TABLE entries")
    yield s
    s.close()


def test_add_then_get_round_trips_an_entry(store):
    entry = Entry(transcript="Today was a good day.")
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched is not None
    assert fetched.id == entry.id
    assert fetched.transcript == entry.transcript
    assert fetched.created_at == entry.created_at


def test_get_returns_none_for_an_unknown_id(store):
    assert store.get("does-not-exist") is None


def test_all_returns_entries_in_chronological_order(store):
    base = datetime.now(timezone.utc)
    first = Entry(transcript="first", created_at=base)
    second = Entry(transcript="second", created_at=base + timedelta(hours=1))
    # Insert out of order to prove ORDER BY, not insertion order, drives the result.
    store.add(second)
    store.add(first)

    entries = store.all()

    assert [e.transcript for e in entries] == ["first", "second"]


def test_range_between_only_returns_entries_inside_the_window(store):
    base = datetime(2026, 1, 15, tzinfo=timezone.utc)
    store.add(Entry(transcript="too early", created_at=base - timedelta(days=1)))
    store.add(Entry(transcript="in range, start", created_at=base))
    store.add(Entry(transcript="in range, middle", created_at=base + timedelta(hours=12)))
    store.add(Entry(transcript="too late", created_at=base + timedelta(days=1)))

    results = store.range_between(base, base + timedelta(days=1))

    assert [e.transcript for e in results] == ["in range, start", "in range, middle"]


def test_range_between_end_is_exclusive(store):
    base = datetime(2026, 1, 15, tzinfo=timezone.utc)
    store.add(Entry(transcript="exactly at end", created_at=base + timedelta(days=1)))

    results = store.range_between(base, base + timedelta(days=1))

    assert results == []


def test_delete_removes_the_entry_and_reports_success(store):
    entry = Entry(transcript="delete me")
    store.add(entry)

    assert store.delete(entry.id) is True
    assert store.get(entry.id) is None


def test_delete_returns_false_for_an_unknown_id(store):
    assert store.delete("does-not-exist") is False


def test_audio_path_is_preserved_when_set(store):
    entry = Entry(transcript="spoken entry", audio_path="/recordings/abc.wav")
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched.audio_path == "/recordings/abc.wav"


def test_video_path_is_preserved_when_set(store):
    entry = Entry(transcript="video entry", video_path="videos/abc.mp4")
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched.video_path == "videos/abc.mp4"


def test_update_video_path_sets_it_on_an_existing_entry(store):
    entry = Entry(transcript="entry")
    store.add(entry)

    assert store.update_video_path(entry.id, "videos/later.mp4") is True
    assert store.get(entry.id).video_path == "videos/later.mp4"


def test_update_video_path_returns_false_for_an_unknown_entry_id(store):
    assert store.update_video_path("does-not-exist", "videos/x.mp4") is False


def test_update_transcript_corrects_an_existing_entry(store):
    entry = Entry(transcript="a bad transcription")
    store.add(entry)

    assert store.update_transcript(entry.id, "the corrected version") is True
    assert store.get(entry.id).transcript == "the corrected version"


def test_update_transcript_returns_false_for_an_unknown_entry_id(store):
    assert store.update_transcript("does-not-exist", "text") is False


def test_entrystore_works_as_a_context_manager():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
        store.add(Entry(transcript="via context manager"))
        assert len(store.all()) == 1

    # The connection should be closed after the `with` block -- confirm
    # by checking further use raises, rather than just trusting close()
    # was called.
    with pytest.raises(psycopg.OperationalError):
        store.all()


def test_entrystore_closes_even_if_an_exception_is_raised_inside_the_with_block():
    with pytest.raises(ValueError):
        with EntryStore(TEST_DATABASE_URL) as store:
            store._conn.execute("TRUNCATE TABLE entries")
            store.add(Entry(transcript="before the exception"))
            raise ValueError("simulated failure")

    with pytest.raises(psycopg.OperationalError):
        store.all()


# ── Sharing flags ────────────────────────────────────────────────────

def test_new_entries_default_to_private_shared_with_no_one(store):
    entry = Entry(transcript="private by default")
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched.shareable_with_partner is False
    assert fetched.shareable_with_provider is False


def test_sharing_flags_round_trip_when_set_explicitly(store):
    entry = Entry(transcript="shared with partner only", shareable_with_partner=True)
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched.shareable_with_partner is True
    assert fetched.shareable_with_provider is False


def test_update_sharing_sets_only_the_flag_passed(store):
    entry = Entry(transcript="entry")
    store.add(entry)

    store.update_sharing(entry.id, shareable_with_provider=True)

    fetched = store.get(entry.id)
    assert fetched.shareable_with_provider is True
    assert fetched.shareable_with_partner is False  # untouched


def test_update_sharing_can_set_both_flags_at_once(store):
    entry = Entry(transcript="entry")
    store.add(entry)

    store.update_sharing(entry.id, shareable_with_partner=True, shareable_with_provider=True)

    fetched = store.get(entry.id)
    assert fetched.shareable_with_partner is True
    assert fetched.shareable_with_provider is True


def test_update_sharing_can_unset_a_previously_set_flag(store):
    entry = Entry(transcript="entry", shareable_with_partner=True)
    store.add(entry)

    store.update_sharing(entry.id, shareable_with_partner=False)

    assert store.get(entry.id).shareable_with_partner is False


def test_update_sharing_returns_false_for_an_unknown_entry_id(store):
    assert store.update_sharing("does-not-exist", shareable_with_partner=True) is False


def test_update_sharing_with_no_flags_passed_is_a_no_op_that_confirms_existence(store):
    entry = Entry(transcript="entry")
    store.add(entry)

    assert store.update_sharing(entry.id) is True
    assert store.update_sharing("does-not-exist") is False


# ── Tags ─────────────────────────────────────────────────────────────

def test_tags_round_trip_when_set(store):
    entry = Entry(transcript="entry about work", tags=["work", "stress"])
    store.add(entry)

    fetched = store.get(entry.id)

    assert fetched.tags == ["work", "stress"]


def test_new_entries_default_to_no_tags(store):
    entry = Entry(transcript="untagged")
    store.add(entry)

    assert store.get(entry.id).tags == []


def test_update_tags_replaces_the_tag_list(store):
    entry = Entry(transcript="entry", tags=["old"])
    store.add(entry)

    assert store.update_tags(entry.id, ["new", "tags"]) is True
    assert store.get(entry.id).tags == ["new", "tags"]


def test_by_tag_returns_only_entries_with_that_tag(store):
    store.add(Entry(transcript="about work", tags=["work"]))
    store.add(Entry(transcript="about family", tags=["family"]))
    store.add(Entry(transcript="about both", tags=["work", "family"]))

    results = store.by_tag("work")

    assert {e.transcript for e in results} == {"about work", "about both"}


def test_all_tags_returns_distinct_tags_sorted():
    with EntryStore(TEST_DATABASE_URL) as s:
        s._conn.execute("TRUNCATE TABLE entries")
        s.add(Entry(transcript="a", tags=["work", "family"]))
        s.add(Entry(transcript="b", tags=["family", "health"]))

        assert s.all_tags() == ["family", "health", "work"]


# ── Speaker ──────────────────────────────────────────────────────────

def test_speaker_round_trips_when_set(store):
    entry = Entry(transcript="from the household", speaker="Landon")
    store.add(entry)

    assert store.get(entry.id).speaker == "Landon"


def test_new_entries_default_to_no_speaker(store):
    entry = Entry(transcript="entry")
    store.add(entry)

    assert store.get(entry.id).speaker is None


# ── Full-text search ─────────────────────────────────────────────────

def test_search_finds_entries_containing_the_query(store):
    store.add(Entry(transcript="I talked to my sister about the move"))
    store.add(Entry(transcript="Nothing much happened today"))

    results = store.search("sister")

    assert [e.transcript for e in results] == ["I talked to my sister about the move"]


def test_search_returns_nothing_for_unmatched_terms(store):
    store.add(Entry(transcript="a totally unrelated entry"))

    assert store.search("nonexistentword") == []


def test_search_is_not_confused_by_word_stems(store):
    # plainto_tsquery + the 'english' config should match "running" to
    # a search for "run" -- proves real full-text search is happening,
    # not a plain substring LIKE.
    store.add(Entry(transcript="I went running this morning"))

    results = store.search("run")

    assert len(results) == 1


# ── On this day ──────────────────────────────────────────────────────

def test_on_this_day_returns_entries_from_the_same_month_and_day_in_past_years(store):
    store.add(Entry(transcript="last year, same day", created_at=datetime(2025, 3, 14, tzinfo=timezone.utc)))
    store.add(Entry(transcript="two years ago, same day", created_at=datetime(2024, 3, 14, tzinfo=timezone.utc)))
    store.add(Entry(transcript="same day this year but later", created_at=datetime(2026, 3, 14, tzinfo=timezone.utc)))
    store.add(Entry(transcript="different day", created_at=datetime(2025, 3, 15, tzinfo=timezone.utc)))

    results = store.on_this_day(date(2026, 3, 14))

    assert {e.transcript for e in results} == {"last year, same day", "two years ago, same day"}


# ── Streak / cadence helper (distinct_entry_dates) ──────────────────

def test_distinct_entry_dates_counts_each_day_once_regardless_of_entry_count(store):
    store.add(Entry(transcript="morning", created_at=datetime(2026, 3, 10, 8, tzinfo=timezone.utc)))
    store.add(Entry(transcript="evening", created_at=datetime(2026, 3, 10, 20, tzinfo=timezone.utc)))
    store.add(Entry(transcript="next day", created_at=datetime(2026, 3, 11, 8, tzinfo=timezone.utc)))
    store.add(Entry(transcript="too old", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    dates = store.distinct_entry_dates(datetime(2026, 3, 1, tzinfo=timezone.utc))

    assert dates == {date(2026, 3, 10), date(2026, 3, 11)}


# ── Media retention helpers ──────────────────────────────────────────

def test_entries_with_media_older_than_finds_only_old_entries_with_media(store):
    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
    old_with_media = Entry(
        transcript="old with audio", audio_path="audio/old.wav", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    old_without_media = Entry(transcript="old, text only", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    recent_with_media = Entry(
        transcript="recent with audio", audio_path="audio/new.wav", created_at=datetime(2026, 3, 15, tzinfo=timezone.utc)
    )
    store.add(old_with_media)
    store.add(old_without_media)
    store.add(recent_with_media)

    results = store.entries_with_media_older_than(cutoff)

    assert [e.transcript for e in results] == ["old with audio"]


def test_clear_media_paths_removes_audio_and_video_but_keeps_the_transcript(store):
    entry = Entry(transcript="keep this text", audio_path="audio/a.wav", video_path="video/a.mp4")
    store.add(entry)

    assert store.clear_media_paths(entry.id) is True

    fetched = store.get(entry.id)
    assert fetched.transcript == "keep this text"
    assert fetched.audio_path is None
    assert fetched.video_path is None


def test_clear_media_paths_returns_false_for_an_unknown_entry_id(store):
    assert store.clear_media_paths("does-not-exist") is False


# ── Encryption at rest ───────────────────────────────────────────────

def test_transcript_is_stored_as_plaintext_when_no_encryption_key_is_configured(store):
    entry = Entry(transcript="plaintext by default")
    store.add(entry)

    raw = store._conn.execute("SELECT transcript FROM entries WHERE id = %s", (entry.id,)).fetchone()[0]
    assert raw == "plaintext by default"


def test_transcript_round_trips_through_encryption_and_is_unreadable_in_the_raw_column():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with EntryStore(TEST_DATABASE_URL, encryption_key=key) as s:
        s._conn.execute("TRUNCATE TABLE entries")
        entry = Entry(transcript="a genuinely private thought")
        s.add(entry)

        raw = s._conn.execute("SELECT transcript FROM entries WHERE id = %s", (entry.id,)).fetchone()[0]
        assert "a genuinely private thought" not in raw
        assert raw.startswith("enc1:")

        fetched = s.get(entry.id)
        assert fetched.transcript == "a genuinely private thought"


def test_search_still_works_when_encryption_is_on():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with EntryStore(TEST_DATABASE_URL, encryption_key=key) as s:
        s._conn.execute("TRUNCATE TABLE entries")
        s.add(Entry(transcript="I talked to my sister about the move"))

        results = s.search("sister")

        assert len(results) == 1
        assert results[0].transcript == "I talked to my sister about the move"


def test_reading_an_encrypted_entry_without_the_key_raises_a_clear_error():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with EntryStore(TEST_DATABASE_URL, encryption_key=key) as s:
        s._conn.execute("TRUNCATE TABLE entries")
        entry = Entry(transcript="locked without the key")
        s.add(entry)
        entry_id = entry.id

    with EntryStore(TEST_DATABASE_URL) as s_no_key:  # no encryption_key this time
        with pytest.raises(RuntimeError, match="TRANSCRIPT_ENCRYPTION_KEY"):
            s_no_key.get(entry_id)


def test_reading_an_encrypted_entry_with_the_wrong_key_raises_a_clear_error():
    from cryptography.fernet import Fernet

    with EntryStore(TEST_DATABASE_URL, encryption_key=Fernet.generate_key().decode()) as s:
        s._conn.execute("TRUNCATE TABLE entries")
        entry = Entry(transcript="encrypted with key A")
        s.add(entry)
        entry_id = entry.id

    with EntryStore(TEST_DATABASE_URL, encryption_key=Fernet.generate_key().decode()) as s_wrong_key:
        with pytest.raises(RuntimeError, match="couldn't be decrypted"):
            s_wrong_key.get(entry_id)


def test_a_db_created_before_the_optional_columns_existed_still_opens_and_defaults_correctly():
    # Simulates an existing DB from before video_path/sharing flags
    # existed -- create the table with the OLD schema by hand, then
    # confirm EntryStore's _ensure_columns() migration guard handles it.
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS entries")
    conn.execute(
        "CREATE TABLE entries (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "transcript TEXT NOT NULL, audio_path TEXT)"
    )
    conn.execute(
        "INSERT INTO entries (id, created_at, transcript, audio_path) VALUES (%s, %s, %s, %s)",
        ("old-id", datetime.now(timezone.utc).isoformat(), "an entry from before this feature existed", None),
    )
    conn.close()

    with EntryStore(TEST_DATABASE_URL) as store:
        fetched = store.get("old-id")
        assert fetched.transcript == "an entry from before this feature existed"
        assert fetched.video_path is None
        assert fetched.shareable_with_partner is False
        assert fetched.shareable_with_provider is False
