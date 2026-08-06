# ───────────────────────────────────────────────────────────────────
# web/app.py — the HTTP API, a thin layer over the existing package
# ───────────────────────────────────────────────────────────────────
# This does NOT duplicate business logic that already lives in
# cli.py/the package -- every route calls the exact same functions
# the CLI does (add_entry, list_entries, report_range, format_report,
# EntryStore.update_sharing). The only genuinely new assembly logic
# here is for audio/video uploads, because the web app's storage
# target (object storage, addressed by key) differs from the CLI's
# (a local file, addressed by path) -- see post_audio_entry().
#
# Keeping real logic here (not in a browser-side JS framework) is
# deliberate: a future native app becomes a new client of THIS API,
# not a second implementation of the same rules.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..analyzer import ClaudeAnalyzer, NoEntriesError
from ..cli import AUDIENCES, DEFAULT_DATABASE_URL, add_entry, list_entries, report_range
from ..entry import Entry
from ..object_storage import ObjectStore
from ..report import FORMATS, build_report_content, format_html, format_markdown, format_pdf, format_text
from ..storage import EntryStore
from ..transcriber import WhisperTranscriber
from ..video import extract_audio

app = FastAPI(title="Soliloquy")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
        transcript = WhisperTranscriber().transcribe(tmp_path)
        suffix = Path(tmp_path).suffix
        key = object_store.upload_file(tmp_path, f"audio/{uuid.uuid4()}{suffix}")
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

        transcript = WhisperTranscriber().transcribe(audio_tmp_path)
        audio_key = object_store.upload_file(audio_tmp_path, f"audio/{uuid.uuid4()}.wav")
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


_REPORT_RENDERERS = {"text": format_text, "markdown": format_markdown, "html": format_html, "pdf": format_pdf}
_REPORT_MEDIA_TYPES = {
    "text": "text/plain", "markdown": "text/markdown", "html": "text/html", "pdf": "application/pdf",
}
_REPORT_EXTENSIONS = {"text": "txt", "markdown": "md", "html": "html", "pdf": "pdf"}


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
        result, entries = report_range(store, ClaudeAnalyzer(), days, audience)
    except NoEntriesError:
        raise HTTPException(
            status_code=404, detail=f"No entries for audience '{audience}' in the last {days} days."
        )
    except RuntimeError as exc:
        # ClaudeAnalyzer raises RuntimeError for a missing API key or a
        # failed API call -- surface that real message instead of a
        # generic 500, so "you forgot to set ANTHROPIC_API_KEY" is
        # actually visible instead of an opaque Internal Server Error.
        raise HTTPException(status_code=502, detail=str(exc))

    content = build_report_content(result, entries, audience, days)
    body = _REPORT_RENDERERS[format](content)
    filename = f"soliloquy-report.{_REPORT_EXTENSIONS[format]}"
    return Response(
        content=body,
        media_type=_REPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/media/{key:path}")
def get_media(key: str, object_store: ObjectStore = Depends(get_object_store)):
    try:
        local_path = object_store.download_to_temp(key)
    except Exception:
        raise HTTPException(status_code=404, detail=f"No media found for key {key!r}")

    media_type = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
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
        request, "entries.html", {"entries": [_entry_to_dict(e) for e in entries]}
    )


@app.get("/new", response_class=HTMLResponse)
def new_entry_page(request: Request):
    return templates.TemplateResponse(request, "new_entry.html", {})


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    return templates.TemplateResponse(request, "report.html", {"audiences": AUDIENCES})
