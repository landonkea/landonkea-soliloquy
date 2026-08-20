# ───────────────────────────────────────────────────────────────────
# mqtt_bridge.py, turns MQTT messages into journal entries (and back)
# ───────────────────────────────────────────────────────────────────
# The Soliloquy side of the voice-triggered entry pipeline:
# landonkea-makeItSoNumberOne's `journal_entry` plugin (see that
# repo's desktop/plugins/examples/journal_entry_plugin.py) publishes
# a JSON message on $MQTT_TOPIC when it hears "Computer, journal
# entry: ..."; this module subscribes and turns that into a real
# Entry via actions.add_entry()/append_or_add_entry() -- the exact
# same functions the web app's "typed entry" form and MQTT "append"
# messages call, so a voice-triggered entry and a browser-typed one go
# through identical rules.
#
# Payload is text-only ({"text": "..."}) -- makeItSoNumberOne already
# transcribed the voice command before publishing, so relaying that
# transcript is the simplest, most robust v1. Sending raw audio over
# MQTT is a heavier future enhancement, not needed for this to work
# end-to-end.
#
# Three topics now, not one (FEATURE_IDEAS.md items 1-4):
#   $MQTT_TOPIC              -- write a new entry, or append to today's
#                                (see "type" in the payload below)
#   $MQTT_TOPIC/ack          -- published back after handling a write,
#                                success or a plain-language reason it
#                                failed, so the voice assistant can say
#                                "Got it, saved" instead of trusting a
#                                fire-and-forget publish
#   $MQTT_TOPIC/query        -- "what have I been journaling about" as
#                                a spoken question, not just a spoken
#                                entry; publishes a short summary back
#                                on $MQTT_TOPIC/query/response
#
# Payload shape for the write topic:
#   {"text": "...", "type": "new"}     -- default, same as before
#   {"text": "...", "type": "append"}  -- appends to the most recent
#                                          entry from today if one
#                                          exists within the last hour
#                                          with the same speaker (see
#                                          actions.append_or_add_entry)
#   {"speaker": "Landon", ...}          -- optional, see entry.py's
#                                          docstring on multi-speaker
#                                          households
#
# Durability (item 4): the client below connects with a fixed
# client_id and clean_session=False, and subscribes at QoS 1 --
# Mosquitto then queues messages for this client_id while Soliloquy's
# listener is offline (asleep Mac, Docker not up yet) and redelivers
# them on reconnect, instead of silently dropping them. That's the
# broker-side half; the PUBLISHER also has to publish at QoS>=1 with
# its own persistent session for a message sent while Soliloquy is
# unreachable to survive at all -- see makeItSoNumberOne's
# journal_entry_plugin.py for that half.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import paho.mqtt.client as mqtt

from .actions import DEFAULT_DATABASE_URL, add_entry, analyze_range, append_or_add_entry
from .analyzer import Analyzer, NoEntriesError, get_default_analyzer
from .entry import Entry
from .storage import EntryStore

logger = logging.getLogger(__name__)

DEFAULT_MQTT_HOST = "localhost"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TOPIC = "soliloquy/journal"
MQTT_CLIENT_ID = "soliloquy-bridge"

DEFAULT_QUERY_DAYS = 7


@dataclass
class WriteOutcome:
    entry: Optional[Entry]
    appended: bool
    error: Optional[str]  # human-readable reason, None on success


def _process_write(payload: bytes, database_url: Optional[str] = None) -> WriteOutcome:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Ignoring non-JSON MQTT message: %r", payload)
        return WriteOutcome(None, False, "message wasn't valid JSON")

    if not isinstance(data, dict):
        logger.warning("Ignoring MQTT message that isn't a JSON object: %r", data)
        return WriteOutcome(None, False, "message must be a JSON object")

    text = data.get("text", "").strip()
    if not text:
        logger.warning("Ignoring MQTT message with no usable \"text\": %r", data)
        return WriteOutcome(None, False, "message had no usable \"text\"")

    speaker = data.get("speaker") or None
    message_type = data.get("type", "new")

    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    with EntryStore(database_url) as store:
        if message_type == "append":
            entry, appended = append_or_add_entry(store, text, speaker=speaker)
        else:
            entry, appended = add_entry(store, text, speaker=speaker), False

    logger.info(
        "%s entry %s from MQTT (%d words).", "Appended to" if appended else "Saved", entry.id, entry.word_count
    )
    return WriteOutcome(entry, appended, None)


