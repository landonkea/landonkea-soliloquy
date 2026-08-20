# ───────────────────────────────────────────────────────────────────
# auth.py, a single-user password gate.
# ───────────────────────────────────────────────────────────────────
# Soliloquy has no user accounts, and doesn't need any -- it's one
# journal for one person. What it does need is a lock on the door
# before this ever sits somewhere less trusted than a home LAN (see
# deployment_mode.py): everything in here, transcripts, sharing flags,
# therapist-facing reports, is the kind of content that shouldn't be
# readable by "anyone who can reach the IP."
#
# No third-party identity provider, no paid auth service -- one
# password, compared in constant time against $AUTH_PASSWORD, backed
# by Starlette's own signed-cookie session (itsdangerous, already an
# indirect dependency of `web`'s FastAPI stack, just not wired up
# until now). Everything involved (Starlette, itsdangerous) is
# BSD/MIT-licensed, free and open source, nothing phones home.
#
# Off by default, like ANALYZER_PROVIDER and deployment_mode.py's
# posture elsewhere in this app: if $AUTH_PASSWORD isn't set, nothing
# is enforced (so a fresh clone with no .env still runs), but
# `describe_auth_mode()` prints a loud one-line warning at startup
# either way, the same pattern deployment_mode.py already uses for
# "here's the situation, informational, not a hard stop."
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

# Paths reachable with no session -- the login page itself, its static
# assets, the container HEALTHCHECK's target (curl never carries a
# browser session cookie, see Dockerfile), and signed report share
# links (report_store.py's expiring token IS the access control for
# that one route, see web/app.py's shared_report -- requiring a login
# on top would defeat the entire point of a link meant for someone
# who doesn't have an account here).
_EXEMPT_PATHS = {"/login", "/healthz"}
_EXEMPT_PREFIXES = ("/static/", "/reports/shared/")

# Failed attempts before a short lockout kicks in, and how long that
# lockout lasts. In-memory, per-process, same tradeoff as
# object_storage.py's single shared client -- this app runs as one
# process, so no shared cache is needed for this to work.
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 30

_failed_attempts: dict[str, list[float]] = defaultdict(list)


def is_auth_enabled() -> bool:
    return bool(os.environ.get("AUTH_PASSWORD"))


def describe_auth_mode() -> str:
    if is_auth_enabled():
        return "Auth ENABLED -- a password is required to reach any page or API route."
    return (
        "Auth DISABLED -- no AUTH_PASSWORD set, every page and API route is reachable with no "
        "login. Fine for local dev; set AUTH_PASSWORD before this is reachable from anywhere "
        "less trusted than your own machine."
    )


def is_locked_out(client_id: str) -> bool:
    attempts = _failed_attempts[client_id]
    cutoff = time.time() - _LOCKOUT_SECONDS
    _failed_attempts[client_id] = attempts = [t for t in attempts if t > cutoff]
    return len(attempts) >= _MAX_ATTEMPTS


def record_failed_attempt(client_id: str) -> None:
    _failed_attempts[client_id].append(time.time())


def clear_failed_attempts(client_id: str) -> None:
    _failed_attempts.pop(client_id, None)


def check_password(submitted: str) -> bool:
    expected = os.environ.get("AUTH_PASSWORD", "")
    # hmac.compare_digest, not `==` -- a plain string comparison
    # short-circuits on the first mismatched character, which leaks
    # how many characters were right via response timing. Not a
    # theoretical concern here specifically, but it's free to do right.
    return bool(expected) and hmac.compare_digest(submitted, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirects to /login unless the session already says
    "authenticated". A no-op entirely when AUTH_PASSWORD isn't set, so
    a fresh clone with no .env still runs with zero setup."""

    async def dispatch(self, request: Request, call_next):
        if not is_auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        if request.session.get("authenticated"):
            return await call_next(request)

        return RedirectResponse(url=f"/login?next={path}", status_code=303)
