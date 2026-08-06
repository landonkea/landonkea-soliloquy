import json
import os

import pytest

from soliloquy.mqtt_bridge import handle_message
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
