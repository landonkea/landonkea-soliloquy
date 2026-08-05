# ───────────────────────────────────────────────────────────────────
# entry.py — the core data model: one journal entry
# ───────────────────────────────────────────────────────────────────
# This is deliberately the smallest possible representation of "one
# thing the user said to their journal." Everything else in this app
# (recording, transcription, analysis) either PRODUCES an Entry or
# CONSUMES one — nothing else needs to know how an Entry got created.
#
# audio_path is Optional because the MVP supports typed (text-only)
# entries too, e.g. for testing the storage/analysis layers without
# needing a microphone or a transcription model set up yet — see
# README.md's "What's built vs. what's next" section.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


@dataclass
class Entry:
    transcript: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audio_path: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def word_count(self) -> int:
        return len(self.transcript.split())
