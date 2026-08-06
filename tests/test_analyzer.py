import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from soliloquy.analyzer import (
    AnalysisResult,
    ClaudeAnalyzer,
    FallbackAnalyzer,
    GeminiAnalyzer,
    NoEntriesError,
    OpenRouterAnalyzer,
    RateLimitError,
    build_free_analyzer,
    get_default_analyzer,
)
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


def test_claude_raises_rate_limit_error_on_429():
    analyzer = ClaudeAnalyzer(api_key="fake-key")
    response = MagicMock()
    response.status_code = 429
    response.text = "rate limited"

    with patch("requests.post", return_value=response):
        with pytest.raises(RateLimitError):
            analyzer.analyze(_make_entries())


# ── OpenRouterAnalyzer ───────────────────────────────────────────────

def _mock_openrouter_response(json_body: dict, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.text = "" if status_code == 200 else json.dumps(json_body)
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(json_body)}}]}
    return response


def test_openrouter_analyzer_parses_a_well_formed_response():
    analyzer = OpenRouterAnalyzer("meta-llama/llama-3.1-8b-instruct:free", api_key="fake-key")
    fake_response = _mock_openrouter_response({"summary": "s", "mood_notes": "m", "key_topics": ["t"]})

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = analyzer.analyze(_make_entries())

    assert result.summary == "s"
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer fake-key"
    assert kwargs["json"]["model"] == "meta-llama/llama-3.1-8b-instruct:free"


def test_openrouter_analyzer_raises_clearly_when_no_api_key_is_available(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    analyzer = OpenRouterAnalyzer("some/model:free")
    with pytest.raises(RuntimeError, match="No OpenRouter API key"):
        analyzer.analyze(_make_entries())


def test_openrouter_analyzer_raises_rate_limit_error_on_429():
    analyzer = OpenRouterAnalyzer("some/model:free", api_key="fake-key")
    response = MagicMock()
    response.status_code = 429
    response.text = "rate limited"

    with patch("requests.post", return_value=response):
        with pytest.raises(RateLimitError):
            analyzer.analyze(_make_entries())


def test_openrouter_analyzer_strips_a_markdown_code_fence_before_parsing():
    analyzer = OpenRouterAnalyzer("some/model:free", api_key="fake-key")
    fenced = "```json\n" + json.dumps({"summary": "s", "mood_notes": "m", "key_topics": []}) + "\n```"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": fenced}}]}

    with patch("requests.post", return_value=response):
        result = analyzer.analyze(_make_entries())

    assert result.summary == "s"


# ── GeminiAnalyzer ───────────────────────────────────────────────────

def _mock_gemini_response(json_body: dict):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(json_body)}]}}]}
    return response


def test_gemini_analyzer_parses_a_well_formed_response():
    analyzer = GeminiAnalyzer(api_key="fake-key")
    fake_response = _mock_gemini_response({"summary": "s", "mood_notes": "m", "key_topics": ["t"]})

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = analyzer.analyze(_make_entries())

    assert result.summary == "s"
    _, kwargs = mock_post.call_args
    assert kwargs["params"]["key"] == "fake-key"


def test_gemini_analyzer_raises_clearly_when_no_api_key_is_available(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    analyzer = GeminiAnalyzer()
    with pytest.raises(RuntimeError, match="No Gemini API key"):
        analyzer.analyze(_make_entries())


def test_gemini_analyzer_raises_rate_limit_error_on_429():
    analyzer = GeminiAnalyzer(api_key="fake-key")
    response = MagicMock()
    response.status_code = 429
    response.text = "rate limited"

    with patch("requests.post", return_value=response):
        with pytest.raises(RateLimitError):
            analyzer.analyze(_make_entries())


# ── FallbackAnalyzer ─────────────────────────────────────────────────

class _StubAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.called = False

    def analyze(self, entries):
        self.called = True
        if self.error:
            raise self.error
        return self.result


def test_fallback_analyzer_returns_the_first_providers_result_without_trying_the_rest():
    good = _StubAnalyzer(result=AnalysisResult(1, 1, "s", "m", []))
    unused = _StubAnalyzer(result=AnalysisResult(1, 1, "unused", "unused", []))
    fallback = FallbackAnalyzer([good, unused])

    result = fallback.analyze(_make_entries())

    assert result.summary == "s"
    assert unused.called is False


def test_fallback_analyzer_moves_to_the_next_provider_on_any_failure():
    first = _StubAnalyzer(error=RateLimitError("rate limited"))
    second = _StubAnalyzer(result=AnalysisResult(1, 1, "from second", "m", []))
    fallback = FallbackAnalyzer([first, second])

    result = fallback.analyze(_make_entries())

    assert result.summary == "from second"


def test_fallback_analyzer_raises_with_all_provider_errors_when_everything_fails():
    first = _StubAnalyzer(error=RuntimeError("first failed"))
    second = _StubAnalyzer(error=RuntimeError("second failed"))
    fallback = FallbackAnalyzer([first, second])

    with pytest.raises(RuntimeError) as exc_info:
        fallback.analyze(_make_entries())
    message = str(exc_info.value)
    assert "All analyzer providers failed" in message
    assert "first failed" in message
    assert "second failed" in message


def test_fallback_analyzer_raises_no_entries_error_without_calling_any_provider():
    provider = _StubAnalyzer(result=AnalysisResult(0, 0, "", "", []))
    fallback = FallbackAnalyzer([provider])

    with pytest.raises(NoEntriesError):
        fallback.analyze([])

    assert provider.called is False


def test_fallback_analyzer_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        FallbackAnalyzer([])


# ── build_free_analyzer / get_default_analyzer ──────────────────────

def test_build_free_analyzer_chains_openrouter_models_then_gemini():
    fallback = build_free_analyzer(openrouter_models=["a/model:free", "b/model:free"])

    assert len(fallback.providers) == 3
    assert isinstance(fallback.providers[0], OpenRouterAnalyzer)
    assert fallback.providers[0].model == "a/model:free"
    assert isinstance(fallback.providers[1], OpenRouterAnalyzer)
    assert fallback.providers[1].model == "b/model:free"
    assert isinstance(fallback.providers[2], GeminiAnalyzer)


def test_get_default_analyzer_returns_a_free_fallback_chain_by_default(monkeypatch):
    monkeypatch.delenv("ANALYZER_PROVIDER", raising=False)
    assert isinstance(get_default_analyzer(), FallbackAnalyzer)


def test_get_default_analyzer_returns_claude_when_explicitly_selected(monkeypatch):
    monkeypatch.setenv("ANALYZER_PROVIDER", "claude")
    assert isinstance(get_default_analyzer(), ClaudeAnalyzer)


def test_get_default_analyzer_rejects_an_unknown_provider(monkeypatch):
    monkeypatch.setenv("ANALYZER_PROVIDER", "not-a-real-provider")
    with pytest.raises(ValueError):
        get_default_analyzer()
