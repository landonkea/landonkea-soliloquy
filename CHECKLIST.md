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

Out of scope for now (confirmed with the user, not forgotten): face/expression analysis of video,
and the unrelated CRM/CMS + PWA project.

## Stage 1 — Storage: Postgres + MinIO ✅ done

- [x] `docker-compose.yml` — local Postgres (port 5433, avoids colliding with any Postgres
      already running natively) + MinIO (port 9000 API / 9001 console)
- [x] `storage.py` rewritten for Postgres (`psycopg`) — same public interface as before, so
      nothing above it had to change
- [x] `object_storage.py` — S3-compatible client (`boto3`) for audio/video files
- [x] `Entry` gains `video_path`, independent of `audio_path`
- [x] App points at `DATABASE_URL` (defaults to the local docker-compose instance)
- [x] CI runs against real Postgres + MinIO service containers, not mocks
- [x] All existing tests pass against the real Postgres backend (68 tests)

## Stage 2 — Backend API (FastAPI) ✅ done

- [x] `src/soliloquy/web/app.py` — thin FastAPI layer over the existing package functions
      (`add_entry`, `list_entries`, `report_range`, `store.update_sharing`) — no business logic
      duplicated into the web layer
- [x] Routes: `GET/POST /entries`, `POST /entries/audio`, `POST /entries/{id}/share`,
      `POST /reports`, `GET /media/{key}`
- [x] `python -m soliloquy.web` (or the `soliloquy-web` console script) runs it via `uvicorn`
- [x] Tested with FastAPI's `TestClient` against real Postgres + MinIO (8 tests); manually
      smoke-tested against a real running server with `curl`

## Stage 3 — Video capture ✅ done

- [x] `src/soliloquy/video.py` — `extract_audio()` via `ffmpeg`, wraps a real external tool the
      same way the (now-removed) CLI-only recorder used to wrap `pyaudio`
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
      "claude" to opt into paying) and `web/app.py` uses it instead of hardcoding Claude
- [x] The web app's Report/Analysis routes surface a clear message (not a raw error) when every
      provider fails — a real bug caught by actually running with no API keys configured
