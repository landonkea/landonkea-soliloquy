# ───────────────────────────────────────────────────────────────────
# analysis_store.py — persisted snapshots of past AnalysisResults
# ───────────────────────────────────────────────────────────────────
# Exists so scheduled/automatic analysis (see scheduler.py) has
# somewhere to land -- without this, a periodic analysis run would
# just disappear the moment it finished. Deliberately separate from
# EntryStore: entries and analysis snapshots are different concepts
# with different lifecycles (an entry is user-authored and permanent;
# a snapshot is a derived, disposable summary that could be
# regenerated from the same entries at any time).
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid

import psycopg

from .analyzer import AnalysisResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_snapshots (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    days INTEGER NOT NULL,
    audience TEXT NOT NULL,
    entry_count INTEGER NOT NULL,
    total_word_count INTEGER NOT NULL,
    summary TEXT NOT NULL,
    mood_notes TEXT NOT NULL,
    key_topics TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_snapshots_created_at ON analysis_snapshots (created_at DESC);
"""

_COLUMNS = "id, created_at, days, audience, entry_count, total_word_count, summary, mood_notes, key_topics"


@dataclass
class AnalysisSnapshot:
    days: int
    audience: str
    result: AnalysisResult
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class AnalysisSnapshotStore:
    def __init__(self, database_url: str):
        self._conn = psycopg.connect(database_url, autocommit=True)
        self._conn.execute(SCHEMA)

    def __enter__(self) -> "AnalysisSnapshotStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add(self, snapshot: AnalysisSnapshot) -> None:
        self._conn.execute(
            f"INSERT INTO analysis_snapshots ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                snapshot.id,
                snapshot.created_at.isoformat(),
                snapshot.days,
                snapshot.audience,
                snapshot.result.entry_count,
                snapshot.result.total_word_count,
                snapshot.result.summary,
                snapshot.result.mood_notes,
                json.dumps(snapshot.result.key_topics),
            ),
        )

    def latest(self, audience: Optional[str] = None) -> Optional[AnalysisSnapshot]:
        """Most recent snapshot, optionally filtered to one audience."""
        if audience:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM analysis_snapshots WHERE audience = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (audience,),
            ).fetchone()
        else:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM analysis_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def recent(self, limit: int = 10) -> list[AnalysisSnapshot]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM analysis_snapshots ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_snapshot(row: tuple) -> AnalysisSnapshot:
        (snap_id, created_at, days, audience, entry_count, total_word_count,
         summary, mood_notes, key_topics) = row
        return AnalysisSnapshot(
            id=snap_id,
            created_at=datetime.fromisoformat(created_at),
            days=days,
            audience=audience,
            result=AnalysisResult(
                entry_count=entry_count,
                total_word_count=total_word_count,
                summary=summary,
                mood_notes=mood_notes,
                key_topics=json.loads(key_topics),
            ),
        )
