import os

# Same guard as test_web.py, and needed for the same reason -- this
# file also spins up the app via TestClient, which runs the real
# lifespan (scheduler + MQTT listener) unless these are set first.
# Belt-and-suspenders here since test_web.py already sets these at
# import time and pytest collects every file before running any test,
# but running this file alone (`pytest tests/test_auth.py`) shouldn't
# depend on that.
os.environ.setdefault("SOLILOQUY_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SOLILOQUY_DISABLE_MQTT", "1")

from fastapi.testclient import TestClient

import soliloquy.auth as auth_module
from soliloquy.web.app import app


def _client() -> TestClient:
    # follow_redirects=False so redirect-vs-200 assertions below are
    # about what THIS request returned, not the page it points at.
    return TestClient(app, follow_redirects=False)


def test_protected_page_loads_fine_when_auth_is_disabled(monkeypatch):
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    with _client() as client:
        response = client.get("/")
        assert response.status_code == 200


def test_protected_page_redirects_to_login_when_auth_is_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        response = client.get("/")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_healthz_is_reachable_with_no_session_even_when_auth_is_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_wrong_password_is_rejected_and_stays_on_login(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        response = client.post("/login", data={"password": "wrong", "next": "/"})
        assert response.status_code == 401
        assert "Wrong password" in response.text

        # The session cookie a wrong password might still have set
        # (there isn't one here, but prove it either way) shouldn't
        # unlock a protected page.
        assert client.get("/").status_code == 303


def test_correct_password_sets_a_session_and_unlocks_protected_pages(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        response = client.post("/login", data={"password": "correct-horse", "next": "/"})
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        assert client.get("/").status_code == 200


def test_login_honors_a_safe_next_path_but_ignores_an_open_redirect(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        safe = client.post("/login", data={"password": "correct-horse", "next": "/report"})
        assert safe.headers["location"] == "/report"

    with _client() as client:
        unsafe = client.post("/login", data={"password": "correct-horse", "next": "//evil.example.com"})
        assert unsafe.headers["location"] == "/"


def test_logout_clears_the_session(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    with _client() as client:
        client.post("/login", data={"password": "correct-horse", "next": "/"})
        assert client.get("/").status_code == 200

        logout = client.post("/logout")
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"
        assert client.get("/").status_code == 303


def test_repeated_wrong_passwords_trigger_a_lockout(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    auth_module._failed_attempts.clear()
    with _client() as client:
        for _ in range(auth_module._MAX_ATTEMPTS):
            client.post("/login", data={"password": "wrong", "next": "/"})

        locked = client.post("/login", data={"password": "correct-horse", "next": "/"})
        assert locked.status_code == 429
        assert "Too many attempts" in locked.text
    auth_module._failed_attempts.clear()


def test_check_password_rejects_when_auth_password_unset(monkeypatch):
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    assert auth_module.check_password("") is False
    assert auth_module.check_password("anything") is False


def test_describe_auth_mode_reflects_whether_a_password_is_set(monkeypatch):
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    assert "DISABLED" in auth_module.describe_auth_mode()

    monkeypatch.setenv("AUTH_PASSWORD", "correct-horse")
    assert "ENABLED" in auth_module.describe_auth_mode()


def test_lockout_expires_after_the_window(monkeypatch):
    monkeypatch.setattr(auth_module, "_LOCKOUT_SECONDS", 0)
    auth_module._failed_attempts.clear()
    client_id = "test-client"
    for _ in range(auth_module._MAX_ATTEMPTS):
        auth_module.record_failed_attempt(client_id)

    assert auth_module.is_locked_out(client_id) is False  # window is 0s, already expired
    auth_module._failed_attempts.clear()
