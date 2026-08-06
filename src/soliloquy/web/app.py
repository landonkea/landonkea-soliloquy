# ───────────────────────────────────────────────────────────────────
# web/app.py — the HTTP API, a thin layer over the existing package
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
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..actions import AUDIENCES, DEFAULT_DATABASE_URL, add_entry, list_entries, report_range
from ..analysis_store import AnalysisSnapshotStore
from ..analyzer import NoEntriesError, get_default_analyzer
from ..deployment_mode import describe_deployment_mode
from ..entry import Entry
from ..mqtt_bridge import start_mqtt_listener
from ..object_storage import ObjectStore
from ..prompts import get_daily_prompt
from ..tips import get_daily_tip
from ..report import FORMATS, build_report_content, format_html, format_markdown, format_pdf, format_text
from ..scheduler import start_scheduler
from ..storage import EntryStore
from ..transcriber import WhisperTranscriber
from ..video import extract_audio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    print(describe_deployment_mode(), flush=True)

    # Both disabled in tests (see tests/test_web.py) so the test suite
    # doesn't spin up a real background timer/MQTT connection against
    # the test database on every TestClient instantiation.
    scheduler = None
    if os.environ.get("SOLILOQUY_DISABLE_SCHEDULER") != "1":
        scheduler = start_scheduler()

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
    store = EntryStore(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
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
    step) -- returns (object storage key, transcript)."""
    transcript = WhisperTranscriber().transcribe(audio_path)
    key = object_store.upload_file(audio_path, f"audio/{uuid.uuid4()}{Path(audio_path).suffix}")
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


# ── HTML pages -- server-rendered, call the same functions the JSON
# API routes above call. Forms on these pages submit to the JSON API
# routes above via a few lines of inline fetch() (see the templates),
# not a separate frontend framework/build step.

@app.get("/", response_class=HTMLResponse)
def entries_page(request: Request, store: EntryStore = Depends(get_store)):
    entries = list(reversed(list_entries(store)))  # newest first for browsing
    return templates.TemplateResponse(
        request, "entries.html", {"entries": [_entry_to_dict(e) for e in entries], "tip": get_daily_tip()}
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
    return templates.TemplateResponse(
        request, "analysis.html",
        {"snapshots": snapshots, "api_keys": _analyzer_key_status(), "tip": get_daily_tip()},
    )
