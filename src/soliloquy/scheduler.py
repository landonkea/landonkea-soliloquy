# ───────────────────────────────────────────────────────────────────
# scheduler.py, periodic, automatic analysis in the background
# ───────────────────────────────────────────────────────────────────
# Runs analysis on a timer (default every 6 hours, configurable via
# $ANALYSIS_INTERVAL_HOURS / $ANALYSIS_WINDOW_DAYS) so a snapshot is
# always waiting on the Analysis page without anyone remembering to
# click "Generate."
#
# This cadence is an explicit starting point, not a considered-final
# choice -- running analysis this often trades rate-limit headroom and
# per-run signal quality (few new entries between 6-hour runs) for
# freshness. Flagged in CHECKLIST.md to revisit once real usage shows
# what cadence actually makes sense.
#
# Always analyzes "self" (every entry, unfiltered) -- scheduled
# analysis auto-generating something under the partner/provider
# audience would mean auto-deciding what's fit to share, which
# contradicts sharing always being an explicit, later, human decision
# (see entry.py's docstring on the sharing flags).
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .analysis_store import AnalysisSnapshot, AnalysisSnapshotStore
from .analyzer import Analyzer, NoEntriesError, get_default_analyzer
from .actions import DEFAULT_DATABASE_URL, report_range
from .notify import send_desktop_notification
from .object_storage import ObjectStore
from .report import build_report_content, format_markdown
from .report_store import SavedReport, SavedReportStore
from .storage import EntryStore

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 6
DEFAULT_WINDOW_DAYS = 1
MONTHLY_REPORT_DAYS = 30


def run_scheduled_analysis(
    database_url: Optional[str] = None,
    days: int = DEFAULT_WINDOW_DAYS,
    analyzer: Optional[Analyzer] = None,
) -> Optional[AnalysisSnapshot]:
    """One run of the scheduled job -- pulled out as its own function,
    with the analyzer/database_url overridable, so it's directly
    testable without spinning up a real scheduler or hitting a real
    provider. Returns the saved snapshot, or None if the run was
    skipped (no entries) or failed (logged, not raised -- a background
    job failing shouldn't crash the process running it)."""
    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    analyzer = analyzer or get_default_analyzer()

    with EntryStore(database_url) as store:
        try:
            result, _entries = report_range(store, analyzer, days, "self")
        except NoEntriesError:
            logger.info("Scheduled analysis skipped -- no entries in the last %s day(s).", days)
            return None
        except RuntimeError as exc:
            logger.warning("Scheduled analysis failed: %s", exc)
            return None

    snapshot = AnalysisSnapshot(days=days, audience="self", result=result)
    with AnalysisSnapshotStore(database_url) as snapshot_store:
        snapshot_store.add(snapshot)
    logger.info("Scheduled analysis saved (%s entries, last %s day(s)).", result.entry_count, days)

    send_desktop_notification(
        "Soliloquy: new analysis ready",
        result.summary[:200] + ("..." if len(result.summary) > 200 else ""),
    )
    return snapshot


def run_scheduled_monthly_report(
    database_url: Optional[str] = None, analyzer: Optional[Analyzer] = None,
) -> Optional[SavedReport]:
    """Generates and saves a 30-day "self" markdown report -- a
    standing monthly record without remembering to click "Generate" on
    the Report page (FEATURE_IDEAS.md item 13). Delivery (e.g. email)
    isn't built -- that item's own wording flags it as a later add-on,
    and this app has no email-sending setup to plug it into yet.
    Saved reports get a shareable, expiring link from the Report page
    (see report_store.py) once one exists."""
    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    analyzer = analyzer or get_default_analyzer()

    with EntryStore(database_url) as store:
        try:
            result, entries = report_range(store, analyzer, MONTHLY_REPORT_DAYS, "self")
        except NoEntriesError:
            logger.info("Scheduled monthly report skipped -- no entries in the last %s days.", MONTHLY_REPORT_DAYS)
            return None
        except RuntimeError as exc:
            logger.warning("Scheduled monthly report failed: %s", exc)
            return None

    content = build_report_content(result, entries, "self", MONTHLY_REPORT_DAYS)
    saved = SavedReport(days=MONTHLY_REPORT_DAYS, audience="self", content=format_markdown(content), source="scheduled")
    with SavedReportStore(database_url) as report_store:
        report_store.add(saved)
    logger.info("Scheduled monthly report saved (%s entries).", result.entry_count)
    return saved


def run_media_retention_cleanup(
    database_url: Optional[str] = None, retention_days: Optional[int] = None,
) -> int:
    """Opt-in: deletes audio/video from object storage for entries
    older than $MEDIA_RETENTION_DAYS (unset -- the default -- means
    keep media forever, same "off unless you ask for it" pattern as
    everything else protective/destructive in this app). The
    transcript and everything else about the entry is untouched, only
    the underlying media files and their paths. Returns how many
    entries were cleaned up, for logging/testing."""
    retention_days = retention_days if retention_days is not None else _media_retention_days()
    if retention_days is None:
        return 0

    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    object_store = ObjectStore()  # no context manager -- boto3's client needs no explicit close

    cleaned = 0
    with EntryStore(database_url) as store:
        for entry in store.entries_with_media_older_than(cutoff):
            for key in (entry.audio_path, entry.video_path):
                if key:
                    try:
                        object_store.delete(key)
                    except Exception as exc:  # noqa: BLE001 -- best-effort, see delete_entry's route for the same pattern
                        logger.warning("Could not delete object storage key %s for entry %s: %s", key, entry.id, exc)
            store.clear_media_paths(entry.id)
            cleaned += 1

    if cleaned:
        logger.info("Media retention cleanup: removed media from %s entries older than %s day(s).", cleaned, retention_days)
    return cleaned


def _media_retention_days() -> Optional[int]:
    raw = os.environ.get("MEDIA_RETENTION_DAYS", "")
    return int(raw) if raw else None


def start_scheduler() -> BackgroundScheduler:
    interval_hours = float(os.environ.get("ANALYSIS_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS))
    window_days = int(os.environ.get("ANALYSIS_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_scheduled_analysis, "interval", hours=interval_hours, kwargs={"days": window_days},
        id="analysis",
    )
    # Monthly, on the 1st -- CronTrigger, not another "interval" job,
    # since "every N hours" can't express "once a month" without
    # drifting (a fixed hour count isn't a fixed number of months).
    scheduler.add_job(
        run_scheduled_monthly_report, CronTrigger(day=1, hour=3, minute=0), id="monthly_report",
    )
    if _media_retention_days() is not None:
        scheduler.add_job(run_media_retention_cleanup, "interval", hours=24, id="media_retention")

    scheduler.start()
    logger.info("Scheduled analysis running every %s hour(s), window=%s day(s).", interval_hours, window_days)
    return scheduler
