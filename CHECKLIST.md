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

## Stage 4 — Web GUI

- [ ] Jinja2 templates, no separate JS build: entries list (with audio/video playback), new-entry
      page (text/audio/video upload — the video `<input>` with `capture` is what makes "record
      with the phone's camera, then upload" work with zero extra code), share toggles, report
      generator
- [ ] Opened and used in a real browser (desktop + phone) — add a text entry, upload real audio,
      upload a real video from a phone, mark something shareable, generate a report

## After that (not started, no immediate plan)

- [ ] Move from self-hosted Postgres/MinIO to managed cloud (Supabase/Neon + Cloudflare R2) once
      the self-hosted version has been used for real for a while — same interfaces, config change
- [ ] MQTT bridge to `makeItSoNumberOne` (voice-triggered journal entries)
- [ ] Face/expression analysis of stored video (a genuinely new analyzer, not a re-use of the
      transcript pipeline)
- [ ] Native mobile app(s), once the web app has proven the product is worth the extra platform
      work — would be a new client of the same backend API, not a rewrite
