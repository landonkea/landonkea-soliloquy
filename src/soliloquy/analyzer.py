# ───────────────────────────────────────────────────────────────────
# analyzer.py, turns a batch of entries into real analysis
# ───────────────────────────────────────────────────────────────────
# This is the actual point of the app -- everything else (recording,
# transcription, storage) exists to feed this. A real Analyzer
# protocol: ClaudeAnalyzer, OpenRouterAnalyzer, and GeminiAnalyzer are
# three implementations of it, not the only possible ones, and
# FallbackAnalyzer composes any of them into a "try each in order"
# chain -- built specifically so analysis can run at zero ongoing cost
# (OpenRouter's free-tagged models, then Gemini's free tier) without
# giving up Claude as an option for anyone who wants to pay for it.
#
# All three providers share the exact same prompt (_build_prompt) and
# response contract/parsing (_parse_response) -- they're asked for,
# and parsed from, the identical JSON shape, so that logic lives once
# instead of three times.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

import requests

from .entry import Entry

CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_API_VERSION = "2023-06-01"

# OpenRouter's catalog of free-tagged models changes over time -- this
# is a reasonable default set as of when this was written, not a
# guarantee any of them stay free or available forever. Override via
# build_free_analyzer(openrouter_models=[...]) if these stop working.
DEFAULT_OPENROUTER_FREE_MODELS = [
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-2-9b-it:free",
    "mistralai/mistral-7b-instruct:free",
]

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


@dataclass
class AnalysisResult:
    entry_count: int
    total_word_count: int
    summary: str
    mood_notes: str
    key_topics: list[str]


class Analyzer(Protocol):
    def analyze(self, entries: list[Entry]) -> AnalysisResult:
        """Analyze a batch of entries (already filtered to whatever date
        range the caller wants -- see EntryStore.range_between). Should
        raise on failure (missing credentials, API error, malformed
        response) rather than returning a fabricated or empty result
        that looks like a real answer."""
        ...


class NoEntriesError(Exception):
    """Raised when asked to analyze an empty list of entries -- there's
    nothing honest an Analyzer can say about zero entries, so this is
    a caller error to catch and message clearly, not something an
    Analyzer implementation should paper over with a placeholder result."""


class RateLimitError(RuntimeError):
    """Raised specifically for HTTP 429s -- a distinguishable subclass
    of RuntimeError so a caller (e.g. FallbackAnalyzer) can tell "this
    provider is temporarily out of quota" apart from other failures,
    even though today's FallbackAnalyzer treats all failures the same
    (moves to the next provider) -- kept distinct for when that ever
    needs to change (e.g. retry-after handling)."""


def _build_prompt(entries: list[Entry]) -> str:
    entries_block = "\n\n".join(
        f"[{entry.created_at.isoformat()}] {entry.transcript}" for entry in entries
    )
    return (
        "You are analyzing a personal voice journal's entries for the writer's own review "
        "(and possibly to share with a therapist). Be honest and specific, not generic or "
        "falsely upbeat. Respond with ONLY a JSON object, no other text, in exactly this shape:\n"
        '{"summary": "a few honest sentences about what stands out across these entries", '
        '"mood_notes": "a few sentences specifically about emotional tone/trends, if any are '
        'genuinely visible -- say so plainly if the entries don\'t show a clear trend, don\'t '
        'invent one", '
        '"key_topics": ["topic1", "topic2", ...]}\n\n'
        f"Entries:\n\n{entries_block}"
    )


def _strip_code_fence(text: str) -> str:
    # Claude reliably returns raw JSON when asked to, but smaller/free
    # models often wrap it in a ```json ... ``` fence regardless of
    # instructions -- stripped here so every provider's response goes
    # through the same JSON parsing, not just Claude's.
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -len("```")]
        if text.startswith("json"):
            text = text[len("json"):]
    return text.strip()


def _parse_response(response_text: str, entry_count: int, entries: list[Entry], provider_name: str) -> AnalysisResult:
    cleaned = _strip_code_fence(response_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider_name} did not return valid JSON: {response_text!r}") from exc

    for required_key in ("summary", "mood_notes", "key_topics"):
        if required_key not in parsed:
            raise RuntimeError(f"{provider_name}'s response is missing required key \"{required_key}\": {parsed!r}")

    return AnalysisResult(
        entry_count=entry_count,
        total_word_count=sum(entry.word_count for entry in entries),
        summary=parsed["summary"],
        mood_notes=parsed["mood_notes"],
        key_topics=list(parsed["key_topics"]),
    )


