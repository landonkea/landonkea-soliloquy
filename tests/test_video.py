import subprocess
import wave
from unittest.mock import patch

import pytest

from soliloquy.video import FfmpegNotFoundError, extract_audio


def test_extract_audio_raises_a_clear_error_if_ffmpeg_is_not_installed(tmp_path):
    with patch("soliloquy.video.shutil.which", return_value=None):
        with pytest.raises(FfmpegNotFoundError, match="ffmpeg not found"):
            extract_audio("in.mp4", str(tmp_path / "out.wav"))


def test_extract_audio_raises_runtime_error_if_ffmpeg_exits_nonzero(tmp_path):
    fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such file")
    with patch("soliloquy.video.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("soliloquy.video.subprocess.run", return_value=fake_result):
            with pytest.raises(RuntimeError, match="ffmpeg failed to extract audio"):
                extract_audio("in.mp4", str(tmp_path / "out.wav"))


def _synthesize_test_video(path: str, duration: float = 1.0) -> None:
    # ffmpeg's own lavfi test-source generator -- a real synthesized
    # video+tone file, not a hand-provided binary fixture.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", path,
        ],
        check=True,
        capture_output=True,
    )


def test_extract_audio_produces_a_real_wav_with_real_audio_data(tmp_path):
    video_path = str(tmp_path / "synthetic.mp4")
    audio_path = str(tmp_path / "extracted.wav")
    _synthesize_test_video(video_path)

    extract_audio(video_path, audio_path)

    with wave.open(audio_path) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() > 0
        frames = w.readframes(w.getnframes())
        # A real 440Hz tone, not silence -- confirms this is genuine
        # extracted audio, not an empty/placeholder file.
        assert any(b != 0 for b in frames)
