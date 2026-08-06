# ───────────────────────────────────────────────────────────────────
# storage.py — Postgres-backed entry storage
# ───────────────────────────────────────────────────────────────────
# EntryStore talks to a real Postgres database via a DATABASE_URL
# connection string (postgres://user:pass@host:port/db), not a local
# file -- this is what makes Soliloquy reachable from more than one
# device/process. See docker-compose.yml for the self-hosted Postgres
# used in local dev; the same connection string shape works unchanged
# against a managed provider (Supabase, Neon) later.
#
# Timestamps are stored as ISO 8601 UTC strings in a TEXT column
# (not a native TIMESTAMP type) so the exact round-trip behavior this
# app already relies on (an Entry's created_at comes back byte-for-
# byte comparable after a save/load) doesn't depend on Postgres's own
# timestamp parsing/timezone handling.
#
# `range_between(start, end)` exists now, with only a handful of
# entries likely to ever be created by hand while testing, because
# the whole point of per-day/week/month analysis (see README's
# roadmap) is querying date ranges — building that query method
# alongside the storage layer itself, instead of bolting it on later,
# is what keeps this from becoming "a pile of entries with no way to
# ask a real question about them."
#
# _ensure_columns() is a deliberate stopgap, not a real migration
# system — fine at this project's current size (see
# landonkea-apple-products-scraper's own history for exactly why this
# doesn't stay fine forever: it moved to real Alembic migrations once
# it had real production data and more than one schema change to
# track). Revisit if this grows past a column or two more.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime
from typing import Optional

import psycopg

from .entry import Entry

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    transcript TEXT NOT NULL,
    audio_path TEXT,
    video_path TEXT,
    shareable_with_partner BOOLEAN NOT NULL DEFAULT FALSE,
    shareable_with_provider BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries (created_at);
"""

_COLUMNS = "id, created_at, transcript, audio_path, video_path, shareable_with_partner, shareable_with_provider"

# Columns that might be missing on a database created before this
# feature existed -- see _ensure_columns().
_OPTIONAL_COLUMNS = {
    "video_path": "TEXT",
    "shareable_with_partner": "BOOLEAN NOT NULL DEFAULT FALSE",
    "shareable_with_provider": "BOOLEAN NOT NULL DEFAULT FALSE",
}


class EntryStore:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._conn = psycopg.connect(database_url, autocommit=True)
        self._conn.execute(SCHEMA)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        # Handles a DB created before one of _OPTIONAL_COLUMNS existed --
        # CREATE TABLE IF NOT EXISTS alone won't add them to an already-
        # existing table. Safe to run every startup: only ALTERs if a
        # column is genuinely missing.
        existing = {
            row[0] for row in self._conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'entries'"
            ).fetchall()
        }
        for column, ddl_type in _OPTIONAL_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {ddl_type}")

    def __enter__(self) -> "EntryStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add(self, entry: Entry) -> None:
        self._conn.execute(
            f"INSERT INTO entries ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                entry.id,
                entry.created_at.isoformat(),
                entry.transcript,
                entry.audio_path,
                entry.video_path,
                entry.shareable_with_partner,
                entry.shareable_with_provider,
            ),
        )

    def get(self, entry_id: str) -> Optional[Entry]:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries WHERE id = %s", (entry_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def all(self) -> list[Entry]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def range_between(self, start: datetime, end: datetime) -> list[Entry]:
        """Entries with created_at in [start, end) — the building block
        every day/week/month rollup in the analysis module is just a
        different (start, end) pair around. Does NOT filter by sharing
        flags -- see cli.py's report command for audience filtering,
        which is a separate concern from "which entries are in this
        date window" (the caller decides which audience it needs)."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries "
            "WHERE created_at >= %s AND created_at < %s ORDER BY created_at ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete(self, entry_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM entries WHERE id = %s", (entry_id,))
        return cursor.rowcount > 0

    def update_sharing(
        self, entry_id: str, shareable_with_partner: Optional[bool] = None,
        shareable_with_provider: Optional[bool] = None,
    ) -> bool:
        """Update one or both sharing flags on an existing entry. Pass
        only the flag(s) you want to change -- None (the default) means
        "leave as-is." Returns False if entry_id doesn't exist."""
        if shareable_with_partner is None and shareable_with_provider is None:
            return self.get(entry_id) is not None

        updates: list[str] = []
        params: list[object] = []
        if shareable_with_partner is not None:
            updates.append("shareable_with_partner = %s")
            params.append(shareable_with_partner)
        if shareable_with_provider is not None:
            updates.append("shareable_with_provider = %s")
            params.append(shareable_with_provider)
        params.append(entry_id)

        cursor = self._conn.execute(f"UPDATE entries SET {', '.join(updates)} WHERE id = %s", params)
        return cursor.rowcount > 0

    def update_video_path(self, entry_id: str, video_path: str) -> bool:
        """Set video_path on an existing entry -- used by the video
        upload flow, which creates the Entry from the extracted audio's
        transcript first, then attaches the video separately once it's
        finished uploading to object storage."""
        cursor = self._conn.execute(
            "UPDATE entries SET video_path = %s WHERE id = %s", (video_path, entry_id)
        )
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_entry(row: tuple) -> Entry:
        entry_id, created_at, transcript, audio_path, video_path, shareable_with_partner, shareable_with_provider = row
        return Entry(
            id=entry_id,
            created_at=datetime.fromisoformat(created_at),
            transcript=transcript,
            audio_path=audio_path,
            video_path=video_path,
            shareable_with_partner=bool(shareable_with_partner),
            shareable_with_provider=bool(shareable_with_provider),
        )
