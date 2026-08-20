import json
import os

import pytest

from soliloquy.analyzer import AnalysisResult, NoEntriesError
from soliloquy.entry import Entry
from soliloquy.mqtt_bridge import _process_write, handle_message, handle_query
from soliloquy.storage import EntryStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


@pytest.fixture(autouse=True)
def _clean_db():
    with EntryStore(TEST_DATABASE_URL) as store:
        store._conn.execute("TRUNCATE TABLE entries")
    yield


def test_handle_message_saves_a_real_entry_from_a_valid_payload():
    payload = json.dumps({"text": "A journal entry that came in over MQTT."}).encode()

    entry = handle_message(payload, database_url=TEST_DATABASE_URL)

    assert entry is not None
    assert entry.transcript == "A journal entry that came in over MQTT."
    with EntryStore(TEST_DATABASE_URL) as store:
        assert store.get(entry.id) is not None


def test_handle_message_returns_none_for_non_json_payload():
    assert handle_message(b"not json at all", database_url=TEST_DATABASE_URL) is None
    with EntryStore(TEST_DATABASE_URL) as store:
        assert store.all() == []


def test_handle_message_returns_none_when_text_key_is_missing():
    payload = json.dumps({"not_text": "whoops"}).encode()
    assert handle_message(payload, database_url=TEST_DATABASE_URL) is None


def test_handle_message_returns_none_for_empty_or_whitespace_text():
    payload = json.dumps({"text": "   "}).encode()
    assert handle_message(payload, database_url=TEST_DATABASE_URL) is None


def test_handle_message_returns_none_when_payload_is_a_json_list_not_object():
    payload = json.dumps(["not", "a", "dict"]).encode()
    assert handle_message(payload, database_url=TEST_DATABASE_URL) is None


def test_handle_message_saves_the_speaker_when_provided():
    payload = json.dumps({"text": "from a household member", "speaker": "Landon"}).encode()

    entry = handle_message(payload, database_url=TEST_DATABASE_URL)

    assert entry.speaker == "Landon"


# ── _process_write (the ack-aware path) ──────────────────────────────

def test_process_write_reports_success_with_the_saved_entry():
    payload = json.dumps({"text": "a new entry"}).encode()

    outcome = _process_write(payload, database_url=TEST_DATABASE_URL)

    assert outcome.entry is not None
    assert outcome.appended is False
    assert outcome.error is None


def test_process_write_reports_a_reason_for_non_json_payload():
    outcome = _process_write(b"not json", database_url=TEST_DATABASE_URL)

    assert outcome.entry is None
    assert "JSON" in outcome.error


def test_process_write_reports_a_reason_for_missing_text():
    payload = json.dumps({"speaker": "Landon"}).encode()

    outcome = _process_write(payload, database_url=TEST_DATABASE_URL)

    assert outcome.entry is None
    assert "text" in outcome.error


def test_process_write_type_append_appends_to_a_recent_entry():
    first = _process_write(json.dumps({"text": "first thought"}).encode(), database_url=TEST_DATABASE_URL)

    second = _process_write(
        json.dumps({"text": "also this", "type": "append"}).encode(), database_url=TEST_DATABASE_URL
    )

    assert second.appended is True
    assert second.entry.id == first.entry.id
    assert second.entry.transcript == "first thought also this"


def test_process_write_type_new_never_appends_even_with_a_recent_entry():
    _process_write(json.dumps({"text": "first thought"}).encode(), database_url=TEST_DATABASE_URL)

    second = _process_write(
        json.dumps({"text": "a separate thought", "type": "new"}).encode(), database_url=TEST_DATABASE_URL
    )

    assert second.appended is False
    with EntryStore(TEST_DATABASE_URL) as store:
        assert len(store.all()) == 2


# ── handle_query ─────────────────────────────────────────────────────

class _FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def analyze(self, entries, instruction=""):
        if self.error:
            raise self.error
        return self.result


def test_handle_query_returns_a_summary_dict():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry to query about"))

    fake_result = AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=["t"])
    response = handle_query(
        json.dumps({"days": 7}).encode(), database_url=TEST_DATABASE_URL, analyzer=_FakeAnalyzer(result=fake_result),
    )

    assert response == {"days": 7, "entry_count": 1, "summary": "s", "mood_notes": "m", "key_topics": ["t"]}


def test_handle_query_defaults_days_when_missing_or_invalid():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry"))

    fake_result = AnalysisResult(entry_count=1, total_word_count=5, summary="s", mood_notes="m", key_topics=[])
    response = handle_query(
        json.dumps({"days": -5}).encode(), database_url=TEST_DATABASE_URL, analyzer=_FakeAnalyzer(result=fake_result),
    )

    assert response["days"] == 7  # DEFAULT_QUERY_DAYS


def test_handle_query_returns_none_when_there_are_no_entries():
    response = handle_query(
        json.dumps({"days": 7}).encode(), database_url=TEST_DATABASE_URL,
        analyzer=_FakeAnalyzer(error=NoEntriesError("empty")),
    )
    assert response is None


def test_handle_query_returns_none_when_the_analyzer_fails():
    with EntryStore(TEST_DATABASE_URL) as store:
        store.add(Entry(transcript="an entry"))

    response = handle_query(
        json.dumps({"days": 7}).encode(), database_url=TEST_DATABASE_URL,
        analyzer=_FakeAnalyzer(error=RuntimeError("provider down")),
    )
    assert response is None
