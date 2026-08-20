#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────
# One-time backfill: encrypt every plaintext transcript already in
# the database, once TRANSCRIPT_ENCRYPTION_KEY has been set for the
# first time. See storage.py's module docstring for why this ISN'T
# run automatically on every startup: turning encryption on only
# affects new writes going forward, and re-encrypting real journal
# entries is a deliberate, one-time action, not something to trigger
# silently as a side effect of a routine schema check.
#
# Safe to run more than once -- rows already encrypted (the "enc1:"
# prefix, see storage.py) are skipped, not double-encrypted.
#
# Usage:
#   TRANSCRIPT_ENCRYPTION_KEY=... DATABASE_URL=... python scripts/encrypt_existing_transcripts.py
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soliloquy.actions import DEFAULT_DATABASE_URL  # noqa: E402
from soliloquy.storage import EntryStore, _ENCRYPTED_PREFIX  # noqa: E402


def main() -> None:
    key = os.environ.get("TRANSCRIPT_ENCRYPTION_KEY")
    if not key:
        print("TRANSCRIPT_ENCRYPTION_KEY is not set -- nothing to do. Set it first, see .env.example.")
        return

    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    with EntryStore(database_url, encryption_key=key) as store:
        rows = store._conn.execute("SELECT id, transcript FROM entries").fetchall()
        plaintext_rows = [(entry_id, transcript) for entry_id, transcript in rows if not transcript.startswith(_ENCRYPTED_PREFIX)]

        if not plaintext_rows:
            print(f"Nothing to do -- all {len(rows)} entries are already encrypted.")
            return

        print(f"Encrypting {len(plaintext_rows)} of {len(rows)} entries...")
        for entry_id, transcript in plaintext_rows:
            store._conn.execute(
                "UPDATE entries SET transcript = %s WHERE id = %s",
                (store._encrypt(transcript), entry_id),
            )
        print(f"Done. {len(plaintext_rows)} entries encrypted.")


if __name__ == "__main__":
    main()
