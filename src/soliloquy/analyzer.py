# ───────────────────────────────────────────────────────────────────
# analyzer.py — turns a batch of entries into real analysis
# ───────────────────────────────────────────────────────────────────
# This is the actual point of the app -- everything else (recording,
# transcription, storage) exists to feed this. A real Analyzer
# protocol, same reasoning as Transcriber: ClaudeAnalyzer is the
# first implementation, not the only possible one.
#
# Follows landonkea-makeItSoNumberOne's exact Claude API convention
# (raw `requests` POST to api.anthropic.com, "x-api-key" header, same
# "anthropic-version" pin) for consistency across this whole project
# rather than introducing a second way of doing the same thing.
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


class ClaudeAnalyzer:
    def __init__(self, api_key: str | None = None):
        # Falls back to the environment, matching makeItSoNumberOne's
        # convention, but accepts an explicit key too so tests (and
        # anyone running multiple profiles) don't have to mutate
        # process-wide environment variables.
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def analyze(self, entries: list[Entry]) -> AnalysisResult:
        if not entries:
            raise NoEntriesError("Cannot analyze an empty list of entries.")
        if not self.api_key:
            raise RuntimeError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY or pass api_key= explicitly."
            )

        prompt = self._build_prompt(entries)
        response_text = self._call_claude(prompt)
        return self._parse_response(response_text, entry_count=len(entries), entries=entries)

    def _build_prompt(self, entries: list[Entry]) -> str:
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

    def _call_claude(self, prompt: str) -> str:
        response = requests.post(
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
        if response.status_code != 200:
            raise RuntimeError(f"Claude API error: {response.status_code} {response.text}")

        data = response.json()
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Claude API response shape: {data}") from exc

    def _parse_response(self, response_text: str, entry_count: int, entries: list[Entry]) -> AnalysisResult:
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude did not return valid JSON: {response_text!r}") from exc

        for required_key in ("summary", "mood_notes", "key_topics"):
            if required_key not in parsed:
                raise RuntimeError(f"Claude's response is missing required key \"{required_key}\": {parsed!r}")

        return AnalysisResult(
            entry_count=entry_count,
            total_word_count=sum(entry.word_count for entry in entries),
            summary=parsed["summary"],
            mood_notes=parsed["mood_notes"],
            key_topics=list(parsed["key_topics"]),
        )
