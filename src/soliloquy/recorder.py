# ───────────────────────────────────────────────────────────────────
# recorder.py — captures real microphone audio to a WAV file
# ───────────────────────────────────────────────────────────────────
# Split into two pieces on purpose:
#   - write_wav(): pure I/O, no hardware involved, fully testable with
#     synthetic bytes.
#   - record_to_file(): the actual pyaudio-touching capture loop.
#
# Manual stop (a threading.Event you set from wherever the caller
# wants — a keypress listener, a UI button, a timer), not silence-
# detection auto-stop. That's a deliberate choice for a REFLECTIVE
# journal entry, not a short voice command: someone pausing to think
# for a few seconds mid-entry shouldn't get cut off. (Contrast this
# with landonkea-makeItSoNumberOne's audio.py, which auto-stops after
# 1.5s of silence — correct for a brief command, wrong for this.)
# max_duration_seconds is still a hard safety ceiling so a forgotten
# recording doesn't run forever.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import threading
import wave
from pathlib import Path

SAMPLE_RATE = 22050
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio, 2 bytes/sample
CHUNK = 1024


def write_wav(path: str, pcm_data: bytes, sample_rate: int = SAMPLE_RATE,
              channels: int = CHANNELS, sample_width: int = SAMPLE_WIDTH) -> None:
    """Write raw PCM bytes to a real .wav file. No hardware involved —
    this is the exact format record_to_file's captured bytes are in,
    kept as its own function so it's testable with synthetic bytes
    instead of a real microphone."""
    Path(path).parent.mkdir(parents=True, exist_ok=True) if Path(path).parent != Path(".") else None
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)


def record_to_file(
    output_path: str,
    stop_event: threading.Event,
    max_duration_seconds: int = 1200,  # 20 min hard ceiling
) -> float:
    """
    Record from the real microphone until `stop_event` is set (or
    `max_duration_seconds` elapses, whichever comes first), then write
    the result to `output_path` as a .wav file.

    Returns the recorded duration in seconds.
    """
    try:
        import pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "PyAudio not installed. Install with: pip install pyaudio "
            "(on macOS you may also need: brew install portaudio)"
        ) from exc

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    frames: list[bytes] = []
    max_chunks = int(SAMPLE_RATE / CHUNK * max_duration_seconds)

    try:
        for _ in range(max_chunks):
            if stop_event.is_set():
                break
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
    finally:
        # Always released, even if the loop raises — an unreleased
        # stream blocks any later recording attempt until the whole
        # process restarts.
        stream.stop_stream()
        stream.close()
        p.terminate()

    if not frames:
        # A real, observed failure mode: the very first time a process
        # opens a mic stream, macOS can take a noticeable moment for
        # permission/device negotiation. If stop_event is set before the
        # stream is actually ready (e.g. a caller stopping almost
        # instantly), the loop can legitimately capture zero chunks.
        # Silently writing an empty .wav and reporting "success, 0.0s"
        # would hide that from the user -- raise instead, so a genuinely
        # empty recording is a loud, actionable failure, not a phantom
        # file cluttering the recordings directory.
        raise RuntimeError(
            "No audio was captured. If this is the very first recording "
            "this process has made, the microphone may not have been "
            "ready yet -- try recording again."
        )

    pcm_data = b"".join(frames)
    write_wav(output_path, pcm_data)

    duration_seconds = len(frames) * CHUNK / SAMPLE_RATE
    return duration_seconds
