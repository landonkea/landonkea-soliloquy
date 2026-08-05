# ───────────────────────────────────────────────────────────────────
# transcriber.py — audio file → text
# ───────────────────────────────────────────────────────────────────
# A real provider abstraction, not a single hardcoded implementation
# — same pattern landonkea-makeItSoNumberOne uses for its AI
# providers (Claude/OpenAI/Ollama), so a cloud Whisper API or a
# different local engine can be swapped in later without any caller
# of transcribe() changing.
#
# WhisperTranscriber (faster-whisper, local, CPU-friendly) is the
# default — deliberately, not just for cost: given this app may hold
# personal, possibly therapy-adjacent disclosure, audio never leaving
# the device is a real privacy requirement here, not an optimization.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import Protocol


class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> str:
        """Return the transcript text for the audio file at `audio_path`.
        Implementations should raise on failure, never return an empty
        string to mean "something went wrong" -- that's indistinguishable
        from genuine silence."""
        ...


class WhisperTranscriber:
    """Local transcription via faster-whisper (CTranslate2-backed
    Whisper). No network access, no API key, nothing leaves the
    device -- see this module's docstring for why that matters here.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None  # lazy: don't load model weights until first real use

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper not installed. Install with: pip install -e \".[transcribe]\""
                ) from exc
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str) -> str:
        model = self._get_model()
        segments, _info = model.transcribe(audio_path)
        return " ".join(segment.text.strip() for segment in segments).strip()
