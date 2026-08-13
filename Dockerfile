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
# from source. curl: used by the HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src

# ───────────────────────── dev ─────────────────────────
# Hot reload, plus dev+test tooling. Meant to be run with ./src bind-
# mounted over the copy above (see docker-compose.app.yml) so code
# changes on the host show up without a rebuild.
FROM base AS dev
RUN pip install --no-cache-dir -e ".[dev,web,transcribe]"
EXPOSE 8000
CMD ["uvicorn", "soliloquy.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ─────────────────────── release ───────────────────────
# Shared by staging and prod: no dev/test tooling, runs as a non-root
# user, no reload (code is baked into the image, not bind-mounted).
FROM base AS release
RUN pip install --no-cache-dir -e ".[web,transcribe]"
RUN useradd --create-home --uid 1000 soliloquy \
    && chown -R soliloquy:soliloquy /app
USER soliloquy
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/entries || exit 1
CMD ["uvicorn", "soliloquy.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# ─────────────────────── staging ───────────────────────
# Identical to release today. Kept as its own stage (rather than
# tagging the release image directly as "staging") so a staging-only
# need later, seed data, a debug flag, extra logging, has somewhere to
# go without touching what actually ships to prod.
FROM release AS staging

# ───────────────────────── prod ─────────────────────────
FROM release AS prod
