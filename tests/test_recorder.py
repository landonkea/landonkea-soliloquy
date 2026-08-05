import sys
import threading
import wave
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from soliloquy.recorder import CHANNELS, CHUNK, SAMPLE_RATE, SAMPLE_WIDTH, record_to_file, write_wav


def test_write_wav_produces_a_real_readable_wav_file(tmp_path):
    path = str(tmp_path / "entry.wav")
    pcm_data = b"\x01\x02" * 1000  # arbitrary synthetic 16-bit samples

    write_wav(path, pcm_data)

    with wave.open(path, "rb") as wav_file:
        assert wav_file.getnchannels() == CHANNELS
        assert wav_file.getsampwidth() == SAMPLE_WIDTH
        assert wav_file.getframerate() == SAMPLE_RATE
        assert wav_file.readframes(wav_file.getnframes()) == pcm_data


def test_write_wav_creates_missing_parent_directories(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "entry.wav")

    write_wav(path, b"\x00\x00")

    with wave.open(path, "rb") as wav_file:
        assert wav_file.getnframes() > 0


def _install_fake_pyaudio(chunks: list[bytes]):
    """Builds a fake `pyaudio` module whose PyAudio().open() stream
    yields `chunks` in order, one per .read() call, so record_to_file
    can be exercised with no real microphone or hardware involved."""
    fake_module = ModuleType("pyaudio")
    fake_module.paInt16 = 8  # arbitrary stand-in constant

    fake_stream = MagicMock()
    fake_stream.read.side_effect = chunks

    fake_pyaudio_instance = MagicMock()
    fake_pyaudio_instance.open.return_value = fake_stream

    fake_module.PyAudio = MagicMock(return_value=fake_pyaudio_instance)
    return fake_module, fake_stream, fake_pyaudio_instance


def test_record_to_file_stops_when_stop_event_is_set(tmp_path):
    one_chunk = b"\x01" * CHUNK * SAMPLE_WIDTH
    fake_module, fake_stream, _ = _install_fake_pyaudio([])

    stop_event = threading.Event()
    call_count = 0

    def read_and_stop_after_three(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            stop_event.set()
        return one_chunk

    fake_stream.read.side_effect = read_and_stop_after_three

    with patch.dict(sys.modules, {"pyaudio": fake_module}):
        output_path = str(tmp_path / "entry.wav")
        duration = record_to_file(output_path, stop_event, max_duration_seconds=1200)

    assert call_count == 3
    assert duration > 0

    with wave.open(output_path, "rb") as wav_file:
        assert wav_file.getnframes() > 0


def test_record_to_file_raises_clearly_if_stop_fires_before_any_chunk_is_read(tmp_path):
    # Reproduces a real observed failure mode: stop_event already set
    # before the loop reads its first chunk (e.g. the very first mic
    # access on a process, where stream setup itself can be slow) --
    # must raise loudly, not silently write an empty .wav and claim
    # a successful 0.0s recording.
    fake_module, fake_stream, _ = _install_fake_pyaudio([])
    stop_event = threading.Event()
    stop_event.set()  # already stopped before recording ever starts

    output_path = str(tmp_path / "entry.wav")
    with patch.dict(sys.modules, {"pyaudio": fake_module}):
        with pytest.raises(RuntimeError, match="No audio was captured"):
            record_to_file(output_path, stop_event)

    fake_stream.read.assert_not_called()
    assert not Path(output_path).exists()


def test_record_to_file_always_releases_the_stream_even_if_reading_raises(tmp_path):
    fake_module, fake_stream, fake_pyaudio_instance = _install_fake_pyaudio([])
    fake_stream.read.side_effect = RuntimeError("simulated hardware failure")

    stop_event = threading.Event()

    with patch.dict(sys.modules, {"pyaudio": fake_module}):
        try:
            record_to_file(str(tmp_path / "entry.wav"), stop_event, max_duration_seconds=1200)
        except RuntimeError:
            pass

    fake_stream.stop_stream.assert_called_once()
    fake_stream.close.assert_called_once()
    fake_pyaudio_instance.terminate.assert_called_once()


def test_record_to_file_respects_max_duration_even_if_stop_event_never_fires(tmp_path):
    # 1 chunk = CHUNK/SAMPLE_RATE seconds; with max_duration_seconds tiny,
    # only a handful of chunks should ever be read.
    one_chunk = b"\x01" * CHUNK * SAMPLE_WIDTH
    fake_module, fake_stream, _ = _install_fake_pyaudio([])
    fake_stream.read.side_effect = lambda *args, **kwargs: one_chunk

    stop_event = threading.Event()  # never set

    with patch.dict(sys.modules, {"pyaudio": fake_module}):
        duration = record_to_file(str(tmp_path / "entry.wav"), stop_event, max_duration_seconds=1)

    # ~1 second of audio at SAMPLE_RATE/CHUNK chunks/sec -> a small, bounded number of reads.
    max_expected_chunks = int(SAMPLE_RATE / CHUNK * 1) + 1
    assert fake_stream.read.call_count <= max_expected_chunks
    assert duration <= 1.5
