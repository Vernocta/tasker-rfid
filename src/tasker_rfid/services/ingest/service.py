"""Ingest: MQTT to reads_raw. SPEC.md section 4, layer 1.

    Subscribe to MQTT, validate, insert into reads_raw. Nothing else.
    Must never block or drop.

Those two requirements pull against each other, so here is how they are
reconciled.

NEVER BLOCK. The MQTT callback does almost nothing: it puts the raw bytes
on an in-memory queue and returns. Decoding, validating and writing all
happen on a separate writer thread. Nothing touches the database on the
network thread, so a slow database cannot stall the MQTT client.

NEVER DROP. The writer acknowledges an MQTT message only after Postgres
has committed it. Until then the message still belongs to the broker,
which will redeliver it if this service crashes or is restarted. If
Postgres is unreachable the writer simply stops acknowledging: unacked
messages accumulate at the broker, the broker stops sending, and the
system applies backpressure instead of losing reads. When Postgres comes
back the same batch is retried.

The cost of that choice, stated plainly: during a database outage this
service slows down and eventually stops accepting reads, rather than
accepting reads it cannot keep. For stock control that is the right way
round.

WHAT THIS SERVICE DOES NOT DO. No debouncing, no direction, no container
lookup, no status changes. Duplicate reads are inserted as duplicate rows
on purpose: reads_raw is the append-only record of what the antennas
actually heard (SPEC.md section 3), and collapsing duplicates is layer 2's
job.
"""

import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import paho.mqtt.client as mqtt
import psycopg
from dotenv import load_dotenv

from .validation import InvalidRead, RawRead, parse_read

log = logging.getLogger("ingest")

QUEUE_MAX = 50_000
"""Reads held in memory awaiting a write.

Reached only if the database is far behind. Past this the MQTT callback
waits, which is deliberate backpressure, not a stall to be tuned away.
"""

BATCH_MAX_ROWS = 500
BATCH_MAX_WAIT_S = 0.25
"""Write whichever comes first: 500 reads, or a quarter second's worth.

Batching is what makes 900 reads/sec affordable. The wait keeps a quiet
portal's reads from sitting in memory.
"""

DB_RETRY_INITIAL_S = 1.0
DB_RETRY_MAX_S = 30.0
SHUTDOWN_DB_GRACE_S = 20.0
"""How long to keep retrying a failed write while shutting down."""

STATS_INTERVAL_S = 10.0

