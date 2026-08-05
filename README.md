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
- A minimal CLI (`soliloquy add "some text"`, `soliloquy list`) for typed entries
- **Real microphone recording** (`soliloquy record`) — reuses the exact `pyaudio` pattern proven
  in `landonkea-makeItSoNumberOne`'s desktop assistant. Manual stop (press Enter), not
  silence-detection auto-stop — a deliberate choice for a reflective entry, where pausing to
  think for a few seconds shouldn't get you cut off (contrast with `makeItSoNumberOne`'s
  1.5-second auto-stop, which is correct for a short voice command but wrong here). Verified
  against a real microphone, not just mocked. `record` doesn't create an `Entry` yet — it saves
  the `.wav` file and prints its path; transcription (next) is what turns that into a real entry.

**Next, in order:**
1. **Transcription** — audio → text. Planned default: local Whisper (`faster-whisper`) for the
   same self-hosted-first reason the rest of this project's tooling prefers local/self-hosted
   options (Mosquitto over a cloud broker, Ollama alongside cloud AI providers) — with a real
   provider-abstraction interface so a cloud Whisper API can be swapped in per-user, not
   hardcoded to one choice. Given this may hold sensitive personal disclosure, local-first
   processing here is a real privacy requirement, not just a cost preference.
2. **Analysis** — the per-day/week/month rollups this whole architecture is actually for.
3. **A readable report export** for a date range (transcripts + rolled-up analysis) — meant to
   be handed to a therapist/psychiatrist or read by the user to see real progress over weeks or
   months, not just raw data. This is a core product goal here, not a nice-to-have.
4. **Storage migration** — SQLite works for local proof-of-concept; the plan once this works
   end-to-end is Postgres (transcripts/metadata/analysis — free managed tier, e.g. Supabase or
   Neon) + object storage for audio specifically (e.g. Cloudflare R2 — no egress fees, matters
   if audio is ever streamed/shared back out). Raw audio should never live inside the database
   itself, free tier or not. `EntryStore`'s interface is already designed so this is a backend
   swap, not a rewrite.

**A future integration point, not built yet:** `landonkea-thinkLessScheduleMore`'s automation
engine (`AutomationAction`/`AutomationRegistry`) makes "record a journal entry" a natural
Tasker-style action — meaning `landonkea-makeItSoNumberOne`'s wake word could eventually
trigger a Soliloquy entry directly ("computer, journal entry: ..."). Worth knowing where this
plugs into the rest of the ecosystem, not a dependency for anything built so far.

## Quick start

```bash
pip install -e ".[dev]"                       # text-only (add/list), no audio deps
python -m soliloquy.cli add "First entry, typed for now."
python -m soliloquy.cli list

pip install -e ".[dev,audio]"                 # adds pyaudio, needed for `record`
# macOS also needs the system library: brew install portaudio
python -m soliloquy.cli record                # press Enter to stop
```

## Running tests

```bash
pytest -v
```
