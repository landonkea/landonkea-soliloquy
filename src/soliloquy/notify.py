# ───────────────────────────────────────────────────────────────────
# notify.py, a local desktop notification when a new snapshot lands
# ───────────────────────────────────────────────────────────────────
# The Analysis page is pull-only otherwise -- nothing tells you a new
# snapshot exists until you open it. macOS only (`osascript`, this
# already runs as a local-first Mac app per README), and a plain no-op
# everywhere else, including inside the Linux container (see
# Dockerfile): no `osascript` on PATH there, so this quietly does
# nothing rather than erroring. Never raises -- a notification failing
# shouldn't take down the scheduled job that triggered it, same
# reasoning as scheduler.py's own try/except around the analysis run.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def send_desktop_notification(title: str, message: str) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return

    script = f'display notification {_applescript_string(message)} with title {_applescript_string(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    except Exception as exc:  # noqa: BLE001 -- a notification failing is never worth crashing over
        logger.warning("Desktop notification failed: %s", exc)


def _applescript_string(text: str) -> str:
    # AppleScript string literals: wrap in double quotes, escape
    # backslashes first (so the quote-escaping below doesn't get
    # double-escaped) then literal double quotes, so an AI-generated
    # summary containing either can't break out of the string or
    # inject extra script.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
