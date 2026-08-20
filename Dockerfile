# syntax=docker/dockerfile:1

# ───────────────────────────────────────────────────────────────────
# Soliloquy application image. Builds the app itself (FastAPI +
# transcription + analysis); Postgres/MinIO/Mosquitto stay separate,
# see docker-compose.yml. One shared base, then a stage per
# environment, since dev/staging/prod need different things installed
# (dev wants hot reload and test tooling; staging/prod don't).
#
# Build a specific stage with:
#   docker build --target dev     -t soliloquy:dev .
#   docker build --target staging -t soliloquy:staging .
#   docker build --target prod    -t soliloquy:prod .
#
# The GitHub Actions workflows in .github/workflows/deploy-*.yml build
# these same targets in CI.
# ───────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# ffmpeg: extracting audio from uploaded video (video.py) and the
# loudness-normalization pass (noise_reduction.py). build-essential:
# deepfilterlib (the transcribe extra's native dependency) compiles
# from source. curl: used by the HEALTHCHECK below AND to install Rust
# just below, since it needs it too. git: NOT used by anything of ours
# -- DeepFilterNet's own `df.logger.init_logger()` shells out to `git
# rev-parse` to log its own commit hash on every startup, and raises
# straight through the app's own startup (`noise_reduction.preload()`)
# if it's missing. Found by actually running the built image, not
# something obvious from reading the Dockerfile -- host installs never
# hit this because macOS ships `git` already.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

# deepfilterlib's own native dependency needs a real Rust toolchain to
# build (same requirement the README calls out for a host install,
# see https://rustup.rs) -- without this, `pip install .[transcribe]`
# below fails partway through in a plain image, which is the reason
# the container path wasn't the primary documented one before. Debian
# slim's own `cargo`/`rustc` packages are old enough to fail on some
# crates deepfilterlib pulls in, so this uses upstream rustup instead,
# same as the human-facing install path.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
        sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

# torch/torchaudio, CPU-only build, installed BEFORE the extras below
# on purpose. PyPI's default `torch` wheel for Linux drags in a full
# CUDA stack as dependencies (cublas/cudnn/nccl/triton/etc, multiple
# GB on their own) even though nothing here has, or needs, a GPU --
# noise_reduction.py and transcriber.py both run on CPU. Installing
# the CPU build first means the `pip install .[transcribe]` in each
# stage below finds torch/torchaudio already satisfied and never
# reaches for the GPU wheels at all, instead of downloading gigabytes
# of CUDA libraries just to delete them again.
RUN pip install --no-cache-dir torch torchaudio \
        --index-url https://download.pytorch.org/whl/cpu

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src

# ───────────────────────── dev ─────────────────────────
# Hot reload, plus dev+test tooling. Meant to be run with ./src bind-
# mounted over the copy above (see docker-compose.yml's app service)
# so code changes on the host show up without a rebuild. This is the
# default `docker compose up` target -- see README's Quick start.
FROM base AS dev
RUN pip install --no-cache-dir -e ".[dev,web,transcribe]" \
    # The Rust toolchain (~1GB) is only needed to compile
    # deepfilterlib above; nothing at runtime touches cargo/rustc, so
    # it doesn't need to ship in the image.
    && rm -rf /root/.cargo /root/.rustup
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "soliloquy.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ─────────────────────── release ───────────────────────
# Shared by staging and prod: no dev/test tooling, runs as a non-root
# user, no reload (code is baked into the image, not bind-mounted).
FROM base AS release
RUN pip install --no-cache-dir -e ".[web,transcribe]" \
    && rm -rf /root/.cargo /root/.rustup
RUN useradd --create-home --uid 1000 soliloquy \
    && chown -R soliloquy:soliloquy /app
USER soliloquy
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "soliloquy.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# ─────────────────────── staging ───────────────────────
# Identical to release today. Kept as its own stage (rather than
# tagging the release image directly as "staging") so a staging-only
# need later, seed data, a debug flag, extra logging, has somewhere to
# go without touching what actually ships to prod.
FROM release AS staging

# ───────────────────────── prod ─────────────────────────
FROM release AS prod