- [x] 26 tests for `analyzer.py` (up from 9), all passing against mocked HTTP responses. **Not
      verified against a real live API call for any of the four providers** — no real API keys
      available in this environment. Set `OPENROUTER_API_KEY`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`
      and generate a report yourself to confirm the live path for whichever provider(s) you use

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

## Extra: CLI removed, web-only ✅ done

- [x] Shared logic (`add_entry`, `list_entries`, `analyze_range`, `report_range`, `AUDIENCES`,
      `DEFAULT_DATABASE_URL`) moved out of `cli.py` into `src/soliloquy/actions.py` — the web
      app's routes, the scheduled analysis job, and the MQTT listener all call these same
      functions, so nothing lost logic by losing the CLI wrapper around it
- [x] Deleted `cli.py` (the argparse interface) and `recorder.py` + its tests (only the CLI's
      `record` command ever used local mic recording via `pyaudio` — the web app records
      client-side, via the browser's own file/camera picker, so this was dead code once the CLI
      was gone)
- [x] `pyproject.toml`: removed the `soliloquy` console script and the `audio` extra (`pyaudio`)
- [x] Tests migrated: `test_cli.py` → `test_actions.py` (pure-function tests kept; the
      argparse-`main()`-specific tests were dropped since the same behavior — share, report
      formats, clean failure messages — is already covered by `test_web.py`'s route tests, so no
      coverage was lost)
- [x] Verified: full suite green (109 tests), and the web server starts clean with `python -m
      soliloquy.web` — no leftover imports from the deleted `cli.py`

## MQTT bridge to makeItSoNumberOne ✅ done

- [x] Soliloquy side: `mqtt_bridge.py` (`handle_message()` directly testable, `start_mqtt_listener()`
      wired into the web app's lifespan) + Mosquitto broker in `docker-compose.yml`
      (anonymous-auth, local-only, mirrors the Postgres/MinIO posture). 5 tests for
      `handle_message()` against real Postgres, no broker needed for those.
- [x] `landonkea-makeItSoNumberOne` side: `desktop/plugins/examples/journal_entry_plugin.py`
      (publisher), following that repo's own documented third-party-plugin pattern exactly.
      `CONFIG_SCHEMA` gained an `integrations.journal` block; `core/ai.py` documents the new
      `journal_entry` action so Claude actually emits it for "Computer, journal entry: ...".
      Activated by copying into `desktop/plugins/` (gitignored there, matching every other
      active third-party plugin).
- [x] Real end-to-end verification, both directions: confirmed the plugin loads through the real
      `discover_plugins()` mechanism, ran makeItSoNumberOne's full test suite (194 tests, all
      still passing), and did a genuine cross-repo round trip — called the plugin's `execute()`
      against a real running Mosquitto broker and a real running Soliloquy server, confirmed the
      entry actually landed in Soliloquy's database and rendered on the Entries page.
- **Known gap**: `paho-mqtt` isn't installed in makeItSoNumberOne's own venv by default (it's
  commented out in `requirements.txt`, like `pyautogui`, since it's optional) — verification used
  Soliloquy's venv as a stand-in Python environment (the plugin has no makeItSoNumberOne-specific
  imports beyond a local relative import and the generic `paho-mqtt` package). To actually use
  this for real, `pip install paho-mqtt` in whichever environment runs `make_it_so.py`.

## Extra: LAN reachability confirmed + local-vs-cloud awareness ✅ done

- **Confirmed live, not assumed**: the web app (`uvicorn` binds `0.0.0.0`) and Postgres/MinIO/
  Mosquitto (all `docker-compose` port publishes are `0.0.0.0`, not `127.0.0.1`) are ALL already
  reachable from any device on the LAN today, no code change needed — verified by hitting this
  machine's actual LAN IP from `curl` and getting real responses back.
- **Known real gap, not yet addressed**: because of the above, Postgres/MinIO's default dev
  credentials and Mosquitto's anonymous auth are reachable by anything else on the same network,
  not just this machine. Fine for a single trusted home LAN; worth locking down (real
  credentials, auth on Mosquitto, or binding those three to `127.0.0.1` while leaving only the
  web app on `0.0.0.0`) before using this somewhere less trusted.
- [x] `deployment_mode.py` — `get_deployment_mode()` inspects `DATABASE_URL`/`S3_ENDPOINT_URL`/
      `MQTT_HOST` and returns `"local"` if all resolve to localhost/a private LAN address, or
      `"cloud"` if any is a real public host. Purely informational (no refuse-to-start, no loud
      warning, per explicit direction) — prints one line at web app startup via
      `describe_deployment_mode()`. 10 tests; verified live in both modes (real local startup, and
      a real startup with `MQTT_HOST` pointed at a fake public hostname) — confirmed the correct
      one-liner prints both times.

## Extra: journaling prompts ✅ done

- [x] `prompts.py` — 116 hand-written prompts, one rotated in per calendar day (deterministic —
      `date.toordinal() % len(PROMPTS)`, not random per page load, so the prompt is stable all
      day and the sequence is reproducible). Cycle repeats every ~4 months.
- [x] Shown on the New Entry page above all three entry methods (typed/audio/video) — the point
      is reducing blank-page hesitation regardless of which way you're adding an entry.
- [x] 8 tests (`prompts.py`) + 1 web test confirming the page actually renders today's prompt.
      Verified visually in a real browser.

## Extra: one-click startup ✅ done

- [x] `start.command` — double-click in Finder (or `./start.command`) to run everything: checks
      Docker is running, installs `ffmpeg` via Homebrew if missing, `docker compose up -d`,
      creates/updates the Python venv, waits for Postgres to be ready, then starts the web app.
      Safe to run repeatedly — every step is a no-op if already done.
- [x] Verified for real, twice: once with an existing `.venv` (fast path), once with `.venv`
      removed entirely to simulate a genuine first-ever run — both times ended with a real HTTP
      200 from the running server, and the printed LAN address was confirmed reachable too.

## Extra: visual redesign + upload/capture choice + delete ✅ done

- [x] Real design system in `base.html` (warm color palette, cards with shadows, better
      typography/spacing/buttons) instead of bare default styling. Header restructured to two
      rows (brand name, then nav below it) per explicit request.
- [x] New Entry page redesigned: a type selector (Text/Audio/Video) shown first, prompt still
      above it. Audio/Video sections each offer two explicit choices — "Upload from device" (no
      `capture` attribute, opens the normal file/photo picker) vs "Record now" (has `capture`,
      opens the camera/mic directly) — fixing the earlier behavior where video always jumped
      straight to the camera with no upload option.
- [x] Share checkboxes on the Entries page: stacked vertically, left-aligned (was side-by-side).
- [x] `DELETE /entries/{id}` route (didn't exist before) — deletes the DB row and best-effort
      cleans up its audio/video objects in MinIO too, not just the row. Delete button added to
      each entry card with a confirm() prompt before it runs. 3 new tests.
- [x] Verified live end-to-end, including catching and correctly diagnosing an apparent delete
      failure that turned out to be concurrent testing (the user testing live on their own device
      at the same time as this session) rather than a real bug — confirmed via server logs.

## Extra: `soliloquy.internal` LAN name, self-healing against IP changes ✅ done

- [x] `dnsmasq` installed via Homebrew, running as a system service (`sudo brew services start
      dnsmasq`) — `/opt/homebrew/etc/dnsmasq.conf` resolves `soliloquy.internal` to this Mac's
      LAN IP, forwards everything else normally. `.internal` specifically (not `.local`, not a
      real TLD like `.com`) — reserved (RFC 9476) for exactly this: a private name that will
      never collide with, or be mistaken for, a real public domain.
- [x] `scripts/update-soliloquy-dns.sh` — since the LAN IP is DHCP-assigned and changes every few
      hours/days, this detects the *current* IP and rewrites/reloads dnsmasq only when it's
      actually different from what's configured (a no-op otherwise). Runs automatically every 5
      minutes via a LaunchAgent (`~/Library/LaunchAgents/com.soliloquy.dns-update.plist`, not
      tracked in the repo since it's machine-specific — the script it calls is).
- [x] Verified for real, twice: manually forced a wrong IP into the config and confirmed the
      script corrected it, then did it again and confirmed the LaunchAgent caught it *on its own*
      via `launchctl kickstart` (not a manual script run) — the self-healing actually self-heals.
- **Remaining manual step, can't be done for you**: point each phone/device's WiFi DNS setting at
  this Mac's IP (Settings → WiFi → network details → DNS → Manual) so it actually uses dnsmasq to
  resolve `soliloquy.internal` — requires the device's own settings, not something scriptable
  from here. Router-level DHCP config (to make this automatic for every device on the LAN) would
  need router admin access, which wasn't available in this session.

## Right after this

- [ ] Test the video-capture flow from a real phone (not just desktop browser + synthesized test
      video) — this is the one part of the original ask ("record with the phone's camera app,
      then hand the file to Soliloquy") not yet verified on an actual phone
- [ ] Set a real `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`/`GEMINI_API_KEY` to verify the
      Report/Analysis happy path end-to-end (currently only the error path is verified here)
- [ ] Decide on and implement real LAN/cloud security hardening (see above) — `deployment_mode.py`
      only describes the situation today, it doesn't yet change any actual behavior

## After that (not started, no immediate plan)

- [ ] Move from self-hosted Postgres/MinIO to managed cloud (Supabase/Neon + Cloudflare R2) once
      the self-hosted version has been used for real for a while — same interfaces, config change
- [ ] Face/expression analysis of stored video (a genuinely new analyzer, not a re-use of the
      transcript pipeline)
- [ ] Native mobile app(s), once the web app has proven the product is worth the extra platform
      work — would be a new client of the same backend API, not a rewrite
