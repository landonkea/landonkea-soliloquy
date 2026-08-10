# Soliloquy

A voice-first journal: record a thought out loud, get it transcribed automatically, and see
real analysis of what you've been thinking about, per day, per week, per month, over years.

Web app only, no CLI. Everything happens through the browser (or, once the MQTT bridge is set
up, by voice through `makeItSoNumberOne`).

## Why voice-first

Typing a journal entry has friction that talking doesn't. The goal here is "say what's on your
mind for 30 seconds," not "sit down and write." Transcription happens automatically after the
fact. The recording is the whole interaction.

## Architecture

```
   record (mic/camera, or voice via MQTT)  →  transcribe  →  Entry  →  Postgres + object storage  →  analysis
```

- **`Entry`** (`src/soliloquy/entry.py`): the one thing every other piece produces or consumes,
  a transcript, a timestamp, optional paths to the original audio/video. Nothing else in this app
  needs to know how an `Entry` was created.
- **`EntryStore`** (`src/soliloquy/storage.py`): Postgres-backed storage (via `psycopg`), driven
  by a `DATABASE_URL` connection string, with a `range_between(start, end)` query built in from
  day one, because per-day/week/month analysis is just different `(start, end)` windows over the
  same query.
- **`ObjectStore`** (`src/soliloquy/object_storage.py`): S3-compatible object storage (via
  `boto3`) for audio/video files. Raw media never lives inside Postgres. Points at self-hosted
  MinIO today; pointing it at Cloudflare R2 (or real S3) later is a config change, not a rewrite,
  since both speak the same S3 API boto3 already uses.
- **`actions.py`**: the core operations (`add_entry`, `list_entries`, `analyze_range`,
  `report_range`) every entry point is built on. The web app's routes, the scheduled analysis
  job, and the MQTT listener all call these same functions instead of each reimplementing them.
- **Transcription** (`src/soliloquy/transcriber.py`) + **video** (`src/soliloquy/video.py`,
  extracts audio from uploaded video via `ffmpeg`): both built, see "What's built" below.
- **Analysis** (`src/soliloquy/analyzer.py`): built, see "What's built" below.
- **`scheduler.py` + `analysis_store.py`**: a background job runs analysis automatically every
  few hours and saves the result so there's usually something to look at on the Analysis page
  without asking for it on demand.
- **`mqtt_bridge.py`**: a background listener that turns an MQTT message into a journal entry,
  for voice-triggered entries via `makeItSoNumberOne` (see "MQTT bridge" below).
- **Web app** (`src/soliloquy/web/`): FastAPI + server-rendered Jinja2 templates, the only
  interface to this app. Nothing above it is CLI-specific; a future native app would be a new
  client of the same routes, not a rewrite.

## What's built

- `Entry` data model, `EntryStore` (Postgres), `ObjectStore` (MinIO/S3): add / get / list /
  delete / date-range query / sharing flags, all against real infrastructure, not SQLite/mocks.
- **Web GUI**: Entries (with audio/video playback), New entry (typed text, or upload audio/video
  on a phone, the video upload input opens the native camera app directly), Report (generate +
  download in four formats), Analysis (automatic background summaries), share toggles per entry.
- **Real local transcription** (`WhisperTranscriber`, `faster-whisper`, fully local, no network
  access, no API key): deliberate, given this may hold sensitive personal disclosure, audio
  never leaving the device is a real privacy requirement here, not just a cost preference. Built
  behind a real `Transcriber` protocol so a cloud provider could be swapped in later. Verified
  against real synthesized speech (macOS `say` → transcribed by Whisper), exact match.
- **Video capture**: upload a video, its audio is extracted (`ffmpeg`) and transcribed through
  the same pipeline as an audio entry, unchanged. Both the video and its extracted audio are kept
  in object storage. Verified against a real ffmpeg-synthesized test video; not yet verified from
  an actual phone recording.
- **Upload format support**: not hardcoded to specific extensions, `ffmpeg` and
  `faster-whisper`'s PyAV-based decoding both detect format from file contents, not extension, so
  `.m4a`, `.mkv`, and most other common audio/video containers already work with zero special
  handling. Verified directly with real ffmpeg-synthesized `.m4a`/`.mkv` files, not assumed. The
  `/media/{key}` route's content-type map covers the common ones for correct browser playback
  headers (some containers, `.mkv` especially, still won't play back in most browsers regardless,
  a browser codec-support limitation, not something fixable from this side).
