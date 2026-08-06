# ───────────────────────────────────────────────────────────────────
# ffmpeg_utils.py — shared "run ffmpeg, fail clearly" helper
# ───────────────────────────────────────────────────────────────────
# Used by both video.py (audio extraction) and noise_reduction.py
# (voice isolation + normalization) -- pulled out so the
# ffmpeg-not-on-PATH / ffmpeg-failed error handling only lives in one
# place instead of being duplicated across both callers.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import shutil
import subprocess


class FfmpegNotFoundError(RuntimeError):
    """Raised when the `ffmpeg` binary isn't on PATH -- a clearer error
    than the FileNotFoundError subprocess would otherwise raise."""


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with `args` (excluding the `ffmpeg` binary name
    itself). Raises FfmpegNotFoundError if ffmpeg isn't installed, or
    RuntimeError if ffmpeg runs but exits non-zero."""
    if shutil.which("ffmpeg") is None:
        raise FfmpegNotFoundError(
            "ffmpeg not found on PATH -- install it (e.g. `brew install ffmpeg` on macOS)."
        )
    result = subprocess.run(["ffmpeg", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
