# ───────────────────────────────────────────────────────────────────
# cli.py — minimal command-line interface
# ───────────────────────────────────────────────────────────────────
# Text-only for now (see README's "What's built vs. what's next") —
# this exists so the storage layer is exercised by a real, typeable
# workflow today, not just unit tests. Voice recording/transcription
# plugs in later as an alternative way to produce the same `transcript`
# string this already knows how to store and list — nothing here
# changes shape when that lands, it's just another way to get text
# into add_entry().
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .entry import Entry
from .storage import EntryStore


def add_entry(store: EntryStore, transcript: str) -> Entry:
    entry = Entry(transcript=transcript)
    store.add(entry)
    return entry


def list_entries(store: EntryStore) -> list[Entry]:
    return store.all()


def record_entry(recordings_dir: str = "recordings") -> str:
    """Record real audio to a timestamped .wav file, stopping when the
    user presses Enter. Returns the file's path.

    Deliberately does NOT create an Entry yet — transcription (next
    piece) is what turns a recorded file into a real journal entry;
    see recorder.py's module comment for why manual stop (not
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

    record_parser = subparsers.add_parser(
        "record", help="Record a voice entry (press Enter to stop). Transcription isn't wired up yet -- saves the audio file only."
    )
    record_parser.add_argument("--dir", default="recordings", help="Directory to save the .wav file in.")

    args = parser.parse_args(argv)

    if args.command == "record":
        # No database needed for this command yet -- see record_entry()'s
        # docstring for why it doesn't create an Entry until transcription exists.
        record_entry(args.dir)
        return 0

    store = EntryStore(args.db)
    try:
        if args.command == "add":
            entry = add_entry(store, args.text)
            print(f"Saved entry {entry.id} ({entry.word_count} words) at {entry.created_at.isoformat()}")
        elif args.command == "list":
            entries = list_entries(store)
            if not entries:
                print("No entries yet.")
            for entry in entries:
                print(f"[{entry.created_at.isoformat()}] {entry.transcript}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
