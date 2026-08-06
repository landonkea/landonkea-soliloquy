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
#
# video_path is Optional and independent of audio_path -- a video
# entry always has BOTH set (the extracted audio track is stored and
# transcribed exactly like a standalone audio entry, see video.py),
# but audio-only and text-only entries never have a video_path. This
# is what lets face/expression analysis be added later as a new
# consumer of video_path without touching the transcript/analysis
# pipeline, which only ever needs the transcript.
#
# shareable_with_partner / shareable_with_provider: two INDEPENDENT
# flags, not one "privacy level" -- a partner and a therapist are
# genuinely different audiences, not points on the same spectrum
# (something clinically relevant for a provider may not belong in
# front of a partner, and something sweet to share with a partner may
# be irrelevant to a provider). Both default to False: every entry is
# private to the journal's owner alone until they explicitly mark it
# for one of these audiences, whenever they're actually getting ready
# to share something -- never assumed at capture time. See
# cli.py's `share` command and `report --audience`.
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
    video_path: Optional[str] = None
    shareable_with_partner: bool = False
    shareable_with_provider: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def word_count(self) -> int:
        return len(self.transcript.split())
