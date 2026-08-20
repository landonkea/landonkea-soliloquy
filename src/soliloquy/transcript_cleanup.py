# ───────────────────────────────────────────────────────────────────
# transcript_cleanup.py, rule-based readability pass after transcription
# ───────────────────────────────────────────────────────────────────
# Raw Whisper output (see transcriber.py) is one unbroken run of text
# with sparse punctuation -- fine for the analyzer (which reads for
# meaning, not style), but noticeably harder to read back in the
# Entries page or an exported report. This is a plain rule-based pass,
# not another AI call: deterministic, free, and testable, and "make
# this readable" doesn't need a model, just capitalization/terminal
# punctuation/paragraph breaks. Only applied to transcribed (audio/
# video) entries -- typed entries are already however the person chose
# to format them, and shouldn't be silently rewritten.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SENTENCES_PER_PARAGRAPH = 4


def clean_transcript(text: str) -> str:
    text = " ".join(text.split())  # collapse whisper's occasional double spaces/newlines
    if not text:
        return text

    sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]
    sentences = [s[0].upper() + s[1:] if s else s for s in sentences]
    sentences = [s if s[-1] in ".!?" else s + "." for s in sentences]

    paragraphs = [
        " ".join(sentences[i:i + _SENTENCES_PER_PARAGRAPH])
        for i in range(0, len(sentences), _SENTENCES_PER_PARAGRAPH)
    ]
    return "\n\n".join(paragraphs)
