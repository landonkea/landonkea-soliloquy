# ───────────────────────────────────────────────────────────────────
# storage.py — SQLite-backed entry storage
# ───────────────────────────────────────────────────────────────────
# One table, one row per Entry. Timestamps are stored as ISO 8601
# UTC strings (not a SQLite-native datetime type — SQLite doesn't
# have one) so they sort correctly as plain strings AND parse back
# into real datetime objects unambiguously regardless of what
# timezone the reading process happens to be in.
#
# `range_between(start, end)` exists now, with only a handful of
# entries likely to ever be created by hand while testing, because
# the whole point of per-day/week/month analysis (see README's
# roadmap) is querying date ranges — building that query method
# alongside the storage layer itself, instead of bolting it on later,
# is what keeps this from becoming "a pile of entries with no way to
# ask a real question about them."
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .entry import Entry

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    transcript TEXT NOT NULL,
    audio_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries (created_at);
"""


class EntryStore:
    def __init__(self, db_path: str = "soliloquy.db"):
        self.db_path = db_path
        parent_dir = Path(db_path).parent
        if parent_dir != Path("."):
            parent_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "EntryStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add(self, entry: Entry) -> None:
        self._conn.execute(
            "INSERT INTO entries (id, created_at, transcript, audio_path) VALUES (?, ?, ?, ?)",
            (entry.id, entry.created_at.isoformat(), entry.transcript, entry.audio_path),
        )
        self._conn.commit()

    def get(self, entry_id: str) -> Optional[Entry]:
        row = self._conn.execute(
            "SELECT id, created_at, transcript, audio_path FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def all(self) -> list[Entry]:
        rows = self._conn.execute(
            "SELECT id, created_at, transcript, audio_path FROM entries ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def range_between(self, start: datetime, end: datetime) -> list[Entry]:
        """Entries with created_at in [start, end) — the building block
        every day/week/month rollup in the (future) analysis module is
        just a different (start, end) pair around."""
        rows = self._conn.execute(
            "SELECT id, created_at, transcript, audio_path FROM entries "
            "WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete(self, entry_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_entry(row: tuple) -> Entry:
        entry_id, created_at, transcript, audio_path = row
        return Entry(
            id=entry_id,
            created_at=datetime.fromisoformat(created_at),
            transcript=transcript,
            audio_path=audio_path,
        )
