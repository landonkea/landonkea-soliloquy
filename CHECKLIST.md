# Soliloquy build checklist

Living status doc — check here before asking "what's next," this gets updated as each stage
lands. See `README.md` for what each piece actually does; this file is just status + sequencing.

## Why this order

Web app (not native apps yet) because "record with the phone's camera app, then hand the file to
Soliloquy" is just a browser file upload — no camera-permission code to write. Self-hosted
Postgres + MinIO now, managed cloud (Supabase/Neon + Cloudflare R2) later, because both speak
standard protocols (Postgres wire protocol, S3 API) — moving later is a config change, not a
rewrite. Real backend API + thin web frontend (not logic baked into the browser) so a native app
later becomes a new client of the same API, not a second implementation.

Out of scope for now (confirmed with the user, not forgotten): the MQTT bridge to
`makeItSoNumberOne`, face/expression analysis of video, and the unrelated CRM/CMS + PWA project.

## Stage 1 — Storage: Postgres + MinIO ✅ done

- [x] `docker-compose.yml` — local Postgres (port 5433, avoids colliding with any Postgres
      already running natively) + MinIO (port 9000 API / 9001 console)
- [x] `storage.py` rewritten for Postgres (`psycopg`) — same public interface as before, so
      nothing above it had to change
- [x] `object_storage.py` — S3-compatible client (`boto3`) for audio/video files
- [x] `Entry` gains `video_path`, independent of `audio_path`
- [x] CLI (`cli.py`) points at `DATABASE_URL` (defaults to the local docker-compose instance)
- [x] CI runs against real Postgres + MinIO service containers, not mocks
- [x] All existing tests pass against the real Postgres backend (68 tests)

## Stage 2 — Backend API (FastAPI) ✅ done

- [x] `src/soliloquy/web/app.py` — thin FastAPI layer over the existing package functions
      (`add_entry`, `list_entries`, `report_range`, `format_report`, `store.update_sharing`) — no
      business logic duplicated into the web layer
- [x] Routes: `GET/POST /entries`, `POST /entries/audio`, `POST /entries/{id}/share`,
      `POST /reports`, `GET /media/{key}`
- [x] `python -m soliloquy.web` (or the `soliloquy-web` console script) runs it via `uvicorn`
- [x] Tested with FastAPI's `TestClient` against real Postgres + MinIO (8 tests); manually
      smoke-tested against a real running server with `curl`

## Stage 3 — Video capture ✅ done

- [x] `src/soliloquy/video.py` — `extract_audio()` via `ffmpeg`, same "wrap a real external tool"
      pattern as `recorder.py`'s `pyaudio` use
