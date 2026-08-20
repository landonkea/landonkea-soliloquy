# ───────────────────────────────────────────────────────────────────
# storage.py, Postgres-backed entry storage
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
# roadmap) is querying date ranges, building that query method
# alongside the storage layer itself, instead of bolting it on later,
# is what keeps this from becoming "a pile of entries with no way to
# ask a real question about them."
#
# _ensure_columns() is a deliberate stopgap, not a real migration
# system, fine at this project's current size (see
# landonkea-apple-products-scraper's own history for exactly why this
# doesn't stay fine forever: it moved to real Alembic migrations once
# it had real production data and more than one schema change to
# track). Revisit if this grows past a column or two more.
#
# ── Encryption at rest + full-text search, together ────────────────
# These two features are normally in tension: Postgres full-text
# search needs the plaintext transcript to build a tsvector index
# from, but "encrypted at rest" means the transcript column itself
# should be unreadable to anyone with raw DB access. The resolution
# here (a real, standard pattern, not a shortcut): `search_vector` is
# computed from the PLAINTEXT transcript at write time, before
# encryption, and stored as its own column, while the `transcript`
# column itself holds ciphertext once $TRANSCRIPT_ENCRYPTION_KEY is
# set. Worth being honest about the limit of this, though: a tsvector
# is a lexeme index, not the original text, but it does still leak
# which words appear in an entry to anyone with raw DB access, so it's
# meaningfully better than a plaintext transcript column, not a full
# privacy guarantee on its own.
#
# Off by default (no $TRANSCRIPT_ENCRYPTION_KEY -> transcripts stored
# as plain text, same as before this existed), matching every other
# "protective but not free to set up" feature in this app (auth,
# hardened LAN binding). Generate a key with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Turning it on only affects NEW writes -- existing plaintext rows
# aren't retroactively encrypted (that's a real data mutation on real
# journal entries, not something to do silently on every startup);
# see scripts/encrypt_existing_transcripts.py to do that once,
# deliberately, when you're ready.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import psycopg
from cryptography.fernet import Fernet, InvalidToken

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

_COLUMNS = (
    "id, created_at, transcript, audio_path, video_path, "
    "shareable_with_partner, shareable_with_provider, tags, speaker"
)

# Columns that might be missing on a database created before this
# feature existed -- see _ensure_columns().
_OPTIONAL_COLUMNS = {
    "video_path": "TEXT",
    "shareable_with_partner": "BOOLEAN NOT NULL DEFAULT FALSE",
    "shareable_with_provider": "BOOLEAN NOT NULL DEFAULT FALSE",
    "tags": "TEXT[] NOT NULL DEFAULT '{}'",
    "speaker": "TEXT",
    "search_vector": "TSVECTOR",
}

# Marks a `transcript` value as Fernet ciphertext rather than plain
# text, so a row written before encryption was turned on (or with it
# turned off again) can still be told apart from one written after,
# without needing to know globally "is encryption on right now."
_ENCRYPTED_PREFIX = "enc1:"


