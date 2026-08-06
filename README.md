# Soliloquy

A voice-first journal: record a thought out loud, get it transcribed automatically, and see
real analysis of what you've been thinking about — per day, per week, per month, over years.

## Why voice-first

Typing a journal entry has friction that talking doesn't. The goal here is "say what's on your
mind for 30 seconds," not "sit down and write." Transcription happens automatically after the
fact — the recording is the whole interaction.

## Architecture

```
   record (mic/camera)  →  transcribe  →  Entry  →  Postgres + object storage  →  analysis
```

- **`Entry`** (`src/soliloquy/entry.py`) — the one thing every other piece produces or consumes:
  a transcript, a timestamp, optional paths to the original audio/video. Nothing else in this app
  needs to know how an `Entry` was created.
- **`EntryStore`** (`src/soliloquy/storage.py`) — Postgres-backed storage (via `psycopg`), driven
  by a `DATABASE_URL` connection string, with a `range_between(start, end)` query built in from
  day one, because per-day/week/month analysis is just different `(start, end)` windows over the
  same query.
- **`ObjectStore`** (`src/soliloquy/object_storage.py`) — S3-compatible object storage (via
  `boto3`) for audio/video files. Raw media never lives inside Postgres. Points at self-hosted
  MinIO today; pointing it at Cloudflare R2 (or real S3) later is a config change, not a rewrite,
  since both speak the same S3 API boto3 already uses.
- **Recording** (`src/soliloquy/recorder.py`) + **transcription** (`src/soliloquy/transcriber.py`)
  — both built, see "What's built vs. what's next" below.
- **Analysis** (`src/soliloquy/analyzer.py`) — built, see "What's built vs. what's next" below.
- **`scheduler.py` + `analysis_store.py`** — a background job (web app only) runs analysis
  automatically every few hours and saves the result so there's usually something to look at on
  the Analysis page without asking for it on demand.
- **CLI** (`src/soliloquy/cli.py`) and a **web app** (`src/soliloquy/web/`, in progress — see
  `CHECKLIST.md`) both sit on top of the same package above; neither duplicates the other's logic.

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
  against a real microphone, not just mocked. Plain `record` saves the `.wav` file and prints
  its path without transcribing.
- **Real local transcription** (`soliloquy transcribe <file>`, or `soliloquy record
  --transcribe` to chain both steps) — `WhisperTranscriber` (`faster-whisper`, fully local, no
  network access, no API key) is the default, deliberately: given this may hold sensitive
  personal disclosure, audio never leaving the device is a real privacy requirement here, not
  just a cost preference. Built behind a real `Transcriber` protocol so a cloud provider could
  be swapped in later without any caller changing. Verified against real synthesized speech
  (macOS `say` → transcribed by Whisper), not just mocked or silent audio — transcript came back
  an exact match.
- **Analysis** (`soliloquy analyze --days N`) — turns a date range of entries into a real
  summary, mood notes, and key topics, asked explicitly to be honest rather than generically
  positive. Built behind a real `Analyzer` protocol with four implementations
  (`src/soliloquy/analyzer.py`): `ClaudeAnalyzer`, `OpenRouterAnalyzer`, `GeminiAnalyzer`, and
  `FallbackAnalyzer` (tries a list of other Analyzers in order, moving on on any failure). **The
  default is free, not Claude**: `get_default_analyzer()` returns `build_free_analyzer()` — a
  chain of OpenRouter's free-tagged models, falling back to Gemini's free tier, so running
  analysis/reports costs nothing unless you explicitly opt into Claude (`ANALYZER_PROVIDER=claude`
  + `ANTHROPIC_API_KEY`). Set `OPENROUTER_API_KEY` and/or `GEMINI_API_KEY` for the free path — a
  provider with no key configured is skipped, not a hard failure, so one of the two is enough.
  **Verification limit, stated plainly:** all four providers are thoroughly tested against mocked
  HTTP responses (shared prompt/parsing logic, rate-limit handling, missing-credential handling,
  fallback-chain behavior) but none has been verified against a real, live API call in the
  environment this was built in — no real API keys were available to verify with. Set the relevant
  env var(s) and run `soliloquy analyze` yourself to confirm the live path.
- **Automatic background analysis** (`/analysis` page, web app only) — a scheduled job
  (`scheduler.py`) runs analysis every `$ANALYSIS_INTERVAL_HOURS` hours (default 6) over the last
  `$ANALYSIS_WINDOW_DAYS` days (default 1) and saves the result via `analysis_store.py`, so the
  Analysis page usually has something to show without asking for it on demand. Always analyzes
  "self" (everything, unfiltered) — auto-generating something under the partner/provider audience
  would mean auto-deciding what's fit to share, which contradicts sharing always being an
  explicit, later, human decision. **This cadence is a starting point, not tuned**: running
  analysis this often trades rate-limit headroom and per-run signal quality (few new entries
  between 6-hour runs) for freshness — flagged in `CHECKLIST.md` to revisit once real usage shows
  what actually makes sense.
