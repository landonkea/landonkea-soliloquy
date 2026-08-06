# ───────────────────────────────────────────────────────────────────
# actions.py — the core operations every interface (web, MQTT bridge,
# scheduled analysis) is built on
# ───────────────────────────────────────────────────────────────────
# Deliberately separate from any one interface: web/app.py's routes,
# scheduler.py's background job, and mqtt_bridge.py's listener all
# call these same functions rather than each reimplementing "how do I
# add an entry" or "how do I run analysis over a date range." This is
# what keeps three different entry points (browser, timer, MQTT
# message) from drifting into three different sets of rules.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .analyzer import Analyzer, AnalysisResult
from .entry import Entry
from .storage import EntryStore

DEFAULT_DATABASE_URL = "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy"

AUDIENCES = ("self", "partner", "provider")


def add_entry(store: EntryStore, transcript: str) -> Entry:
    entry = Entry(transcript=transcript)
    store.add(entry)
    return entry


def list_entries(store: EntryStore) -> list[Entry]:
    return store.all()


def _entries_in_last_days(store: EntryStore, days: int) -> list[Entry]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return store.range_between(start, end)


def analyze_range(store: EntryStore, analyzer: Analyzer, days: int) -> AnalysisResult:
    """Analyze the last `days` days of entries. Reuses EntryStore's
    range_between exactly as documented (see storage.py's module
    comment on why that method exists in the first place)."""
    return analyzer.analyze(_entries_in_last_days(store, days))


def report_range(store: EntryStore, analyzer: Analyzer, days: int, audience: str) -> tuple[AnalysisResult, list[Entry]]:
    """Like analyze_range, but audience-aware: for "partner"/"provider",
    entries are filtered down to only those explicitly marked shareable
    with that audience BEFORE they ever reach the Analyzer. Filtering
    only the displayed transcripts afterward would still let private
    entries leak into the AI-generated summary text itself -- the
    analyzer must never see an entry it isn't allowed to describe."""
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown audience {audience!r}, must be one of {AUDIENCES}")

    entries = _entries_in_last_days(store, days)
    if audience == "partner":
        entries = [e for e in entries if e.shareable_with_partner]
    elif audience == "provider":
        entries = [e for e in entries if e.shareable_with_provider]

    result = analyzer.analyze(entries)
    return result, entries