- [x] Tested against a real synthesized test video (ffmpeg's own `lavfi` test source), not just
      mocks (3 tests) — plus an end-to-end test of the upload route against real Postgres + MinIO
- [x] `POST /entries/video` route: upload video → store in object storage → extract audio → store
      that too → transcribe → create an `Entry` with both `video_path` and `audio_path` set
- [x] Verified against a real running server with `curl` (real ffmpeg extraction, real object
      storage round trip for both files). **Not yet verified against an actual phone-recorded
      video file** — only a synthesized test clip so far; worth doing once stage 4's upload page
      exists to test from an actual phone.

## Stage 4 — Web GUI ✅ done

- [x] Jinja2 templates, no separate JS build: entries list (with audio/video playback), new-entry
      page (text/audio/video upload — the video `<input>` with `capture` is what makes "record
      with the phone's camera, then upload" work with zero extra code), share toggles, report
      generator
- [x] Opened and used in a real desktop browser: added a real text entry, toggled a share flag
      live (confirmed it persisted), generated a report and caught a real bug in the process (a
      missing API key surfaced as an opaque "Internal Server Error" instead of a clear message —
      fixed, now shows the actual reason)
- [ ] **Not yet tested from an actual phone** — desktop browser only so far. Worth doing next:
      open the site from a phone on the same network/tailnet and try the camera-capture upload for
      real (video upload has only been verified with a synthesized test clip + `curl` so far, not
      a real phone recording through the actual page)

## Extra: report export formats ✅ done

- [x] `src/soliloquy/report.py` — one shared `ReportContent`, rendered four ways:
      `format_text`, `format_markdown`, `format_html`, `format_pdf` (via `fpdf2`, no system deps)
- [x] CLI: `soliloquy report --format text|markdown|html|pdf` (`pdf` requires `--output`)
- [x] Web: Report page has a Format selector; download link + inline preview (text/markdown show
      as `<pre>`, html previews in an iframe, pdf previews in an embedded viewer) for all four
- [x] Real bug caught and fixed during this: `fpdf2`'s `multi_cell` needs the cursor reset to the
      left margin after each cell (`new_x=XPos.LMARGIN, new_y=YPos.NEXT`) or the next cell's width
      calculation can hit zero and crash — found by actually generating a PDF, not just writing
      the code

## Extra: free-by-default analysis (OpenRouter + Gemini fallback) ✅ done

- [x] `OpenRouterAnalyzer` and `GeminiAnalyzer` added alongside `ClaudeAnalyzer` in
      `analyzer.py`, all three sharing one prompt-building/response-parsing implementation
- [x] `FallbackAnalyzer` — tries a list of providers in order, moves on on ANY failure (missing
      key, rate limit, bad response), only raises (with every provider's error included) if all
      fail; a `RateLimitError` subclass exists for future use even though today's fallback logic
      treats all failures the same
- [x] `build_free_analyzer()` — the default $0 chain: OpenRouter's free-tagged models, then
      Gemini's free tier. `get_default_analyzer()` reads `$ANALYZER_PROVIDER` ("free" by default,
      "claude" to opt into paying) and both `cli.py` and `web/app.py` use it instead of hardcoding
      Claude
- [x] CLI (`analyze`/`report`) now fails with a clear one-line message instead of a raw Python
      traceback when every provider fails — a real bug caught by actually running the command
      with no API keys configured, not something noticed by reading the code
- [x] 26 tests for `analyzer.py` (up from 9), all passing against mocked HTTP responses. **Not
      verified against a real live API call for any of the four providers** — no real API keys
      available in this environment. Set `OPENROUTER_API_KEY`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`
      and run `soliloquy analyze` yourself to confirm the live path for whichever provider(s) you use

## Extra: automatic background analysis ✅ done

- [x] `analysis_store.py` — `AnalysisSnapshotStore`, a persisted history of past `AnalysisResult`s
      (separate table/concept from entries -- a snapshot is derived and disposable, an entry isn't)
- [x] `scheduler.py` — `run_scheduled_analysis()` (directly testable, analyzer/db overridable) +
      `start_scheduler()` (APScheduler `BackgroundScheduler`, interval configurable via
      `$ANALYSIS_INTERVAL_HOURS`, default **6 hours**, window via `$ANALYSIS_WINDOW_DAYS`, default
      1 day). Wired into the web app's FastAPI `lifespan`, started/stopped with the server.
      Failures are logged, not raised -- a background job failing shouldn't crash the process.
- [x] New `/analysis` page shows the most recent snapshots, so there's usually something to look
      at without clicking "Generate" on the Report page
- [x] Verified live: ran `run_scheduled_analysis()` directly against the real running server's
      database (both the real "no keys configured" failure path, and a stubbed-analyzer success
      path), then confirmed the saved snapshot actually renders on `/analysis` in a real browser
- [x] **Explicitly flagged as a starting point, not a tuned cadence**: every-6-hours trades
      rate-limit headroom and signal quality (little new material between runs) for freshness.
      Revisit once real usage shows what cadence actually makes sense -- the interval/window are
      both just env vars, no code change needed to retune them later

## Right after this

- [ ] Test the video-capture flow from a real phone (not just desktop browser + synthesized test
      video) — this is the one part of the original ask ("record with the phone's camera app,
      then hand the file to Soliloquy") not yet verified on an actual phone
- [ ] Set a real `ANTHROPIC_API_KEY` to verify the report page's happy path end-to-end (currently
      only the error path is verified in this environment)

## After that (not started, no immediate plan)

- [ ] Move from self-hosted Postgres/MinIO to managed cloud (Supabase/Neon + Cloudflare R2) once
      the self-hosted version has been used for real for a while — same interfaces, config change
- [ ] MQTT bridge to `makeItSoNumberOne` (voice-triggered journal entries)
- [ ] Face/expression analysis of stored video (a genuinely new analyzer, not a re-use of the
      transcript pipeline)
- [ ] Native mobile app(s), once the web app has proven the product is worth the extra platform
      work — would be a new client of the same backend API, not a rewrite
