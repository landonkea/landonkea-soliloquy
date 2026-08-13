# Build log: how Soliloquy came to be, and how to rebuild it from nothing

`CHECKLIST.md` tracks what's done and what's next, stage by stage, going forward. This file
looks backward and sideways instead: the actual sequence of decisions that got this repo from
an empty directory to its current state, and a literal, runnable path to reproduce that state
on a machine that has nothing on it yet. If you (or an agent with no memory of any of this) ever
need to stand Soliloquy up from scratch, start here.

## How this actually happened, condensed from git history

36 commits, in order, cluster into a handful of real phases. This isn't a plan that was written
up front and executed; you can see the pivots in the commit messages themselves.

**1. A CLI-first, SQLite-first prototype.** The very first commit (`Initial scaffold: Entry
model, SQLite storage, text-only CLI`) is the whole idea in its smallest possible form: an
`Entry` (text + timestamp), a store, a way to add and list entries from a terminal. No
transcription, no web, no MQTT. This mattered because everything that came after kept the same
`Entry` shape and the same "one function per operation" pattern (`add_entry`, `list_entries`)
even as the plumbing underneath changed completely.

**2. Real audio, then real transcription, then real analysis.** Three commits in a row
(`Real microphone recording`, `Real local transcription`, `Real analysis engine`) each add one
capability end to end, working against real infrastructure rather than a mock, before moving to
the next. `faster-whisper` was chosen for transcription specifically because it runs fully
local: journal entries are personal disclosure, and "your voice recording leaves this machine"
wasn't an acceptable default.

**3. Storage moved from local SQLite to self-hosted Postgres + MinIO.** Once entries needed to
be reachable from a phone (not just the machine that recorded them), a single SQLite file
stopped being enough. Postgres and MinIO were picked over a managed cloud database specifically
because both speak standard wire protocols (`postgresql://`, the S3 API), so switching to
Supabase/Neon/R2 later is a connection-string change, not a rewrite. This is still true today;
see "Not started yet" in `CHECKLIST.md`.

**4. FastAPI backend, then the CLI was deleted outright.** A web layer went in first
(`Add a FastAPI backend`), coexisting with the CLI for a few commits, then the CLI was removed
entirely once the web app covered everything it did (`Remove the CLI: web app is now the only
interface`). The shared logic didn't disappear with it. It had already been pulled into
`actions.py`, so deleting `cli.py` meant deleting an argparse wrapper, not deleting behavior.

**5. Video, then the web GUI, then export formats.** Video upload landed as a backend route
before there was any page to use it from (verified with `curl` first). The GUI came next, and
report export formats (text/markdown/HTML/PDF) after that, once there was a report worth
exporting.

**6. Analysis went from "Claude only" to "free by default."** `Make analysis free by default:
OpenRouter free models, then Gemini` exists because paying per report isn't reasonable for
something meant to run continuously. `ANALYZER_PROVIDER` defaults to `"free"`; Claude is opt-in.

**7. The MQTT bridge to `makeItSoNumberOne`.** This is the one piece of Soliloquy that reaches
outside this repo. A Mosquitto broker was added to `docker-compose.yml`, and
`mqtt_bridge.py` subscribes to `soliloquy/journal`, turning a `{"text": "..."}` message into a
real entry. The matching publisher lives in the `landonkea-makeItSoNumberOne` repo
(`desktop/plugins/examples/journal_entry_plugin.py`), so "Computer, journal entry: ..." spoken
into that app's voice assistant lands here.

**8. Everything after that is refinement, not new architecture.** Sharing flags per audience
(partner vs. provider, both private by default), rotating prompts and tips, voice isolation
(DeepFilterNet3) and loudness normalization, a LAN hostname (first `dnsmasq` + `.internal`, then
pivoted to mDNS + Caddy + `.local` once pointing every device's own DNS at this Mac turned out
to be the wrong tradeoff), local daily backups, and a security pass that moved Postgres/MinIO
to `127.0.0.1`-only bindings while deliberately leaving Mosquitto on `0.0.0.0` (the MQTT bridge
needs to be reachable from wherever `makeItSoNumberOne` is running). The full detail on each of
these, including the real bugs found and fixed along the way, is in `CHECKLIST.md`; this
section is the short version.

