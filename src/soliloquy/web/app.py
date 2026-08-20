# ───────────────────────────────────────────────────────────────────
# web/app.py, the HTTP API, a thin layer over the existing package
# ───────────────────────────────────────────────────────────────────
# This does NOT duplicate business logic that already lives in
# actions.py -- every route calls the exact same functions the
# scheduled analysis job and the MQTT bridge use (add_entry,
# list_entries, report_range, EntryStore.update_sharing). The only
# genuinely new assembly logic here is for audio/video uploads,
# because the web app's storage target (object storage, addressed by
# key) differs from a local path -- see post_audio_entry().
#
# Keeping real logic here (not in a browser-side JS framework) is
# deliberate: a future native app becomes a new client of THIS API,
# not a second implementation of the same rules.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os
import secrets
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import auth
from ..actions import AUDIENCES, DEFAULT_DATABASE_URL, add_entry, journaling_streak, list_entries, on_this_day, report_range
from ..analysis_store import AnalysisSnapshotStore
from ..analyzer import NoEntriesError, get_default_analyzer
from ..deployment_mode import describe_deployment_mode
from ..entry import Entry
from ..mood_chart import render_mood_trend_svg
from ..mqtt_bridge import start_mqtt_listener
from ..object_storage import ObjectStore
from ..prompts import get_daily_prompt
from ..tips import get_daily_tip
from ..report import FORMATS, build_report_content, format_html, format_markdown, format_pdf, format_text
from ..report_store import DEFAULT_SHARE_LINK_DAYS, SavedReport, SavedReportStore, make_share_token, resolve_share_token
from ..scheduler import start_scheduler
from ..storage import EntryStore
from .. import noise_reduction
from ..noise_reduction import isolate_voice_and_normalize
from ..transcriber import WhisperTranscriber
from ..transcript_cleanup import clean_transcript
from ..video import extract_audio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    print(describe_deployment_mode(), flush=True)
    print(auth.describe_auth_mode(), flush=True)

    # Must happen on the main thread, before any request can reach a
    # worker thread -- see noise_reduction.preload()'s docstring.
    noise_reduction.preload()

    # Both disabled in tests (see tests/test_web.py) so the test suite
    # doesn't spin up a real background timer/MQTT connection against
    # the test database on every TestClient instantiation.
    scheduler = None
    if os.environ.get("SOLILOQUY_DISABLE_SCHEDULER") != "1":
        scheduler = start_scheduler()
    # Stashed on app.state (not just this local var) so the /analysis
    # route -- which runs long after lifespan's own scope has moved on
    # to `yield` -- can still ask it "when's the next run" (see
    # analysis_page's next_run_at).
    app.state.scheduler = scheduler

    mqtt_client = None
    if os.environ.get("SOLILOQUY_DISABLE_MQTT") != "1":
        mqtt_client = start_mqtt_listener()

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    if mqtt_client is not None:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


app = FastAPI(title="Soliloquy", lifespan=_lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# Registered in this order deliberately, not the order they run in --
# Starlette's add_middleware() prepends, so the LAST one added ends up
# OUTERMOST (runs first on every request). AuthMiddleware.dispatch()
# reads request.session, which only exists once SessionMiddleware has
# run, so SessionMiddleware has to be the outer one, meaning it has to
# be added second. Get this backwards and every request 500s with
# "SessionMiddleware must be installed to access request.session".
#
# SessionMiddleware itself is unconditional (request.session has to
# exist for base.html's logout-button check and the login route either
# way); the actual gate is AuthMiddleware, which no-ops entirely when
# AUTH_PASSWORD isn't set -- see auth.py. $SESSION_SECRET_KEY should be
# a real fixed value in .env so sessions survive a restart; falling
# back to a random one here just means "everyone's logged out next
# restart" instead of refusing to start over a missing dev convenience.
app.add_middleware(auth.AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32),
    same_site="lax",
)


