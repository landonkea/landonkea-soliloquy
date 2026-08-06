# ───────────────────────────────────────────────────────────────────
# cli.py — minimal command-line interface
# ───────────────────────────────────────────────────────────────────
# `add`/`list` are text-only. `record` captures real audio.
# `transcribe` (or `record --transcribe`) turns an audio file into a
# real Entry via a Transcriber (see transcriber.py) — the point where
# recording and text entries converge back onto the same add_entry()
# path, just with audio_path set too.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analyzer import Analyzer, AnalysisResult
from .entry import Entry
from .storage import EntryStore
from .transcriber import Transcriber


def add_entry(store: EntryStore, transcript: str) -> Entry:
    entry = Entry(transcript=transcript)
    store.add(entry)
    return entry


def add_entry_from_audio(store: EntryStore, transcriber: Transcriber, audio_path: str) -> Entry:
    """Transcribe `audio_path` and save the result as a real Entry with
    audio_path set — the point where a recorded file and a typed entry
    converge onto the same storage path."""
    transcript = transcriber.transcribe(audio_path)
    entry = Entry(transcript=transcript, audio_path=audio_path)
    store.add(entry)
    return entry


def list_entries(store: EntryStore) -> list[Entry]:
    return store.all()


def analyze_range(store: EntryStore, analyzer: Analyzer, days: int) -> AnalysisResult:
    """Analyze the last `days` days of entries — the building block
    behind `soliloquy analyze --days N`. Reuses EntryStore's
    range_between exactly as documented (see storage.py's module
    comment on why that method exists in the first place)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    entries = store.range_between(start, end)
    return analyzer.analyze(entries)


def record_entry(recordings_dir: str = "recordings") -> str:
    """Record real audio to a timestamped .wav file, stopping when the
    user presses Enter. Returns the file's path. Does NOT transcribe —
    see add_entry_from_audio()/`soliloquy transcribe` for that, or use
    `soliloquy record --transcribe` to chain both steps.

    See recorder.py's module comment for why manual stop (not
    silence-detection) is the right default for a reflective entry
    rather than a short command."""
    from .recorder import record_to_file  # deferred: only needed here, keeps `add`/`list` free of the pyaudio dependency

    Path(recordings_dir).mkdir(parents=True, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S") + ".wav"
    output_path = str(Path(recordings_dir) / filename)

    stop_event = threading.Event()

    def wait_for_enter():
        input()
        stop_event.set()

    listener = threading.Thread(target=wait_for_enter, daemon=True)
    listener.start()

    print("Recording... press Enter to stop.")
    try:
        duration = record_to_file(output_path, stop_event)
    except RuntimeError as exc:
        print(f"Recording failed: {exc}")
        raise
    print(f"Saved {duration:.1f}s of audio to {output_path}")

    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soliloquy", description="A voice-first journal.")
    parser.add_argument("--db", default="soliloquy.db", help="Path to the SQLite database file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new journal entry.")
    add_parser.add_argument("text", help="The entry text.")

    subparsers.add_parser("list", help="List all journal entries.")

    record_parser = subparsers.add_parser("record", help="Record a voice entry (press Enter to stop).")
    record_parser.add_argument("--dir", default="recordings", help="Directory to save the .wav file in.")
    record_parser.add_argument(
        "--transcribe", action="store_true",
        help="Transcribe immediately after recording and save as a real entry (requires the [transcribe] extra)."
    )

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe an existing audio file into a real entry.")
    transcribe_parser.add_argument("audio_path", help="Path to the .wav file to transcribe.")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze recent entries (requires ANTHROPIC_API_KEY).")
    analyze_parser.add_argument("--days", type=int, default=7, help="How many days back to analyze (default: 7).")

    args = parser.parse_args(argv)

    if args.command == "record" and not args.transcribe:
        # No database needed for this path -- plain `record` just saves
        # the audio file, see record_entry()'s docstring.
        record_entry(args.dir)
        return 0

    store = EntryStore(args.db)
    try:
        if args.command == "add":
            entry = add_entry(store, args.text)
            print(f"Saved entry {entry.id} ({entry.word_count} words) at {entry.created_at.isoformat()}")
        elif args.command == "record":
            audio_path = record_entry(args.dir)
            from .transcriber import WhisperTranscriber
            print("Transcribing...")
            entry = add_entry_from_audio(store, WhisperTranscriber(), audio_path)
            print(f"Saved entry {entry.id}: \"{entry.transcript}\"")
        elif args.command == "transcribe":
            from .transcriber import WhisperTranscriber
            print("Transcribing...")
            entry = add_entry_from_audio(store, WhisperTranscriber(), args.audio_path)
            print(f"Saved entry {entry.id}: \"{entry.transcript}\"")
        elif args.command == "list":
            entries = list_entries(store)
            if not entries:
                print("No entries yet.")
            for entry in entries:
                print(f"[{entry.created_at.isoformat()}] {entry.transcript}")
        elif args.command == "analyze":
            from .analyzer import ClaudeAnalyzer, NoEntriesError
            try:
                result = analyze_range(store, ClaudeAnalyzer(), args.days)
            except NoEntriesError:
                print(f"No entries in the last {args.days} days to analyze.")
                return 0
            print(f"\n{result.entry_count} entries, {result.total_word_count} words, last {args.days} days\n")
            print(f"Summary: {result.summary}\n")
            print(f"Mood: {result.mood_notes}\n")
            print(f"Key topics: {', '.join(result.key_topics)}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
