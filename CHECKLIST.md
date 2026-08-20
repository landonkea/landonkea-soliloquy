# Soliloquy build checklist

Living status doc, check here before asking "what's next," this gets updated as each stage
lands. See `README.md` for what each piece actually does; this file is just status + sequencing.

## Why this order

Web app (not native apps yet) because "record with the phone's camera app, then hand the file to
Soliloquy" is just a browser file upload, no camera-permission code to write. Self-hosted
Postgres + MinIO now, managed cloud (Supabase/Neon + Cloudflare R2) later, because both speak
standard protocols (Postgres wire protocol, S3 API), moving later is a config change, not a
rewrite. Real backend API + thin web frontend (not logic baked into the browser) so a native app
later becomes a new client of the same API, not a second implementation.

Out of scope for now (confirmed with the user, not forgotten): face/expression analysis of video,
and the unrelated CRM/CMS + PWA project.

## Stage 1, Storage: Postgres + MinIO ✅ done

- [x] `docker-compose.yml`, local Postgres (port 5433, avoids colliding with any Postgres
      already running natively) + MinIO (port 9000 API / 9001 console)
- [x] `storage.py` rewritten for Postgres (`psycopg`), same public interface as before, so
      nothing above it had to change
- [x] `object_storage.py`, S3-compatible client (`boto3`) for audio/video files
- [x] `Entry` gains `video_path`, independent of `audio_path`
- [x] App points at `DATABASE_URL` (defaults to the local docker-compose instance)
- [x] CI runs against real Postgres + MinIO service containers, not mocks
- [x] All existing tests pass against the real Postgres backend (68 tests)

## Stage 2, Backend API (FastAPI) ✅ done

- [x] `src/soliloquy/web/app.py`, thin FastAPI layer over the existing package functions
      (`add_entry`, `list_entries`, `report_range`, `store.update_sharing`), no business logic
      duplicated into the web layer
- [x] Routes: `GET/POST /entries`, `POST /entries/audio`, `POST /entries/{id}/share`,
      `POST /reports`, `GET /media/{key}`
- [x] `python -m soliloquy.web` (or the `soliloquy-web` console script) runs it via `uvicorn`
- [x] Tested with FastAPI's `TestClient` against real Postgres + MinIO (8 tests); manually
      smoke-tested against a real running server with `curl`

## Stage 3, Video capture ✅ done

- [x] `src/soliloquy/video.py`, `extract_audio()` via `ffmpeg`, wraps a real external tool the
      same way the (now-removed) CLI-only recorder used to wrap `pyaudio`
