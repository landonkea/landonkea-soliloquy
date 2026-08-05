import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from soliloquy.transcriber import WhisperTranscriber


def test_transcribe_raises_a_clear_error_if_faster_whisper_is_not_installed():
    transcriber = WhisperTranscriber()
    with patch.dict(sys.modules, {"faster_whisper": None}):
        with pytest.raises(RuntimeError, match="faster-whisper not installed"):
            transcriber.transcribe("/some/file.wav")


def _install_fake_faster_whisper(segment_texts: list[str]):
    fake_module = ModuleType("faster_whisper")

    fake_segments = [MagicMock(text=text) for text in segment_texts]
    fake_model_instance = MagicMock()
    fake_model_instance.transcribe.return_value = (fake_segments, MagicMock())

    fake_module.WhisperModel = MagicMock(return_value=fake_model_instance)
    return fake_module, fake_model_instance


def test_transcribe_joins_segment_texts_into_one_transcript():
    fake_module, fake_model_instance = _install_fake_faster_whisper(
        [" Hello there. ", " How are you? "]
    )
    transcriber = WhisperTranscriber()

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        result = transcriber.transcribe("/some/file.wav")

    assert result == "Hello there. How are you?"
    fake_model_instance.transcribe.assert_called_once_with("/some/file.wav")


def test_transcribe_returns_empty_string_for_genuinely_silent_audio():
    fake_module, _ = _install_fake_faster_whisper([])  # no segments = no speech detected
    transcriber = WhisperTranscriber()

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        result = transcriber.transcribe("/some/silent.wav")

    assert result == ""


def test_model_is_constructed_lazily_only_on_first_transcribe_call():
    fake_module, _ = _install_fake_faster_whisper(["hi"])
    transcriber = WhisperTranscriber()

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        fake_module.WhisperModel.assert_not_called()
        transcriber.transcribe("/some/file.wav")
        fake_module.WhisperModel.assert_called_once()

        # A second call reuses the already-loaded model.
        transcriber.transcribe("/another/file.wav")
        fake_module.WhisperModel.assert_called_once()


def test_model_size_and_device_are_passed_through_to_whispermodel():
    fake_module, _ = _install_fake_faster_whisper(["hi"])
    transcriber = WhisperTranscriber(model_size="small", device="cpu", compute_type="int8")

    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        transcriber.transcribe("/some/file.wav")

    fake_module.WhisperModel.assert_called_once_with("small", device="cpu", compute_type="int8")
