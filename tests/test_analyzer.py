import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from soliloquy.analyzer import ClaudeAnalyzer, NoEntriesError
from soliloquy.entry import Entry


def _make_entries():
    return [
        Entry(transcript="Today was a good day at work.", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Entry(transcript="Feeling a bit tired but okay.", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ]


def _mock_claude_response(json_body: dict, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.text = json.dumps(json_body) if status_code != 200 else ""
    response.json.return_value = {
        "content": [{"text": json.dumps(json_body)}]
    }
    return response


def test_analyze_raises_no_entries_error_for_an_empty_list():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    with pytest.raises(NoEntriesError):
        analyzer.analyze([])


def test_analyze_raises_clearly_when_no_api_key_is_available(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    analyzer = ClaudeAnalyzer()  # no explicit key, and env var cleared
    with pytest.raises(RuntimeError, match="No Anthropic API key"):
        analyzer.analyze(_make_entries())


def test_explicit_api_key_is_used_over_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    analyzer = ClaudeAnalyzer(api_key="explicit-key")
    assert analyzer.api_key == "explicit-key"


def test_falls_back_to_environment_variable_when_no_explicit_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    analyzer = ClaudeAnalyzer()
    assert analyzer.api_key == "env-key"


def test_analyze_parses_a_well_formed_response_into_an_analysisresult():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    entries = _make_entries()
    fake_response = _mock_claude_response({
        "summary": "You had a mix of good and tiring days.",
        "mood_notes": "Generally positive with some fatigue.",
        "key_topics": ["work", "tiredness"],
    })

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = analyzer.analyze(entries)

    assert result.entry_count == 2
    assert result.total_word_count == sum(e.word_count for e in entries)
    assert result.summary == "You had a mix of good and tiring days."
    assert result.mood_notes == "Generally positive with some fatigue."
    assert result.key_topics == ["work", "tiredness"]

    # Confirm the real request shape: headers and model.
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["x-api-key"] == "fake-key"
    assert kwargs["json"]["model"]
    assert "Today was a good day at work." in kwargs["json"]["messages"][0]["content"]
    assert "Feeling a bit tired but okay." in kwargs["json"]["messages"][0]["content"]


def test_analyze_raises_on_non_200_status(monkeypatch):
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    error_response = MagicMock()
    error_response.status_code = 401
    error_response.text = "unauthorized"

    with patch("requests.post", return_value=error_response):
        with pytest.raises(RuntimeError, match="401"):
            analyzer.analyze(_make_entries())


def test_analyze_raises_on_malformed_json_in_claude_response():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"content": [{"text": "this is not json"}]}

    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="did not return valid JSON"):
            analyzer.analyze(_make_entries())


def test_analyze_raises_when_response_is_missing_a_required_key():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    response = _mock_claude_response({"summary": "fine", "mood_notes": "fine"})  # missing key_topics

    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="key_topics"):
            analyzer.analyze(_make_entries())


def test_analyze_raises_on_unexpected_response_shape():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"unexpected": "shape"}

    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="Unexpected Claude API response shape"):
            analyzer.analyze(_make_entries())
