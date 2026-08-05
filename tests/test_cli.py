from soliloquy.cli import add_entry, add_entry_from_audio, list_entries
from soliloquy.storage import EntryStore


class FakeTranscriber:
    def __init__(self, transcript: str = "a fake transcript"):
        self.transcript = transcript
        self.last_audio_path = None

    def transcribe(self, audio_path: str) -> str:
        self.last_audio_path = audio_path
        return self.transcript


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
