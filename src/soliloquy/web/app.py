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

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..analyzer import ClaudeAnalyzer, NoEntriesError
from ..cli import AUDIENCES, DEFAULT_DATABASE_URL, add_entry, format_report, list_entries, report_range
from ..entry import Entry
from ..object_storage import ObjectStore
from ..storage import EntryStore
from ..transcriber import WhisperTranscriber

app = FastAPI(title="Soliloquy")

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


@app.post("/reports")
def post_report(
    days: int = Form(7),
    audience: str = Form("self"),
    store: EntryStore = Depends(get_store),
):
    if audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail=f"Unknown audience {audience!r}, must be one of {AUDIENCES}")
    try:
        result, entries = report_range(store, ClaudeAnalyzer(), days, audience)
    except NoEntriesError:
        raise HTTPException(
            status_code=404, detail=f"No entries for audience '{audience}' in the last {days} days."
        )
    return PlainTextResponse(format_report(result, entries, audience, days))


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
