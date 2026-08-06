# ───────────────────────────────────────────────────────────────────
# scheduler.py — periodic, automatic analysis in the background
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
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from .analysis_store import AnalysisSnapshot, AnalysisSnapshotStore
from .analyzer import Analyzer, NoEntriesError, get_default_analyzer
from .cli import DEFAULT_DATABASE_URL, analyze_range
from .storage import EntryStore

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 6
DEFAULT_WINDOW_DAYS = 1


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
            result = analyze_range(store, analyzer, days)
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
    return snapshot


def start_scheduler() -> BackgroundScheduler:
    interval_hours = float(os.environ.get("ANALYSIS_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS))
    window_days = int(os.environ.get("ANALYSIS_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_scheduled_analysis, "interval", hours=interval_hours, kwargs={"days": window_days})
    scheduler.start()
    logger.info("Scheduled analysis running every %s hour(s), window=%s day(s).", interval_hours, window_days)
    return scheduler