def handle_message(payload: bytes, database_url: Optional[str] = None) -> Optional[Entry]:
    """Turn one MQTT message payload into a saved (or appended-to)
    Entry. Pulled out as its own function (same pattern as
    scheduler.py's run_scheduled_analysis) so it's directly testable
    without a real broker. Kept as the stable public entry point --
    _process_write carries the richer (entry, appended, error) result
    the ack topic needs; this stays a plain Optional[Entry] for
    backward compatibility with every existing caller/test."""
    return _process_write(payload, database_url).entry


def handle_query(
    payload: bytes, database_url: Optional[str] = None, analyzer: Optional[Analyzer] = None,
) -> Optional[dict]:
    """"What have I been journaling about lately" as a spoken question.
    Payload: {"days": 7} (defaults to DEFAULT_QUERY_DAYS if omitted or
    unparseable) -- deliberately a plain integer, not a natural-
    language range like "last week"; makeItSoNumberOne's own AI action
    already interprets the spoken question, so it's the one turning
    "last week" into a day count before publishing, the same division
    of labor as the write topic relaying an already-transcribed
    string. Returns a plain dict (not AnalysisResult) since this is
    what gets JSON-published back on $MQTT_TOPIC/query/response, or
    None if there's nothing to say (empty range, or the analyzer
    failed) -- logged either way, never raised, same reasoning as
    handle_message: a bad query shouldn't crash the listener."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = {}
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, int) or days <= 0:
        days = DEFAULT_QUERY_DAYS

    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    analyzer = analyzer or get_default_analyzer()

    with EntryStore(database_url) as store:
        try:
            result = analyze_range(store, analyzer, days)
        except NoEntriesError:
            logger.info("MQTT query: no entries in the last %s day(s).", days)
            return None
        except RuntimeError as exc:
            logger.warning("MQTT query failed: %s", exc)
            return None

    return {
        "days": days,
        "entry_count": result.entry_count,
        "summary": result.summary,
        "mood_notes": result.mood_notes,
        "key_topics": result.key_topics,
    }


def start_mqtt_listener() -> mqtt.Client:
    host = os.environ.get("MQTT_HOST", DEFAULT_MQTT_HOST)
    port = int(os.environ.get("MQTT_PORT", DEFAULT_MQTT_PORT))
    topic = os.environ.get("MQTT_TOPIC", DEFAULT_MQTT_TOPIC)
    ack_topic = f"{topic}/ack"
    query_topic = f"{topic}/query"
    query_response_topic = f"{topic}/query/response"

    # client_id fixed (not None/auto-generated) + clean_session=False:
    # Mosquitto ties a persistent session, and any QoS>=1 messages
    # published to it while offline, to this exact client_id. An
    # auto-generated one would get a fresh, empty session every
    # reconnect, silently defeating the durability this is here for.
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID, clean_session=False,
    )

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe([(topic, 1), (query_topic, 1)])
            logger.info("MQTT listener connected to %s:%s, subscribed to %r and %r.", host, port, topic, query_topic)
        else:
            logger.warning("MQTT connect failed: %s", reason_code)

    def on_message(client, userdata, message):
        if message.topic == query_topic:
            response = handle_query(message.payload)
            if response is not None:
                client.publish(query_response_topic, json.dumps(response), qos=1)
            return

        outcome = _process_write(message.payload)
        ack = (
            {"status": "ok", "entry_id": outcome.entry.id, "appended": outcome.appended}
            if outcome.entry is not None
            else {"status": "error", "reason": outcome.error}
        )
        client.publish(ack_topic, json.dumps(ack), qos=1)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port)
    client.loop_start()
    return client
