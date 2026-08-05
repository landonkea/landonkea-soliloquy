from soliloquy.cli import add_entry, list_entries
from soliloquy.storage import EntryStore


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
