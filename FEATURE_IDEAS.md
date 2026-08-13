# Feature ideas

Not a roadmap, nothing here is scheduled. A working list of things that would genuinely fit
Soliloquy specifically (voice-first journaling, local transcription, sharing flags per audience,
the MQTT bridge to `makeItSoNumberOne`), rather than generic app features that happen to apply.
Move anything here into `CHECKLIST.md` when it's actually being worked on.

## Around the MQTT bridge

1. **Acknowledge entries back over MQTT.** `mqtt_bridge.py` saves an entry today and that's the
   end of it. `makeItSoNumberOne` has no idea whether it worked. Publishing a short confirmation
   to `soliloquy/journal/ack` (success, or a reason it failed) would let the voice assistant
   actually say "Got it, saved" instead of silently trusting a fire-and-forget publish.

2. **A query topic, not just a write topic.** Right now the bridge only turns messages into new
   entries. A `soliloquy/journal/query` topic that accepts something like "what did I journal
   about last week" and publishes back a short summary (built from the same `report_range`
   `actions.py` already exposes) would let "Computer, what have I been thinking about lately"
   work as a spoken question, not just a spoken entry.

3. **Append-to-today instead of always-new.** "Computer, journal entry: also I forgot to
   mention..." currently creates a second, disconnected entry. A message type that appends to
   the most recent entry from today (if one exists within, say, the last hour) would match how
   people actually think out loud: the afterthought a minute later.

4. **A retry queue for MQTT drops.** If Soliloquy's listener is down when
   `makeItSoNumberOne` publishes (this Mac asleep, Docker not running yet), that entry is gone.
   Nothing today re-delivers it. A persistent MQTT session (QoS 1, `clean_session=False`) plus a
   local buffer on the publisher side would mean a journal entry never silently disappears just
   because the two machines weren't both awake at the same moment.

5. **Multi-speaker support.** The bridge assumes one journaler. If `makeItSoNumberOne` ever adds
   voice identification, the MQTT payload could carry a `speaker` field, and Soliloquy could keep
   entries separated per household member instead of merging everyone into one stream.

## Making use of what's already collected

6. **Full-text search across entries.** Postgres already has `tsvector` support built in and
   there's no search box on the Entries page today. Once there are a few hundred entries,
   scrolling to find "that thing I said about my sister in March" stops working. A search bar
   backed by a `tsvector` column would be a small addition on top of storage that's already there.

7. **Per-entry topic tags, not just per-report ones.** The analyzer already extracts "key
   topics" for a date range in `AnalysisResult`, but that's computed fresh each run and thrown
   away at the entry level. Storing topics per entry (even as a simple text array) would let the
   Entries page filter by topic, "show me everything I've said about work," directly.

8. **A mood trend line on the Analysis page.** `AnalysisSnapshotStore` already keeps a history of
   past snapshots, and that data is sitting there unused beyond showing the latest one. A small
   chart plotting mood/sentiment across snapshots over time would turn "here's today's summary"
   into "here's how this has actually been trending," which is closer to what makes a journaling
   habit feel worth keeping up.

9. **"On this day" resurfacing.** Pull up whatever was journaled exactly a year (or month) ago
   on today's date, shown quietly on the Entries page. Cheap to build against the existing
   `range_between` query, and it's one of the few things that reliably pulls people back into a
   journal they'd otherwise stop opening.

10. **Punctuation and paragraph cleanup after transcription.** Raw Whisper output is often a
    single unbroken run of text with sparse punctuation. A lightweight cleanup pass (rule-based,
    or one more call through whichever free analyzer is already configured) before storing the
    transcript would make entries meaningfully more readable in the exported reports, without
    changing what was actually said.

## Sharing and reports

11. **Expiring signed links for reports.** Handing a PDF report to a therapist today means either
    they get an app account or you email the file yourself. A signed, time-limited URL
    (`/reports/{token}`, expires after N days) would let you share a specific report without
    giving provider-side access to anything else in the app.

12. **Per-audience analysis instructions.** The two sharing flags (`shareable_with_partner`,
    `shareable_with_provider`) already filter which entries an audience's report can see, but the
    analyzer prompt itself is identical regardless of audience. Letting each audience carry its
    own short instruction ("focus on patterns relevant to therapy, not day-to-day detail") would
    make provider reports read less like a raw summary and more like something meant for that
    specific reader.

13. **Scheduled report generation and delivery.** `scheduler.py` already runs analysis on a
    timer; a monthly variant that generates a PDF report and saves it (or, later, emails it) on
    the 1st of each month would give a standing record without remembering to click "Generate."

14. **A streak or cadence indicator, understated.** Not a gamified badge system, just a quiet
    line ("journaled 4 of the last 7 days") near the New Entry prompt. The whole premise of this
    app is lowering friction to journal regularly; a small honest signal about actual consistency
    fits that better than a generic streak counter would.

## Storage, privacy, and housekeeping

15. **Encrypt transcripts at rest.** The README already calls out that audio staying local is a
    real privacy requirement, not just a preference. The transcript text in Postgres is the
    other half of that same sensitivity and isn't encrypted today. Encrypting the `transcript`
    column with a locally-held key (application-level, not just relying on disk encryption)
    would close that gap without changing the query patterns `actions.py` already relies on.

16. **Opt-in retention policy for raw audio/video.** Once an entry has been transcribed,
    analyzed, and backed up, the original audio/video file in MinIO is mostly there for playback
    and doesn't need to live forever. An opt-in setting to auto-delete media older than N days
    (keeping the transcript, which is the part actually used for analysis and reports) would keep
    MinIO's storage growth in check, especially once video entries start accumulating.

17. **A "next analysis run" indicator.** The Analysis page shows the most recent snapshot but
    gives no sense of when the next one will land. Since the interval is already just
    `$ANALYSIS_INTERVAL_HOURS`, showing "next run in ~2h" would cut down on wondering whether
    today's analysis is stuck versus just not due yet.

18. **A desktop notification when a new snapshot lands.** The Analysis page is pull-only today.
    Nothing tells you a new summary exists until you open it. A local notification (macOS
    `osascript`, since this already runs as a local-first Mac app) fired from `scheduler.py` when
    a scheduled run completes would close that loop without adding a mobile push infrastructure
    this app doesn't otherwise need.
