import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from soliloquy.entry import Entry
from soliloquy.storage import EntryStore


@pytest.fixture
def store(tmp_path):
    s = EntryStore(str(tmp_path / "test.db"))
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


def test_a_fresh_db_file_is_created_and_usable(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "fresh.db"
    store = EntryStore(str(db_path))
    try:
        store.add(Entry(transcript="works on a fresh db"))
        assert len(store.all()) == 1
    finally:
        store.close()


def test_entrystore_works_as_a_context_manager(tmp_path):
    db_path = str(tmp_path / "test.db")
    with EntryStore(db_path) as store:
        store.add(Entry(transcript="via context manager"))
        assert len(store.all()) == 1

    # The connection should be closed after the `with` block -- confirm
    # by checking further use raises, rather than just trusting close()
    # was called.
    with pytest.raises(sqlite3.ProgrammingError):
        store.all()


def test_entrystore_closes_even_if_an_exception_is_raised_inside_the_with_block(tmp_path):
    db_path = str(tmp_path / "test.db")
    with pytest.raises(ValueError):
        with EntryStore(db_path) as store:
            store.add(Entry(transcript="before the exception"))
            raise ValueError("simulated failure")

    with pytest.raises(sqlite3.ProgrammingError):
        store.all()
