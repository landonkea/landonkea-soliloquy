from datetime import datetime, timezone

from soliloquy.entry import Entry


def test_entry_generates_a_unique_id_by_default():
    a = Entry(transcript="hello")
    b = Entry(transcript="hello")
    assert a.id != b.id


def test_word_count_counts_whitespace_separated_words():
    entry = Entry(transcript="This has exactly five words.")
    assert entry.word_count == 5


def test_word_count_is_zero_for_empty_transcript():
    entry = Entry(transcript="")
    assert entry.word_count == 0


def test_created_at_defaults_to_now_utc():
    before = datetime.now(timezone.utc)
    entry = Entry(transcript="hi")
    after = datetime.now(timezone.utc)
    assert before <= entry.created_at <= after


def test_audio_path_defaults_to_none_for_text_only_entries():
    entry = Entry(transcript="typed, not spoken")
    assert entry.audio_path is None


def test_video_path_defaults_to_none():
    entry = Entry(transcript="typed, not filmed")
    assert entry.video_path is None