INSERT_SQL = """
    INSERT INTO reads_raw (tid, epc, reader_id, antenna_id, rssi, read_at)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


def psycopg_dsn(database_url: str) -> str:
    """Turn a SQLAlchemy URL into one psycopg understands.

    The rest of the project uses SQLAlchemy, so .env holds
    postgresql+psycopg://... . This service talks to psycopg directly
    because the insert path is the hot path and there is nothing to gain
    from a layer above it here.
    """
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class Ingest:
    """Reads from MQTT, writes to reads_raw, acknowledges only once committed."""

    def __init__(
        self,
        *,
        database_url: str,
        mqtt_host: str,
        mqtt_port: int,
        topic_base: str,
        client_id: str,
        username: str = "",
        password: str = "",
    ) -> None:
        self.dsn = psycopg_dsn(database_url)
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.topic = f"{topic_base}/#"
        self.username = username
        self.password = password

        self.pending: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.stop_event = threading.Event()
        self.writer_failed = threading.Event()

        self.inserted = 0
        self.rejected = 0
        self.conn: psycopg.Connection | None = None

        # clean_session=False with a fixed client id is what makes the broker
        # hold messages for us while this service is down, and redeliver
        # anything we never acknowledged.
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=False,
        )
        # Do not acknowledge on receipt. We acknowledge after the commit.
        self.client.manual_ack_set(True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    # -- MQTT side. Keep everything here short. --------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT connection refused: %s", reason_code)
            return
        # Re-subscribe on every connect, so a reconnect cannot leave us
        # silently subscribed to nothing.
        client.subscribe(self.topic, qos=1)
        log.info("connected to MQTT, subscribed to %s", self.topic)

    def on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.warning("MQTT disconnected (%s); paho will reconnect", reason_code)

    def on_message(self, client, userdata, message):
        """Hand the payload to the writer thread and return immediately.

        This blocks only if the queue is full, which means the database is
        tens of thousands of reads behind. Blocking then is the intended
        backpressure: it slows the broker down instead of discarding reads.
        """
        self.pending.put((message.payload, message.mid, message.qos))

    # -- Writer side. Everything slow happens here. ----------------------

    def collect_batch(self) -> list[tuple[bytes, int, int]]:
        """Gather up to BATCH_MAX_ROWS messages, waiting at most BATCH_MAX_WAIT_S."""
        batch: list[tuple[bytes, int, int]] = []
        deadline = time.monotonic() + BATCH_MAX_WAIT_S
        while len(batch) < BATCH_MAX_ROWS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self.pending.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    def validate_batch(
        self, batch: list[tuple[bytes, int, int]]
    ) -> tuple[list[RawRead], list[tuple[int, int]]]:
        """Split a batch into rows to insert and messages to acknowledge.

        A message that cannot become a row is logged in full and then
        acknowledged. It is not a lost read — it was never a read. Leaving
        it unacknowledged would have the broker redeliver the same broken
        message forever and wedge the pipeline behind it.
        """
        rows: list[RawRead] = []
        acks: list[tuple[int, int]] = []
        for payload, mid, qos in batch:
            acks.append((mid, qos))
            try:
                decoded = json.loads(payload)
                read, warnings = parse_read(decoded)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.rejected += 1
                log.error("REJECTED (not JSON): %s | payload=%r", exc, payload[:500])
                continue
            except InvalidRead as exc:
                self.rejected += 1
                log.error("REJECTED (%s) | payload=%r", exc, payload[:500])
                continue
            for warning in warnings:
                log.warning("%s | tid=%s", warning, read.tid)
            rows.append(read)
        return rows, acks

    def connect_db(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn, autocommit=False)
        return self.conn

    def write_batch(self, rows: list[RawRead]) -> bool:
        """Insert and commit a batch, retrying until it succeeds.

        Returns False only if we gave up during shutdown, in which case the
        rows were never acknowledged and the broker still holds them.
        """
        delay = DB_RETRY_INITIAL_S
        gave_up_at = None
        while True:
            try:
                conn = self.connect_db()
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, [row.as_row() for row in rows])
                conn.commit()
                self.inserted += len(rows)
                return True
            except psycopg.Error as exc:
                log.error(
                    "database write failed (%s); %d reads held, retrying in %.0fs. "
                    "Nothing is acknowledged, so no read is lost.",
                    exc,
                    len(rows),
                    delay,
                )
                try:
                    if self.conn is not None:
                        self.conn.close()
                except psycopg.Error:
                    pass
                self.conn = None

                if self.stop_event.is_set():
                    gave_up_at = gave_up_at or time.monotonic() + SHUTDOWN_DB_GRACE_S
                    if time.monotonic() >= gave_up_at:
                        log.error(
                            "shutting down with %d reads uncommitted. They were never "
                            "acknowledged and remain queued at the broker; they will be "
                            "redelivered when this service restarts.",
                            len(rows),
                        )
                        return False
                time.sleep(delay)
                delay = min(delay * 2, DB_RETRY_MAX_S)

    def writer_loop(self) -> None:
        """Batch, validate, write, then acknowledge. In that order, always."""
        last_stats = time.monotonic()
        try:
            while not (self.stop_event.is_set() and self.pending.empty()):
                batch = self.collect_batch()
                if batch:
                    rows, acks = self.validate_batch(batch)
                    if rows and not self.write_batch(rows):
                        return  # gave up during shutdown; deliberately no ack
                    # Only now is it safe to tell the broker we have these.
                    for mid, qos in acks:
                        self.client.ack(mid, qos)

                if time.monotonic() - last_stats >= STATS_INTERVAL_S:
                    log.info(
                        "inserted=%d rejected=%d queued=%d",
                        self.inserted,
                        self.rejected,
                        self.pending.qsize(),
                    )
                    last_stats = time.monotonic()
        except Exception:
            log.exception("writer thread died; shutting down so nothing is lost")
            self.writer_failed.set()
            self.stop_event.set()

    # -- Lifecycle -------------------------------------------------------

    def run(self) -> int:
        try:
            self.client.connect(self.mqtt_host, self.mqtt_port)
        except OSError as exc:
            log.error(
                "cannot reach the MQTT broker at %s:%s (%s). Is it running?",
                self.mqtt_host,
                self.mqtt_port,
                exc,
            )
            return 1

        writer = threading.Thread(target=self.writer_loop, name="writer", daemon=True)
        writer.start()
        self.client.loop_start()
        log.info("ingest running; waiting for reads")

        self.stop_event.wait()

        # Drain before disconnecting: acknowledgements have to reach the
        # broker over a live connection.
        log.info("stopping; draining %d queued reads", self.pending.qsize())
        writer.join(timeout=SHUTDOWN_DB_GRACE_S + 30)
        self.client.loop_stop()
        self.client.disconnect()
        if self.conn is not None and not self.conn.closed:
            self.conn.close()
        log.info("stopped. inserted=%d rejected=%d", self.inserted, self.rejected)
        return 1 if self.writer_failed.is_set() else 0

    def request_stop(self, *_args) -> None:
        self.stop_event.set()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("ERROR: DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

    service = Ingest(
        database_url=database_url,
        mqtt_host=os.getenv("MQTT_HOST", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        topic_base=os.getenv("MQTT_TOPIC", "tasker/reads"),
        client_id=os.getenv("INGEST_CLIENT_ID", "tasker-ingest"),
        username=os.getenv("MQTT_USERNAME", ""),
        password=os.getenv("MQTT_PASSWORD", ""),
    )
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    sys.exit(service.run())


if __name__ == "__main__":
    main()