class _HttpAnalyzer:
    """Shared analyze()/error-handling for all three HTTP-based
    providers below -- each subclass only supplies how to make its
    request and how to pull the text out of its own response shape.
    Collapses what used to be ~90 lines of near-identical
    empty-check/key-check/429-check/parse code (repeated once per
    provider) into one place."""

    key_label: str = ""        # e.g. "Anthropic" -- used in the "No X API key found" message
    api_key_env_var: str = ""  # e.g. "ANTHROPIC_API_KEY"

    def __init__(self, api_key: str | None = None):
        # Falls back to the environment, matching makeItSoNumberOne's
        # convention, but accepts an explicit key too so tests (and
        # anyone running multiple profiles) don't have to mutate
        # process-wide environment variables.
        self.api_key = api_key or os.environ.get(self.api_key_env_var, "")

    @property
    def _label(self) -> str:
        """Used in rate-limit/API-error/parse-error messages. Overridden
        by providers that vary by model (OpenRouter, Gemini) to include
        which model, so FallbackAnalyzer's aggregated errors stay
        debuggable about exactly which model failed."""
        return self.key_label

    def analyze(self, entries: list[Entry]) -> AnalysisResult:
        if not entries:
            raise NoEntriesError("Cannot analyze an empty list of entries.")
        if not self.api_key:
            raise RuntimeError(
                f"No {self.key_label} API key found. Set {self.api_key_env_var} or pass api_key= explicitly."
            )

        response = self._request(_build_prompt(entries))
        if response.status_code == 429:
            raise RateLimitError(f"{self._label} rate limited: {response.text}")
        if response.status_code != 200:
            raise RuntimeError(f"{self._label} API error: {response.status_code} {response.text}")

        data = response.json()
        try:
            text = self._extract_text(data)
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected {self._label} response shape: {data}") from exc

        return _parse_response(text, len(entries), entries, self._label)

    def _request(self, prompt: str):  # -> requests.Response, subclass provides
        raise NotImplementedError

    def _extract_text(self, data: dict) -> str:
        raise NotImplementedError


class ClaudeAnalyzer(_HttpAnalyzer):
    # The service is "Anthropic" (used in the missing-key message, to
    # match its env var name) but the model/product is "Claude" (used
    # everywhere else -- rate-limit/API-error/parse-error messages).
    key_label = "Anthropic"
    api_key_env_var = "ANTHROPIC_API_KEY"

    @property
    def _label(self) -> str:
        return "Claude"

    def _request(self, prompt: str):
        return requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": CLAUDE_API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )

    def _extract_text(self, data: dict) -> str:
        return data["content"][0]["text"]


class OpenRouterAnalyzer(_HttpAnalyzer):
    """OpenRouter (https://openrouter.ai) fronts many models behind one
    OpenAI-compatible API -- only models with a `:free` suffix cost
    nothing, everything else is billed. One instance = one specific
    model; see build_free_analyzer() for chaining several free models
    together with FallbackAnalyzer."""

    key_label = "OpenRouter"
    api_key_env_var = "OPENROUTER_API_KEY"

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        super().__init__(api_key)

    @property
    def _label(self) -> str:
        return f"OpenRouter ({self.model})"

    def _request(self, prompt: str):
        return requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )

    def _extract_text(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]


class GeminiAnalyzer(_HttpAnalyzer):
    """Google's Gemini API has a real, ongoing free tier (rate-limited,
    not a one-time trial) -- the last resort in build_free_analyzer()'s
    chain, after OpenRouter's free models."""

    key_label = "Gemini"
    api_key_env_var = "GEMINI_API_KEY"

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL, api_key: str | None = None):
        self.model = model
        super().__init__(api_key)

    @property
    def _label(self) -> str:
        return f"Gemini ({self.model})"

    def _request(self, prompt: str):
        return requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )

    def _extract_text(self, data: dict) -> str:
        return data["candidates"][0]["content"]["parts"][0]["text"]


class FallbackAnalyzer:
    """Tries each provider in order, moving to the next on ANY failure
    -- missing/invalid key, rate limit, malformed response. Only raises
    if every provider fails, and that error lists what each one said,
    so a chain of free providers failing is still debuggable instead of
    silently swallowed. NoEntriesError is checked once up front and
    propagates immediately -- an empty entry list isn't a provider
    problem, so there's no reason to try (and fail) every provider to
    discover that."""

    def __init__(self, providers: list[Analyzer]):
        if not providers:
            raise ValueError("FallbackAnalyzer needs at least one provider")
        self.providers = providers

    def analyze(self, entries: list[Entry]) -> AnalysisResult:
        if not entries:
            raise NoEntriesError("Cannot analyze an empty list of entries.")

        errors = []
        for provider in self.providers:
            try:
                return provider.analyze(entries)
            except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
                errors.append(f"{type(provider).__name__}: {exc}")

        raise RuntimeError("All analyzer providers failed:\n" + "\n".join(errors))


def build_free_analyzer(
    openrouter_models: list[str] | None = None, gemini_model: str = DEFAULT_GEMINI_MODEL
) -> FallbackAnalyzer:
    """The default $0 chain: try each OpenRouter free-tagged model in
    order, then fall back to Gemini's free tier. Set OPENROUTER_API_KEY
    and/or GEMINI_API_KEY -- a provider with no key configured just
    fails over to the next one (see FallbackAnalyzer), so this works
    fine with only one of the two actually set."""
    models = openrouter_models if openrouter_models is not None else DEFAULT_OPENROUTER_FREE_MODELS
    providers: list[Analyzer] = [OpenRouterAnalyzer(model) for model in models]
    providers.append(GeminiAnalyzer(gemini_model))
    return FallbackAnalyzer(providers)


def get_default_analyzer() -> Analyzer:
    """The analyzer cli.py/web/app.py actually use, chosen via
    $ANALYZER_PROVIDER: "free" (default) -> build_free_analyzer(), or
    "claude" -> ClaudeAnalyzer(). "free" is the default specifically so
    running this app doesn't cost anything unless you opt into Claude."""
    provider = os.environ.get("ANALYZER_PROVIDER", "free")
    if provider == "claude":
        return ClaudeAnalyzer()
    if provider == "free":
        return build_free_analyzer()
    raise ValueError(f"Unknown ANALYZER_PROVIDER {provider!r}, must be 'free' or 'claude'")