- [x] Tested against a real synthesized test video (ffmpeg's own `lavfi` test source), not just
      mocks (3 tests), plus an end-to-end test of the upload route against real Postgres + MinIO
- [x] `POST /entries/video` route: upload video → store in object storage → extract audio → store
      that too → transcribe → create an `Entry` with both `video_path` and `audio_path` set
- [x] Verified against a real running server with `curl` (real ffmpeg extraction, real object
      storage round trip for both files). **Not yet verified against an actual phone-recorded
      video file**, only a synthesized test clip so far; worth doing once stage 4's upload page
      exists to test from an actual phone.

## Stage 4, Web GUI ✅ done

- [x] Jinja2 templates, no separate JS build: entries list (with audio/video playback), new-entry
      page (text/audio/video upload, the video `<input>` with `capture` is what makes "record
      with the phone's camera, then upload" work with zero extra code), share toggles, report
      generator
- [x] Opened and used in a real desktop browser: added a real text entry, toggled a share flag
      live (confirmed it persisted), generated a report and caught a real bug in the process (a
      missing API key surfaced as an opaque "Internal Server Error" instead of a clear message,
      fixed, now shows the actual reason)
- [ ] **Not yet tested from an actual phone**, desktop browser only so far. Worth doing next:
      open the site from a phone on the same network/tailnet and try the camera-capture upload for
      real (video upload has only been verified with a synthesized test clip + `curl` so far, not
      a real phone recording through the actual page) (blocked on the user: needs a real phone in
      hand, nothing left to code here)

## Extra: report export formats ✅ done

- [x] `src/soliloquy/report.py`, one shared `ReportContent`, rendered four ways:
      `format_text`, `format_markdown`, `format_html`, `format_pdf` (via `fpdf2`, no system deps)
- [x] Web: Report page has a Format selector; download link + inline preview (text/markdown show
      as `<pre>`, html previews in an iframe, pdf previews in an embedded viewer) for all four
- [x] Real bug caught and fixed during this: `fpdf2`'s `multi_cell` needs the cursor reset to the
      left margin after each cell (`new_x=XPos.LMARGIN, new_y=YPos.NEXT`) or the next cell's width
      calculation can hit zero and crash, found by actually generating a PDF, not just writing
      the code

## Extra: free-by-default analysis (OpenRouter + Gemini fallback) ✅ done

- [x] `OpenRouterAnalyzer` and `GeminiAnalyzer` added alongside `ClaudeAnalyzer` in
      `analyzer.py`, all three sharing one prompt-building/response-parsing implementation
- [x] `FallbackAnalyzer`, tries a list of providers in order, moves on on ANY failure (missing
      key, rate limit, bad response), only raises (with every provider's error included) if all
      fail; a `RateLimitError` subclass exists for future use even though today's fallback logic
      treats all failures the same
- [x] `build_free_analyzer()`, the default $0 chain: OpenRouter's free-tagged models, then
      Gemini's free tier. `get_default_analyzer()` reads `$ANALYZER_PROVIDER` ("free" by default,
      "claude" to opt into paying) and `web/app.py` uses it instead of hardcoding Claude
- [x] The web app's Report/Analysis routes surface a clear message (not a raw error) when every
      provider fails, a real bug caught by actually running with no API keys configured
- [x] 26 tests for `analyzer.py` (up from 9), all passing against mocked HTTP responses. **Not
      verified against a real live API call for any of the four providers**, no real API keys
      available in this environment. Set `OPENROUTER_API_KEY`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`
      and generate a report yourself to confirm the live path for whichever provider(s) you use

## Extra: automatic background analysis ✅ done

- [x] `analysis_store.py`, `AnalysisSnapshotStore`, a persisted history of past `AnalysisResult`s
      (separate table/concept from entries -- a snapshot is derived and disposable, an entry isn't)
- [x] `scheduler.py`, `run_scheduled_analysis()` (directly testable, analyzer/db overridable) +
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
      `DEFAULT_DATABASE_URL`) moved out of `cli.py` into `src/soliloquy/actions.py`, the web
      app's routes, the scheduled analysis job, and the MQTT listener all call these same
      functions, so nothing lost logic by losing the CLI wrapper around it
- [x] Deleted `cli.py` (the argparse interface) and `recorder.py` + its tests (only the CLI's
      `record` command ever used local mic recording via `pyaudio`, the web app records
      client-side, via the browser's own file/camera picker, so this was dead code once the CLI
      was gone)
- [x] `pyproject.toml`: removed the `soliloquy` console script and the `audio` extra (`pyaudio`)
- [x] Tests migrated: `test_cli.py` → `test_actions.py` (pure-function tests kept; the
      argparse-`main()`-specific tests were dropped since the same behavior, share, report
      formats, clean failure messages, is already covered by `test_web.py`'s route tests, so no
      coverage was lost)
- [x] Verified: full suite green (109 tests), and the web server starts clean with `python -m
      soliloquy.web`, no leftover imports from the deleted `cli.py`

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
      still passing), and did a genuine cross-repo round trip, called the plugin's `execute()`
      against a real running Mosquitto broker and a real running Soliloquy server, confirmed the
      entry actually landed in Soliloquy's database and rendered on the Entries page.
- **Known gap**: `paho-mqtt` isn't installed in makeItSoNumberOne's own venv by default (it's
  commented out in `requirements.txt`, like `pyautogui`, since it's optional), verification used
  Soliloquy's venv as a stand-in Python environment (the plugin has no makeItSoNumberOne-specific
  imports beyond a local relative import and the generic `paho-mqtt` package). To actually use
  this for real, `pip install paho-mqtt` in whichever environment runs `make_it_so.py`.

## Extra: LAN reachability confirmed + local-vs-cloud awareness ✅ done

- **Confirmed live, not assumed**: the web app (`uvicorn` binds `0.0.0.0`) and Postgres/MinIO/
  Mosquitto (all `docker-compose` port publishes are `0.0.0.0`, not `127.0.0.1`) are ALL already
  reachable from any device on the LAN today, no code change needed, verified by hitting this
  machine's actual LAN IP from `curl` and getting real responses back.
- **Known real gap, not yet addressed**: because of the above, Postgres/MinIO's default dev
  credentials and Mosquitto's anonymous auth are reachable by anything else on the same network,
  not just this machine. Fine for a single trusted home LAN; worth locking down (real
  credentials, auth on Mosquitto, or binding those three to `127.0.0.1` while leaving only the
  web app on `0.0.0.0`) before using this somewhere less trusted.
- [x] `deployment_mode.py`, `get_deployment_mode()` inspects `DATABASE_URL`/`S3_ENDPOINT_URL`/
      `MQTT_HOST` and returns `"local"` if all resolve to localhost/a private LAN address, or
      `"cloud"` if any is a real public host. Purely informational (no refuse-to-start, no loud
      warning, per explicit direction), prints one line at web app startup via
      `describe_deployment_mode()`. 10 tests; verified live in both modes (real local startup, and
      a real startup with `MQTT_HOST` pointed at a fake public hostname), confirmed the correct
      one-liner prints both times.

## Extra: journaling prompts ✅ done

- [x] `prompts.py`, 116 hand-written prompts, one rotated in per calendar day (deterministic,
      `date.toordinal() % len(PROMPTS)`, not random per page load, so the prompt is stable all
      day and the sequence is reproducible). Cycle repeats every ~4 months.
- [x] Shown on the New Entry page above all three entry methods (typed/audio/video), the point
      is reducing blank-page hesitation regardless of which way you're adding an entry.
- [x] 8 tests (`prompts.py`) + 1 web test confirming the page actually renders today's prompt.
      Verified visually in a real browser.

## Extra: rotating encouraging tips ✅ done

- [x] `tips.py`, 40 hand-written, genuine encouraging/practical mental-health tips (grounding
      techniques, self-compassion reminders, DBT/CBT-style skills, not generic "just think
      positive" phrasing), same deterministic daily rotation as `prompts.py`.
- [x] Shown on Entries, Report, and Analysis pages (not New Entry, which already has its own
      rotating journaling prompt, two different rotating boxes on one page would be clutter, not
      double the value). Styled in the complement blue to visually distinguish from the orange
      prompt box.
- [x] 6 tests (`tips.py`) + 3 web tests confirming each of the three pages actually renders
      today's tip. Verified visually in a real browser.

## Extra: one-click startup ✅ done

- [x] `start.command`, double-click in Finder (or `./start.command`) to run everything: checks
      Docker is running, installs `ffmpeg` via Homebrew if missing, `docker compose up -d`,
      creates/updates the Python venv, waits for Postgres to be ready, then starts the web app.
      Safe to run repeatedly, every step is a no-op if already done.
- [x] Verified for real, twice: once with an existing `.venv` (fast path), once with `.venv`
      removed entirely to simulate a genuine first-ever run, both times ended with a real HTTP
      200 from the running server, and the printed LAN address was confirmed reachable too.

## Extra: visual redesign + upload/capture choice + delete ✅ done

- [x] Real design system in `base.html` (warm color palette, cards with shadows, better
      typography/spacing/buttons) instead of bare default styling. Header restructured to two
      rows (brand name, then nav below it) per explicit request.
- [x] New Entry page redesigned: a type selector (Text/Audio/Video) shown first, prompt still
      above it. Audio/Video sections each offer two explicit choices, "Upload from device" (no
      `capture` attribute, opens the normal file/photo picker) vs "Record now" (has `capture`,
      opens the camera/mic directly), fixing the earlier behavior where video always jumped
      straight to the camera with no upload option.
- [x] Share checkboxes on the Entries page: stacked vertically, left-aligned (was side-by-side).
- [x] `DELETE /entries/{id}` route (didn't exist before), deletes the DB row and best-effort
      cleans up its audio/video objects in MinIO too, not just the row. Delete button added to
      each entry card with a confirm() prompt before it runs. 3 new tests.
- [x] Verified live end-to-end, including catching and correctly diagnosing an apparent delete
      failure that turned out to be concurrent testing (the user testing live on their own device
      at the same time as this session) rather than a real bug, confirmed via server logs.

## Extra: `soliloquy.internal` LAN name, self-healing against IP changes ✅ done

- [x] `dnsmasq` installed via Homebrew, running as a system service (`sudo brew services start
      dnsmasq`), `/opt/homebrew/etc/dnsmasq.conf` resolves `soliloquy.internal` to this Mac's
      LAN IP, forwards everything else normally. `.internal` specifically (not `.local`, not a
      real TLD like `.com`), reserved (RFC 9476) for exactly this: a private name that will
      never collide with, or be mistaken for, a real public domain.
- [x] `scripts/update-soliloquy-dns.sh`, since the LAN IP is DHCP-assigned and changes every few
      hours/days, this detects the *current* IP and rewrites/reloads dnsmasq only when it's
      actually different from what's configured (a no-op otherwise). Runs automatically every 5
      minutes via a LaunchAgent (`~/Library/LaunchAgents/com.soliloquy.dns-update.plist`, not
      tracked in the repo since it's machine-specific, the script it calls is).
- [x] Verified for real, twice: manually forced a wrong IP into the config and confirmed the
      script corrected it, then did it again and confirmed the LaunchAgent caught it *on its own*
      via `launchctl kickstart` (not a manual script run), the self-healing actually self-heals.
- **Remaining manual step, can't be done for you**: point each phone/device's WiFi DNS setting at
  this Mac's IP (Settings → WiFi → network details → DNS → Manual) so it actually uses dnsmasq to
  resolve `soliloquy.internal`, requires the device's own settings, not something scriptable
  from here. Router-level DHCP config (to make this automatic for every device on the LAN) would
  need router admin access, which wasn't available in this session.
- **Real bug found and fixed**: `soliloquy.internal` didn't work even on this Mac at first,
  because this Mac's own network DNS was still pointed at the router, not at dnsmasq. Fixed with
  `networksetup -setdnsservers Wi-Fi 127.0.0.1`. That fix immediately broke normal internet
  browsing, though, macOS auto-rewrote `/etc/resolv.conf` to `nameserver 127.0.0.1` once the Mac's
  DNS pointed there, and dnsmasq's default behavior is to read that same file for where to
  forward everything else, creating a self-referential loop (dnsmasq asking itself, forever;
  confirmed via `dig` returning `REFUSED`). Fixed with `no-resolv` + explicit `server=1.1.1.1` /
  `server=8.8.8.8` lines in `dnsmasq.conf`, bypassing `/etc/resolv.conf` entirely. Verified both
  directions afterward: `soliloquy.internal` resolves AND real domains (`google.com`) still
  resolve, confirmed in a real browser too.
- **Note on `sudo`**: dnsmasq needs root to bind port 53 (a privileged port) and to run as a
  system-wide LaunchDaemon that starts at boot, not avoidable for a real local DNS server. The
  automatic 5-minute IP-update script normally restarts it without a password prompt (it's just
  signaling the already-loaded daemon); a full config change like this fix needed one manual
  `sudo brew services restart dnsmasq` to take effect.

## Extra: `soliloquy.local` via real mDNS + Caddy (no port, no per-device DNS) ✅ done

- **Pivoted away from `soliloquy.internal`/dnsmasq**: that approach required pointing every
  device's own DNS settings at this Mac, which was explicitly rejected. Also explicitly rejected:
  renaming the Mac itself (`ComputerName`/`LocalHostName`), this machine runs ~20 other
  apps/repos that would each need their own name, so the Mac's own identity has to stay untouched.
- [x] `scripts/soliloquy-mdns.sh` + `~/Library/LaunchAgents/com.soliloquy.mdns.plist` (not
      tracked, machine-specific), registers a real, separate mDNS service name
      (`soliloquy.local`) via `dns-sd -P`, independent of the Mac's own Bonjour hostname. Re-checks
      the LAN IP and re-registers on change, same self-healing pattern as the dnsmasq script.
      Verified `scutil --get LocalHostName` is unchanged (still `Landons-MacBook-Pro`) before and
      after.
- [x] Caddy (`brew install caddy`, `Caddyfile` at repo root + `/opt/homebrew/etc/Caddyfile`), a
      reverse proxy (not a redirect: the browser talks to Caddy on port 80 the whole time, Caddy
      forwards to the app on 8000 behind the scenes) so `http://soliloquy.local` works with no
      port typed at all.
- **Real bug found and fixed**: Caddy silently failed to bind port 80 because an unrelated,
  orphaned `docker-web-1` (nginx) container, 12 days old, from a since-deleted project directory
 , was already holding it. Per explicit instruction, stopped ALL running Docker containers, then
  brought Soliloquy's own stack back up plus Caddy; confirmed clean afterward.
- [x] Verified for real: `curl http://soliloquy.local/` (no port) → 200 with the correct page
      title, then confirmed again in an actual browser on this Mac.
- **Same manual step as `.internal` before it**: still requires each device to resolve mDNS
  (`.local`), which most phones/laptops do out of the box with zero configuration, that's the
  whole point of this pivot versus dnsmasq.

## Extra: color scheme, code refactor pass, analysis/report defaults ✅ done

- [x] Default color scheme: light diffused orange primary (`--accent`) + its true color-wheel
      complement, a muted slate blue (`--complement`), used purposefully (secondary buttons,
      tip-box vs prompt-box, "Upload from device" vs "Record now"), not a flat 50/50 split so
      orange stays the dominant note. Lives in `base.html`'s `:root` CSS variables.
- [x] Refactor pass across the whole codebase: `pyflakes`-clean, `_HttpAnalyzer` base class in
      `analyzer.py` collapsing ~90 duplicated lines across the three provider classes,
      `_entries_in_last_days()` extracted in `actions.py`, `_topics_line()` extracted in
      `report.py`, `_upload_and_transcribe_audio()` + a single `_REPORT_FORMATS` dict replacing
      three parallel dicts in `web/app.py`.
- [x] Analysis page now shows OpenRouter/Gemini/Claude API key status (masked, first 4 + last 4
      characters, never the full key, or "Not set") so it's visible at a glance without touching
      environment variables.
- [x] Report page defaults changed: 30 days (was 7), markdown format (was text).

## Extra: volume slider, manual transcript editing, better transcription ✅ done

- [x] Real `<input type="range">` volume slider on every audio/video player (previously native
      browser controls acted like a plain on/off mute toggle, not a slider).
- [x] `WhisperTranscriber`'s default model upgraded from `"base"` to `"small"` (~2GB RAM, ~3-4x
      realtime on CPU) for meaningfully better transcription accuracy.
- [x] Manual transcript editing: `store.update_transcript()` + `POST /entries/{id}/transcript` +
      inline edit/save/cancel UI on the Entries page, so a wrong word/phrase can be fixed by hand
      without needing to re-record.
- [x] **Voice isolation + loudness normalization** (`noise_reduction.py`), run on every audio/video
      upload before transcription and storage:
  - DeepFilterNet3 (a real neural network trained to separate speech from background noise, not
    just a steady-hiss filter), chosen specifically because it also handles *irregular* noise
    (wind, a dog barking, sheets moving), which a simpler ffmpeg-only filter can't.
  - ffmpeg's `speechnorm` + `alimiter` afterward, brings quiet passages up and caps everything
    just under clipping, so recordings come out consistent without ever needing to raise your
    voice.
  - **Real, non-trivial installation blocker resolved**: DeepFilterNet's native dependency
    (`deepfilterlib`) needs a Rust toolchain to build, installed via `rustup` (official installer,
    one-time). Then DeepFilterNet's own bundled `torchaudio` usage turned out to reference APIs
    (`torchaudio.backend.common.AudioMetaData`, `torchaudio.load`/`save`/`info`) that current
    torchaudio releases have removed entirely, worked around by stubbing the missing import (we
    never call DeepFilterNet's own file I/O) and doing all real audio I/O ourselves via `ffmpeg` +
    `soundfile` instead. See `noise_reduction.py`'s module docstring.
  - **Real crash found and fixed**: torch's compiled C extension segfaults if it's first
    initialized on a non-main thread. FastAPI/Starlette run regular (sync) route handlers in a
    background worker thread pool, so the first upload would reliably crash the whole process.
    Fixed with `noise_reduction.preload()`, called from the app's startup lifecycle (`_lifespan`)
    so torch loads on the main thread before any request can reach a worker thread. Also fixed the
    test fixture, which was creating `TestClient(app)` without a `with` block, meaning FastAPI's
    startup/shutdown lifecycle (and therefore `preload()`) never ran in tests at all, masking the
    bug there.
  - Shared `ffmpeg_utils.py` extracted (`run_ffmpeg()`, `FfmpegNotFoundError`) so `video.py` and
    `noise_reduction.py` don't each duplicate "is ffmpeg on PATH / did it exit non-zero" handling.
  - Verified live against the real running server (not just tests): real audio upload → denoised,
    normalized, transcribed, stored, twice in a row, clean shutdown, no crash.
  - **Not yet validated against a real human voice recording**, testing so far used macOS's
    built-in text-to-speech (`say`) mixed with synthetic noise, since that's what's scriptable
    here. DeepFilterNet is trained on real human speech and may behave differently (likely
    better) on an actual recorded voice than on TTS audio. Worth a real test recording once you're
    using the app normally.

## Extra: local automated backups ✅ done

- [x] `scripts/backup.sh`, daily backup of both real data stores: `pg_dump` of Postgres (all
      entries/transcripts/sharing flags) and a full `rclone copy` of the MinIO bucket (every
      audio/video file), into a timestamped folder under `~/soliloquy-backups/`. Keeps the last 14
      days, prunes older ones automatically each run.
- [x] `~/Library/LaunchAgents/com.soliloquy.backup.plist` (not tracked, machine-specific, same
      pattern as the mDNS/dnsmasq LaunchAgents), runs the script daily at 3am via launchd. If the
      Mac is asleep at 3am, launchd runs it as soon as the Mac next wakes, rather than skipping it.
- [x] Verified for real: ran it against the actual live database and MinIO bucket, not a test
      instance, produced a real `postgres.sql.gz` (containing an actual test entry, confirmed by
      grepping the decompressed dump) and a real `minio-objects/` folder (230 real files, 5.9MB).
- **Setup needed on any other machine this ever runs from** (not automatable from here): `brew
  install rclone`, then an `rclone.conf` pointing at the local MinIO, see README's Backups
  section for the exact config. Machine-specific credentials file, not tracked in git.
- **What this does and doesn't protect against**: protects against accidental deletes, a bad
  `DELETE`, or Docker/Postgres corruption, there's always a second copy on disk. Does **not**
  protect against this Mac's disk itself failing, since both the original and the backup currently
  live on the same physical disk. See README's Backups section for free offsite-redundancy options
  to close that gap, and the tradeoffs between them.

## Extra: password gate, automatic dark mode + PWA, container-first setup ✅ done

- [x] `src/soliloquy/auth.py`: single-user password gate, off by default (no `AUTH_PASSWORD` set
      means every route stays open, so a fresh clone with no `.env` still runs with zero setup),
      a startup log line either way (`describe_auth_mode()`, same pattern as
      `deployment_mode.py`'s informational print). One password compared with
      `hmac.compare_digest` (constant-time, not `==`) against `$AUTH_PASSWORD`, a Starlette
      `SessionMiddleware`-backed signed cookie (`itsdangerous`, added to the `web` extra), and a
      small in-memory lockout (5 wrong attempts, 30s) -- no accounts, no third-party identity
      provider, nothing paid, entirely self-contained.
  - **Non-obvious ordering trap, worth flagging for next time this changes**: Starlette's
    `add_middleware()` prepends to the middleware list, so the middleware added *last* ends up
    *outermost* (runs first on every request). Registering `AuthMiddleware` before
    `SessionMiddleware` would put auth outside the session layer, and `AuthMiddleware.dispatch()`
    reads `request.session` -- every request would 500 with "SessionMiddleware must be installed
    to access request.session." Confirmed by reading Starlette's own `add_middleware`/
    `build_middleware_stack` source directly rather than guessing, then registered
    `SessionMiddleware` second on purpose (see the comment above the registration in
    `web/app.py`), so this was never actually broken live, just a trap worth documenting since
    it's easy to get backwards.
  - `/healthz` added, deliberately exempt from the auth gate, so the container `HEALTHCHECK`
    (which never carries a session cookie) doesn't start reporting a correctly-locked-down app as
    unhealthy the moment `AUTH_PASSWORD` gets set. `Dockerfile`'s `HEALTHCHECK` now points at it
    instead of `/entries`.
  - 11 new tests (`tests/test_auth.py`): auth disabled is a no-op, enabled redirects to `/login`,
    `/healthz` bypasses it, wrong password rejected, correct password unlocks the session, an
    open-redirect attempt via `?next=` is rejected in favor of `/`, logout clears the session,
    repeated wrong attempts trigger the lockout and it expires on schedule. Full suite (164
    tests total now) verified green, and verified live against a real running server with
    `AUTH_PASSWORD` set (curl + a real browser): `/` redirects to `/login`, wrong password
    rejected, correct password lands on Entries with a working "Log out" button, `/healthz`
    reachable throughout with no cookie at all.
- [x] Automatic dark mode: `static/style.css` (extracted from `base.html`'s old inline `<style>`
      block, so `login.html` -- which can't extend `base.html`'s nav, there's no session yet --
      can share it) defines the same CSS variable names twice, once under plain `:root` (light,
      the default) and once under `@media (prefers-color-scheme: dark)`. No manual toggle, by
      request -- it follows whatever the device is already set to, the same way the rest of a
      phone's apps do.
- [x] Default light palette redone as diffused orange **with a soft yellow sunburst glow** behind
      the header (two radial gradients fixed to the top corners, dimmed further in the dark
      palette so it reads as warmth, not a lighting bug), per explicit request. Verified visually
      in a real browser in both palettes (a temporary static preview page forced the light
      variables regardless of the OS's actual dark-mode setting, to check both without needing to
      flip a system preference back and forth); the real login/entries pages were also checked
      live against the OS's actual (dark) preference.
- [x] Installable as a PWA: `static/manifest.json` + a generated icon set (`icon.svg`, a warm
      sunburst mark with a soundwave glyph, rendered to `icon-192/512.png`, `apple-touch-icon.png`,
      and `favicon.ico` via `rsvg-convert`), linked from `base.html`'s `<head>` (`manifest`,
      `icon`, `apple-touch-icon`, `theme-color`). "Add to Home Screen" on a phone now gives
      Soliloquy its own icon and a full-screen launch, no browser chrome.
- [x] Container-first: `docker-compose.yml` gained an `app` service (dev target, hot-reloaded via
      a bind mount) as a fourth first-class member alongside Postgres/MinIO/Mosquitto, so
      `docker compose up -d --build` (after `cp .env.example .env`) is now the entire setup, no
      host Python/ffmpeg/Rust needed. `docker-compose.app.yml` (previously *the* way to run the
      app in a container, explicitly flagged as "not the normal dev loop") is repurposed as an
      overlay specifically for testing the shipped `release` image before it goes anywhere real,
      the same role it always had, just no longer the only way to get the app into a container at
      all.
  - **Real gap found and fixed**: `Dockerfile`'s `base` stage installed `build-essential` but not
    a Rust toolchain, and `deepfilterlib` (the `transcribe` extra's native dependency, see
    `noise_reduction.py`) needs one to compile -- meaning `docker build`/`docker compose up
    --build` would have failed partway through `pip install .[transcribe]` in a plain container,
    the real reason the app container wasn't the primary documented path before now. Fixed by
    installing rustup (matching the README's own host-install instructions, not Debian's older
    packaged `cargo`/`rustc`) in the `base` stage, then removing `~/.cargo`/`~/.rustup` (~1GB)
    again in both the `dev` and `release` stages right after their `pip install`, since nothing at
    runtime needs the toolchain, only the native build step does.
  - **Second real gap found and fixed, this one only surfaces at runtime, not build time**:
    DeepFilterNet's own `df.logger.init_logger()` shells out to `git rev-parse` on every startup
    to log its own commit hash, and raised `FileNotFoundError` straight through
    `noise_reduction.preload()` (called from the app's `lifespan`, so this crashed the whole
    container on boot) the first time the built image actually ran, `git` was never installed in
    the base image. Never surfaces on a host install since macOS ships `git` already, only found
    by actually starting the container, not by the build succeeding. Fixed with one more package
    in the same `apt-get install` line. (DeepFilterNet's own code already catches the "installed
    but not inside a git repo" case, `CalledProcessError`, gracefully, logging `None` and moving
    on, confirmed by reading `df/utils.py` directly -- it only ever needed `git` to *exist*, not
    for `/app` to actually be a git checkout.)
  - **Third real gap found while first building the image, unrelated to Rust**: PyPI's default
    `torch` wheel for Linux pulls in a full CUDA stack (`nvidia-cublas`, `nvidia-cudnn`,
    `nvidia-nccl`, `triton`, ...) as dependencies even though nothing here has, or needs, a GPU,
    caught mid-build the first time (multiple gigabytes of downloads before the fix, watched it
    happen, didn't just assume it would be a problem). Fixed by installing the CPU-only
    `torch`/`torchaudio` build (`--index-url https://download.pytorch.org/whl/cpu`) in the `base`
    stage before either extras install, confirmed on the rebuild that `pip install .[transcribe]`
    then reports `torch>=2.0` "already satisfied" with the CPU build instead of reaching for the
    GPU wheels.
  - `.dockerignore` added (didn't exist before) -- without it, every `docker build` sent the
    *entire* repo as build context to the daemon, `.venv/` alone can be gigabytes once
    torch/deepfilternet are installed on the host, even though the `Dockerfile` only ever `COPY`s
    `pyproject.toml` and `src/`.
  - **Fourth gap, in `start.command`, not `Dockerfile`**: `start.command`'s own `docker compose up
    -d` (no service list) would now also try to build and start the new `app` service, competing
    for port 8000 with that same script's later `python -m soliloquy.web`, and would fail outright
    for anyone who hadn't created a `.env` yet (the `app` service's `env_file`). Fixed by naming
    the three infra services explicitly (`docker compose up -d postgres minio mosquitto`);
    verified the fixed command only touches those three, `app` untouched, against the real
    running stack.
  - `.env.example`/`.env.dev`/`.env.staging`/`.env.prod` all gained `AUTH_PASSWORD`/
    `SESSION_SECRET_KEY` entries (blank in `.env.example`/`.env.dev`, `CHANGEME` placeholders in
    `.env.staging`/`.env.prod`, matching how every other real-credential placeholder in those two
    files is already marked).
  - **Verified live, the real way, not just "the build succeeded"**: `docker compose up -d` (the
    exact command README's Quick start now leads with) brings up all four containers, `app`
    reaches Docker's own `(healthy)` status against `/healthz`. Hit the running containerized app
    directly over HTTP: created a real entry (`POST /entries`), listed it back (`GET /entries`),
    fetched `/static/manifest.json` and an icon, deleted the test entry again. Then set
    `AUTH_PASSWORD` in `.env`, recreated just the `app` container, confirmed `/` now 303s to
    `/login` while `/healthz` still bypasses it, then reset `.env` back to blank/default before
    finishing. `docker compose config` (both the plain file and with `docker-compose.app.yml`
    layered on top) also checked directly to confirm the service-DNS-name overrides, the
    `.env`/`.env.staging` `env_file` swap between the two, and the bind-mount removal in the
    overlay all resolve the way the comments say they do.

## Extra: all 18 FEATURE_IDEAS.md items, plus the makeItSoNumberOne side of 2 of them ✅ done

Everything that was in `FEATURE_IDEAS.md` is built now; that file is back to empty. Grouped by
area, not the original numbering (see git history for the original list if it's ever useful).

- [x] **Full-text search + tags + speaker.** `entries` gained `tags TEXT[]`, `speaker TEXT`, and
      a `search_vector TSVECTOR` column (GIN-indexed), all via the existing `_ensure_columns()`
      migration-stopgap pattern. `EntryStore.search()`, `.by_tag()`, `.all_tags()`. Entries page
      gained a search box + tag filter dropdown + inline tag editor (comma-separated text input,
      same interaction pattern as transcript editing). Verified live in a browser in both
      light/dark: searched "sister" against two real entries, got the one real match; tag chips
      link back to `/?tag=...`.
- [x] **Encrypt transcripts at rest, without breaking search.** These two were flagged as in
      tension (search needs plaintext to index) and resolved the standard way: `search_vector` is
      built from plaintext at write time, BEFORE the `transcript` column itself gets encrypted
      (Fernet, `$TRANSCRIPT_ENCRYPTION_KEY`, off by default like every other protective-but-not-
      free-to-set-up feature here). An `"enc1:"` prefix marks which rows are ciphertext so mixed
      encrypted/plaintext data (from turning the key on partway through this journal's life)
      reads back correctly either way; reading an encrypted row with no/the-wrong key raises a
      clear `RuntimeError`, not silent garbage. `scripts/encrypt_existing_transcripts.py`,
      one-time, explicit, backfills existing plaintext rows -- NOT run automatically on startup,
      re-encrypting real journal entries is a deliberate action, not a schema-check side effect.
      Genuinely verified, not assumed: encrypted a real entry, confirmed the raw `transcript`
      column is unreadable ciphertext, confirmed `search()` still finds it by content, confirmed
      the backfill script actually encrypts existing plaintext rows in place.
- [x] **"On this day" + a streak line.** `EntryStore.on_this_day()` (same month/day, any earlier
      year) shown on the unfiltered Entries page. `actions.journaling_streak()` ("journaled N of
      the last 7 days") shown above the search box. Both skip rendering during an active
      search/tag filter -- showing "on this day" underneath search results doesn't make sense.
- [x] **Mood trend chart.** `AnalysisResult` gained an optional `mood_score` (1-10, the analyzer's
      own rough read, asked for in the same prompt, NOT required in the response so a provider
      that ignores the field doesn't fail the whole analysis over a chart data point).
      `analysis_snapshots` gained a nullable `mood_score` column via the same migration pattern.
      `mood_chart.py` renders a plain inline SVG polyline (no charting library, matches this app's
      "no separate JS build" stance) on the Analysis page, only when there are 2+ scored
      snapshots. Verified live: seeded 5 real snapshots with real scores, the chart rendered
      correctly in a real browser, tracking the shape of the seeded data.
- [x] **Per-audience analysis instructions.** `actions.AUDIENCE_INSTRUCTIONS` (empty for "self",
      real framing text for "partner"/"provider") appended to the analyzer prompt via a new
      `instruction` param threaded through `Analyzer.analyze()` and every implementation
      (`_HttpAnalyzer`, `FallbackAnalyzer`) -- one new optional param, not a second prompt-
      building path.
- [x] **Saved reports + expiring signed share links.** New `report_store.py`
      (`SavedReport`/`SavedReportStore`, one Postgres table, always markdown, "the most natural
      format for actually handing to a therapist" per this README) and signed tokens
      (`itsdangerous`, reusing `$SESSION_SECRET_KEY` with a distinct `salt` so a share link and a
      login session can never be confused for each other even off the same secret). `/reports/save`
      (manual, any audience/day-range), `/reports/saved` (list + "Get share link"),
      `/reports/shared/{token}` (deliberately exempt from `AuthMiddleware`, see `auth.py` -- the
      whole point is a link someone with no account here can open). A scheduled monthly job
      (`run_scheduled_monthly_report`, `CronTrigger(day=1, hour=3)`, "self", 30 days) saves one
      automatically. Verified live: saved a report, minted a share link, opened it with the
      session cookie cleared entirely (confirms the auth-exemption actually works, not just that
      the route exists), got the real content back.
- [x] **"Next analysis run" indicator.** `_lifespan` stashes the running `BackgroundScheduler` on
      `app.state.scheduler`; `/analysis` reads its `analysis` job's `next_run_time`.
- [x] **Punctuation/paragraph cleanup after transcription.** `transcript_cleanup.py`, rule-based
      (capitalize sentence starts, add missing terminal punctuation, break into paragraphs every 4
      sentences), deliberately NOT another AI call, applied only to transcribed (audio/video)
      entries, never to typed ones (which are already however the person chose to format them).
      Broke 3 existing tests' exact-transcript assertions in the expected way (raw fake-Whisper
      output vs. cleaned output) -- fixed those assertions to match the new, correct behavior.
- [x] **Opt-in media retention.** `$MEDIA_RETENTION_DAYS` (unset -- the default -- means keep
      forever). `scheduler.run_media_retention_cleanup()`, a daily job when the env var is set,
      deletes audio/video from object storage for entries older than the cutoff and clears their
      `audio_path`/`video_path`, transcript and everything else untouched. Verified against real
      MinIO: uploaded a real file, ran the job, confirmed it's actually gone from object storage
      (not just that the DB row changed) and that a recent entry's media is left alone.
- [x] **Desktop notification on a new snapshot.** `notify.py`, `osascript`, macOS only, a plain
      no-op everywhere else including inside the Linux container (no `osascript` on `PATH` there)
      -- never raises, same "a notification failing shouldn't crash the job" reasoning as the rest
      of `scheduler.py`. AppleScript string escaping tested directly (an AI-generated summary
      landing in a shell-adjacent string is exactly the kind of thing worth escaping correctly).
- [x] **MQTT: ack, query, append, durable session.** `mqtt_bridge.py`: writes to `$MQTT_TOPIC` now
      get a real ack published to `$MQTT_TOPIC/ack` (`{"status": "ok", "entry_id", "appended"}` or
      `{"status": "error", "reason"}`); a `{"type": "append"}` message merges into today's most
      recent entry (same speaker, within the last hour) via the new
      `actions.append_or_add_entry()`; a new `$MQTT_TOPIC/query` topic (`{"days": N}`) runs a real
      analysis and publishes a summary back on `$MQTT_TOPIC/query/response`, day-count only (not
      natural language like "last week") since makeItSoNumberOne's own AI is what's already
      turning a spoken question into structured params before publishing, same division of labor
      as the write topic relaying an already-transcribed string. The listener itself now connects
      with a fixed `client_id` + `clean_session=False` and subscribes at QoS 1, so Mosquitto
      durably queues messages for it while it's offline instead of dropping them.
  - **Real, load-bearing verification, not just unit tests against a fake broker**: started the
    actual listener against the real Mosquitto container, published a real message, confirmed a
    real ack came back; published an append message, confirmed the merge; then the actual
    durability claim specifically -- STOPPED the listener, published a real QoS-1 message while it
    was down, confirmed nothing happened yet, RESTARTED the listener, confirmed the queued message
    was delivered on reconnect. This is the one behavior that's easy to convince yourself works
    from reading the code and be wrong about, so it got run for real rather than assumed.
- [x] **Both makeItSoNumberOne-side items, not skipped.** FEATURE_IDEAS.md's items 4 (retry queue)
      and 5 (multi-speaker) both needed real work in `landonkea-makeItSoNumberOne`, a separate
      repo, done this session too, not deferred:
  - `journal_entry_plugin.py` now publishes at QoS 1 (was QoS 0 -- without this fix, the durable
    subscriber session above literally couldn't have helped, Mosquitto only queues QoS>=1
    messages for an offline subscriber), waits briefly for Soliloquy's ack and reports what
    ACTUALLY happened back to the user instead of just "the publish call didn't raise," and now
    buffers an entry to a local file (`journal_pending.jsonl`, gitignored) when the BROKER itself
    is unreachable (not just Soliloquy -- durable sessions can't help with that case, the
    connection never even exists), flushing it automatically the next time the action runs.
  - `core/voice_id.py` (new): pure-Python voice identification, no numpy/ML framework, matching
    this codebase's existing hand-rolled-DSP style (see `core/audio.py`). Pitch via autocorrelation
    + zero-crossing rate + RMS energy, averaged into a small per-person fingerprint.
    `enroll_voice_plugin.py` (new) records a sample and saves a profile; `make_it_so.py`'s main
    loop identifies the speaker on every turn and makes it available to other plugins via
    `config["_identified_speaker"]`, which `journal_entry_plugin.py` now attaches to the MQTT
    payload when present. Explicitly NOT a neural embedding model -- genuinely worse at telling
    apart two similar-pitched voices than something like Resemblyzer would be, documented plainly
    in `voice_id.py`'s own module docstring, not oversold.
  - **Real, cross-repo, end-to-end verification**: `pip install paho-mqtt` into
    makeItSoNumberOne's venv (still not installed there by default, same known gap as before,
    documented in that repo's own history), enrolled a real (synthesized) voice profile,
    identified it, ran the ACTUAL `JournalEntryPlugin.execute()` (not a mock) against the real
    running Soliloquy MQTT listener, confirmed the entry landed in Soliloquy's real database with
    the correct `speaker` attached, then did the same for an append-type message and confirmed the
    merge. `python3 -m unittest discover -s tests` run in makeItSoNumberOne afterward (273 tests,
    1 pre-existing unrelated failure -- `requests` isn't installed in that venv either, nothing to
    do with this work) to confirm nothing broke.

## Right after this

- [ ] Test the video-capture flow from a real phone (not just desktop browser + synthesized test
      video), this is the one part of the original ask ("record with the phone's camera app,
      then hand the file to Soliloquy") not yet verified on an actual phone (blocked on the user:
      needs a real phone in hand, nothing left to code here)
- [ ] Record a real journal entry with your actual voice to hear how DeepFilterNet + normalization
      actually sounds, everything so far has been verified with synthesized TTS audio, not a real
      human recording (blocked on the user: needs your actual voice, nothing left to code here)
- [ ] Set a real `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`/`GEMINI_API_KEY` to verify the
      Report/Analysis happy path end-to-end (currently only the error path is verified here)
      (blocked on the user: needs a real key in `.env`, nothing left to code here)
- [x] Decide on and implement real LAN/cloud security hardening (see above), `deployment_mode.py`
      only describes the situation today, it doesn't yet change any actual behavior. **Partially
      implemented**: `docker-compose.yml` now binds Postgres (5433) and MinIO (9000/9001) to
      `127.0.0.1` instead of `0.0.0.0`, closing the dev-credential exposure on those two, verified
      live (`docker compose up -d`, confirmed via `docker port` that both now show
      `127.0.0.1:...->...` while still reachable from the web app on localhost, then `docker
      compose down`) and against the full test suite (153 passed). Mosquitto is deliberately left
      on `0.0.0.0`: makeItSoNumberOne's `journal_entry` plugin is meant to publish to it from a
      separate device on the LAN, so locking it to localhost would break that feature outright,
      whether that's the actual setup is a call only you can make. Real auth/TLS on Mosquitto (see
      `mosquitto/mosquitto.conf`), and whether to also add real (non-dev) Postgres/MinIO
      credentials, remain open, both need you to pick actual values, not more code (blocked on the
      user for the rest)

## After that (not started, no immediate plan)

- [ ] Move from self-hosted Postgres/MinIO to managed cloud (Supabase/Neon + Cloudflare R2) once
      the self-hosted version has been used for real for a while, same interfaces, config change
      (blocked on the user: needs real usage first, then a provider choice and real credentials,
      not more code right now)
- [ ] Face/expression analysis of stored video (a genuinely new analyzer, not a re-use of the
      transcript pipeline) (blocked on the user: a new feature needing product scoping before any
      code, not started)
- [ ] Native mobile app(s), once the web app has proven the product is worth the extra platform
      (blocked on the user: a product decision on whether to build this at all, not started)
      work, would be a new client of the same backend API, not a rewrite
