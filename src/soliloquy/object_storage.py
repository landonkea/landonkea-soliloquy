# ───────────────────────────────────────────────────────────────────
# object_storage.py, S3-compatible storage for audio/video files
# ───────────────────────────────────────────────────────────────────
# Raw audio/video should never live inside the database (see
# storage.py's module comment) -- it belongs in object storage.
# This wraps boto3's S3 client rather than a MinIO-specific SDK
# specifically because boto3 is the SAME client that works unchanged
# against Cloudflare R2 (or real AWS S3) later -- pointing this at
# managed cloud storage instead of self-hosted MinIO is a config
# change (S3_ENDPOINT_URL etc.), not a rewrite.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import boto3
from botocore.client import Config


class ObjectStore:
    def __init__(
        self,
        endpoint_url: str | None = None,
        bucket: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ):
        self.bucket = bucket or os.environ.get("S3_BUCKET", "soliloquy")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
            aws_access_key_id=access_key or os.environ.get("S3_ACCESS_KEY", "soliloquy"),
            aws_secret_access_key=secret_key or os.environ.get("S3_SECRET_KEY", "soliloquy123"),
            config=Config(signature_version="s3v4"),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self._client.create_bucket(Bucket=self.bucket)

    def upload_file(self, local_path: str, key: str) -> str:
        """Upload a local file under `key`, returning that same key --
        the value callers save as Entry.audio_path/video_path. Keeping
        the return value a plain key (not a full URL) is what keeps
        Entry storage-provider-agnostic; GET /media/{key} (the web
        app) is the only place that turns a key into bytes."""
        self._client.upload_file(local_path, self.bucket, key)
        return key

    def download_to_temp(self, key: str) -> Path:
        """Download `key` to a temp file and return its path -- used to
        serve playback through the web app, and to hand a local path to
        tools (ffmpeg, the transcriber) that need one."""
        suffix = Path(key).suffix
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        self._client.download_file(self.bucket, key, path)
        return Path(path)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)