- **Analysis**: turns a date range of entries into a real summary, mood notes, and key topics,
  asked explicitly to be honest rather than generically positive. Four `Analyzer` implementations
  (`src/soliloquy/analyzer.py`): `ClaudeAnalyzer`, `OpenRouterAnalyzer`, `GeminiAnalyzer`, and
  `FallbackAnalyzer` (tries a list of providers in order, moving on on any failure). **The
  default is free, not Claude**: `get_default_analyzer()` returns `build_free_analyzer()`, a
  chain of OpenRouter's free-tagged models, falling back to Gemini's free tier, so running
  analysis/reports costs nothing unless you explicitly opt into Claude (`ANALYZER_PROVIDER=claude`
  + `ANTHROPIC_API_KEY`). Set `OPENROUTER_API_KEY` and/or `GEMINI_API_KEY` for the free path, a
  provider with no key configured is skipped, not a hard failure. **Verification limit, stated
  plainly:** all four providers are thoroughly tested against mocked HTTP responses but none has
  been verified against a real, live API call in the environment this was built in, no real API
  keys were available to verify with.
- **Automatic background analysis** (`/analysis` page): a scheduled job (`scheduler.py`) runs
  analysis every `$ANALYSIS_INTERVAL_HOURS` hours (default 6) over the last
  `$ANALYSIS_WINDOW_DAYS` days (default 1) and saves the result. Always analyzes "self"
  (everything, unfiltered), auto-generating something under the partner/provider audience would
  mean auto-deciding what's fit to share, which contradicts sharing always being an explicit,
  later, human decision. **This cadence is a starting point, not tuned**: running analysis this
  often trades rate-limit headroom and per-run signal quality (few new entries between 6-hour
  runs) for freshness, flagged in `CHECKLIST.md` to revisit once real usage shows what actually
  makes sense.
- **MQTT bridge** (`mqtt_bridge.py`): a background listener (self-hosted Mosquitto broker) that
  turns a `{"text": "..."}` message on the `soliloquy/journal` topic into a real journal entry.
  Pairs with a `journal_entry` plugin in `landonkea-makeItSoNumberOne` (see that repo's
  `desktop/plugins/examples/journal_entry_plugin.py`) so saying "Computer, journal entry: ..."
  saves a Soliloquy entry. Text-only for now, the voice assistant already transcribes the
  command before publishing, so relaying that transcript is the simple, robust v1; sending raw
  audio over MQTT is a heavier future enhancement, not needed for this to work end-to-end.
