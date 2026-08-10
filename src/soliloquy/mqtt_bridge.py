# ───────────────────────────────────────────────────────────────────
# mqtt_bridge.py, turns an MQTT message into a journal entry
# ───────────────────────────────────────────────────────────────────
# The Soliloquy side of the voice-triggered entry pipeline:
# landonkea-makeItSoNumberOne's `journal_entry` plugin (see that
# repo's desktop/plugins/examples/journal_entry_plugin.py) publishes
# a JSON message on $MQTT_TOPIC when it hears "Computer, journal
# entry: ..."; this module subscribes and turns that into a real
# Entry via actions.add_entry() -- the exact same function the web
# app's "typed entry" form calls, so a voice-triggered entry and a
# browser-typed one go through identical rules.
#
# Payload is text-only ({"text": "..."}) -- makeItSoNumberOne already
# transcribed the voice command before publishing, so relaying that
# transcript is the simplest, most robust v1. Sending raw audio over
# MQTT is a heavier future enhancement, not needed for this to work
# end-to-end.
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import paho.mqtt.client as mqtt

from .actions import DEFAULT_DATABASE_URL, add_entry
from .entry import Entry
from .storage import EntryStore

logger = logging.getLogger(__name__)

DEFAULT_MQTT_HOST = "localhost"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TOPIC = "soliloquy/journal"


def handle_message(payload: bytes, database_url: Optional[str] = None) -> Optional[Entry]:
    """Turn one MQTT message payload into a saved Entry. Pulled out as
    its own function (same pattern as scheduler.py's
    run_scheduled_analysis) so it's directly testable without a real
    broker. Logs and returns None on a malformed payload rather than
    raising -- a bad message shouldn't crash the listener."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Ignoring non-JSON MQTT message: %r", payload)
        return None

    text = data.get("text", "").strip() if isinstance(data, dict) else ""
    if not text:
        logger.warning("Ignoring MQTT message with no usable \"text\": %r", data)
        return None

    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    with EntryStore(database_url) as store:
        entry = add_entry(store, text)
    logger.info("Saved entry %s from MQTT (%d words).", entry.id, entry.word_count)
    return entry


def start_mqtt_listener() -> mqtt.Client:
    host = os.environ.get("MQTT_HOST", DEFAULT_MQTT_HOST)
    port = int(os.environ.get("MQTT_PORT", DEFAULT_MQTT_PORT))
    topic = os.environ.get("MQTT_TOPIC", DEFAULT_MQTT_TOPIC)

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(topic)
            logger.info("MQTT listener connected to %s:%s, subscribed to %r.", host, port, topic)
        else:
            logger.warning("MQTT connect failed: %s", reason_code)

    def on_message(client, userdata, message):
        handle_message(message.payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port)
    client.loop_start()
    return client