def _masked_key(value: str) -> str:
    # Shows just enough to confirm "yes, something is set" without ever
    # putting the real secret in rendered HTML -- these are read-only
    # status indicators, not an editable form (keys are only ever set
    # via the environment/.env, never stored in the database).
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def _analyzer_key_status() -> list[dict]:
    return [
        {"label": "OpenRouter", "env_var": "OPENROUTER_API_KEY", "masked": _masked_key(os.environ.get("OPENROUTER_API_KEY", ""))},
        {"label": "Gemini", "env_var": "GEMINI_API_KEY", "masked": _masked_key(os.environ.get("GEMINI_API_KEY", ""))},
        {"label": "Claude (optional)", "env_var": "ANTHROPIC_API_KEY", "masked": _masked_key(os.environ.get("ANTHROPIC_API_KEY", ""))},
    ]


def get_analysis_store():
    store = AnalysisSnapshotStore(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    try:
        yield store
    finally:
        store.close()


def get_saved_report_store():
    store = SavedReportStore(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    try:
        yield store
    finally:
        store.close()

_object_store: Optional[ObjectStore] = None


def get_object_store() -> ObjectStore:
    # A single shared client -- boto3's S3 client is safe for concurrent
    # use, and reusing it avoids re-checking the bucket exists on every
    # single request.
    global _object_store
    if _object_store is None:
        _object_store = ObjectStore()
    return _object_store


def get_store():
    # A fresh connection per request, same as `with EntryStore(...) as
    # store:` in cli.py -- psycopg connections aren't safe to share
    # across concurrently-handled requests.
    store = EntryStore(
        os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        encryption_key=os.environ.get("TRANSCRIPT_ENCRYPTION_KEY") or None,
    )
    try:
        yield store
    finally:
        store.close()


def _entry_to_dict(entry: Entry) -> dict:
    return {
        "id": entry.id,
        "created_at": entry.created_at.isoformat(),
        "transcript": entry.transcript,
        "audio_path": entry.audio_path,
        "video_path": entry.video_path,
        "shareable_with_partner": entry.shareable_with_partner,
        "shareable_with_provider": entry.shareable_with_provider,
        "tags": entry.tags,
        "speaker": entry.speaker,
        "word_count": entry.word_count,
    }


def _save_upload_to_temp(upload: UploadFile, default_suffix: str) -> str:
    suffix = Path(upload.filename or "").suffix or default_suffix
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(upload.file.read())
    except Exception:
        os.remove(path)
        raise
    return path


def _upload_and_transcribe_audio(object_store: ObjectStore, audio_path: str) -> tuple[str, str]:
    """Shared by post_audio_entry and post_video_entry (whose extracted
    audio track goes through the exact same store-then-transcribe
    step) -- returns (object storage key, transcript).

    Runs voice isolation + loudness normalization first, so both the
    stored audio and the transcript come from the cleaned-up version,
    not the raw upload."""
    cleaned_path = f"{audio_path}.cleaned.wav"
    isolate_voice_and_normalize(audio_path, cleaned_path)
    try:
        transcript = clean_transcript(WhisperTranscriber().transcribe(cleaned_path))
        key = object_store.upload_file(cleaned_path, f"audio/{uuid.uuid4()}.wav")
    finally:
        os.remove(cleaned_path)
    return key, transcript


@app.get("/entries")
def get_entries(store: EntryStore = Depends(get_store)):
    return [_entry_to_dict(e) for e in list_entries(store)]


@app.post("/entries")
def post_entry(text: str = Form(...), store: EntryStore = Depends(get_store)):
    entry = add_entry(store, text)
    return _entry_to_dict(entry)


@app.post("/entries/audio")
def post_audio_entry(
    file: UploadFile = File(...),
    store: EntryStore = Depends(get_store),
    object_store: ObjectStore = Depends(get_object_store),
):
    tmp_path = _save_upload_to_temp(file, ".wav")
    try:
        key, transcript = _upload_and_transcribe_audio(object_store, tmp_path)
    finally:
        os.remove(tmp_path)

    entry = Entry(transcript=transcript, audio_path=key)
    store.add(entry)
    return _entry_to_dict(entry)


@app.post("/entries/video")
def post_video_entry(
    file: UploadFile = File(...),
    store: EntryStore = Depends(get_store),
    object_store: ObjectStore = Depends(get_object_store),
):
    """Upload video -> store the video as-is -> extract its audio track
    -> store that too -> transcribe the extracted audio. The Entry ends
    up with BOTH video_path and audio_path set, so the existing
    transcript/analysis pipeline works completely unchanged (it only
    ever reads the transcript), and a future face/expression analyzer
    can consume video_path independently -- see entry.py's docstring."""
    video_tmp_path = _save_upload_to_temp(file, ".mp4")
    audio_tmp_path = video_tmp_path + ".wav"
    try:
        video_key = object_store.upload_file(video_tmp_path, f"video/{uuid.uuid4()}{Path(video_tmp_path).suffix}")

        try:
            extract_audio(video_tmp_path, audio_tmp_path)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not extract audio from video: {exc}")

        audio_key, transcript = _upload_and_transcribe_audio(object_store, audio_tmp_path)
    finally:
        os.remove(video_tmp_path)
        if os.path.exists(audio_tmp_path):
            os.remove(audio_tmp_path)

    entry = Entry(transcript=transcript, audio_path=audio_key, video_path=video_key)
    store.add(entry)
    return _entry_to_dict(entry)


@app.post("/entries/{entry_id}/share")
def share_entry(
    entry_id: str,
    partner: Optional[bool] = Form(None),
    provider: Optional[bool] = Form(None),
    store: EntryStore = Depends(get_store),
):
    updated = store.update_sharing(
        entry_id, shareable_with_partner=partner, shareable_with_provider=provider
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"No entry found with id {entry_id}")
    return _entry_to_dict(store.get(entry_id))


@app.post("/entries/{entry_id}/tags")
def update_tags(
    entry_id: str,
    tags: str = Form(""),
    store: EntryStore = Depends(get_store),
):
    """`tags` is a comma-separated string from the entries page's plain
    text input -- no separate tag-picker widget, splitting/trimming
    happens here rather than pushing that parsing into JS."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    updated = store.update_tags(entry_id, tag_list)
    if not updated:
        raise HTTPException(status_code=404, detail=f"No entry found with id {entry_id}")
    return _entry_to_dict(store.get(entry_id))


@app.post("/entries/{entry_id}/transcript")
def update_transcript(
    entry_id: str,
    transcript: str = Form(...),
    store: EntryStore = Depends(get_store),
):
    updated = store.update_transcript(entry_id, transcript)
    if not updated:
        raise HTTPException(status_code=404, detail=f"No entry found with id {entry_id}")
    return _entry_to_dict(store.get(entry_id))


@app.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str,
    store: EntryStore = Depends(get_store),
    object_store: ObjectStore = Depends(get_object_store),
):
    entry = store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No entry found with id {entry_id}")

    # Delete the DB row first -- if object storage cleanup fails
    # partway through, an orphaned file in MinIO is a much smaller
    # problem than an entry that still shows up but whose media
    # deletion silently failed.
    store.delete(entry_id)
    for key in (entry.audio_path, entry.video_path):
        if key:
            try:
                object_store.delete(key)
            except Exception as exc:
                # Best-effort -- the entry itself is already gone; an
                # orphaned object in MinIO is a real but minor cleanup
                # gap, not worth failing the whole delete over.
                logger.warning("Could not delete object storage key %s for entry %s: %s", key, entry_id, exc)
    return {"ok": True}


# (renderer, media type, file extension) per format -- one table
# instead of three parallel dicts that could drift out of sync.
_REPORT_FORMATS = {
    "text": (format_text, "text/plain", "txt"),
    "markdown": (format_markdown, "text/markdown", "md"),
    "html": (format_html, "text/html", "html"),
    "pdf": (format_pdf, "application/pdf", "pdf"),
}


@app.post("/reports")
def post_report(
    days: int = Form(7),
    audience: str = Form("self"),
    format: str = Form("text"),
    store: EntryStore = Depends(get_store),
):
    if audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail=f"Unknown audience {audience!r}, must be one of {AUDIENCES}")
    if format not in FORMATS:
        raise HTTPException(status_code=400, detail=f"Unknown format {format!r}, must be one of {FORMATS}")
    try:
        result, entries = report_range(store, get_default_analyzer(), days, audience)
    except NoEntriesError:
        raise HTTPException(
            status_code=404, detail=f"No entries for audience '{audience}' in the last {days} days."
        )
    except RuntimeError as exc:
        # Every Analyzer provider raises RuntimeError for a missing API
        # key or a failed API call -- surface that real message instead
        # of a generic 500, so "you forgot to set an API key" is
        # actually visible instead of an opaque Internal Server Error.
        raise HTTPException(status_code=502, detail=str(exc))

    content = build_report_content(result, entries, audience, days)
    render, media_type, extension = _REPORT_FORMATS[format]
    return Response(
        content=render(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="soliloquy-report.{extension}"'},
    )


@app.get("/media/{key:path}")
def get_media(key: str, object_store: ObjectStore = Depends(get_object_store)):
    try:
        local_path = object_store.download_to_temp(key)
    except Exception:
        raise HTTPException(status_code=404, detail=f"No media found for key {key!r}")

    # Upload/transcription/extraction itself is format-agnostic --
    # ffmpeg and faster-whisper's PyAV-based decoding both detect the
    # actual container/codec from file contents, not the extension
    # (verified directly: a real .m4a transcribes and a real .mkv's
    # audio extracts with zero code changes). This map only controls
    # the Content-Type header for browser playback -- an unlisted
    # extension still uploads/transcribes/extracts fine, it just falls
    # back to a generic type here. Some containers (.mkv especially)
    # won't play back in most browsers regardless of a correct
    # Content-Type -- that's a browser codec-support limitation, not
    # something fixable from this side.
    media_type = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    }.get(local_path.suffix, "application/octet-stream")

    def stream():
        try:
            with open(local_path, "rb") as f:
                yield from f
        finally:
            os.remove(local_path)

    return StreamingResponse(stream(), media_type=media_type)


@app.get("/healthz")
def healthz():
    # Deliberately exempt from AuthMiddleware (see auth.py) -- the
    # Dockerfile's HEALTHCHECK curls this with no session cookie, and
    # a login redirect there would make Docker think a healthy
    # container was unhealthy the moment AUTH_PASSWORD gets set.
    return {"status": "ok"}


def _safe_next(next_path: str) -> str:
    # Only ever redirect somewhere inside this app. "//evil.com" is a
    # valid-looking relative URL to a browser (protocol-relative), so
    # a single leading "/" isn't enough on its own to rule out an open
    # redirect through a crafted ?next= value.
    if next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


# ── Auth -- see auth.py for why this exists and how it's gated.

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...), next: str = Form("/")):
    next_path = _safe_next(next)
    client_id = request.client.host if request.client else "unknown"

    if auth.is_locked_out(client_id):
        return templates.TemplateResponse(
            request, "login.html",
            {"next": next_path, "error": "Too many attempts. Wait 30 seconds and try again."},
            status_code=429,
        )
    if not auth.check_password(password):
        auth.record_failed_attempt(client_id)
        return templates.TemplateResponse(
            request, "login.html", {"next": next_path, "error": "Wrong password."}, status_code=401,
        )

    auth.clear_failed_attempts(client_id)
    request.session["authenticated"] = True
    return RedirectResponse(url=next_path, status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ── HTML pages -- server-rendered, call the same functions the JSON
# API routes above call. Forms on these pages submit to the JSON API
# routes above via a few lines of inline fetch() (see the templates),
# not a separate frontend framework/build step.

@app.get("/", response_class=HTMLResponse)
def entries_page(
    request: Request, q: str = "", tag: str = "", store: EntryStore = Depends(get_store),
):
    q = q.strip()
    tag = tag.strip()
    if q:
        entries = store.search(q)  # already newest-first, see EntryStore.search
    elif tag:
        entries = store.by_tag(tag)  # already newest-first, see EntryStore.by_tag
    else:
        entries = list(reversed(list_entries(store)))  # newest first for browsing

    journaled_days, streak_window = journaling_streak(store)

    return templates.TemplateResponse(
        request, "entries.html",
        {
            "entries": [_entry_to_dict(e) for e in entries],
            "tip": get_daily_tip(),
            "query": q,
            "selected_tag": tag,
            "all_tags": store.all_tags(),
            # "On this day" and the streak line are about the whole
            # journal, not a search/filter result -- showing them
            # underneath a search wouldn't make sense, so both are
            # only computed/shown on the unfiltered view.
            "on_this_day_entries": [_entry_to_dict(e) for e in on_this_day(store)] if not (q or tag) else [],
            "journaled_days": journaled_days,
            "streak_window": streak_window,
        },
    )


@app.get("/new", response_class=HTMLResponse)
def new_entry_page(request: Request):
    return templates.TemplateResponse(request, "new_entry.html", {"prompt": get_daily_prompt()})


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    return templates.TemplateResponse(request, "report.html", {"audiences": AUDIENCES, "tip": get_daily_tip()})


@app.get("/analysis", response_class=HTMLResponse)
def analysis_page(request: Request, snapshot_store: AnalysisSnapshotStore = Depends(get_analysis_store)):
    snapshots = snapshot_store.recent(limit=10)
    chart_snapshots = snapshot_store.recent(limit=20)
    return templates.TemplateResponse(
        request, "analysis.html",
        {
            "snapshots": snapshots,
            "api_keys": _analyzer_key_status(),
            "tip": get_daily_tip(),
            # render_mood_trend_svg wants oldest-first; recent() is
            # newest-first, hence the reverse.
            "mood_chart_svg": render_mood_trend_svg(list(reversed(chart_snapshots))),
            "next_run_at": _next_analysis_run(request),
        },
    )


def _next_analysis_run(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return None
    job = scheduler.get_job("analysis")
    return job.next_run_time if job else None


# ── Saved reports + expiring signed share links (FEATURE_IDEAS.md
# items 11 and 13) -- see report_store.py for the design.

@app.get("/reports/saved", response_class=HTMLResponse)
def saved_reports_page(request: Request, report_store: SavedReportStore = Depends(get_saved_report_store)):
    return templates.TemplateResponse(
        request, "saved_reports.html", {"reports": report_store.recent(), "tip": get_daily_tip()},
    )


@app.post("/reports/save")
def save_report(
    days: int = Form(30),
    audience: str = Form("self"),
    store: EntryStore = Depends(get_store),
    report_store: SavedReportStore = Depends(get_saved_report_store),
):
    """Manually save a report for later sharing, same idea as the
    scheduled monthly one (see scheduler.run_scheduled_monthly_report)
    but on demand and for any audience/day range, not just the fixed
    30-day "self" default."""
    if audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail=f"Unknown audience {audience!r}, must be one of {AUDIENCES}")
    try:
        result, entries = report_range(store, get_default_analyzer(), days, audience)
    except NoEntriesError:
        raise HTTPException(status_code=404, detail=f"No entries for audience '{audience}' in the last {days} days.")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    content = build_report_content(result, entries, audience, days)
    saved = SavedReport(days=days, audience=audience, content=format_markdown(content), source="manual")
    report_store.add(saved)
    return {"id": saved.id}


@app.post("/reports/saved/{report_id}/share-link")
def create_share_link(
    request: Request,
    report_id: str,
    expires_in_days: int = Form(DEFAULT_SHARE_LINK_DAYS),
    report_store: SavedReportStore = Depends(get_saved_report_store),
):
    if report_store.get(report_id) is None:
        raise HTTPException(status_code=404, detail=f"No saved report with id {report_id}")

    secret_key = os.environ.get("SESSION_SECRET_KEY")
    if not secret_key:
        raise HTTPException(
            status_code=500,
            detail="SESSION_SECRET_KEY must be set to create share links -- see .env.example.",
        )
    token = make_share_token(report_id, secret_key, expires_in_days)
    return {"url": str(request.url_for("shared_report", token=token)), "expires_in_days": expires_in_days}


@app.get("/reports/shared/{token}", response_class=HTMLResponse, name="shared_report")
def shared_report(request: Request, token: str, report_store: SavedReportStore = Depends(get_saved_report_store)):
    # Deliberately NOT behind AuthMiddleware (see auth.py's exempt
    # paths) -- the entire point is handing this link to someone
    # (a therapist, a partner) who doesn't have -- and shouldn't need
    # -- a login to this app. The signed, expiring token IS the access
    # control for this one route.
    secret_key = os.environ.get("SESSION_SECRET_KEY")
    report_id = resolve_share_token(token, secret_key) if secret_key else None
    if not report_id:
        raise HTTPException(status_code=404, detail="This share link is invalid or has expired.")

    report = report_store.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="This report no longer exists.")

    return templates.TemplateResponse(request, "shared_report.html", {"report": report})