- **LAN reachability + local-vs-cloud awareness**: confirmed live (not assumed) that the web app
  and all three backends (Postgres/MinIO/Mosquitto) are already reachable from any device on your
  LAN by default, the web server binds `0.0.0.0`, and every `docker-compose` port publish does
  too. That's convenient for using this from a phone on the same network, but it also means the
  default dev credentials are reachable by anything else on that network, not just this machine,
  worth real hardening before using this somewhere less trusted than a home LAN.
  `deployment_mode.py` prints one line at startup describing whether it's currently in LOCAL mode
  (everything on localhost/a private address) or CLOUD mode (something's a public host), purely
  informational today, doesn't change any actual behavior yet.
- **Journaling prompts** (`prompts.py`): 116 hand-written prompts, one rotated in per calendar
  day (deterministic, not random per page load, the same prompt shows all day). Shown on the New
  Entry page above all three entry methods, to cut down on blank-page hesitation before recording
  or typing.
- **Rotating encouraging tips** (`tips.py`): 40 hand-written, genuine mental-health tips
  (grounding techniques, self-compassion reminders, practical DBT/CBT-style skills), same daily
  rotation as the journaling prompts. Shown on the Entries, Report, and Analysis pages.
- **Voice isolation + loudness normalization** (`noise_reduction.py`): every audio/video upload
  is run through DeepFilterNet3 (a neural model trained to separate speech from background noise,
  including irregular sounds like wind or a dog barking, not just steady hiss) before
  transcription and storage, then normalized with ffmpeg so quiet and loud entries come out
  consistent and just under clipping. See its module docstring for the real installation/threading
  issues found and worked around along the way.
- **Sharing flags and audience-filtered reports**: every entry has two independent flags,
  `shareable_with_partner` and `shareable_with_provider`, both defaulting to private/`False`.
  Deliberately NOT one "privacy level", a partner and a therapist are different audiences with
  different appropriate content. Nothing is shared automatically; sharing is always a separate,
  explicit, later decision. The Report page filters to ONLY entries marked for the chosen
  audience, critically, the filtering happens before entries ever reach the analyzer, so a
  private entry can't leak into the AI-generated summary text even indirectly.
- **Report export formats** (`text|markdown|html|pdf`): the same `ReportContent` (see
  `src/soliloquy/report.py`) rendered four ways: plain text, real Markdown (renders nicely on
  GitHub/Obsidian/Notion), a self-contained HTML page, and a PDF (via `fpdf2`, no system-level
  dependencies), the most natural format for actually handing to a therapist or printing.

**Next:** see `CHECKLIST.md` for the full status and what's after this.

## Quick start

**Easiest way (macOS): double-click `start.command`** in Finder (or run `./start.command` from a
terminal). It handles everything, starting Docker services, creating the Python environment,
installing dependencies, installing `ffmpeg` if missing, and starting the web app, and is safe
to run again any time (each step is a no-op if already done). Leave the window it opens open
while you're using the app; closing it (or Ctrl+C) stops the server. It prints both the
`localhost` address and this machine's LAN address (for using it from your phone).

Manual equivalent, if you'd rather run it yourself:

```bash
docker compose up -d       # local Postgres (port 5433), MinIO (port 9000), Mosquitto (port 1883)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,web,transcribe]"
# macOS also needs: brew install ffmpeg   (used to extract audio from uploaded video, and by the
# voice-isolation/normalization step below)
# The `transcribe` extra also installs DeepFilterNet, which needs a Rust toolchain to build its
# native dependency -- if you don't have Rust: https://rustup.rs (official installer, one-time)

python -m soliloquy.web    # runs the web app at http://localhost:8000
```

Then open `http://localhost:8000` in a browser: **Entries** to browse, **New entry** to add a
typed/audio/video entry, **Report** to generate a shareable write-up, **Analysis** to see
automatic background summaries.

By default the app points at the local docker-compose Postgres
(`postgresql://soliloquy:soliloquy@localhost:5433/soliloquy`), MinIO
(`http://localhost:9000`, bucket `soliloquy`), and Mosquitto (`localhost:1883`), override with
the `DATABASE_URL`/`S3_*`/`MQTT_*` environment variables to point at anything else, including a
managed cloud database later. See `.env.example` for the full list.

## Backups

`scripts/backup.sh` runs daily (3am, via a LaunchAgent) and copies both real data stores:
`pg_dump` of Postgres, `rclone copy` of the entire MinIO bucket, into a timestamped folder under
`~/soliloquy-backups/`, keeping the last 14 days. One-time setup on any machine this runs from:

```bash
brew install rclone
mkdir -p ~/.config/rclone
cat > ~/.config/rclone/rclone.conf <<'EOF'
[soliloquy-minio]
type = s3
provider = Minio
env_auth = false
access_key_id = soliloquy
secret_access_key = soliloquy123
endpoint = http://localhost:9000
EOF
chmod 600 ~/.config/rclone/rclone.conf
```

**This is a local backup, a second copy on the same disk as the original.** It protects against
an accidental delete or a Docker/Postgres problem, but not against this Mac's disk itself failing,
since both copies live on the same disk today.

### Free offsite redundancy (optional, not set up yet)

To actually protect against this machine dying, a copy needs to leave it. Recommended approach,
[restic](https://restic.net) (open source, encrypts everything client-side before it ever leaves
this Mac, since journal entries are the kind of personal content that shouldn't sit unencrypted in
someone else's cloud bucket) pointed at one of:

- **Cloudflare R2**: 10GB storage free, and critically, **zero egress fees** (most providers
  charge to download your own data back out, which matters if you ever need to actually restore).
  The best free option if a restore is ever tested for real.
- **Backblaze B2**: 10GB storage free, small egress fees beyond a modest daily allowance. Very
  well-documented pairing with `restic`.

Both free tiers cap around 10GB. That's comfortable for entries + audio for a long time, but video
will eat into it fast, a handful of video journal entries can be tens of MB each. **There isn't a
genuinely free option once video volume grows past that**; every provider's free tier tops out
in the 10–25GB range. At real scale, the cheapest paths are Backblaze B2 (~$6/TB/month) or a
second physical external drive kept at a different location (one-time cost, no ongoing fee, but
needs manually swapping/updating rather than running automatically).

Not implemented yet, creating an account on either service isn't something that can be done on
your behalf. Once you've picked one and created an account, share the credentials as environment
variables and this can be wired into `backup.sh` as an additional step.

## Running tests

Tests run against real local Postgres + MinIO (via `docker compose up -d`), not mocks, create
the one-time test database first:

```bash
docker compose up -d
psql postgresql://soliloquy:soliloquy@localhost:5433/soliloquy -c "CREATE DATABASE soliloquy_test"
pip install -e ".[dev,web,transcribe]"
pytest -v
```
