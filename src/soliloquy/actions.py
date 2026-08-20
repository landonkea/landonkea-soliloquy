# ───────────────────────────────────────────────────────────────────
# actions.py, the core operations every interface (web, MQTT bridge,
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

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .analyzer import Analyzer, AnalysisResult
from .entry import Entry
from .storage import EntryStore

DEFAULT_DATABASE_URL = "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy"

AUDIENCES = ("self", "partner", "provider")

# Extra framing appended to the analysis prompt per audience (see
# analyzer._build_prompt) -- the sharing flags already control WHICH
# entries an audience's report can see; this controls how the
# analyzer talks about them. Empty for "self": no extra framing needed
# when the reader is the journal's own owner.
AUDIENCE_INSTRUCTIONS = {
    "self": "",
    "partner": (
        "Focus on emotional patterns and relationship-relevant context a partner would find "
        "meaningful, not routine day-to-day logistics."
    ),
    "provider": (
        "Focus on patterns clinically relevant to therapy (mood trends, recurring stressors, "
        "coping attempts), not routine daily detail."
    ),
}


def add_entry(
    store: EntryStore, transcript: str, tags: Optional[list[str]] = None, speaker: Optional[str] = None
) -> Entry:
    entry = Entry(transcript=transcript, tags=tags or [], speaker=speaker)
    store.add(entry)
    return entry


def append_or_add_entry(
    store: EntryStore, text: str, speaker: Optional[str] = None, within_minutes: int = 60
) -> tuple[Entry, bool]:
    """Used by the MQTT bridge's "append to today" message type (see
    mqtt_bridge.py) -- "also I forgot to mention..." a minute after a
    voice entry should land on the SAME entry, not a second,
    disconnected one. Appends to the most recent entry from today if
    one exists within the last `within_minutes` minutes AND has the
    same speaker (or neither has a speaker set); otherwise creates a
    new entry, same as add_entry. Returns (entry, appended)."""
    now = datetime.now(timezone.utc)
    today_entries = store.range_between(now.replace(hour=0, minute=0, second=0, microsecond=0), now)
    if today_entries:
        latest = today_entries[-1]
        age = now - latest.created_at
        if age <= timedelta(minutes=within_minutes) and latest.speaker == speaker:
            updated_transcript = f"{latest.transcript} {text}".strip()
            store.update_transcript(latest.id, updated_transcript)
            return store.get(latest.id), True

    return add_entry(store, text, speaker=speaker), False


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

    result = analyzer.analyze(entries, AUDIENCE_INSTRUCTIONS.get(audience, ""))
    return result, entries


def journaling_streak(store: EntryStore, window_days: int = 7) -> tuple[int, int]:
    """"Journaled N of the last `window_days` days" -- an honest
    cadence signal, not a gamified streak counter (see
    FEATURE_IDEAS.md's own framing of this). Returns (days_journaled,
    window_days); today counts as one of the window's days even if
    it's not over yet, same as how a person would describe their own
    week."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    return len(store.distinct_entry_dates(since)), window_days


def on_this_day(store: EntryStore, today: Optional[date] = None) -> list[Entry]:
    return store.on_this_day(today or datetime.now(timezone.utc).date())
