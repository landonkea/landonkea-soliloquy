# ───────────────────────────────────────────────────────────────────
# noise_reduction.py, isolate the speaker's voice and normalize it
# ───────────────────────────────────────────────────────────────────
# Two real, separate steps chained together, run on every audio/video
# upload before transcription and storage:
#
#   1. DeepFilterNet3 -- a neural network trained specifically to tell
#      speech apart from background sound. Unlike a plain noise gate
#      or steady-hiss filter, it also strips *irregular* noise (wind
#      gusts, a dog barking, sheets moving, cars passing), which is
#      what was actually asked for here.
#   2. ffmpeg's loudnorm + alimiter -- measures overall loudness (not
#      just local peaks) and pushes the signal up to just under
#      clipping, so recordings never come out too quiet to hear or so
#      loud they distort, without the speaker having to raise their
#      voice.
#
# DeepFilterNet (last released in 2023) imports a torchaudio symbol
# that newer torchaudio releases removed, and does its own file I/O
# through a torchaudio backend that no longer exists at all -- see
# _stub_torchaudio_backend() below. We never call DeepFilterNet's own
# load_audio/save_audio; ffmpeg (already a dependency, see video.py)
# and soundfile do all the actual file I/O here instead.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
import types

from .ffmpeg_utils import run_ffmpeg

DF_SAMPLE_RATE = 48000
OUTPUT_SAMPLE_RATE = 16000  # matches what WhisperTranscriber expects


def _stub_torchaudio_backend() -> None:
    if "torchaudio.backend.common" in sys.modules:
        return
    backend_mod = types.ModuleType("torchaudio.backend")
    common_mod = types.ModuleType("torchaudio.backend.common")

    class AudioMetaData:  # only used as a type hint inside df/io.py, which we never call
        pass

    common_mod.AudioMetaData = AudioMetaData
    sys.modules.setdefault("torchaudio.backend", backend_mod)
    sys.modules["torchaudio.backend.common"] = common_mod


_model = None
_df_state = None


def _get_model():
    global _model, _df_state
    if _model is None:
        _stub_torchaudio_backend()
        from df.enhance import init_df

        _model, _df_state, _ = init_df()
    return _model, _df_state


def preload() -> None:
    """Load torch and the DeepFilterNet model now, on whatever thread
    calls this. FastAPI/Starlette run sync route handlers in a worker
    thread pool, and torch's C extension isn't safe to initialize for
    the first time there (it reliably segfaults on import if that's
    the first touch of torch in the process) -- so app.py's startup
    calls this on the main thread before any request can reach a
    worker thread first. See app.py's _lifespan."""
    _get_model()


def isolate_voice_and_normalize(input_path: str, output_path: str) -> None:
    """Run the voice-isolation + normalization pipeline on `input_path`,
    writing a clean 16kHz mono WAV to `output_path`."""
    import soundfile as sf
    import torch

    model, df_state = _get_model()  # stubs torchaudio.backend before df.enhance is imported
    from df.enhance import enhance

    raw_path = f"{input_path}.df_raw.wav"
    enhanced_path = f"{input_path}.df_enhanced.wav"
    try:
        # Normalize the incoming file (whatever container/codec it is)
        # to mono 48kHz -- DeepFilterNet's native sample rate.
        run_ffmpeg(["-y", "-i", input_path, "-ac", "1", "-ar", str(DF_SAMPLE_RATE), raw_path])

        audio, _sr = sf.read(raw_path, dtype="float32", always_2d=True)
        audio_t = torch.from_numpy(audio.T)  # [channels, samples]
        enhanced = enhance(model, df_state, audio_t)
        sf.write(enhanced_path, enhanced.numpy().T, DF_SAMPLE_RATE)

        # loudnorm measures overall integrated loudness (not just
        # local peaks -- speechnorm was tried first, but a single loud
        # transient like a breath or a plosive made it think the
        # signal was already "loud enough" and barely boost the actual
        # speech). alimiter is a hard safety net so nothing ever
        # actually clips, on top of loudnorm's own TP target.
        run_ffmpeg([
            "-y", "-i", enhanced_path,
            "-af", "loudnorm=I=-11:TP=-0.3:LRA=7,alimiter=limit=0.97",
            "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", "1",
            output_path,
        ])
    finally:
        for path in (raw_path, enhanced_path):
            if os.path.exists(path):
                os.remove(path)
