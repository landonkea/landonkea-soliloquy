# ───────────────────────────────────────────────────────────────────
# report_store.py, saved reports + expiring signed share links
# ───────────────────────────────────────────────────────────────────
# Two related features that share one table: scheduler.py's monthly
# job generates a report and needs somewhere to keep it (a "standing
# record without remembering to click Generate", see FEATURE_IDEAS.md
# item 13); and any saved report -- scheduled or manually generated
# from the Report page -- can get a signed, time-limited link so it
# can be handed to a therapist without giving them an app account or
# emailing a file by hand (item 11). Always markdown: the "most
# natural format for actually handing to a therapist" per README, and
# storing one plain-text format keeps this table simple rather than
# needing to store four renderings (or bytes, for the PDF case) of
# every saved report.
#
# Signing reuses $SESSION_SECRET_KEY (see auth.py) rather than a
# second secret to manage -- itsdangerous's `salt` parameter
# namespaces this from the login session's own tokens off the same
# key, so a session cookie and a share link can never be confused for
# each other even though they're signed with the same underlying
# secret.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

import psycopg
from itsdangerous import BadSignature, URLSafeTimedSerializer

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_reports (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    days INTEGER NOT NULL,
    audience TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_reports_created_at ON saved_reports (created_at DESC);
"""

_COLUMNS = "id, created_at, days, audience, content, source"

_SHARE_LINK_SALT = "soliloquy-report-share"
DEFAULT_SHARE_LINK_DAYS = 7


@dataclass
class SavedReport:
    days: int
    audience: str
    content: str
    source: str  # "scheduled" or "manual" -- informational only
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class SavedReportStore:
    def __init__(self, database_url: str):
        self._conn = psycopg.connect(database_url, autocommit=True)
        self._conn.execute(SCHEMA)

    def __enter__(self) -> "SavedReportStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add(self, report: SavedReport) -> None:
        self._conn.execute(
            f"INSERT INTO saved_reports ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s)",
            (report.id, report.created_at.isoformat(), report.days, report.audience, report.content, report.source),
        )

    def get(self, report_id: str) -> Optional[SavedReport]:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM saved_reports WHERE id = %s", (report_id,)
        ).fetchone()
        return self._row_to_report(row) if row else None

    def recent(self, limit: int = 20) -> list[SavedReport]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM saved_reports ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
        return [self._row_to_report(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_report(row: tuple) -> SavedReport:
        report_id, created_at, days, audience, content, source = row
        return SavedReport(
            id=report_id, created_at=datetime.fromisoformat(created_at),
            days=days, audience=audience, content=content, source=source,
        )


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_SHARE_LINK_SALT)


def make_share_token(report_id: str, secret_key: str, expires_in_days: int = DEFAULT_SHARE_LINK_DAYS) -> str:
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    return _serializer(secret_key).dumps({"report_id": report_id, "expires_at": expires_at})


def resolve_share_token(token: str, secret_key: str) -> Optional[str]:
    """Returns the report_id if the token has a valid signature (never
    tampered with) AND hasn't passed its own encoded expiry, else None
    -- callers turn None into a 404, deliberately not distinguishing
    "expired" from "invalid" in the HTTP response, so a guessed/altered
    token doesn't get useful feedback either way."""
    try:
        data = _serializer(secret_key).loads(token)
    except BadSignature:
        return None

    expires_at = datetime.fromisoformat(data["expires_at"])
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return data["report_id"]
