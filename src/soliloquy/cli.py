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
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analyzer import Analyzer, AnalysisResult
from .entry import Entry
from .report import FORMATS, build_report_content, format_html, format_markdown, format_pdf, format_text
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


def _transcribe_and_print(store: EntryStore, audio_path: str) -> None:
    """Shared by the `record --transcribe` and `transcribe` CLI paths —
    both end up doing exactly this, just from a different audio source."""
    from .transcriber import WhisperTranscriber  # deferred: keeps `add`/`list`/`record` (no --transcribe) free of the faster-whisper dependency

    print("Transcribing...")
    entry = add_entry_from_audio(store, WhisperTranscriber(), audio_path)
    print(f"Saved entry {entry.id}: \"{entry.transcript}\"")


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


AUDIENCES = ("self", "partner", "provider")


def report_range(store: EntryStore, analyzer: Analyzer, days: int, audience: str) -> tuple[AnalysisResult, list[Entry]]:
    """Like analyze_range, but audience-aware: for "partner"/"provider",
    entries are filtered down to only those explicitly marked shareable
    with that audience BEFORE they ever reach the Analyzer. Filtering
    only the displayed transcripts afterward would still let private
    entries leak into the AI-generated summary text itself -- the
    analyzer must never see an entry it isn't allowed to describe."""
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown audience {audience!r}, must be one of {AUDIENCES}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    entries = store.range_between(start, end)

    if audience == "partner":
        entries = [e for e in entries if e.shareable_with_partner]
    elif audience == "provider":
        entries = [e for e in entries if e.shareable_with_provider]

    result = analyzer.analyze(entries)
    return result, entries


def format_report(result: AnalysisResult, entries: list[Entry], audience: str, days: int) -> str:
    """A readable, shareable write-up in plain text -- the actual point
    of `report` over `analyze` (which is a quick self-facing terminal
    glance). See report.py for markdown/html/pdf renderings of the
    same content, all built from the same ReportContent."""
    return format_text(build_report_content(result, entries, audience, days))


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


DEFAULT_DATABASE_URL = "postgresql://soliloquy:soliloquy@localhost:5433/soliloquy"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soliloquy", description="A voice-first journal.")
    parser.add_argument(
        "--db", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="Postgres connection string (default: $DATABASE_URL, or the local docker-compose db).",
    )
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

    share_parser = subparsers.add_parser("share", help="Mark an existing entry as shareable with an audience.")
    share_parser.add_argument("entry_id", help="The entry's id (see `soliloquy list`).")
    share_parser.add_argument("--partner", action=argparse.BooleanOptionalAction, default=None,
                               help="Set (--partner) or clear (--no-partner) shareable_with_partner.")
    share_parser.add_argument("--provider", action=argparse.BooleanOptionalAction, default=None,
                               help="Set (--provider) or clear (--no-provider) shareable_with_provider.")

    report_parser = subparsers.add_parser(
        "report", help="Generate a readable, audience-filtered report (requires ANTHROPIC_API_KEY)."
    )
    report_parser.add_argument("--days", type=int, default=7, help="How many days back to report on (default: 7).")
    report_parser.add_argument("--audience", choices=AUDIENCES, default="self",
                                help="Who this report is for -- 'self' sees everything; "
                                     "'partner'/'provider' only see entries explicitly shared with them.")
    report_parser.add_argument("--format", choices=FORMATS, default="text",
                                help="Report format (default: text). 'pdf' requires --output, "
                                     "since PDF bytes can't be printed to a terminal.")
    report_parser.add_argument("--output", help="Write the report to this file instead of printing it.")

    args = parser.parse_args(argv)

    if args.command == "record" and not args.transcribe:
        # No database needed for this path -- plain `record` just saves
        # the audio file, see record_entry()'s docstring.
        record_entry(args.dir)
        return 0

    with EntryStore(args.db) as store:
        if args.command == "add":
            entry = add_entry(store, args.text)
            print(f"Saved entry {entry.id} ({entry.word_count} words) at {entry.created_at.isoformat()}")
        elif args.command == "record":
            audio_path = record_entry(args.dir)
            _transcribe_and_print(store, audio_path)
        elif args.command == "transcribe":
            _transcribe_and_print(store, args.audio_path)
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
        elif args.command == "share":
            if args.partner is None and args.provider is None:
                print("Nothing to change -- pass --partner/--no-partner and/or --provider/--no-provider.")
                return 1
            updated = store.update_sharing(
                args.entry_id, shareable_with_partner=args.partner, shareable_with_provider=args.provider
            )
            if not updated:
                print(f"No entry found with id {args.entry_id}")
                return 1
            print(f"Updated sharing for entry {args.entry_id}.")
        elif args.command == "report":
            from .analyzer import ClaudeAnalyzer, NoEntriesError
            if args.format == "pdf" and not args.output:
                print("--format pdf requires --output <file.pdf> -- PDF bytes can't be printed to a terminal.")
                return 1
            try:
                result, entries = report_range(store, ClaudeAnalyzer(), args.days, args.audience)
            except NoEntriesError:
                print(f"No entries for audience '{args.audience}' in the last {args.days} days.")
                return 0
            content = build_report_content(result, entries, args.audience, args.days)
            renderer = {"text": format_text, "markdown": format_markdown, "html": format_html, "pdf": format_pdf}[args.format]
            report_output = renderer(content)
            if args.output:
                if args.format == "pdf":
                    Path(args.output).write_bytes(report_output)
                else:
                    Path(args.output).write_text(report_output)
                print(f"Wrote report to {args.output}")
            else:
                print(report_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
