import uuid

import pytest

from soliloquy.object_storage import ObjectStore


@pytest.fixture
def store():
    return ObjectStore()


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("hello object storage")
    return path


def test_upload_file_returns_the_key_it_was_given(store, sample_file):
    key = f"test/{uuid.uuid4()}.txt"

    result = store.upload_file(str(sample_file), key)

    assert result == key
    store.delete(key)


def test_download_to_temp_round_trips_the_original_content(store, sample_file):
    key = f"test/{uuid.uuid4()}.txt"
    store.upload_file(str(sample_file), key)

    downloaded = store.download_to_temp(key)

    assert downloaded.read_text() == "hello object storage"
    store.delete(key)


def test_delete_removes_the_object(store, sample_file):
    key = f"test/{uuid.uuid4()}.txt"
    store.upload_file(str(sample_file), key)

    store.delete(key)

    with pytest.raises(Exception):
        store.download_to_temp(key)
