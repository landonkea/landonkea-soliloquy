# ───────────────────────────────────────────────────────────────────
# video.py — pulls the audio track out of a video file
# ───────────────────────────────────────────────────────────────────
# Video entries reuse the EXACT SAME transcription/analysis pipeline
# as audio entries -- extract_audio() is the only new step, and it
# hands off a plain .wav to the already-tested Transcriber protocol.
# Wraps a real external tool (ffmpeg) the same way recorder.py wraps
# pyaudio, rather than pulling in a heavy Python video library for
# one operation. Requires the `ffmpeg` binary on PATH -- see README
# ("brew install ffmpeg" on macOS).
#
# video_path is stored independently of audio_path (see entry.py) so
# a future face/expression analyzer can consume the original video
# without touching this extraction step or the transcript pipeline
# at all.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from .ffmpeg_utils import FfmpegNotFoundError, run_ffmpeg

__all__ = ["FfmpegNotFoundError", "extract_audio"]


def extract_audio(video_path: str, output_audio_path: str) -> None:
    """Extract the audio track from `video_path` into a 16-bit PCM WAV
    at `output_audio_path`, ready for the existing Transcriber. Raises
    FfmpegNotFoundError if ffmpeg isn't installed, or RuntimeError if
    ffmpeg runs but fails (e.g. the video has no audio track)."""
    run_ffmpeg([
        "-y", "-i", video_path,
        "-vn",                       # no video in the output
        "-acodec", "pcm_s16le",      # 16-bit PCM, matches what the transcriber expects
        "-ar", "16000", "-ac", "1",  # 16kHz mono -- what Whisper wants anyway
        output_audio_path,
    ])