## Decisions worth knowing the reasoning behind

A few choices in this repo look arbitrary if you only read the code, so they're worth stating
directly:

- **Web-only, no CLI, no separate JS framework.** A native app later becomes a new client of
  the FastAPI routes, not a second implementation of the same rules. Business logic lives in
  `actions.py`, not in `web/app.py` and not in browser-side JavaScript.
- **Self-hosted Postgres/MinIO instead of managed cloud, for now.** Same protocols either way.
  The move to Supabase/Neon + Cloudflare R2 is deliberately deferred until there's been real
  usage to learn from, not blocked on anything technical.
- **Transcription is local, not an API call.** `faster-whisper` running on-device, no network
  access required, because the audio is personal disclosure and shouldn't leave the machine by
  default.
- **Analysis is free by default, Claude is opt-in.** `build_free_analyzer()` chains OpenRouter's
  free-tagged models, then Gemini's free tier; `FallbackAnalyzer` moves on on any failure
  (missing key, rate limit, bad response) so the app degrades rather than throwing at the user.
- **Two independent sharing flags, not one privacy level.** A partner and a therapist are
  different audiences with different appropriate content. Filtering happens before an entry
  ever reaches the analyzer, so a private entry can't leak into an AI-generated summary even
  indirectly.
- **Mosquitto is the one service left open on the LAN.** Postgres and MinIO were locked to
  `127.0.0.1` once nothing outside this machine needed to reach them directly (the web app
  proxies media through its own `/media` route). Mosquitto stayed on `0.0.0.0` because the whole
  point of the MQTT bridge is a different device on the LAN publishing to it.

## Rebuilding from zero: exact steps

