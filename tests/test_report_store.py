import os
import time

import pytest

from soliloquy.report_store import SavedReport, SavedReportStore, make_share_token, resolve_share_token

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy_test"
)


@pytest.fixture
def store():
    s = SavedReportStore(TEST_DATABASE_URL)
    s._conn.execute("TRUNCATE TABLE saved_reports")
    yield s
    s.close()


def _report(**overrides):
    defaults = dict(days=30, audience="self", content="# Report\n\nsome content", source="manual")
    defaults.update(overrides)
    return SavedReport(**defaults)


def test_add_then_get_round_trips_a_report(store):
    report = _report()
    store.add(report)

    fetched = store.get(report.id)

    assert fetched.id == report.id
    assert fetched.content == "# Report\n\nsome content"
    assert fetched.audience == "self"
    assert fetched.source == "manual"


def test_get_returns_none_for_an_unknown_id(store):
    assert store.get("does-not-exist") is None


def test_recent_returns_newest_first_and_respects_limit(store):
    store.add(_report(content="a"))
    store.add(_report(content="b"))
    store.add(_report(content="c"))

    recent = store.recent(limit=2)

    assert [r.content for r in recent] == ["c", "b"]


# ── Signed share links ──────────────────────────────────────────────

def test_a_freshly_made_token_resolves_to_its_report_id():
    token = make_share_token("report-123", secret_key="test-secret")

    assert resolve_share_token(token, secret_key="test-secret") == "report-123"


def test_a_tampered_token_does_not_resolve():
    # Flips a character in the middle of the token (the payload
    # segment, not the very last base64 character) -- the last
    # character of a base64 group can have unused bits, so changing
    # it doesn't always change the decoded bytes, which made this
    # test flaky when it tampered with token[-1] instead.
    token = make_share_token("report-123", secret_key="test-secret")
    middle = len(token) // 2
    tampered = token[:middle] + ("a" if token[middle] != "a" else "b") + token[middle + 1:]

    assert resolve_share_token(tampered, secret_key="test-secret") is None


def test_a_token_signed_with_a_different_secret_does_not_resolve():
    token = make_share_token("report-123", secret_key="secret-a")

    assert resolve_share_token(token, secret_key="secret-b") is None


def test_an_expired_token_does_not_resolve():
    token = make_share_token("report-123", secret_key="test-secret", expires_in_days=0)
    time.sleep(0.01)

    assert resolve_share_token(token, secret_key="test-secret") is None


def test_a_token_with_a_longer_expiry_still_resolves_soon_after_creation():
    token = make_share_token("report-123", secret_key="test-secret", expires_in_days=7)

    assert resolve_share_token(token, secret_key="test-secret") == "report-123"
