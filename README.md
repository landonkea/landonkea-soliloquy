# Soliloquy

A voice-first journal: record a thought out loud, get it transcribed automatically, and see
real analysis of what you've been thinking about — per day, per week, per month, over years.

## Why voice-first

Typing a journal entry has friction that talking doesn't. The goal here is "say what's on your
mind for 30 seconds," not "sit down and write." Transcription happens automatically after the
fact — the recording is the whole interaction.

## Architecture

```
   record (mic)  →  transcribe (speech-to-text)  →  Entry  →  storage (SQLite)  →  analysis
```

- **`Entry`** (`src/soliloquy/entry.py`) — the one thing every other piece produces or consumes:
  a transcript, a timestamp, an optional path to the original audio. Nothing else in this app
  needs to know how an `Entry` was created.
- **`EntryStore`** (`src/soliloquy/storage.py`) — SQLite-backed storage, with a
  `range_between(start, end)` query built in from day one, because per-day/week/month analysis
  is just different `(start, end)` windows over the same query.
- **Recording + transcription** — not built yet, see "What's built vs. what's next" below.
- **Analysis** — not built yet either. The plan: periodic Claude calls over a date range's
  entries (mood/sentiment trends, recurring topics, a real weekly/monthly summary) — the same
  AI-provider pattern already used elsewhere (see `landonkea-makeItSoNumberOne`), not a bespoke
  ML model.

## What's built vs. what's next

**Built and tested today:**
- `Entry` data model
- `EntryStore` — add / get / list / delete / date-range query, all against a real SQLite file
- A minimal CLI (`soliloquy add "some text"`, `soliloquy list`) — **text-only for now**, so the
  storage layer is exercised by a real workflow immediately, without needing a microphone or a
  transcription model configured first.

**Next, in order:**
1. **Recording** — capture real audio from a microphone (reusing the exact `pyaudio` pattern
   already proven in `landonkea-makeItSoNumberOne`'s desktop assistant, not reinvented).
2. **Transcription** — audio → text. Planned default: local Whisper (`faster-whisper`) for the
   same self-hosted-first reason the rest of this project's tooling prefers local/self-hosted
   options (Mosquitto over a cloud broker, Ollama alongside cloud AI providers) — with a real
   provider-abstraction interface so a cloud Whisper API can be swapped in per-user, not
   hardcoded to one choice.
3. **Analysis** — the per-day/week/month rollups this whole architecture is actually for.
4. **A real recording UI** — desktop first (matching how every other app in this project's
   ecosystem started narrow and grew), not a specific platform commitment yet.

**A future integration point, not built yet:** `landonkea-thinkLessScheduleMore`'s automation
engine (`AutomationAction`/`AutomationRegistry`) makes "record a journal entry" a natural
Tasker-style action — meaning `landonkea-makeItSoNumberOne`'s wake word could eventually
trigger a Soliloquy entry directly ("computer, journal entry: ..."). Worth knowing where this
plugs into the rest of the ecosystem, not a dependency for anything built so far.

## Quick start

```bash
pip install -e ".[dev]"
python -m soliloquy.cli add "First entry, typed for now."
python -m soliloquy.cli list
pytest
```

## Running tests

```bash
pytest -v
```