- **Sharing flags and audience-filtered reports** (`soliloquy share`, `soliloquy report
  --audience`) — every entry has two independent, per-entry flags, `shareable_with_partner` and
  `shareable_with_provider`, both defaulting to private/`False`. These are deliberately NOT one
  "privacy level" — a partner and a therapist are different audiences with different appropriate
  content, not points on the same scale, so an entry can be shared with one, both, or neither.
  Nothing is shared automatically or assumed at recording time; sharing is always a separate,
  explicit, later decision (`soliloquy share <id> --partner`). `soliloquy report --audience
  partner|provider|self` then generates a readable write-up (summary, mood notes, key topics,
  full transcripts) built from ONLY the entries marked for that audience — critically, the
  filtering happens before entries ever reach the analyzer, so a private entry can't leak into
  the AI-generated summary text even indirectly. `--audience self` (the default) includes
  everything, for your own review. Add `--output some-file` to write the report to a file
  instead of the terminal, for actually handing it to someone.
- **Report export formats** (`--format text|markdown|html|pdf`, both CLI and the web app's Report
  page) — the same `ReportContent` (see `src/soliloquy/report.py`) rendered four ways: plain text
  (the original, terminal-friendly), real Markdown (renders nicely on GitHub/Obsidian/Notion,
  proper headings instead of plain-text underlines), a self-contained HTML page, and a PDF (via
  `fpdf2`, no system-level dependencies) — the most natural format for actually handing to a
  therapist or printing. `pdf` requires `--output <file>` on the CLI since PDF bytes can't be
  printed to a terminal; the web app always offers a download link regardless of format, plus an
  inline preview for text/markdown/html and an embedded PDF viewer.

**Next:** see `CHECKLIST.md` for status — Postgres/MinIO storage, the FastAPI backend, video
capture, and the web GUI are all done; what's next is testing the video flow from a real phone
and what comes after.

**A future integration point, not built yet:** `landonkea-thinkLessScheduleMore`'s automation
engine (`AutomationAction`/`AutomationRegistry`) makes "record a journal entry" a natural
Tasker-style action — meaning `landonkea-makeItSoNumberOne`'s wake word could eventually
trigger a Soliloquy entry directly ("computer, journal entry: ..."). Worth knowing where this
plugs into the rest of the ecosystem, not a dependency for anything built so far.

## Quick start

```bash
docker compose up -d                           # starts local Postgres (port 5433) + MinIO (port 9000)
pip install -e ".[dev]"
python -m soliloquy.cli add "First entry, typed for now."
python -m soliloquy.cli list

pip install -e ".[dev,audio]"                 # adds pyaudio, needed for `record`
# macOS also needs the system library: brew install portaudio
python -m soliloquy.cli record                # press Enter to stop

pip install -e ".[dev,audio,transcribe]"       # adds faster-whisper, needed for transcription
python -m soliloquy.cli record --transcribe    # record, then transcribe, then save as an entry
python -m soliloquy.cli transcribe some-file.wav   # or transcribe an existing recording

# Video entries also need the ffmpeg binary on PATH (used to extract the
# audio track): macOS `brew install ffmpeg`. No extra Python package needed.
pip install -e ".[dev,web]"
python -m soliloquy.web                        # runs the web app (see /entries/video)

export ANTHROPIC_API_KEY=sk-ant-...
python -m soliloquy.cli analyze --days 7           # analyze the last week's entries

python -m soliloquy.cli share <entry-id> --partner             # mark an entry shareable with your partner
python -m soliloquy.cli share <entry-id> --no-partner --provider  # unshare from partner, share with provider
python -m soliloquy.cli report --days 30 --audience partner --output partner-report.md
python -m soliloquy.cli report --days 30 --audience provider --output provider-report.md
```

By default the CLI points at the local docker-compose Postgres
(`postgresql://soliloquy:soliloquy@localhost:5433/soliloquy`) and MinIO
(`http://localhost:9000`, bucket `soliloquy`) — override with the `DATABASE_URL`/`S3_*`
environment variables (or `--db`) to point at anything else, including a managed cloud database
later.

## Running tests

Tests run against a real local Postgres + MinIO (via `docker compose up -d`), not mocks — create
the one-time test database first:

```bash
docker compose up -d
psql postgresql://soliloquy:soliloquy@localhost:5433/soliloquy -c "CREATE DATABASE soliloquy_test"
pip install -e ".[dev,web]"
pytest -v
```
