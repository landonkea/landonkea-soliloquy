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

from .entry import Entry
from .storage import EntryStore


def add_entry(store: EntryStore, transcript: str) -> Entry:
    entry = Entry(transcript=transcript)
    store.add(entry)
    return entry


def list_entries(store: EntryStore) -> list[Entry]:
    return store.all()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soliloquy", description="A voice-first journal.")
    parser.add_argument("--db", default="soliloquy.db", help="Path to the SQLite database file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new journal entry.")
    add_parser.add_argument("text", help="The entry text.")

    subparsers.add_parser("list", help="List all journal entries.")

    args = parser.parse_args(argv)
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
