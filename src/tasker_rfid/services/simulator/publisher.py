"""Publishes generated reads to MQTT, at the speed a real reader would.

Message format — one JSON object per read, on topic `<base>/<reader_id>`:

    {
      "tid":        "E28011702000A7F312345678",
      "epc":        "3034F4A2B1C80D0000000001",
      "reader_id":  "READER-EXIT",
      "antenna_id": 3,
      "rssi":       -52,
      "read_at":    "2026-09-03T14:22:31.482000+00:00"
    }

The six fields are exactly the six columns an ingest service has to fill in
`reads_raw` (SPEC.md section 3). SPEC.md section 4 requires layer 1 to do
nothing but validate and insert, so the message deliberately carries nothing
that would need interpreting first.

`portal` is not in the message on purpose. A reader knows it has antennas;
it does not know the warehouse calls antennas 3 and 4 "the exit".
`reads_raw` has no portal column either — portal first appears on
`observations`, once config/tasker.yaml has been consulted.

Published at QoS 1 (at-least-once). Warehouse networks drop out and a lost
dispatch read is an unrecoverable inventory error (SPEC.md 2.4), so losing
a message is unacceptable. A duplicated message is harmless: the whole point
of the state machine in SPEC.md 2.3 is that seeing the same tag twice is a
no-op.
"""

import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import paho.mqtt.client as mqtt

from .model import GateEvent, Read

QOS_AT_LEAST_ONCE = 1


@dataclass(frozen=True)
class Message:
    """One MQTT message and the moment it should be sent."""

    topic: str
    payload: str
    at: datetime


def read_to_payload(read: Read) -> str:
    """Serialise one read into the JSON an ingest service will receive."""
    return json.dumps(
        {
            "tid": read.tid,
            "epc": read.epc,
            "reader_id": read.reader_id,
            "antenna_id": read.antenna_id,
            "rssi": read.rssi,
            "read_at": read.read_at.isoformat(),
        }
    )


def gate_event_to_payload(event: GateEvent) -> str:
    """Serialise one beam-break event.

    Same shape of decision as a read: the four fields are the four columns
    of gate_events. The gate reports which beam of which gate changed and
    when; which portal that gate belongs to is warehouse configuration,
    exactly as antenna numbers are.
    """
    return json.dumps(
        {
            "gate_id": event.gate_id,
            "beam": event.beam,
            "state": event.state,
            "occurred_at": event.occurred_at.isoformat(),
        }
    )


def read_messages(reads: Sequence[Read], topic_base: str) -> list[Message]:
    return [
        Message(f"{topic_base}/{r.reader_id}", read_to_payload(r), r.read_at)
        for r in reads
    ]


def gate_messages(events: Sequence[GateEvent], topic_base: str) -> list[Message]:
    return [
        Message(f"{topic_base}/{e.gate_id}", gate_event_to_payload(e), e.occurred_at)
        for e in events
    ]


def print_messages(messages: Sequence[Message]) -> None:
    """Dry run: write the messages to the screen instead of publishing them."""
    for message in messages:
        print(f"{message.topic}  {message.payload}")


def publish_messages(
    messages: Sequence[Message],
    *,
    host: str,
    port: int,
    username: str = "",
    password: str = "",
    speed: float = 1.0,
) -> None:
    """Publish messages to MQTT, pacing them to match their own timestamps.

    Tag reads and beam events share one timeline, so a gate crossing lands
    in the middle of the reads it belongs to, the way it would in the
    warehouse.

    speed=1.0 publishes in real time — a 1.8 second pass takes 1.8 seconds.
    Higher values compress it; speed=0 publishes as fast as the broker will
    accept, which is what the throughput test wants.
    """
    if not messages:
        print("nothing to publish", file=sys.stderr)
        return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if username:
        client.username_pw_set(username, password)

    try:
        client.connect(host, port)
    except OSError as exc:
        sys.exit(
            f"ERROR: could not reach the MQTT broker at {host}:{port} ({exc}).\n"
            "Is it running? Try: docker compose up -d"
        )

    client.loop_start()

    first_at = messages[0].at
    started = time.monotonic()
    published = 0
    try:
        for message in messages:
            if speed > 0:
                # Where this message belongs on the wall clock, relative to
                # when we started publishing.
                due = (message.at - first_at).total_seconds() / speed
                behind = due - (time.monotonic() - started)
                if behind > 0:
                    time.sleep(behind)
            client.publish(message.topic, message.payload, qos=QOS_AT_LEAST_ONCE)
            published += 1
    finally:
        # Wait for the queue to drain so we do not report messages we dropped.
        client.loop_stop()
        client.disconnect()

    wall_seconds = time.monotonic() - started
    rate = published / wall_seconds if wall_seconds > 0 else float(published)
    print(
        f"published {published} messages "
        f"in {wall_seconds:.2f}s ({rate:.0f} messages/sec)"
    )