This assumes a machine with nothing Soliloquy-specific on it yet. Genuinely manual prerequisites
(things a script can't do on your behalf) are marked; everything else is copy-pasteable.

### 0. Prerequisites (manual, one-time, can't be scripted)

- **macOS with [Homebrew](https://brew.sh) installed.** `start.command` (step 4 below) uses it
  to install `ffmpeg` automatically, but Homebrew itself has to already be there.
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running.**
  Postgres, MinIO, and Mosquitto all run as containers; nothing works without it.
- **Python 3.10+** (`python3 --version`). Ships with recent macOS, or `brew install python@3.12`.
- **A Rust toolchain**, only needed for the `transcribe` extra (`deepfilternet`'s native
  dependency, `deepfilterlib`, compiles from source): [rustup.rs](https://rustup.rs), the
  official one-time installer.
- Optional, only if you want AI-generated summaries beyond the error path: an
  `OPENROUTER_API_KEY` and/or `GEMINI_API_KEY` (both free tiers), or `ANTHROPIC_API_KEY` if you
  want to pay for Claude specifically. The app runs fully without any of these; Report/Analysis
  just show a clear "no provider configured" message instead of a summary.

### 1. Clone and check out the code

```bash
git clone git@github.com:landonkea/landonkea-soliloquy.git
cd landonkea-soliloquy
```

(If you're rebuilding without the original git history, from a zip export, say, skip this and
just recreate the file structure described below; nothing after this step depends on git itself.)

### 2. Bring up the infrastructure containers

```bash
docker compose up -d
```

This starts three containers, defined in `docker-compose.yml`:

- **Postgres 16** on `127.0.0.1:5433` (not the default 5432, to avoid colliding with any
  Postgres already running natively), user/password/db all `soliloquy`.
- **MinIO** on `127.0.0.1:9000` (S3 API) and `127.0.0.1:9001` (web console), root user
  `soliloquy` / password `soliloquy123`, bucket `soliloquy`.
- **Mosquitto 2** on `0.0.0.0:1883`, anonymous auth, config at `mosquitto/mosquitto.conf`.

All three are dev credentials, intentionally not secrets. See `docker-compose.yml`'s own
comments for why Postgres/MinIO are `127.0.0.1`-only while Mosquitto is left open.

### 3. Create the Python environment and install the app

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web,transcribe]"
```

`pyproject.toml` defines four dependency groups: the base install (Postgres/MinIO clients, PDF
export, required no matter what), `dev` (pytest), `web` (FastAPI/uvicorn/APScheduler/MQTT,
needed to run the app at all, since web is the only interface), and `transcribe` (Whisper +
DeepFilterNet + torch, needed to turn audio/video into text). Installing all three extras is
the full setup; the `transcribe` extra is the slow one (torch is a large download, and
`deepfilterlib` compiles native code).

macOS also needs `ffmpeg` on `PATH` (used both to extract audio from uploaded video and by the
loudness-normalization step):

```bash
brew install ffmpeg
```

### 4. Copy and fill in the environment file

```bash
cp .env.example .env
```

The defaults in `.env.example` already point at the docker-compose services from step 2, so a
first run needs no edits at all. Set `OPENROUTER_API_KEY`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`
only if you want live AI summaries rather than the "no provider configured" message.

### 5. Run it

```bash
python -m soliloquy.web
```

Opens on `http://localhost:8000` (and on this machine's LAN IP too, since `uvicorn` binds
`0.0.0.0` on purpose, for using it from a phone on the same network).

Steps 2 through 5, in that exact order with the same no-op-if-already-done safety, are also
exactly what `start.command` automates. Double-clicking that file in Finder does all four in
one shot and is safe to run repeatedly.

### 6. (Optional) run the test suite

Tests run against real Postgres and MinIO, not mocks, so they need one extra one-time step: a
dedicated test database.

```bash
psql postgresql://soliloquy:soliloquy@localhost:5433/soliloquy -c "CREATE DATABASE soliloquy_test"
pytest -v
```

CI (`.github/workflows/ci.yml`) does the equivalent on every push/PR: a Postgres 16 service
container, MinIO started by hand (GitHub Actions' `services:` block can't override a container's
startup command, and MinIO's image needs `server /data` passed explicitly), then
`pip install -e ".[dev,web]"` and `pytest -v`. CI intentionally skips the `transcribe` extra
(torch + DeepFilterNet would make every CI run slow for coverage that doesn't touch it), see
`tests/` for how transcription/noise-reduction are tested with lighter fixtures instead.

### 7. (Optional) wire up the LAN hostname and MQTT bridge

Two things from `CHECKLIST.md` that are real but machine-specific, not part of a from-scratch
rebuild of the app itself:

- **`http://soliloquy.local` instead of typing a port**: `brew install caddy`, point
  `/opt/homebrew/etc/Caddyfile` at this repo's `Caddyfile` (reverse-proxies port 80 to 8000), and
  run `scripts/soliloquy-mdns.sh` (registers the mDNS name; see that script and
  `CHECKLIST.md`'s "`soliloquy.local`" section for the LaunchAgent that keeps it current).
- **Voice-triggered entries via `makeItSoNumberOne`**: that's a separate repo
  (`landonkea-makeItSoNumberOne`). Its `desktop/plugins/examples/journal_entry_plugin.py` needs
  to be copied into that repo's `desktop/plugins/` directory (gitignored there, same as every
  other active third-party plugin) and `paho-mqtt` installed in whichever Python environment
  runs `make_it_so.py`. Nothing on Soliloquy's side is needed beyond Mosquitto already running
  (step 2). `mqtt_bridge.py` starts automatically with the web app.

### What a script genuinely cannot do for you

Everything above is scriptable and is, in fact, scripted in `start.command`. A short list of
things that stay manual no matter what, because they need a physical device, an account, or a
judgment call:

- Installing Docker Desktop and Homebrew themselves (chicken-and-egg: nothing here can install
  the tool it needs to run its own install steps).
- Creating accounts and getting API keys (OpenRouter/Gemini/Anthropic for analysis, Cloudflare
  R2/Backblaze B2 for offsite backups), see `USER_TODO.md` for the current list.
  Testing the actual microphone/camera capture flow from a real phone, and hearing how a real
  human voice sounds after DeepFilterNet, since everything verified so far used synthesized
  audio/video.
- Deciding on LAN security hardening tradeoffs (real credentials vs. convenience), a judgment
  call about acceptable risk on your own network, not a coding task.
