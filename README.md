# Soliloquy

A voice-first journal: record a thought out loud, get it transcribed automatically, and see
real analysis of what you've been thinking about — per day, per week, per month, over years.

Web app only — no CLI. Everything happens through the browser (or, once the MQTT bridge is set
up, by voice through `makeItSoNumberOne`).

## Why voice-first

Typing a journal entry has friction that talking doesn't. The goal here is "say what's on your
mind for 30 seconds," not "sit down and write." Transcription happens automatically after the
fact — the recording is the whole interaction.

## Architecture

```
   record (mic/camera, or voice via MQTT)  →  transcribe  →  Entry  →  Postgres + object storage  →  analysis
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
- **`actions.py`** — the core operations (`add_entry`, `list_entries`, `analyze_range`,
  `report_range`) every entry point is built on. The web app's routes, the scheduled analysis
  job, and the MQTT listener all call these same functions instead of each reimplementing them.
- **Transcription** (`src/soliloquy/transcriber.py`) + **video** (`src/soliloquy/video.py`,
  extracts audio from uploaded video via `ffmpeg`) — both built, see "What's built" below.
- **Analysis** (`src/soliloquy/analyzer.py`) — built, see "What's built" below.
- **`scheduler.py` + `analysis_store.py`** — a background job runs analysis automatically every
  few hours and saves the result so there's usually something to look at on the Analysis page
  without asking for it on demand.
- **`mqtt_bridge.py`** — a background listener that turns an MQTT message into a journal entry,
  for voice-triggered entries via `makeItSoNumberOne` (see "MQTT bridge" below).
- **Web app** (`src/soliloquy/web/`) — FastAPI + server-rendered Jinja2 templates, the only
  interface to this app. Nothing above it is CLI-specific; a future native app would be a new
  client of the same routes, not a rewrite.

## What's built

- `Entry` data model, `EntryStore` (Postgres), `ObjectStore` (MinIO/S3) — add / get / list /
  delete / date-range query / sharing flags, all against real infrastructure, not SQLite/mocks.
- **Web GUI**: Entries (with audio/video playback), New entry (typed text, or upload audio/video
  — on a phone, the video upload input opens the native camera app directly), Report (generate +
  download in four formats), Analysis (automatic background summaries), share toggles per entry.
- **Real local transcription** (`WhisperTranscriber`, `faster-whisper`, fully local, no network
  access, no API key) — deliberate: given this may hold sensitive personal disclosure, audio
  never leaving the device is a real privacy requirement here, not just a cost preference. Built
  behind a real `Transcriber` protocol so a cloud provider could be swapped in later. Verified
  against real synthesized speech (macOS `say` → transcribed by Whisper) — exact match.
- **Video capture**: upload a video, its audio is extracted (`ffmpeg`) and transcribed through
  the same pipeline as an audio entry, unchanged. Both the video and its extracted audio are kept
  in object storage. Verified against a real ffmpeg-synthesized test video; not yet verified from
  an actual phone recording.
- **Upload format support**: not hardcoded to specific extensions — `ffmpeg` and
  `faster-whisper`'s PyAV-based decoding both detect format from file contents, not extension, so
  `.m4a`, `.mkv`, and most other common audio/video containers already work with zero special
  handling. Verified directly with real ffmpeg-synthesized `.m4a`/`.mkv` files, not assumed. The
  `/media/{key}` route's content-type map covers the common ones for correct browser playback
  headers (some containers, `.mkv` especially, still won't play back in most browsers regardless
  — a browser codec-support limitation, not something fixable from this side).
- **Analysis** — turns a date range of entries into a real summary, mood notes, and key topics,
  asked explicitly to be honest rather than generically positive. Four `Analyzer` implementations
  (`src/soliloquy/analyzer.py`): `ClaudeAnalyzer`, `OpenRouterAnalyzer`, `GeminiAnalyzer`, and
  `FallbackAnalyzer` (tries a list of providers in order, moving on on any failure). **The
  default is free, not Claude**: `get_default_analyzer()` returns `build_free_analyzer()` — a
  chain of OpenRouter's free-tagged models, falling back to Gemini's free tier, so running
  analysis/reports costs nothing unless you explicitly opt into Claude (`ANALYZER_PROVIDER=claude`
  + `ANTHROPIC_API_KEY`). Set `OPENROUTER_API_KEY` and/or `GEMINI_API_KEY` for the free path — a
  provider with no key configured is skipped, not a hard failure. **Verification limit, stated
  plainly:** all four providers are thoroughly tested against mocked HTTP responses but none has
  been verified against a real, live API call in the environment this was built in — no real API
  keys were available to verify with.
- **Automatic background analysis** (`/analysis` page) — a scheduled job (`scheduler.py`) runs
  analysis every `$ANALYSIS_INTERVAL_HOURS` hours (default 6) over the last
  `$ANALYSIS_WINDOW_DAYS` days (default 1) and saves the result. Always analyzes "self"
  (everything, unfiltered) — auto-generating something under the partner/provider audience would
  mean auto-deciding what's fit to share, which contradicts sharing always being an explicit,
  later, human decision. **This cadence is a starting point, not tuned**: running analysis this
  often trades rate-limit headroom and per-run signal quality (few new entries between 6-hour
  runs) for freshness — flagged in `CHECKLIST.md` to revisit once real usage shows what actually
  makes sense.
- **MQTT bridge** (`mqtt_bridge.py`) — a background listener (self-hosted Mosquitto broker) that
  turns a `{"text": "..."}` message on the `soliloquy/journal` topic into a real journal entry.
  Pairs with a `journal_entry` plugin in `landonkea-makeItSoNumberOne` (see that repo's
  `desktop/plugins/examples/journal_entry_plugin.py`) so saying "Computer, journal entry: ..."
  saves a Soliloquy entry. Text-only for now — the voice assistant already transcribes the
  command before publishing, so relaying that transcript is the simple, robust v1; sending raw
  audio over MQTT is a heavier future enhancement, not needed for this to work end-to-end.
- **LAN reachability + local-vs-cloud awareness**: confirmed live (not assumed) that the web app
  and all three backends (Postgres/MinIO/Mosquitto) are already reachable from any device on your
  LAN by default — the web server binds `0.0.0.0`, and every `docker-compose` port publish does
  too. That's convenient for using this from a phone on the same network, but it also means the
  default dev credentials are reachable by anything else on that network, not just this machine —
  worth real hardening before using this somewhere less trusted than a home LAN.
  `deployment_mode.py` prints one line at startup describing whether it's currently in LOCAL mode
  (everything on localhost/a private address) or CLOUD mode (something's a public host) — purely
  informational today, doesn't change any actual behavior yet.
- **Journaling prompts** (`prompts.py`) — 116 hand-written prompts, one rotated in per calendar
  day (deterministic, not random per page load — the same prompt shows all day). Shown on the New
  Entry page above all three entry methods, to cut down on blank-page hesitation before recording
  or typing.
- **Sharing flags and audience-filtered reports** — every entry has two independent flags,
  `shareable_with_partner` and `shareable_with_provider`, both defaulting to private/`False`.
  Deliberately NOT one "privacy level" — a partner and a therapist are different audiences with
  different appropriate content. Nothing is shared automatically; sharing is always a separate,
  explicit, later decision. The Report page filters to ONLY entries marked for the chosen
  audience — critically, the filtering happens before entries ever reach the analyzer, so a
  private entry can't leak into the AI-generated summary text even indirectly.
- **Report export formats** (`text|markdown|html|pdf`) — the same `ReportContent` (see
  `src/soliloquy/report.py`) rendered four ways: plain text, real Markdown (renders nicely on
  GitHub/Obsidian/Notion), a self-contained HTML page, and a PDF (via `fpdf2`, no system-level
  dependencies) — the most natural format for actually handing to a therapist or printing.

**Next:** see `CHECKLIST.md` for the full status and what's after this.

## Quick start

```bash
docker compose up -d       # local Postgres (port 5433), MinIO (port 9000), Mosquitto (port 1883)
pip install -e ".[dev,web,transcribe]"
# macOS also needs: brew install ffmpeg   (used to extract audio from uploaded video)

python -m soliloquy.web    # runs the web app at http://localhost:8000
```

Then open `http://localhost:8000` in a browser: **Entries** to browse, **New entry** to add a
typed/audio/video entry, **Report** to generate a shareable write-up, **Analysis** to see
automatic background summaries.

By default the app points at the local docker-compose Postgres
(`postgresql://soliloquy:soliloquy@localhost:5433/soliloquy`), MinIO
(`http://localhost:9000`, bucket `soliloquy`), and Mosquitto (`localhost:1883`) — override with
the `DATABASE_URL`/`S3_*`/`MQTT_*` environment variables to point at anything else, including a
managed cloud database later. See `.env.example` for the full list.

## Running tests

Tests run against real local Postgres + MinIO (via `docker compose up -d`), not mocks — create
the one-time test database first:

```bash
docker compose up -d
psql postgresql://soliloquy:soliloquy@localhost:5433/soliloquy -c "CREATE DATABASE soliloquy_test"
pip install -e ".[dev,web]"
pytest -v
```
