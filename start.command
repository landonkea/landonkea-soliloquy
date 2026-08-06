#!/bin/bash
# ───────────────────────────────────────────────────────────────────
# start.command — double-click this to run Soliloquy
# ───────────────────────────────────────────────────────────────────
# Does everything by hand a first-time/every-time setup needs:
# Docker services, Python virtual environment, dependencies, ffmpeg --
# then starts the web app. Safe to double-click again any time; each
# step is a no-op if already done.
# ───────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

echo "Starting Soliloquy..."
echo ""

# 1. Docker must be running (Docker Desktop) for Postgres/MinIO/Mosquitto.
if ! docker info > /dev/null 2>&1; then
    echo "Docker isn't running. Please open Docker Desktop, wait for it to"
    echo "finish starting, then double-click this file again."
    read -p "Press Enter to close this window."
    exit 1
fi

# 2. ffmpeg (needed to process uploaded video) -- install it via
# Homebrew automatically if it's missing and Homebrew is available.
if ! command -v ffmpeg > /dev/null 2>&1; then
    if command -v brew > /dev/null 2>&1; then
        echo "Installing ffmpeg (needed for video entries, one-time)..."
        brew install ffmpeg
    else
        echo "ffmpeg isn't installed, and Homebrew isn't available to install it"
        echo "automatically. Video entries won't work until you install it yourself"
        echo "(see https://ffmpeg.org) -- everything else will still work fine."
    fi
fi

# 3. Postgres + MinIO + Mosquitto.
echo "Starting Postgres, MinIO, and the MQTT broker..."
docker compose up -d

# 4. Python virtual environment + dependencies.
if [ ! -d ".venv" ]; then
    echo "First-time setup -- creating a Python environment (this can take a minute)..."
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -e ".[dev,web,transcribe]"

# 5. Wait for Postgres to actually be ready to accept connections.
echo "Waiting for the database to be ready..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U soliloquy > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo ""
echo "Soliloquy is running:"
echo "  On this Mac:                 http://localhost:8000"
if [ -n "$LAN_IP" ]; then
    echo "  On your phone (same WiFi):   http://$LAN_IP:8000"
fi
echo ""
echo "Leave this window open while you're using it. Press Ctrl+C here (or just"
echo "close this window) to stop."
echo ""

python -m soliloquy.web