class EntryStore:
    def __init__(self, database_url: str, encryption_key: Optional[str] = None):
        self.database_url = database_url
        self._fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key) \
            if encryption_key else None
        self._conn = psycopg.connect(database_url, autocommit=True)
        self._conn.execute(SCHEMA)
        self._ensure_columns()
        self._ensure_indexes()

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

    def _ensure_indexes(self) -> None:
        # Split from SCHEMA (which only runs once against a fresh
        # table) because search_vector might have just been added by
        # _ensure_columns() above on an existing database -- the GIN
        # index needs the column to already exist.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_search_vector ON entries USING GIN (search_vector)"
        )

    def __enter__(self) -> "EntryStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _encrypt(self, plaintext: str) -> str:
        if not self._fernet:
            return plaintext
        return _ENCRYPTED_PREFIX + self._fernet.encrypt(plaintext.encode()).decode()

    def _decrypt(self, stored: str) -> str:
        if not stored.startswith(_ENCRYPTED_PREFIX):
            return stored  # plaintext -- written before encryption was on, or it's still off
        if not self._fernet:
            raise RuntimeError(
                "This entry's transcript is encrypted but no TRANSCRIPT_ENCRYPTION_KEY is "
                "configured -- set the same key it was encrypted with to read it back."
            )
        token = stored[len(_ENCRYPTED_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError(
                "This entry's transcript couldn't be decrypted with the configured "
                "TRANSCRIPT_ENCRYPTION_KEY -- it was likely encrypted with a different key."
            ) from exc

    def add(self, entry: Entry) -> None:
        self._conn.execute(
            f"INSERT INTO entries ({_COLUMNS}, search_vector) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s))",
            (
                entry.id,
                entry.created_at.isoformat(),
                self._encrypt(entry.transcript),
                entry.audio_path,
                entry.video_path,
                entry.shareable_with_partner,
                entry.shareable_with_provider,
                entry.tags,
                entry.speaker,
                entry.transcript,  # plaintext, only for building search_vector -- never stored as-is
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
        """Entries with created_at in [start, end), the building block
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

    def search(self, query: str, limit: int = 50) -> list[Entry]:
        """Full-text search over search_vector (built from plaintext at
        write time, see this module's docstring) -- works whether or
        not encryption is on, since it never depends on the transcript
        column itself being readable SQL-side. plainto_tsquery handles
        normal free-text input ("my sister march") without needing the
        caller to know tsquery's own operator syntax."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries "
            "WHERE search_vector @@ plainto_tsquery('english', %s) "
            "ORDER BY created_at DESC LIMIT %s",
            (query, limit),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def by_tag(self, tag: str) -> list[Entry]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries WHERE %s = ANY(tags) ORDER BY created_at DESC",
            (tag,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def all_tags(self) -> list[str]:
        """Every distinct tag in use, for populating a filter dropdown
        without hardcoding a tag list anywhere."""
        rows = self._conn.execute(
            "SELECT DISTINCT unnest(tags) AS tag FROM entries ORDER BY tag"
        ).fetchall()
        return [row[0] for row in rows]

    def on_this_day(self, today: date) -> list[Entry]:
        """Entries from the same month/day as `today`, any earlier
        year -- "on this day" resurfacing. Cast to timestamptz in SQL
        rather than parsing in Python since Postgres already has to
        parse created_at for the comparison against `today` anyway."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries "
            "WHERE created_at::date < %s "
            "AND EXTRACT(MONTH FROM created_at::timestamptz) = %s "
            "AND EXTRACT(DAY FROM created_at::timestamptz) = %s "
            "ORDER BY created_at DESC",
            (today.isoformat(), today.month, today.day),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def distinct_entry_dates(self, since: datetime) -> set[date]:
        """Every calendar date (UTC) with at least one entry since
        `since` -- the building block for a streak/cadence indicator
        (see actions.journaling_streak), which is really just "how
        many distinct days in the last N had an entry."""
        rows = self._conn.execute(
            "SELECT DISTINCT created_at::date FROM entries WHERE created_at >= %s",
            (since.isoformat(),),
        ).fetchall()
        return {row[0] for row in rows}

    def entries_with_media_older_than(self, cutoff: datetime) -> list[Entry]:
        """Entries with audio/video still attached, created before
        `cutoff` -- see the opt-in media retention job in scheduler.py.
        Transcripts (and everything else) are unaffected; this is only
        ever used to find media worth deleting from object storage."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM entries "
            "WHERE created_at < %s AND (audio_path IS NOT NULL OR video_path IS NOT NULL)",
            (cutoff.isoformat(),),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def clear_media_paths(self, entry_id: str) -> bool:
        """Detach audio/video from an entry after its underlying object
        storage files have been deleted (see the media retention job) --
        the transcript and everything else about the entry stays."""
        cursor = self._conn.execute(
            "UPDATE entries SET audio_path = NULL, video_path = NULL WHERE id = %s", (entry_id,)
        )
        return cursor.rowcount > 0

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

    def update_transcript(self, entry_id: str, transcript: str) -> bool:
        """Manually correct an entry's transcript -- e.g. fixing a
        transcription mistake. Returns False if entry_id doesn't exist.
        Re-derives search_vector from the new plaintext and re-encrypts
        (if a key is configured), same as add()."""
        cursor = self._conn.execute(
            "UPDATE entries SET transcript = %s, search_vector = to_tsvector('english', %s) WHERE id = %s",
            (self._encrypt(transcript), transcript, entry_id),
        )
        return cursor.rowcount > 0

    def update_tags(self, entry_id: str, tags: list[str]) -> bool:
        cursor = self._conn.execute(
            "UPDATE entries SET tags = %s WHERE id = %s", (tags, entry_id)
        )
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

    def _row_to_entry(self, row: tuple) -> Entry:
        (entry_id, created_at, transcript, audio_path, video_path,
         shareable_with_partner, shareable_with_provider, tags, speaker) = row
        return Entry(
            id=entry_id,
            created_at=datetime.fromisoformat(created_at),
            transcript=self._decrypt(transcript),
            audio_path=audio_path,
            video_path=video_path,
            shareable_with_partner=bool(shareable_with_partner),
            shareable_with_provider=bool(shareable_with_provider),
            tags=list(tags) if tags else [],
            speaker=speaker,
        )
