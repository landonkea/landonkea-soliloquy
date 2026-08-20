# Things only you can do

Not code, these need your phone, your voice, an account you'd create, or a decision about
tradeoffs. Ask "what's left for me to do" any time and this is the reference.

## Testing
- [ ] Test a real recording with the fixed volume boost (loudnorm swap), the whole reason this
      was flagged before.
- [ ] Test video capture from an actual phone (only synthesized test clips verified so far).
- [ ] Record a real voice entry to hear how DeepFilterNet's noise isolation actually sounds,
      testing so far used macOS text-to-speech, not a real human voice.

## Accounts / credentials only you can set
- [ ] Set a real `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, or `GEMINI_API_KEY` so Report/Analysis
      can be verified against a real live AI response (only the "no key configured" error path has
      been tested so far).
- [ ] Pick and create an account with Cloudflare R2 or Backblaze B2 (both ~10GB free) for offsite
      backup redundancy, see README's Backups section. Once you have credentials, hand them over
      and the encrypted push (via restic) can be wired into `scripts/backup.sh`.

## Decisions
- [ ] Set `AUTH_PASSWORD` (and a fixed `SESSION_SECRET_KEY`) in `.env` whenever this becomes
      reachable by anything other than just you on this Mac -- another device on your LAN, a real
      deploy, either counts. Off by default today (see `src/soliloquy/auth.py`), which is fine
      while it's only ever `localhost`, not fine the moment that's no longer true. Generate a
      session key with `python -c "import secrets; print(secrets.token_hex(32))"`.
- [ ] LAN security hardening: Postgres/MinIO/Mosquitto currently use default dev credentials and
      are reachable by anything else on your home network, not just this Mac. Fine for a trusted
      home network; say the word and this gets locked down (real credentials, auth on Mosquitto,
      or binding those three to `127.0.0.1` while only the web app stays on `0.0.0.0`).
  - Router-level DHCP config, so every device on the LAN resolves `soliloquy.local`/`.internal`
    automatically instead of relying on mDNS support, needs your router's admin login, not
    something doable from here.

- [ ] Decide whether to turn on `TRANSCRIPT_ENCRYPTION_KEY` (see `.env.example`). If you turn it
      on, run `scripts/encrypt_existing_transcripts.py` once afterward to encrypt whatever's
      already in the database -- turning the key on alone only affects new entries going forward.
- [ ] Decide whether to set `MEDIA_RETENTION_DAYS` (see `.env.example`) if you want old audio/
      video auto-deleted from object storage after N days. Off by default, keeps media forever.
- [ ] In `landonkea-makeItSoNumberOne`: say "Computer, enroll my voice as [your name]" once (and
      have anyone else in the household do the same under their own name) to actually start
      getting per-speaker journal entries -- nothing's enrolled yet, so `speaker` stays blank on
      every entry until you do this. Needs `pip install paho-mqtt` in that repo's venv if you
      haven't already (still commented out in its `requirements.txt` by default).

## Not started yet (no immediate plan, no urgency)
- [ ] Move from self-hosted Postgres/MinIO to managed cloud (Supabase/Neon + Cloudflare R2),
      same interfaces either way, a config change not a rewrite, whenever you're ready. See
      README's Backups section for the same Neon-vs-Supabase reasoning applied to a primary
      database instead of a backup target.
- [ ] Face/expression analysis of stored video, a genuinely new analyzer, not built yet.
- [ ] Native mobile app(s), would reuse the same backend API, not a rewrite, once the web app has
      proven the idea is worth the extra platform work.
