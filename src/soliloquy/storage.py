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
#
# _ensure_sharing_columns() is a deliberate stopgap, not a real
# migration system — fine at this project's current size (see
# landonkea-apple-products-scraper's own history for exactly why this
# doesn't stay fine forever: it moved to real Alembic migrations once
# it had real production data and more than one schema change to
# track). Revisit if this grows past a column or two more.
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
    audio_path TEXT,
    shareable_with_partner INTEGER NOT NULL DEFAULT 0,
    shareable_with_provider INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries (created_at);
"""

_COLUMNS = "id, created_at, transcript, audio_path, shareable_with_partner, shareable_with_provider"


class EntryStore:
    def __init__(self, db_path: str = "soliloquy.db"):
        self.db_path = db_path
        parent_dir = Path(db_path).parent
        if parent_dir != Path("."):
            parent_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(SCHEMA)
        self._ensure_sharing_columns()
        self._conn.commit()

    def _ensure_sharing_columns(self) -> None:
        # Handles a DB file created before these two columns existed --
        # CREATE TABLE IF NOT EXISTS alone won't add them to an already-
        # existing table. Safe to run every startup: only ALTERs if a
        # column is genuinely missing.
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(entries)")}
        if "shareable_with_partner" not in existing:
            self._conn.execute("ALTER TABLE entries ADD COLUMN shareable_with_partner INTEGER NOT NULL DEFAULT 0")
        if "shareable_with_provider" not in existing:
            self._conn.execute("ALTER TABLE entries ADD COLUMN shareable_with_provider INTEGER NOT NULL DEFAULT 0")

    def __enter__(self) -> "EntryStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add(self, entry: Entry) -> None:
        self._conn.execute(
            f"INSERT INTO entries ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry.id,
                entry.created_at.isoformat(),
                entry.transcript,
                entry.audio_path,
                int(entry.shareable_with_partner),
                int(entry.shareable_with_provider),
            ),
        )
        self._conn.commit()

    def get(self, entry_id: str) -> Optional[Entry]:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries WHERE id = ?", (entry_id,)
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
            "WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete(self, entry_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._conn.commit()
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
            updates.append("shareable_with_partner = ?")
            params.append(int(shareable_with_partner))
        if shareable_with_provider is not None:
            updates.append("shareable_with_provider = ?")
            params.append(int(shareable_with_provider))
        params.append(entry_id)

        cursor = self._conn.execute(f"UPDATE entries SET {', '.join(updates)} WHERE id = ?", params)
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_entry(row: tuple) -> Entry:
        entry_id, created_at, transcript, audio_path, shareable_with_partner, shareable_with_provider = row
        return Entry(
            id=entry_id,
            created_at=datetime.fromisoformat(created_at),
            transcript=transcript,
            audio_path=audio_path,
            shareable_with_partner=bool(shareable_with_partner),
            shareable_with_provider=bool(shareable_with_provider),
        )
